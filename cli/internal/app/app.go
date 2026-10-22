// Package app 实现 amane CLI 的命令解析与派发.
//
// 命令表固定, 不随实例工具变化: CLI 不认识任何工具名, 只把端点当作目标.
package app

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"github.com/sqzw-x/amane/cli/internal/api"
	"github.com/sqzw-x/amane/cli/internal/config"
	"github.com/sqzw-x/amane/cli/internal/httpx"
)

// 退出码.
//
// 0 表示请求已完成, 业务结果仍在响应体里; 4 表示服务端拒绝了这次请求 (4xx/5xx),
// 与「工具返回了 error」不是一回事. 本地失败 (配置损坏、目录不可写) 单独给 1,
// 免得让调用方以为该去修网络.
const (
	ExitOK         = 0
	ExitLocal      = 1
	ExitUsage      = 2
	ExitConnection = 3
	ExitServer     = 4
)

// defaultHTTPTimeout 取统一的默认超时, 见 internal/httpx.
const defaultHTTPTimeout = httpx.DefaultTimeout

// Env 是 Run 的依赖注入点: 标准输入输出、环境变量、时钟与平台信息.
// 零值字段在 Run 入口补齐.
type Env struct {
	Stdin           io.Reader
	Stdout          io.Writer
	Stderr          io.Writer
	Getenv          func(string) string
	StdinIsTerminal bool
	Version         string

	// 下列字段供测试覆盖, 为空时取真实值.
	ConfigPath       string
	HTTPClient       *http.Client
	Now              func() time.Time
	Sleep            func(context.Context, time.Duration) error
	ExecutablePath   string
	HomeDir          string
	UpgradeBaseURL   string
	UpgradeLatestURL string
	GOOS             string
	GOARCH           string
}

func (e *Env) fillDefaults() {
	if e.Stdin == nil {
		e.Stdin = os.Stdin
	}
	if e.Stdout == nil {
		e.Stdout = os.Stdout
	}
	if e.Stderr == nil {
		e.Stderr = os.Stderr
	}
	if e.Getenv == nil {
		e.Getenv = os.Getenv
	}
	if e.Version == "" {
		e.Version = "dev"
	}
	if e.GOOS == "" {
		e.GOOS = runtime.GOOS
	}
	if e.GOARCH == "" {
		e.GOARCH = runtime.GOARCH
	}
}

// cliError 携带退出码.
type cliError struct {
	code int
	msg  string
}

func (e *cliError) Error() string { return e.msg }

func usagef(format string, a ...any) *cliError {
	return &cliError{code: ExitUsage, msg: fmt.Sprintf(format, a...)}
}

func connf(format string, a ...any) *cliError {
	return &cliError{code: ExitConnection, msg: fmt.Sprintf(format, a...)}
}

// Run 执行一次命令并返回退出码.
func Run(args []string, env *Env) int {
	env.fillDefaults()
	p, err := parseArgs(args)
	if err != nil {
		fmt.Fprintf(env.Stderr, "amane: %v\n\n%s", err, usageText)
		return ExitUsage
	}
	if p.version {
		fmt.Fprintf(env.Stdout, "amane %s (%s/%s)\n", env.Version, env.GOOS, env.GOARCH)
		return ExitOK
	}
	if p.help {
		fmt.Fprint(env.Stdout, usageText)
		return ExitOK
	}
	if p.cmd == "" {
		fmt.Fprintf(env.Stderr, "amane: 缺少命令\n\n%s", usageText)
		return ExitUsage
	}
	if err := p.validate(); err != nil {
		fmt.Fprintf(env.Stderr, "amane: %v\n\n%s", err, usageText)
		return ExitUsage
	}

	r := &runner{env: env, p: p}
	ctx := context.Background()
	if err := r.dispatch(ctx); err != nil {
		code := ExitLocal
		var ce *cliError
		if errors.As(err, &ce) {
			code = ce.code
		}
		fmt.Fprintf(env.Stderr, "amane: %v\n", err)
		return code
	}
	return ExitOK
}

func (r *runner) dispatch(ctx context.Context) error {
	switch r.p.cmd {
	case "catalog":
		return r.cmdCatalog(ctx)
	case "schema":
		return r.cmdSchema(ctx)
	case "call":
		return r.cmdCall(ctx)
	case "wait":
		return r.cmdWait(ctx)
	case "api":
		return r.cmdAPI(ctx)
	case "login":
		return r.cmdLogin(ctx)
	case "config":
		return r.cmdConfig(ctx)
	case "doctor":
		return r.cmdDoctor(ctx)
	case "skill":
		return r.cmdSkill(ctx)
	case "upgrade":
		return r.cmdUpgrade(ctx)
	default:
		return usagef("未知命令 %q", r.p.cmd)
	}
}

// runner 承载一次命令执行的共享状态.
type runner struct {
	env     *Env
	p       *parsed
	cfg     *config.File
	cfgPath string
}

// arg 返回命令后的第 i 个位置参数, 缺失时返回空串.
func (r *runner) arg(i int) string {
	if i < len(r.p.args) {
		return r.p.args[i]
	}
	return ""
}

// requireArg 取必需的位置参数.
func (r *runner) requireArg(i int, name string) (string, error) {
	v := strings.TrimSpace(r.arg(i))
	if v == "" {
		return "", usagef("缺少参数 <%s>", name)
	}
	return v, nil
}

// config 惰性加载配置文件.
func (r *runner) config() (*config.File, error) {
	if r.cfg != nil {
		return r.cfg, nil
	}
	path := r.env.ConfigPath
	if path == "" {
		p, err := config.Path()
		if err != nil {
			return nil, err
		}
		path = p
	}
	cfg, err := config.Load(path)
	if err != nil {
		return nil, err
	}
	r.cfg = cfg
	r.cfgPath = path
	return cfg, nil
}

// server 返回归一化后的实例地址: --server 优先, 其次当前 profile.
func (r *runner) server() (string, error) {
	if raw := strings.TrimSpace(r.p.server); raw != "" {
		return config.NormalizeServer(raw)
	}
	cfg, err := r.config()
	if err != nil {
		return "", err
	}
	_, profile, ok := cfg.Select(r.p.profile)
	if !ok || strings.TrimSpace(profile.Server) == "" {
		// 与实例同机: 没配置过 profile, 但凭据就在本机数据目录里.
		// 这条路径是桌面版的常态, 引导里「无需操作」指的就是它.
		if _, path := config.LocalToken(r.env.Getenv); path != "" {
			return config.DefaultLocalServer(r.env.Getenv), nil
		}
		return "", usagef("未配置实例地址, 先运行 amane login --server <地址>")
	}
	return config.NormalizeServer(profile.Server)
}

// token 按 profile → 环境变量 → 本机 token 文件 的顺序取值.
func (r *runner) token() (string, string, error) {
	cfg, err := r.config()
	if err != nil {
		return "", "", err
	}
	_, profile, _ := cfg.Select(r.p.profile)
	token, source := config.ResolveToken(profile, r.env.Getenv)
	return token, source, nil
}

// httpTimeout 返回本次请求的 HTTP 超时. wait 的 --timeout 表示轮询截止时间,
// 因此该命令的 HTTP 超时固定为默认值.
func (r *runner) httpTimeout() time.Duration {
	if r.p.cmd == "wait" {
		return defaultHTTPTimeout
	}
	if r.p.timeoutSet {
		return r.p.timeout
	}
	return defaultHTTPTimeout
}

func (r *runner) httpClient() *http.Client {
	if r.env.HTTPClient != nil {
		return r.env.HTTPClient
	}
	return &http.Client{Timeout: r.httpTimeout()}
}

// client 构造实例客户端, 并在明文 http 指向非回环地址时提示一次.
func (r *runner) client() (*api.Client, error) {
	server, err := r.server()
	if err != nil {
		return nil, err
	}
	token, _, err := r.token()
	if err != nil {
		return nil, err
	}
	if config.PlaintextWarning(server) {
		fmt.Fprintf(r.env.Stderr, "amane: 提示: %s 是明文 http 且不在本机, token 会以明文传输, 建议改用 https 或反向代理\n", server)
	}
	return &api.Client{
		BaseURL:   server,
		Token:     token,
		UserAgent: "amane-cli/" + r.env.Version,
		HTTP:      r.httpClient(),
	}, nil
}

// connErr 把网络层失败转换为退出码 3.
func (r *runner) connErr(baseURL string, err error) error {
	if api.IsTransport(err) {
		return connf("连接实例失败 (%s): %v", baseURL, err)
	}
	return err
}

// writeBody 输出响应体. pretty 为真时缩进 JSON; 无法解析时原样输出.
func (r *runner) writeBody(body []byte) error {
	data := body
	if r.p.pretty {
		var buf bytes.Buffer
		if err := json.Indent(&buf, body, "", "  "); err == nil {
			data = buf.Bytes()
		}
	}
	return r.writeRaw(data)
}

// writeDownloadable 写出直通端点的响应, 二进制原样落盘.
//
// `amane api` 能取到任务记录包 (application/zip) 这类二进制; 统一补换行会把包写坏,
// 因此只在文本响应上补, 并且不做任何解码.
func (r *runner) writeDownloadable(resp *api.Response) error {
	body := resp.Body
	contentType := resp.Header.Get("Content-Type")
	textual := strings.HasPrefix(contentType, "application/json") || strings.HasPrefix(contentType, "text/")
	if textual && r.p.pretty {
		var buf bytes.Buffer
		if err := json.Indent(&buf, body, "", "  "); err == nil {
			body = buf.Bytes()
		}
	}
	if _, err := r.env.Stdout.Write(body); err != nil {
		return err
	}
	if textual && len(body) > 0 && body[len(body)-1] != '\n' {
		_, err := io.WriteString(r.env.Stdout, "\n")
		return err
	}
	return nil
}

// writeRaw 原样写出数据, 补一个换行.
func (r *runner) writeRaw(data []byte) error {
	if len(data) == 0 {
		return nil
	}
	if _, err := r.env.Stdout.Write(data); err != nil {
		return err
	}
	if data[len(data)-1] != '\n' {
		if _, err := io.WriteString(r.env.Stdout, "\n"); err != nil {
			return err
		}
	}
	return nil
}

// checkStatus 处理鉴权失败, 其余状态码只留诊断, 响应体照常输出.
func (r *runner) checkStatus(resp *api.Response) error {
	if resp.Status >= 200 && resp.Status < 300 {
		return nil
	}
	if api.IsAuthFailure(resp.Status) {
		return connf("鉴权失败 (HTTP %d): %s; token 由 amane login 写入, 或由环境变量 %s 提供", resp.Status, detailOf(resp.Body), config.EnvToken)
	}
	return &cliError{
		code: ExitServer,
		msg:  fmt.Sprintf("服务端拒绝 (HTTP %d): %s", resp.Status, detailOf(resp.Body)),
	}
}

// detailOf 从错误响应中取出可读说明.
func detailOf(body []byte) string {
	var payload struct {
		Detail json.RawMessage `json:"detail"`
	}
	if err := json.Unmarshal(body, &payload); err == nil && len(payload.Detail) > 0 {
		var text string
		if err := json.Unmarshal(payload.Detail, &text); err == nil {
			return text
		}
		return truncate(string(payload.Detail), 300)
	}
	return truncate(strings.TrimSpace(string(body)), 300)
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}

// executablePath 返回自身可执行文件路径.
func (r *runner) executablePath() (string, error) {
	if r.env.ExecutablePath != "" {
		return r.env.ExecutablePath, nil
	}
	path, err := os.Executable()
	if err != nil {
		return "", fmt.Errorf("无法确定自身路径: %w", err)
	}
	return filepath.Abs(path)
}

// homeDir 返回用户主目录.
func (r *runner) homeDir() (string, error) {
	if r.env.HomeDir != "" {
		return r.env.HomeDir, nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", fmt.Errorf("无法确定用户主目录: %w", err)
	}
	return home, nil
}

// writeJSON 输出 JSON 值, --pretty 时缩进.
func (r *runner) writeJSON(v any) error {
	var (
		data []byte
		err  error
	)
	if r.p.pretty {
		data, err = json.MarshalIndent(v, "", "  ")
	} else {
		data, err = json.Marshal(v)
	}
	if err != nil {
		return fmt.Errorf("序列化输出失败: %w", err)
	}
	return r.writeRaw(data)
}
