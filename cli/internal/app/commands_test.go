package app

import (
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/sqzw-x/amane/cli/internal/config"
)

func TestCatalogRendersIndex(t *testing.T) {
	srv := httptest.NewServer(jsonHandler(t, http.StatusOK, catalogBody, nil))
	defer srv.Close()

	h := newHarness(t, srv.URL)
	if code := h.run("catalog"); code != ExitOK {
		t.Fatalf("退出码为 %d, stderr: %s", code, h.errOut())
	}
	for _, want := range []string{"0.22.1", "sql", "sql_explore(sql: string)", "执行只读 SQL", "amane catalog <tool>"} {
		if !strings.Contains(h.out(), want) {
			t.Errorf("输出缺少 %q:\n%s", want, h.out())
		}
	}
}

func TestCatalogJSON(t *testing.T) {
	srv := httptest.NewServer(jsonHandler(t, http.StatusOK, catalogBody, nil))
	defer srv.Close()

	h := newHarness(t, srv.URL)
	if code := h.run("catalog", "--json"); code != ExitOK {
		t.Fatalf("退出码为 %d", code)
	}
	if h.out() != catalogBody+"\n" {
		t.Fatalf("输出为 %q", h.out())
	}
}

func TestCatalogPretty(t *testing.T) {
	srv := httptest.NewServer(jsonHandler(t, http.StatusOK, catalogBody, nil))
	defer srv.Close()

	h := newHarness(t, srv.URL)
	if code := h.run("catalog", "--json", "--pretty"); code != ExitOK {
		t.Fatalf("退出码为 %d", code)
	}
	if !strings.Contains(h.out(), "\n  \"version\"") {
		t.Fatalf("输出未缩进:\n%s", h.out())
	}
	h.decodeJSON(t, &map[string]any{})
}

func TestCatalogSendsBearer(t *testing.T) {
	var gotAuth string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotAuth = r.Header.Get("Authorization")
		_, _ = w.Write([]byte(`{}`))
	}))
	defer srv.Close()

	h := newHarness(t, srv.URL)
	if code := h.run("catalog"); code != ExitOK {
		t.Fatalf("退出码为 %d", code)
	}
	if gotAuth != "Bearer "+testToken {
		t.Fatalf("Authorization 为 %q", gotAuth)
	}
}

func TestCatalogAuthFailure(t *testing.T) {
	srv := httptest.NewServer(jsonHandler(t, http.StatusUnauthorized, `{"detail":"缺少或无效的 API token."}`, nil))
	defer srv.Close()

	h := newHarness(t, srv.URL)
	if code := h.run("catalog"); code != ExitConnection {
		t.Fatalf("退出码为 %d, 期望 %d", code, ExitConnection)
	}
	if h.out() != "" {
		t.Fatalf("鉴权失败不应写标准输出: %q", h.out())
	}
	if !strings.Contains(h.errOut(), "鉴权失败") || !strings.Contains(h.errOut(), "HTTP 401") {
		t.Fatalf("标准错误为 %q", h.errOut())
	}
	if strings.Contains(h.errOut(), testToken) {
		t.Fatal("token 不应出现在输出中")
	}
}

func TestNetworkFailureExitCode(t *testing.T) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("监听失败: %v", err)
	}
	addr := ln.Addr().String()
	_ = ln.Close()

	h := newHarness(t, "http://"+addr)
	if code := h.run("catalog"); code != ExitConnection {
		t.Fatalf("退出码为 %d, 期望 %d", code, ExitConnection)
	}
	if !strings.Contains(h.errOut(), "连接实例失败") {
		t.Fatalf("标准错误为 %q", h.errOut())
	}
}

func TestNoProfileIsUsageError(t *testing.T) {
	h := newHarness(t, "")
	if code := h.run("catalog"); code != ExitUsage {
		t.Fatalf("退出码为 %d, 期望 %d", code, ExitUsage)
	}
	if !strings.Contains(h.errOut(), "login") {
		t.Fatalf("标准错误为 %q", h.errOut())
	}
}

// roundTripFunc 让请求不经过网络, 用于只关心请求构造的用例.
type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestPlaintextWarning(t *testing.T) {
	h := newHarness(t, "")
	h.env.HTTPClient = &http.Client{Transport: roundTripFunc(func(*http.Request) (*http.Response, error) {
		return &http.Response{StatusCode: http.StatusOK, Body: io.NopCloser(strings.NewReader(`{}`)), Header: http.Header{}}, nil
	})}
	if code := h.run("catalog", "--server", "http://192.168.1.10:8000"); code != ExitOK {
		t.Fatalf("退出码为 %d, stderr: %s", code, h.errOut())
	}
	if !strings.Contains(h.errOut(), "明文 http") {
		t.Fatalf("标准错误为 %q", h.errOut())
	}
}

func TestCallArgumentSources(t *testing.T) {
	var bodies []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		buf, err := io.ReadAll(r.Body)
		if err != nil {
			t.Errorf("读取请求体失败: %v", err)
		}
		bodies = append(bodies, string(buf))
		_, _ = w.Write([]byte(`"OK"`))
	}))
	defer srv.Close()

	cases := []struct {
		name    string
		setup   func(h *harness) []string
		wantArg string
	}{
		{
			name: "文件",
			setup: func(h *harness) []string {
				path := h.writeFile("args.json", `{"number":"ABC-123"}`)
				return []string{"call", "update_metadata", "@" + path}
			},
			wantArg: `{"number":"ABC-123"}`,
		},
		{
			name: "标准输入连字符",
			setup: func(h *harness) []string {
				h.stdin(`{"number":"ABC-124"}`)
				return []string{"call", "update_metadata", "-"}
			},
			wantArg: `{"number":"ABC-124"}`,
		},
		{
			name: "管道自动读取",
			setup: func(h *harness) []string {
				h.stdin(`{"number":"ABC-125"}`).terminal(false)
				return []string{"call", "update_metadata"}
			},
			wantArg: `{"number":"ABC-125"}`,
		},
		{
			name: "JSON 字面量",
			setup: func(h *harness) []string {
				return []string{"call", "update_metadata", `{"number":"ABC-126"}`}
			},
			wantArg: `{"number":"ABC-126"}`,
		},
		{
			name: "空输入当作空对象",
			setup: func(h *harness) []string {
				h.stdin("").terminal(false)
				return []string{"call", "list_feeds"}
			},
			wantArg: `{}`,
		},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			bodies = nil
			h := newHarness(t, srv.URL)
			args := c.setup(h)
			if code := h.run(args...); code != ExitOK {
				t.Fatalf("退出码为 %d, stderr: %s", code, h.errOut())
			}
			if len(bodies) != 1 {
				t.Fatalf("请求次数为 %d", len(bodies))
			}
			if want := `{"args":` + c.wantArg + `}`; bodies[0] != want {
				t.Fatalf("请求体为 %s, 期望 %s", bodies[0], want)
			}
		})
	}
}

func TestCallArgumentErrors(t *testing.T) {
	srv := httptest.NewServer(jsonHandler(t, http.StatusOK, `"OK"`, nil))
	defer srv.Close()

	cases := []struct {
		name  string
		setup func(h *harness) []string
		want  string
	}{
		{
			name:  "终端且无入参",
			setup: func(h *harness) []string { return []string{"call", "sql_explore"} },
			want:  "缺少入参",
		},
		{
			name:  "非法 JSON",
			setup: func(h *harness) []string { return []string{"call", "sql_explore", "{not json"} },
			want:  "JSON 对象",
		},
		{
			name:  "非对象",
			setup: func(h *harness) []string { return []string{"call", "sql_explore", "[1,2]"} },
			want:  "JSON 对象",
		},
		{
			name:  "null",
			setup: func(h *harness) []string { return []string{"call", "sql_explore", "null"} },
			want:  "JSON 对象",
		},
		{
			name:  "文件不存在",
			setup: func(h *harness) []string { return []string{"call", "sql_explore", "@/nonexistent/args.json"} },
			want:  "读取入参文件失败",
		},
		{
			name:  "非法工具名",
			setup: func(h *harness) []string { return []string{"call", "a/b", "{}"} },
			want:  "不合法",
		},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			h := newHarness(t, srv.URL)
			if code := h.run(c.setup(h)...); code != ExitUsage {
				t.Fatalf("退出码为 %d, 期望 %d", code, ExitUsage)
			}
			if !strings.Contains(h.errOut(), c.want) {
				t.Fatalf("标准错误为 %q, 期望包含 %q", h.errOut(), c.want)
			}
		})
	}
}

func TestCallBusinessErrorStaysZero(t *testing.T) {
	srv := httptest.NewServer(jsonHandler(t, http.StatusOK, `{"error":"任务不存在"}`, nil))
	defer srv.Close()

	h := newHarness(t, srv.URL)
	if code := h.run("call", "retry_task", `{"task_id":1}`); code != ExitOK {
		t.Fatalf("退出码为 %d, 期望 0", code)
	}
	if !strings.Contains(h.out(), "任务不存在") {
		t.Fatalf("输出为 %q", h.out())
	}
}

func TestCallRejectedByServerExitsFour(t *testing.T) {
	srv := httptest.NewServer(jsonHandler(t, http.StatusUnprocessableEntity, `{"detail":[{"loc":["body","args","task_id"],"msg":"field required"}]}`, nil))
	defer srv.Close()

	h := newHarness(t, srv.URL)
	// 参数校验失败是「服务端拒绝了这次请求」, 与工具返回 error 不同, 不能报成 0.
	if code := h.run("call", "retry_task", "{}"); code != ExitServer {
		t.Fatalf("退出码为 %d, 期望 %d", code, ExitServer)
	}
	if !strings.Contains(h.errOut(), "task_id") {
		t.Fatalf("标准错误为 %q", h.errOut())
	}
}

func TestWaitPollsUntilTerminal(t *testing.T) {
	var calls int32
	statuses := []string{
		`{"id":7,"status":"queued"}`,
		`{"id":7,"status":"running"}`,
		`{"id":7,"status":"done","result":{}}`,
	}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/tasks/7" {
			t.Errorf("路径为 %q", r.URL.Path)
		}
		i := int(atomic.AddInt32(&calls, 1)) - 1
		if i >= len(statuses) {
			i = len(statuses) - 1
		}
		_, _ = w.Write([]byte(statuses[i]))
	}))
	defer srv.Close()

	h := newHarness(t, srv.URL)
	clock := h.useClock()
	if code := h.run("wait", "7"); code != ExitOK {
		t.Fatalf("退出码为 %d, stderr: %s", code, h.errOut())
	}
	if got := atomic.LoadInt32(&calls); got != 3 {
		t.Fatalf("轮询 %d 次, 期望 3 次", got)
	}
	if !strings.Contains(h.out(), `"status":"done"`) {
		t.Fatalf("输出为 %q", h.out())
	}
	if elapsed := clock.now.Sub(time.Unix(0, 0)); elapsed != 4*time.Second {
		t.Fatalf("累计等待 %s, 期望 4s", elapsed)
	}
}

func TestWaitTimeoutExitsThree(t *testing.T) {
	srv := httptest.NewServer(jsonHandler(t, http.StatusOK, `{"id":7,"status":"running"}`, nil))
	defer srv.Close()

	h := newHarness(t, srv.URL)
	h.useClock()
	if code := h.run("wait", "7", "--timeout", "5s"); code != ExitConnection {
		t.Fatalf("退出码为 %d, 期望 %d", code, ExitConnection)
	}
	if !strings.Contains(h.errOut(), "等待超时") {
		t.Fatalf("标准错误为 %q", h.errOut())
	}
	if !strings.Contains(h.out(), `"status":"running"`) {
		t.Fatalf("超时时应输出最后一次响应, 实际 %q", h.out())
	}
}

func TestWaitUnknownTask(t *testing.T) {
	srv := httptest.NewServer(jsonHandler(t, http.StatusNotFound, `{"detail":"task 99 不存在"}`, nil))
	defer srv.Close()

	h := newHarness(t, srv.URL)
	h.useClock()
	// 任务不存在不是终态; 它属于「服务端拒绝」, 不是网络问题.
	if code := h.run("wait", "99"); code != ExitServer {
		t.Fatalf("退出码为 %d, 期望 %d", code, ExitServer)
	}
	if !strings.Contains(h.errOut(), "不存在") {
		t.Fatalf("标准错误为 %q", h.errOut())
	}
	if !strings.Contains(h.errOut(), "HTTP 404") {
		t.Fatalf("标准错误为 %q", h.errOut())
	}
}

func TestWaitInvalidTaskID(t *testing.T) {
	h := newHarness(t, "http://127.0.0.1:1")
	if code := h.run("wait", "abc"); code != ExitUsage {
		t.Fatalf("退出码为 %d, 期望 %d", code, ExitUsage)
	}
}

func TestAPIPathPrefix(t *testing.T) {
	var paths []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		paths = append(paths, r.URL.Path)
		_, _ = w.Write([]byte(`{}`))
	}))
	defer srv.Close()

	cases := map[string]string{
		"tasks/1":      "/api/tasks/1",
		"/tasks/1":     "/api/tasks/1",
		"/api/tasks/1": "/api/tasks/1",
	}
	for in, want := range cases {
		paths = nil
		h := newHarness(t, srv.URL)
		if code := h.run("api", in); code != ExitOK {
			t.Fatalf("%s: 退出码为 %d, stderr: %s", in, code, h.errOut())
		}
		if len(paths) != 1 || paths[0] != want {
			t.Fatalf("%s: 请求路径为 %v, 期望 %s", in, paths, want)
		}
	}
}

func TestAPINonGETRejectedWithoutRequest(t *testing.T) {
	var calls int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&calls, 1)
	}))
	defer srv.Close()

	h := newHarness(t, srv.URL)
	if code := h.run("api", "tasks/1", "--method", "POST"); code != ExitUsage {
		t.Fatalf("退出码为 %d, 期望 %d", code, ExitUsage)
	}
	if atomic.LoadInt32(&calls) != 0 {
		t.Fatal("非 GET 请求不应发出")
	}
}

func TestLoginWritesConfig(t *testing.T) {
	h := newHarness(t, "")
	h.stdin("secret-token-0123456789\n")
	if code := h.run("login", "--server", "127.0.0.1:8000"); code != ExitOK {
		t.Fatalf("退出码为 %d, stderr: %s", code, h.errOut())
	}
	if strings.Contains(h.out()+h.errOut(), "secret-token-0123456789") {
		t.Fatal("token 不应出现在输出中")
	}
	cfg := h.loadConfig(t)
	profile, ok := cfg.Profiles["default"]
	if !ok {
		t.Fatalf("配置为 %+v", cfg)
	}
	if profile.Server != "http://127.0.0.1:8000" {
		t.Fatalf("地址为 %q", profile.Server)
	}
	if profile.Token != "secret-token-0123456789" {
		t.Fatalf("token 未写入")
	}
	if cfg.Current != "default" {
		t.Fatalf("当前 profile 为 %q", cfg.Current)
	}
	info, err := os.Stat(h.env.ConfigPath)
	if err != nil {
		t.Fatalf("读取权限失败: %v", err)
	}
	// Windows 上普通文件的 Perm() 恒为 0666 (只读才是 0444), 权限由目录 ACL 保证.
	if runtime.GOOS != "windows" {
		if mode := info.Mode().Perm(); mode != 0o600 {
			t.Fatalf("权限为 %o, 期望 600", mode)
		}
	}
}

func TestLoginEmptyStdin(t *testing.T) {
	h := newHarness(t, "")
	if code := h.run("login", "--server", "http://127.0.0.1:8000"); code != ExitUsage {
		t.Fatalf("退出码为 %d, 期望 %d", code, ExitUsage)
	}
	if !strings.Contains(h.errOut(), "没有读到 token") {
		t.Fatalf("标准错误为 %q", h.errOut())
	}
}

func TestLoginWithoutServerRequiresFlag(t *testing.T) {
	h := newHarness(t, "")
	h.stdin("token")
	if code := h.run("login"); code != ExitUsage {
		t.Fatalf("退出码为 %d, 期望 %d", code, ExitUsage)
	}
	if !strings.Contains(h.errOut(), "缺少 --server") {
		t.Fatalf("标准错误为 %q", h.errOut())
	}
}

func TestLoginKeepsExistingServer(t *testing.T) {
	h := newHarness(t, "http://127.0.0.1:9000")
	h.stdin("new-token")
	if code := h.run("login"); code != ExitOK {
		t.Fatalf("退出码为 %d, stderr: %s", code, h.errOut())
	}
	cfg := h.loadConfig(t)
	if got := cfg.Profiles["default"].Server; got != "http://127.0.0.1:9000" {
		t.Fatalf("地址为 %q", got)
	}
	if got := cfg.Profiles["default"].Token; got != "new-token" {
		t.Fatalf("token 未更新")
	}
}

func TestConfigMasksToken(t *testing.T) {
	h := newHarness(t, "http://127.0.0.1:8000")
	if code := h.run("config"); code != ExitOK {
		t.Fatalf("退出码为 %d, stderr: %s", code, h.errOut())
	}
	var out struct {
		Profile string `json:"profile"`
		Server  string `json:"server"`
		Token   string `json:"token"`
	}
	h.decodeJSON(t, &out)
	if out.Token != "tok-****cdef" {
		t.Fatalf("打码结果为 %q", out.Token)
	}
	if strings.Contains(h.out(), testToken) {
		t.Fatal("完整 token 不应出现")
	}
	if out.Profile != "default" || out.Server != "http://127.0.0.1:8000" {
		t.Fatalf("输出为 %+v", out)
	}
}

func TestConfigUseAndList(t *testing.T) {
	h := newHarness(t, "http://127.0.0.1:8000")
	cfg := h.loadConfig(t)
	cfg.Profiles["work"] = config.Profile{Server: "http://10.0.0.2:8000"}
	if err := cfg.Save(h.env.ConfigPath); err != nil {
		t.Fatalf("写入失败: %v", err)
	}

	if code := h.run("config", "use", "work"); code != ExitOK {
		t.Fatalf("退出码为 %d, stderr: %s", code, h.errOut())
	}
	if got := h.loadConfig(t).Current; got != "work" {
		t.Fatalf("当前 profile 为 %q", got)
	}

	h.stdout.Reset()
	if code := h.run("config", "list"); code != ExitOK {
		t.Fatalf("退出码为 %d", code)
	}
	var out struct {
		Current  string `json:"current"`
		Profiles []struct {
			Name   string `json:"name"`
			Server string `json:"server"`
			Token  string `json:"token"`
		} `json:"profiles"`
	}
	h.decodeJSON(t, &out)
	if out.Current != "work" || len(out.Profiles) != 2 {
		t.Fatalf("输出为 %+v", out)
	}
	if out.Profiles[0].Name != "default" || out.Profiles[1].Name != "work" {
		t.Fatalf("profile 未按名字排序: %+v", out.Profiles)
	}

	if code := h.run("config", "use", "missing"); code != ExitUsage {
		t.Fatalf("未知 profile 的退出码为 %d", code)
	}
}

func TestConfigUnknownSubcommand(t *testing.T) {
	h := newHarness(t, "http://127.0.0.1:8000")
	if code := h.run("config", "frob"); code != ExitUsage {
		t.Fatalf("退出码为 %d", code)
	}
}

func TestConfigDirIsCreated(t *testing.T) {
	h := newHarness(t, "http://127.0.0.1:8000")
	// 配置文件路径指向尚不存在的子目录.
	h.env.ConfigPath = filepath.Join(h.dir, "deep", "nested", "cli.json")
	h.stdin("t")
	if code := h.run("login", "--server", "http://127.0.0.1:8000"); code != ExitOK {
		t.Fatalf("退出码为 %d, stderr: %s", code, h.errOut())
	}
	if _, err := os.Stat(h.env.ConfigPath); err != nil {
		t.Fatalf("配置文件未创建: %v", err)
	}
}

func TestCatalogToolDetail(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/tools/sql_explore" {
			t.Errorf("路径为 %q", r.URL.Path)
		}
		_, _ = w.Write([]byte(`{"name":"sql_explore","group":"sql","description":"执行只读 SQL","params":{"type":"object","properties":{"sql":{"type":"string"}}}}`))
	}))
	defer srv.Close()

	h := newHarness(t, srv.URL)
	if code := h.run("catalog", "sql_explore"); code != ExitOK {
		t.Fatalf("退出码为 %d, stderr: %s", code, h.errOut())
	}
	for _, want := range []string{"sql_explore (sql)", "执行只读 SQL", "入参:", `"sql"`} {
		if !strings.Contains(h.out(), want) {
			t.Errorf("输出缺少 %q:\n%s", want, h.out())
		}
	}
}

func TestCatalogToolDetailUnknown(t *testing.T) {
	srv := httptest.NewServer(jsonHandler(t, http.StatusNotFound, `{"detail":"未知工具"}`, nil))
	defer srv.Close()

	h := newHarness(t, srv.URL)
	if code := h.run("catalog", "no_such_tool"); code != ExitServer {
		t.Fatalf("退出码为 %d, 期望 %d", code, ExitServer)
	}
	if !strings.Contains(h.errOut(), "HTTP 404") {
		t.Fatalf("stderr 为 %q", h.errOut())
	}
}

func TestCatalogRejectsBadToolName(t *testing.T) {
	h := newHarness(t, "http://127.0.0.1:1")
	if code := h.run("catalog", "bad name"); code != ExitUsage {
		t.Fatalf("退出码为 %d", code)
	}
	if !strings.Contains(h.errOut(), "不合法") {
		t.Fatalf("stderr 为 %q", h.errOut())
	}
}

func TestSchemaPrintsDoc(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/db-schema" {
			t.Errorf("路径为 %q", r.URL.Path)
		}
		_, _ = w.Write([]byte(`{"schema_doc":"CREATE TABLE t (id INTEGER);"}`))
	}))
	defer srv.Close()

	h := newHarness(t, srv.URL)
	if code := h.run("schema"); code != ExitOK {
		t.Fatalf("退出码为 %d, stderr: %s", code, h.errOut())
	}
	if strings.TrimSpace(h.out()) != "CREATE TABLE t (id INTEGER);" {
		t.Fatalf("输出为 %q", h.out())
	}
}

func TestServerFallsBackOnlyWithoutProfile(t *testing.T) {
	// 显式 profile 拼错时必须报错: 静默换成本机实例等于换了个操作对象.
	var hits int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&hits, 1)
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	dataDir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dataDir, "token"), []byte("local-token"), 0o600); err != nil {
		t.Fatalf("准备 token 失败: %v", err)
	}

	h := newHarness(t, "")
	h.env.Getenv = func(k string) string {
		if k == "AMANE_DATA_DIR" {
			return dataDir
		}
		return ""
	}
	if code := h.run("catalog", "--profile", "missing"); code != ExitUsage {
		t.Fatalf("退出码为 %d, 期望 %d", code, ExitUsage)
	}
	if got := atomic.LoadInt32(&hits); got != 0 {
		t.Fatalf("不应发起请求, 实际 %d 次", got)
	}
}

func TestServerFallsBackToLocalInstance(t *testing.T) {
	srv := httptest.NewServer(jsonHandler(t, http.StatusOK, catalogBody, nil))
	defer srv.Close()
	port := strings.TrimPrefix(srv.URL, "http://127.0.0.1:")

	dataDir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dataDir, "token"), []byte("local-token"), 0o600); err != nil {
		t.Fatalf("准备 token 失败: %v", err)
	}

	h := newHarness(t, "")
	h.env.Getenv = func(k string) string {
		switch k {
		case "AMANE_DATA_DIR":
			return dataDir
		case "AMANE_PORT":
			return port
		}
		return ""
	}
	if code := h.run("catalog"); code != ExitOK {
		t.Fatalf("退出码为 %d, stderr: %s", code, h.errOut())
	}
	if !strings.Contains(h.out(), "tool") {
		t.Fatalf("输出为 %q", h.out())
	}
}

func TestLoginReadsOneLineInTTY(t *testing.T) {
	h := newHarness(t, "")
	h.stdin("secret-token\nignored")
	h.terminal(true)

	if code := h.run("login", "--server", "http://127.0.0.1:8000"); code != ExitOK {
		t.Fatalf("退出码为 %d, stderr: %s", code, h.errOut())
	}
	if !strings.Contains(h.errOut(), "粘贴") {
		t.Fatalf("终端下应给出提示, 标准错误为 %q", h.errOut())
	}
	cfg := h.loadConfig(t)
	if got := cfg.Profiles[config.DefaultProfile].Token; got != "secret-token" {
		t.Fatalf("token 为 %q", got)
	}
}

func TestAPIBinaryVerbatim(t *testing.T) {
	// 任务记录包是二进制; 补换行或缩进都会写坏它.
	payload := []byte{0x50, 0x4b, 0x03, 0x04, 0x00}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/zip")
		_, _ = w.Write(payload)
	}))
	defer srv.Close()

	h := newHarness(t, srv.URL)
	if code := h.run("api", "/api/tasks/7/record"); code != ExitOK {
		t.Fatalf("退出码为 %d", code)
	}
	if got := h.out(); got != string(payload) {
		t.Fatalf("输出为 %v", []byte(got))
	}
}

func TestWaitRejectsUnexpectedResponses(t *testing.T) {
	cases := []struct {
		name   string
		status int
		body   string
	}{
		{name: "网关错误", status: http.StatusBadGateway, body: "<html>502</html>"},
		{name: "服务端错误", status: http.StatusInternalServerError, body: `{"detail":"boom"}`},
		{name: "200 但不是任务详情", status: http.StatusOK, body: "<html>index</html>"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.WriteHeader(c.status)
				_, _ = w.Write([]byte(c.body))
			}))
			defer srv.Close()

			h := newHarness(t, srv.URL)
			h.useClock()
			if code := h.run("wait", "7"); code != ExitServer {
				t.Fatalf("退出码为 %d, 期望 %d", code, ExitServer)
			}
			if h.out() != "" {
				t.Fatalf("不应输出正文, 实际 %q", h.out())
			}
		})
	}
}

func TestConfigRejectsMissingProfile(t *testing.T) {
	// 与 server() 一致: 显式 profile 拼错时不能回落成一份「本机实例」的配置.
	dataDir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dataDir, "token"), []byte("local-token"), 0o600); err != nil {
		t.Fatalf("准备 token 失败: %v", err)
	}
	h := newHarness(t, "")
	h.env.Getenv = func(k string) string {
		if k == "AMANE_DATA_DIR" {
			return dataDir
		}
		return ""
	}
	if code := h.run("config", "--profile", "missing"); code != ExitUsage {
		t.Fatalf("退出码为 %d, 期望 %d", code, ExitUsage)
	}
	if strings.Contains(h.out(), "127.0.0.1") {
		t.Fatalf("不应输出本机地址, 实际 %q", h.out())
	}
}
