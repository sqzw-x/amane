package app

import (
	"net"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
)

// installSkill 在测试主目录下写入 Skill 版本文件.
// doctorServer 同时提供 /api/health 与 /api/tools.
func doctorServer(t *testing.T, toolsStatus int) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/health":
			_, _ = w.Write([]byte(`{"status":"ok"}`))
		case "/api/tools":
			if toolsStatus != http.StatusOK {
				w.WriteHeader(toolsStatus)
				_, _ = w.Write([]byte(`{"detail":"缺少或无效的 API token."}`))
				return
			}
			_, _ = w.Write([]byte(catalogBody))
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	return srv
}

func TestDoctorAllPass(t *testing.T) {
	srv := doctorServer(t, http.StatusOK)
	defer srv.Close()

	h := newHarness(t, srv.URL)
	h.env.ExecutablePath = filepath.Join(h.dir, "amane")

	if code := h.run("doctor"); code != ExitOK {
		t.Fatalf("退出码为 %d, stderr: %s\nstdout: %s", code, h.errOut(), h.out())
	}
	for _, want := range []string{"[通过] CLI", "[通过] 实例", "[通过] 鉴权"} {
		if !strings.Contains(h.out(), want) {
			t.Errorf("输出缺少 %q:\n%s", want, h.out())
		}
	}
	if strings.Contains(h.out(), testToken) {
		t.Fatal("token 不应出现在输出中")
	}
}

func TestDoctorJSON(t *testing.T) {
	srv := doctorServer(t, http.StatusOK)
	defer srv.Close()

	h := newHarness(t, srv.URL)
	h.env.ExecutablePath = filepath.Join(h.dir, "amane")

	if code := h.run("doctor", "--json"); code != ExitOK {
		t.Fatalf("退出码为 %d", code)
	}
	var out struct {
		OK     bool          `json:"ok"`
		Checks []doctorCheck `json:"checks"`
	}
	h.decodeJSON(t, &out)
	// CLI、实例、鉴权、版本.
	if !out.OK || len(out.Checks) != 4 {
		t.Fatalf("输出为 %+v", out)
	}
	if out.Checks[3].Name != "版本" || !out.Checks[3].OK {
		t.Fatalf("版本项为 %+v", out.Checks[3])
	}
}

func TestDoctorAuthFailure(t *testing.T) {
	srv := doctorServer(t, http.StatusUnauthorized)
	defer srv.Close()

	h := newHarness(t, srv.URL)
	h.env.ExecutablePath = filepath.Join(h.dir, "amane")
	if code := h.run("doctor"); code != ExitConnection {
		t.Fatalf("退出码为 %d, 期望 %d", code, ExitConnection)
	}
	if !strings.Contains(h.out(), "[通过] 实例") {
		t.Fatalf("实例应可达: %q", h.out())
	}
	if !strings.Contains(h.out(), "[失败] 鉴权") {
		t.Fatalf("输出为 %q", h.out())
	}
}

func TestDoctorUnreachable(t *testing.T) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("监听失败: %v", err)
	}
	addr := ln.Addr().String()
	_ = ln.Close()

	h := newHarness(t, "http://"+addr)
	h.env.ExecutablePath = filepath.Join(h.dir, "amane")
	if code := h.run("doctor"); code != ExitConnection {
		t.Fatalf("退出码为 %d, 期望 %d", code, ExitConnection)
	}
	if !strings.Contains(h.out(), "[失败] 实例") {
		t.Fatalf("输出为 %q", h.out())
	}
}

func TestDoctorWithoutServer(t *testing.T) {
	h := newHarness(t, "")
	h.env.ExecutablePath = filepath.Join(h.dir, "amane")
	if code := h.run("doctor"); code != ExitConnection {
		t.Fatalf("退出码为 %d, 期望 %d", code, ExitConnection)
	}
	if !strings.Contains(h.out(), "login") {
		t.Fatalf("输出为 %q", h.out())
	}
}
