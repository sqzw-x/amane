package app

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/sqzw-x/amane/cli/internal/config"
)

// testToken 长度足够触发首尾保留的打码.
const testToken = "tok-0123456789abcdef"

// harness 是一次命令执行的测试环境.
type harness struct {
	env    *Env
	stdout *bytes.Buffer
	stderr *bytes.Buffer
	cfg    *config.File
	dir    string
	home   string
}

// newHarness 准备配置文件与注入点. serverURL 为空时不写入 profile.
func newHarness(t *testing.T, serverURL string) *harness {
	t.Helper()
	dir := t.TempDir()
	home := filepath.Join(dir, "home")
	if err := os.MkdirAll(home, 0o755); err != nil {
		t.Fatalf("准备主目录失败: %v", err)
	}
	cfgPath := filepath.Join(dir, "cli.json")
	cfg := &config.File{Profiles: map[string]config.Profile{}}
	if serverURL != "" {
		cfg.Profiles["default"] = config.Profile{Server: serverURL, Token: testToken}
	}
	if err := cfg.Save(cfgPath); err != nil {
		t.Fatalf("写入配置失败: %v", err)
	}
	stdout := &bytes.Buffer{}
	stderr := &bytes.Buffer{}
	return &harness{
		env: &Env{
			Stdin:           strings.NewReader(""),
			Stdout:          stdout,
			Stderr:          stderr,
			Getenv:          func(string) string { return "" },
			StdinIsTerminal: true,
			Version:         "0.1.0",
			ConfigPath:      cfgPath,
			HomeDir:         home,
			GOOS:            "linux",
			GOARCH:          "amd64",
		},
		stdout: stdout,
		stderr: stderr,
		cfg:    cfg,
		dir:    dir,
		home:   home,
	}
}

// run 执行一次命令并返回退出码.
func (h *harness) run(args ...string) int {
	return Run(args, h.env)
}

// stdin 替换标准输入.
func (h *harness) stdin(s string) *harness {
	h.env.Stdin = strings.NewReader(s)
	return h
}

// terminal 设置标准输入是否为终端.
func (h *harness) terminal(v bool) *harness {
	h.env.StdinIsTerminal = v
	return h
}

// out 返回标准输出.
func (h *harness) out() string { return h.stdout.String() }

// errOut 返回标准错误.
func (h *harness) errOut() string { return h.stderr.String() }

// writeFile 在临时目录写一个文件并返回路径.
func (h *harness) writeFile(name, content string) string {
	path := filepath.Join(h.dir, name)
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		panic(err)
	}
	return path
}

// loadConfig 读回当前配置文件.
func (h *harness) loadConfig(t *testing.T) *config.File {
	t.Helper()
	cfg, err := config.Load(h.env.ConfigPath)
	if err != nil {
		t.Fatalf("读取配置失败: %v", err)
	}
	return cfg
}

// fakeClock 是只在 Sleep 时前进的时钟, 让 wait 测试不消耗真实时间.
type fakeClock struct {
	now time.Time
}

func (c *fakeClock) Now() time.Time { return c.now }

func (c *fakeClock) Sleep(_ context.Context, d time.Duration) error {
	c.now = c.now.Add(d)
	return nil
}

// useClock 注入假时钟.
func (h *harness) useClock() *fakeClock {
	clock := &fakeClock{now: time.Unix(0, 0)}
	h.env.Now = clock.Now
	h.env.Sleep = clock.Sleep
	return clock
}

// jsonHandler 返回一个固定 JSON 响应的服务端.
func jsonHandler(t *testing.T, status int, body string, calls *int) http.HandlerFunc {
	t.Helper()
	return func(w http.ResponseWriter, r *http.Request) {
		if calls != nil {
			*calls++
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		_, _ = w.Write([]byte(body))
	}
}

// catalogBody 是一份最小的目录响应.
const catalogBody = `{"version":"0.22.1","paths":{"data_dir":"/data","log_dir":"/data/logs","db_path":"/data/amane.db","config_path":"/data/config.toml","env":["AMANE_DATA_DIR","AMANE_TOKEN"]},"tools":[{"name":"sql_explore","group":"sql","summary":"执行只读 SQL","signature":"sql: string"}]}`

// decodeJSON 解析标准输出.
func (h *harness) decodeJSON(t *testing.T, v any) {
	t.Helper()
	if err := json.Unmarshal(h.stdout.Bytes(), v); err != nil {
		t.Fatalf("标准输出不是 JSON: %v\n%s", err, h.out())
	}
}
