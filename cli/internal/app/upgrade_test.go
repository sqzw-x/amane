package app

import (
	"crypto/sha256"
	"encoding/hex"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
)

// newReleaseServer 模拟发布页与产物目录: /latest 重定向到 tag 页, /dl 下是产物.
func newReleaseServer(t *testing.T, tag string, payload []byte) *httptest.Server {
	t.Helper()
	sum := sha256.Sum256(payload)
	var srv *httptest.Server
	srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/latest":
			http.Redirect(w, r, srv.URL+"/tag/"+tag, http.StatusFound)
		case "/dl/SHA256SUMS":
			_, _ = w.Write([]byte(hex.EncodeToString(sum[:]) + "  amane-linux-amd64\n"))
		case "/dl/amane-linux-amd64":
			_, _ = w.Write(payload)
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(srv.Close)
	return srv
}

func TestUpgradeDryRun(t *testing.T) {
	srv := newReleaseServer(t, "v0.2.0", []byte("new-binary"))
	h := newHarness(t, "")
	target := h.writeFile("amane", "old")
	h.env.ExecutablePath = target
	h.env.UpgradeBaseURL = srv.URL + "/dl"
	h.env.UpgradeLatestURL = srv.URL + "/latest"
	h.env.GOOS = "darwin"
	h.env.GOARCH = "arm64"
	h.env.HTTPClient = srv.Client()

	if code := h.run("upgrade", "--dry-run"); code != ExitOK {
		t.Fatalf("退出码为 %d, stderr: %s", code, h.errOut())
	}
	for _, want := range []string{"darwin/arm64", srv.URL + "/dl/amane-darwin-arm64", "SHA256SUMS", target, "v0.2.0"} {
		if !strings.Contains(h.out(), want) {
			t.Errorf("输出缺少 %q:\n%s", want, h.out())
		}
	}
	if got, err := os.ReadFile(target); err != nil || string(got) != "old" {
		t.Fatalf("dry-run 不应改动文件: %q, %v", got, err)
	}
}

func TestUpgradeReplacesSelf(t *testing.T) {
	payload := []byte("new-binary")
	srv := newReleaseServer(t, "v0.2.0", payload)

	h := newHarness(t, "")
	target := h.writeFile("amane", "old")
	h.env.ExecutablePath = target
	h.env.UpgradeBaseURL = srv.URL + "/dl"
	h.env.UpgradeLatestURL = srv.URL + "/latest"
	h.env.HTTPClient = srv.Client()

	if code := h.run("upgrade"); code != ExitOK {
		t.Fatalf("退出码为 %d, stderr: %s", code, h.errOut())
	}
	got, err := os.ReadFile(target)
	if err != nil {
		t.Fatalf("读取失败: %v", err)
	}
	if string(got) != string(payload) {
		t.Fatalf("文件内容为 %q", got)
	}
}

func TestUpgradeSkipsWhenLatest(t *testing.T) {
	// 已是最新: 不应发起产物下载.
	srv := newReleaseServer(t, "v0.1.0", []byte("new-binary"))

	h := newHarness(t, "")
	target := h.writeFile("amane", "old")
	h.env.ExecutablePath = target
	h.env.UpgradeBaseURL = srv.URL + "/dl"
	h.env.UpgradeLatestURL = srv.URL + "/latest"
	h.env.HTTPClient = srv.Client()

	if code := h.run("upgrade"); code != ExitOK {
		t.Fatalf("退出码为 %d, stderr: %s", code, h.errOut())
	}
	if got, err := os.ReadFile(target); err != nil || string(got) != "old" {
		t.Fatalf("已是最新时不应改动文件: %q, %v", got, err)
	}
	if !strings.Contains(h.out(), "已是最新") {
		t.Errorf("输出为 %q", h.out())
	}
}

func TestUpgradeUnreachableReportsError(t *testing.T) {
	// 取不到最新版本时如实报错, 由 Skill 指示智能体忽略并继续.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer srv.Close()

	h := newHarness(t, "")
	target := h.writeFile("amane", "old")
	h.env.ExecutablePath = target
	h.env.UpgradeBaseURL = srv.URL + "/dl"
	h.env.UpgradeLatestURL = srv.URL + "/latest"
	h.env.HTTPClient = srv.Client()

	if code := h.run("upgrade"); code == ExitOK {
		t.Fatalf("期望非零退出码")
	}
	if !strings.Contains(h.errOut(), "最新版本") {
		t.Errorf("标准错误为 %q", h.errOut())
	}
	if got, err := os.ReadFile(target); err != nil || string(got) != "old" {
		t.Fatalf("失败时不应改动文件: %q, %v", got, err)
	}
}

func TestUpgradeUnsupportedPlatform(t *testing.T) {
	h := newHarness(t, "")
	h.env.ExecutablePath = h.writeFile("amane", "old")
	h.env.GOOS = "freebsd"
	h.env.GOARCH = "amd64"

	// 平台不受支持是本机环境问题, 不是连接失败.
	if code := h.run("upgrade", "--dry-run"); code != ExitLocal {
		t.Fatalf("退出码为 %d, 期望 %d", code, ExitLocal)
	}
	if !strings.Contains(h.errOut(), "freebsd") {
		t.Fatalf("标准错误为 %q", h.errOut())
	}
}
