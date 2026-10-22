package api

import (
	"context"
	"encoding/json"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestAPIPath(t *testing.T) {
	cases := []struct {
		name    string
		in      string
		want    string
		wantErr bool
	}{
		{name: "补前缀", in: "tasks/1", want: "/api/tasks/1"},
		{name: "已带斜杠", in: "/tasks/1", want: "/api/tasks/1"},
		{name: "已带 api 前缀", in: "/api/tasks/1", want: "/api/tasks/1"},
		{name: "api 根", in: "/api", want: "/api"},
		{name: "去掉空白", in: "  tasks/1  ", want: "/api/tasks/1"},
		{name: "空路径", in: "   ", wantErr: true},
		{name: "拒绝完整 URL", in: "http://evil.example/api/tools", wantErr: true},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got, err := APIPath(c.in)
			if c.wantErr {
				if err == nil {
					t.Fatalf("期望报错, 实际得到 %q", got)
				}
				return
			}
			if err != nil {
				t.Fatalf("未预期的错误: %v", err)
			}
			if got != c.want {
				t.Fatalf("得到 %q, 期望 %q", got, c.want)
			}
		})
	}
}

func TestValidToolName(t *testing.T) {
	cases := map[string]bool{
		"update_metadata": true,
		"sql_explore":     true,
		"":                false,
		"a/b":             false,
		"a b":             false,
		"a?b":             false,
	}
	for name, want := range cases {
		if got := ValidToolName(name); got != want {
			t.Errorf("%q: 得到 %v, 期望 %v", name, got, want)
		}
	}
}

func TestCatalogParsesIndex(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/tools" {
			t.Errorf("路径为 %q", r.URL.Path)
		}
		if got := r.Header.Get("Authorization"); got != "Bearer tok" {
			t.Errorf("Authorization 为 %q", got)
		}
		_, _ = w.Write([]byte(`{"version":"0.22.1","paths":{"data_dir":"/data"},"tools":[{"name":"sql_explore","group":"sql","summary":"执行只读 SQL","signature":"sql: string"}]}`))
	}))
	defer srv.Close()

	c := &Client{BaseURL: srv.URL, Token: "tok"}
	cat, resp, err := c.Catalog(context.Background())
	if err != nil {
		t.Fatalf("未预期的错误: %v", err)
	}
	if resp.Status != http.StatusOK {
		t.Fatalf("状态码 %d", resp.Status)
	}
	if cat.Version != "0.22.1" || cat.Paths.DataDir != "/data" {
		t.Fatalf("目录解析错误: %+v", cat)
	}
	if len(cat.Tools) != 1 {
		t.Fatalf("工具数为 %d", len(cat.Tools))
	}
	tool := cat.Tools[0]
	if tool.Name != "sql_explore" || tool.Signature != "sql: string" || tool.Summary != "执行只读 SQL" {
		t.Fatalf("工具解析错误: %+v", tool)
	}
}

func TestCallToolPostsArgs(t *testing.T) {
	var gotBody map[string]json.RawMessage
	var gotMethod string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotMethod = r.Method
		if r.URL.Path != "/api/tools/update_metadata" {
			t.Errorf("路径为 %q", r.URL.Path)
		}
		if ct := r.Header.Get("Content-Type"); ct != "application/json" {
			t.Errorf("Content-Type 为 %q", ct)
		}
		if err := json.NewDecoder(r.Body).Decode(&gotBody); err != nil {
			t.Errorf("请求体解析失败: %v", err)
		}
		_, _ = w.Write([]byte(`"OK"`))
	}))
	defer srv.Close()

	c := &Client{BaseURL: srv.URL, Token: "tok"}
	resp, err := c.CallTool(context.Background(), "update_metadata", json.RawMessage(`{"number":"ABC-123"}`))
	if err != nil {
		t.Fatalf("未预期的错误: %v", err)
	}
	if gotMethod != http.MethodPost {
		t.Fatalf("方法为 %q", gotMethod)
	}
	if string(gotBody["args"]) != `{"number":"ABC-123"}` {
		t.Fatalf("args 为 %s", gotBody["args"])
	}
	if string(resp.Body) != `"OK"` {
		t.Fatalf("响应体为 %s", resp.Body)
	}
}

func TestCallToolEmptyArgs(t *testing.T) {
	var gotBody map[string]json.RawMessage
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if err := json.NewDecoder(r.Body).Decode(&gotBody); err != nil {
			t.Errorf("请求体解析失败: %v", err)
		}
	}))
	defer srv.Close()

	c := &Client{BaseURL: srv.URL}
	if _, err := c.CallTool(context.Background(), "list_feeds", nil); err != nil {
		t.Fatalf("未预期的错误: %v", err)
	}
	if string(gotBody["args"]) != "{}" {
		t.Fatalf("args 为 %s", gotBody["args"])
	}
}

func TestErrorStatusIsNotTransportError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnprocessableEntity)
		_, _ = w.Write([]byte(`{"detail":[{"loc":["body","args"]}]}`))
	}))
	defer srv.Close()

	c := &Client{BaseURL: srv.URL}
	resp, err := c.CallTool(context.Background(), "sql_explore", json.RawMessage(`{}`))
	if err != nil {
		t.Fatalf("未预期的错误: %v", err)
	}
	if resp.Status != http.StatusUnprocessableEntity {
		t.Fatalf("状态码为 %d", resp.Status)
	}
	if !strings.Contains(string(resp.Body), "detail") {
		t.Fatalf("响应体为 %s", resp.Body)
	}
}

func TestTransportError(t *testing.T) {
	// 监听后立刻关闭, 得到一个必定拒绝连接的地址.
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("监听失败: %v", err)
	}
	addr := ln.Addr().String()
	_ = ln.Close()

	c := &Client{BaseURL: "http://" + addr, HTTP: &http.Client{Timeout: 2 * time.Second}}
	_, err = c.Get(context.Background(), "/api/tools")
	if err == nil {
		t.Fatal("期望连接失败, 实际成功")
	}
	if !IsTransport(err) {
		t.Fatalf("期望 TransportError, 实际 %T", err)
	}
}

func TestTimeoutIsTransportError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(200 * time.Millisecond)
		_, _ = w.Write([]byte(`{}`))
	}))
	defer srv.Close()

	c := &Client{BaseURL: srv.URL, HTTP: &http.Client{Timeout: 20 * time.Millisecond}}
	_, err := c.Get(context.Background(), "/api/tools")
	if err == nil {
		t.Fatal("期望超时, 实际成功")
	}
	if !IsTransport(err) {
		t.Fatalf("期望 TransportError, 实际 %T", err)
	}
}

func TestUserAgent(t *testing.T) {
	var got string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got = r.Header.Get("User-Agent")
	}))
	defer srv.Close()

	c := &Client{BaseURL: srv.URL, UserAgent: "amane-cli/0.1.0"}
	if _, err := c.Get(context.Background(), "/api/tools"); err != nil {
		t.Fatalf("未预期的错误: %v", err)
	}
	if got != "amane-cli/0.1.0" {
		t.Fatalf("User-Agent 为 %q", got)
	}
}

func TestIsAuthFailure(t *testing.T) {
	if !IsAuthFailure(401) || !IsAuthFailure(403) {
		t.Fatal("401 与 403 应判为鉴权失败")
	}
	if IsAuthFailure(200) || IsAuthFailure(404) {
		t.Fatal("200 与 404 不应判为鉴权失败")
	}
}
