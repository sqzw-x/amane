package app

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"sort"
	"strings"

	"github.com/sqzw-x/amane/cli/internal/api"
	"github.com/sqzw-x/amane/cli/internal/config"
)

// cmdLogin 解析实例地址并写入一份 profile.
//
// token 有三个来源, 按「用户可能已经做过什么」排序: 显式给出的 (管道或终端输入)、
// 本机桌面版留下的 token 文件、现场提示用户粘贴. 本机文件这条要当场验一次鉴权 ——
// 实例未必是桌面版, 猜来的凭据写进配置只会让后续每条命令都 401.
func (r *runner) cmdLogin(ctx context.Context) error {
	cfg, err := r.config()
	if err != nil {
		return err
	}
	server, err := r.loginServer(cfg)
	if err != nil {
		return err
	}
	if config.PlaintextWarning(server) {
		fmt.Fprintf(r.env.Stderr, "amane: 提示: %s 是明文 http 且不在本机, token 会以明文传输, 建议改用 https 或反向代理\n", server)
	}

	token, source, err := r.loginToken(ctx, server)
	if err != nil {
		return err
	}
	if token == "" {
		return usagef("没有读到 token: 用管道传入, 或在终端里粘贴")
	}
	if source != "" {
		fmt.Fprintf(r.env.Stderr, "amane: token 取自 %s\n", source)
	}

	name := r.p.profile
	if name == "" {
		name = cfg.Current
	}
	cfg.Profiles[name] = config.Profile{Server: server, Token: token}
	cfg.Current = name
	if err := cfg.Save(r.cfgPath); err != nil {
		return err
	}
	fmt.Fprintf(r.env.Stderr, "amane: 已保存 %s 的凭据\n", server)
	return nil
}

// loginServer 取本次要写入的实例地址.
func (r *runner) loginServer(cfg *config.File) (string, error) {
	if raw := strings.TrimSpace(r.p.server); raw != "" {
		server, err := config.NormalizeServer(raw)
		if err != nil {
			return "", usagef("%v", err)
		}
		return server, nil
	}
	name := r.p.profile
	if name == "" {
		name = cfg.Current
	}
	existing, ok := cfg.Profiles[name]
	if !ok || strings.TrimSpace(existing.Server) == "" {
		return "", usagef("缺少 --server: amane login --server <实例地址>")
	}
	server, err := config.NormalizeServer(existing.Server)
	if err != nil {
		return "", usagef("%v", err)
	}
	return server, nil
}

// loginToken 选一个 token: 显式给出的优先, 其次本机桌面版留下的, 最后问用户.
//
// 空管道不等于「没有凭据」: 智能体的执行器常把 stdin 接到空 pipe 或 /dev/null,
// 这时仍要继续找本机 token, 否则引导承诺的「本机无需粘贴」对这类调用方直接失效.
func (r *runner) loginToken(ctx context.Context, server string) (string, string, error) {
	terminal := r.env.StdinIsTerminal
	if !terminal {
		data, err := io.ReadAll(r.env.Stdin)
		if err != nil {
			return "", "", usagef("读取标准输入失败: %v", err)
		}
		if token := strings.TrimSpace(string(data)); token != "" {
			return token, "标准输入", nil
		}
	}

	// 地址指向本机时才去找桌面版留下的凭据. 找到要当场验一次: 这个地址上跑的不一定
	// 是桌面版, 属于别处的凭据写进配置只会让后续每条命令都 401.
	if config.IsLoopback(server) {
		if token, _ := config.LocalToken(r.env.Getenv); token != "" {
			if err := r.verifyToken(ctx, server, token); err == nil {
				return token, "", nil
			} else if api.IsTransport(err) {
				return "", "", r.loginConnectError(server, err)
			}
		}
	}

	// 后面的文案要区分「实例在但不认这个凭据」与「这个地址上没有实例」, 否则会引导
	// 用户去给一台不存在的实例登录. 探活走免鉴权的健康检查.
	//
	// 只在非交互时探: 终端里由人当场粘贴, 先探一次只会让人多等一个来回.
	if !terminal {
		if err := r.pingInstance(ctx, server); err != nil {
			return "", "", r.loginConnectError(server, err)
		}
		return "", "", &cliError{
			code: ExitConnection,
			msg:  fmt.Sprintf("此实例无法自动鉴权, 需引导用户手动登录\n示例: amane login --server %s", server),
		}
	}

	fmt.Fprintln(r.env.Stderr, "请粘贴该实例的 API Token 后回车:")
	token, err := readToken(r.env.Stdin, true)
	if err != nil {
		return "", "", usagef("读取标准输入失败: %v", err)
	}
	return token, "", nil
}

// pingInstance 用免鉴权的健康检查确认该地址上有实例在跑.
func (r *runner) pingInstance(ctx context.Context, server string) error {
	client := &api.Client{BaseURL: server, UserAgent: "amane-cli/" + r.env.Version, HTTP: r.httpClient()}
	resp, err := client.Get(ctx, "/api/health")
	if err != nil {
		return err
	}
	if resp.Status < 200 || resp.Status >= 300 {
		return fmt.Errorf("健康检查返回 HTTP %d", resp.Status)
	}
	return nil
}

// loginConnectError 给出连不上时的下一步: 该确认地址或启动实例, 不是去登录.
func (r *runner) loginConnectError(server string, err error) error {
	if api.IsRefused(err) {
		return &cliError{
			code: ExitConnection,
			msg:  fmt.Sprintf("连接实例失败 (%s): 端口上没有实例在监听\n请向用户确认实例地址, 或让用户先启动实例", server),
		}
	}
	return &cliError{
		code: ExitConnection,
		msg:  fmt.Sprintf("连接实例失败 (%s): %v\n请向用户确认实例地址与网络", server, err),
	}
}

// verifyToken 用一次需要鉴权的请求确认 token 可用, 只用于自动取到的本机凭据.
//
// 端点用 /api/system/desktop: 菜单栏每三秒轮询它, 是历来版本都有的路径; 换成新端点会
// 让旧服务端上的本机登录被判成「凭据不可用」. 只有 401/403 算不可用 —— 其他状态说明不了
// token 的对错, 留给 doctor 报告.
func (r *runner) verifyToken(ctx context.Context, server, token string) error {
	client := &api.Client{BaseURL: server, Token: token, UserAgent: "amane-cli/" + r.env.Version, HTTP: r.httpClient()}
	resp, err := client.Get(ctx, "/api/system/desktop")
	if err != nil {
		return r.connErr(server, err)
	}
	if api.IsAuthFailure(resp.Status) {
		return fmt.Errorf("鉴权失败 (HTTP %d)", resp.Status)
	}
	return nil
}

func (r *runner) cmdConfig(_ context.Context) error {
	cfg, err := r.config()
	if err != nil {
		return err
	}
	switch sub := r.arg(0); sub {
	case "":
		name, profile, ok := cfg.Select(r.p.profile)
		if !ok {
			if r.p.profile != "" {
				return usagef("profile %q 不存在; 用 amane config list 查看已有 profile", name)
			}
			return usagef("未配置实例地址, 先运行 amane login --server <地址>")
		}
		token, source := config.ResolveToken(profile, r.env.Getenv)
		return r.writeJSON(struct {
			Profile     string `json:"profile"`
			Server      string `json:"server"`
			Token       string `json:"token"`
			TokenSource string `json:"token_source"`
			ConfigPath  string `json:"config_path"`
		}{
			Profile:     name,
			Server:      profile.Server,
			Token:       config.Mask(token),
			TokenSource: source,
			ConfigPath:  r.cfgPath,
		})
	case "use":
		name, err := r.requireArg(1, "profile")
		if err != nil {
			return err
		}
		if _, ok := cfg.Profiles[name]; !ok {
			return usagef("profile %q 不存在; 先运行 amane login --server <地址> --profile %s 创建", name, name)
		}
		cfg.Current = name
		if err := cfg.Save(r.cfgPath); err != nil {
			return err
		}
		fmt.Fprintf(r.env.Stderr, "amane: 已切换到 profile %s\n", name)
		return nil
	case "list":
		type entry struct {
			Name   string `json:"name"`
			Server string `json:"server"`
			Token  string `json:"token"`
		}
		names := make([]string, 0, len(cfg.Profiles))
		for name := range cfg.Profiles {
			names = append(names, name)
		}
		sort.Strings(names)
		entries := make([]entry, 0, len(names))
		for _, name := range names {
			profile := cfg.Profiles[name]
			token, _ := config.ResolveToken(profile, r.env.Getenv)
			entries = append(entries, entry{Name: name, Server: profile.Server, Token: config.Mask(token)})
		}
		return r.writeJSON(struct {
			Current  string  `json:"current"`
			Profiles []entry `json:"profiles"`
		}{Current: cfg.Current, Profiles: entries})
	default:
		return usagef("未知的 config 子命令 %q, 可用: use, list", sub)
	}
}

// readToken 读取 token: 交互终端读一行, 管道读全部.
func readToken(stdin io.Reader, terminal bool) (string, error) {
	if terminal {
		line, err := bufio.NewReader(stdin).ReadString('\n')
		// EOF 只说明没按回车就结束了, 已读到的部分仍然有效.
		if err != nil && !errors.Is(err, io.EOF) {
			return "", err
		}
		return strings.TrimSpace(line), nil
	}
	data, err := io.ReadAll(stdin)
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(string(data)), nil
}
