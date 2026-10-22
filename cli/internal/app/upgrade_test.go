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

func TestUpgradeDryRun(t *testing.T) {
	h := newHarness(t, "")
	target := h.writeFile("amane", "old")
	h.env.ExecutablePath = target
	h.env.UpgradeBaseURL = "https://example.invalid/dl"
	h.env.GOOS = "darwin"
	h.env.GOARCH = "arm64"

	if code := h.run("upgrade", "--dry-run"); code != ExitOK {
		t.Fatalf("退出码为 %d, stderr: %s", code, h.errOut())
	}
	for _, want := range []string{"darwin/arm64", "https://example.invalid/dl/amane-darwin-arm64", "SHA256SUMS", target} {
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
	sum := sha256.Sum256(payload)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/SHA256SUMS":
			_, _ = w.Write([]byte(hex.EncodeToString(sum[:]) + "  amane-linux-amd64\n"))
		case "/amane-linux-amd64":
			_, _ = w.Write(payload)
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer srv.Close()

	h := newHarness(t, "")
	target := h.writeFile("amane", "old")
	h.env.ExecutablePath = target
	h.env.UpgradeBaseURL = srv.URL
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

func TestUpgradeUnsupportedPlatform(t *testing.T) {
	h := newHarness(t, "")
	h.env.ExecutablePath = h.writeFile("amane", "old")
	h.env.GOOS = "freebsd"
	h.env.GOARCH = "amd64"

	if code := h.run("upgrade", "--dry-run"); code != ExitConnection {
		t.Fatalf("退出码为 %d, 期望 %d", code, ExitConnection)
	}
	if !strings.Contains(h.errOut(), "freebsd") {
		t.Fatalf("标准错误为 %q", h.errOut())
	}
}
