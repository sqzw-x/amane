package upgrade

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestAssetName(t *testing.T) {
	cases := []struct {
		goos, goarch string
		want         string
		wantErr      bool
	}{
		{goos: "linux", goarch: "amd64", want: "amane-linux-amd64"},
		{goos: "linux", goarch: "arm64", want: "amane-linux-arm64"},
		{goos: "darwin", goarch: "arm64", want: "amane-darwin-arm64"},
		{goos: "windows", goarch: "amd64", want: "amane-windows-amd64.exe"},
		{goos: "windows", goarch: "arm64", wantErr: true},
		{goos: "freebsd", goarch: "amd64", wantErr: true},
		{goos: "linux", goarch: "386", wantErr: true},
	}
	for _, c := range cases {
		got, err := AssetName(c.goos, c.goarch)
		if c.wantErr {
			if err == nil {
				t.Errorf("%s/%s: 期望报错, 实际得到 %q", c.goos, c.goarch, got)
			}
			continue
		}
		if err != nil {
			t.Errorf("%s/%s: 未预期的错误: %v", c.goos, c.goarch, err)
			continue
		}
		if got != c.want {
			t.Errorf("%s/%s: 得到 %q, 期望 %q", c.goos, c.goarch, got, c.want)
		}
	}
}

func TestParseChecksums(t *testing.T) {
	hash := strings.Repeat("a", 64)
	sums := []byte(
		"1111111111111111111111111111111111111111111111111111111111111111  amane-linux-amd64\n" +
			hash + " *amane-darwin-arm64\n" +
			"\n" +
			"not a checksum line\n",
	)

	cases := []struct {
		name    string
		file    string
		want    string
		wantErr bool
	}{
		{name: "两个空格", file: "amane-linux-amd64", want: strings.Repeat("1", 64)},
		{name: "星号前缀", file: "amane-darwin-arm64", want: hash},
		{name: "缺失", file: "amane-windows-amd64.exe", wantErr: true},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got, err := ParseChecksums(sums, c.file)
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

	if _, err := ParseChecksums([]byte("zzzz  file\n"), "file"); err == nil {
		t.Fatal("非十六进制校验和应报错")
	}
	if _, err := ParseChecksums([]byte("abcd  file\n"), "file"); err == nil {
		t.Fatal("长度错误的校验和应报错")
	}
}

func TestVerifySHA256(t *testing.T) {
	data := []byte("hello")
	sum := sha256.Sum256(data)
	good := hex.EncodeToString(sum[:])
	if err := VerifySHA256(data, good); err != nil {
		t.Fatalf("未预期的错误: %v", err)
	}
	if err := VerifySHA256(data, strings.ToUpper(good)); err != nil {
		t.Fatalf("大写校验和应通过: %v", err)
	}
	if err := VerifySHA256(data, strings.Repeat("0", 64)); err == nil {
		t.Fatal("校验和不匹配时应报错")
	}
}

func TestReplace(t *testing.T) {
	for _, goos := range []string{"linux", "windows"} {
		t.Run(goos, func(t *testing.T) {
			dir := t.TempDir()
			target := filepath.Join(dir, "amane")
			if err := os.WriteFile(target, []byte("old"), 0o755); err != nil {
				t.Fatalf("准备文件失败: %v", err)
			}
			if err := Replace(target, []byte("new"), goos); err != nil {
				t.Fatalf("未预期的错误: %v", err)
			}
			got, err := os.ReadFile(target)
			if err != nil {
				t.Fatalf("读取失败: %v", err)
			}
			if string(got) != "new" {
				t.Fatalf("内容为 %q", got)
			}
			if goos == "windows" {
				if _, err := os.Stat(target + ".old"); !os.IsNotExist(err) {
					t.Fatalf(".old 文件应被清理, 实际 %v", err)
				}
			}
			entries, err := os.ReadDir(dir)
			if err != nil {
				t.Fatalf("读取目录失败: %v", err)
			}
			if len(entries) != 1 {
				t.Fatalf("目录残留临时文件: %v", entries)
			}
		})
	}
}

func TestRunDryRun(t *testing.T) {
	var out bytes.Buffer
	err := Run(context.Background(), Options{
		BaseURL:        "https://example.invalid/dl",
		GOOS:           "darwin",
		GOARCH:         "arm64",
		CurrentVersion: "dev",
		TargetPath:     "/tmp/amane",
		DryRun:         true,
		Out:            &out,
		Err:            io.Discard,
	})
	if err != nil {
		t.Fatalf("未预期的错误: %v", err)
	}
	for _, want := range []string{"darwin/arm64", "https://example.invalid/dl/amane-darwin-arm64", "SHA256SUMS", "/tmp/amane"} {
		if !strings.Contains(out.String(), want) {
			t.Errorf("输出缺少 %q:\n%s", want, out.String())
		}
	}
}

func TestRunDownloadsAndVerifies(t *testing.T) {
	payload := []byte("binary-payload")
	sum := sha256.Sum256(payload)
	var hits []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits = append(hits, r.URL.Path)
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

	dir := t.TempDir()
	target := filepath.Join(dir, "amane")
	if err := os.WriteFile(target, []byte("old"), 0o755); err != nil {
		t.Fatalf("准备文件失败: %v", err)
	}
	var out bytes.Buffer
	err := Run(context.Background(), Options{
		BaseURL:    srv.URL,
		GOOS:       "linux",
		GOARCH:     "amd64",
		TargetPath: target,
		Out:        &out,
		Err:        io.Discard,
		HTTP:       srv.Client(),
	})
	if err != nil {
		t.Fatalf("未预期的错误: %v", err)
	}
	got, err := os.ReadFile(target)
	if err != nil {
		t.Fatalf("读取失败: %v", err)
	}
	if string(got) != string(payload) {
		t.Fatalf("内容为 %q", got)
	}
	if len(hits) != 2 {
		t.Fatalf("请求了 %v", hits)
	}
}

func TestRunRejectsBadChecksum(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/SHA256SUMS":
			_, _ = w.Write([]byte(strings.Repeat("0", 64) + "  amane-linux-amd64\n"))
		default:
			_, _ = w.Write([]byte("payload"))
		}
	}))
	defer srv.Close()

	target := filepath.Join(t.TempDir(), "amane")
	if err := os.WriteFile(target, []byte("old"), 0o755); err != nil {
		t.Fatalf("准备文件失败: %v", err)
	}
	err := Run(context.Background(), Options{
		BaseURL:    srv.URL,
		GOOS:       "linux",
		GOARCH:     "amd64",
		TargetPath: target,
		Out:        io.Discard,
		Err:        io.Discard,
		HTTP:       srv.Client(),
	})
	if err == nil || !strings.Contains(err.Error(), "校验和") {
		t.Fatalf("期望校验和错误, 实际 %v", err)
	}
	if got, _ := os.ReadFile(target); string(got) != "old" {
		t.Fatalf("校验失败时不应改动目标文件, 实际 %q", got)
	}
}

func TestRunMissingChecksumEntry(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(strings.Repeat("0", 64) + "  other-file\n"))
	}))
	defer srv.Close()

	err := Run(context.Background(), Options{
		BaseURL:    srv.URL,
		GOOS:       "linux",
		GOARCH:     "amd64",
		TargetPath: filepath.Join(t.TempDir(), "amane"),
		Out:        io.Discard,
		Err:        io.Discard,
		HTTP:       srv.Client(),
	})
	if err == nil || !strings.Contains(err.Error(), "没有") {
		t.Fatalf("期望缺少记录错误, 实际 %v", err)
	}
}
