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

// newReleaseServer 模拟发布页与产物目录.
//
// /latest 重定向到 tag 页, 与 GitHub 的行为一致; /dl 下放产物与校验和.
func newReleaseServer(t *testing.T, tag, sums string, payload []byte) *httptest.Server {
	t.Helper()
	var srv *httptest.Server
	srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/latest":
			http.Redirect(w, r, srv.URL+"/tag/"+tag, http.StatusFound)
		case "/dl/SHA256SUMS":
			_, _ = w.Write([]byte(sums))
		case "/dl/amane-linux-amd64":
			_, _ = w.Write(payload)
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(srv.Close)
	return srv
}

func sumsFor(payload []byte) string {
	sum := sha256.Sum256(payload)
	return hex.EncodeToString(sum[:]) + "  amane-linux-amd64\n"
}

func testOptions(t *testing.T, srv *httptest.Server) Options {
	return Options{
		BaseURL:        srv.URL + "/dl",
		LatestURL:      srv.URL + "/latest",
		GOOS:           "linux",
		GOARCH:         "amd64",
		CurrentVersion: "0.1.0",
		TargetPath:     filepath.Join(t.TempDir(), "amane"),
		HTTP:           srv.Client(),
		Out:            io.Discard,
		Err:            io.Discard,
	}
}

func TestLatestTag(t *testing.T) {
	cases := []struct {
		name    string
		handler http.HandlerFunc
		want    string
		wantErr bool
	}{
		{
			name: "正常重定向",
			handler: func(w http.ResponseWriter, r *http.Request) {
				http.Redirect(w, r, "https://github.com/sqzw-x/amane/releases/tag/v0.22.1", http.StatusFound)
			},
			want: "v0.22.1",
		},
		{
			name: "重定向目标带尾斜杠",
			handler: func(w http.ResponseWriter, r *http.Request) {
				http.Redirect(w, r, "https://github.com/sqzw-x/amane/releases/tag/v1.2.3/", http.StatusFound)
			},
			want: "v1.2.3",
		},
		{
			name:    "没有重定向",
			handler: func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(http.StatusOK) },
			wantErr: true,
		},
		{
			name: "重定向目标不含 tag 段",
			handler: func(w http.ResponseWriter, r *http.Request) {
				http.Redirect(w, r, "https://github.com/sqzw-x/amane/releases", http.StatusFound)
			},
			wantErr: true,
		},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			srv := httptest.NewServer(c.handler)
			defer srv.Close()
			got, err := LatestTag(context.Background(), srv.Client(), srv.URL)
			if c.wantErr {
				if err == nil {
					t.Fatalf("期望错误, 实际得到 %q", got)
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

func TestRunDryRun(t *testing.T) {
	srv := newReleaseServer(t, "v0.2.0", "", nil)
	opts := testOptions(t, srv)
	opts.TargetPath = "/tmp/amane"
	opts.DryRun = true
	var out bytes.Buffer
	opts.Out = &out

	if err := Run(context.Background(), opts); err != nil {
		t.Fatalf("未预期的错误: %v", err)
	}
	for _, want := range []string{"linux/amd64", "0.1.0", "v0.2.0", srv.URL + "/dl/amane-linux-amd64", "SHA256SUMS", "/tmp/amane"} {
		if !strings.Contains(out.String(), want) {
			t.Errorf("输出缺少 %q:\n%s", want, out.String())
		}
	}
	if _, err := os.Stat("/tmp/amane"); err == nil {
		t.Fatalf("dry-run 不应写入文件")
	}
}

func TestRunDownloadsAndVerifies(t *testing.T) {
	payload := []byte("binary-payload")
	srv := newReleaseServer(t, "v0.2.0", sumsFor(payload), payload)
	opts := testOptions(t, srv)
	if err := os.WriteFile(opts.TargetPath, []byte("old"), 0o755); err != nil {
		t.Fatalf("准备文件失败: %v", err)
	}

	if err := Run(context.Background(), opts); err != nil {
		t.Fatalf("未预期的错误: %v", err)
	}
	got, err := os.ReadFile(opts.TargetPath)
	if err != nil {
		t.Fatalf("读取失败: %v", err)
	}
	if string(got) != string(payload) {
		t.Fatalf("内容为 %q", got)
	}
}

func TestRunSkipsWhenNotNewer(t *testing.T) {
	cases := []struct {
		name    string
		current string
		tag     string
	}{
		{name: "同版本", current: "0.1.0", tag: "v0.1.0"},
		{name: "当前更新", current: "0.3.0", tag: "v0.2.0"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			payload := []byte("binary-payload")
			var hits []string
			srv := newReleaseServer(t, c.tag, sumsFor(payload), payload)
			opts := testOptions(t, srv)
			opts.CurrentVersion = c.current
			opts.HTTP = &http.Client{Transport: roundTripFunc(func(r *http.Request) (*http.Response, error) {
				hits = append(hits, r.URL.Path)
				return http.DefaultTransport.RoundTrip(r)
			})}
			opts.HTTP.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
			if err := os.WriteFile(opts.TargetPath, []byte("old"), 0o755); err != nil {
				t.Fatalf("准备文件失败: %v", err)
			}

			if err := Run(context.Background(), opts); err != nil {
				t.Fatalf("未预期的错误: %v", err)
			}
			if got, _ := os.ReadFile(opts.TargetPath); string(got) != "old" {
				t.Fatalf("不应改动文件, 实际 %q", got)
			}
			for _, h := range hits {
				if strings.HasPrefix(h, "/dl/") {
					t.Fatalf("不应下载产物, 请求了 %v", hits)
				}
			}
		})
	}
}

func TestRunUncomparableVersion(t *testing.T) {
	payload := []byte("binary-payload")
	srv := newReleaseServer(t, "v0.2.0", sumsFor(payload), payload)
	opts := testOptions(t, srv)
	opts.CurrentVersion = "dev"
	var out bytes.Buffer
	opts.Out = &out
	if err := os.WriteFile(opts.TargetPath, []byte("old"), 0o755); err != nil {
		t.Fatalf("准备文件失败: %v", err)
	}

	if err := Run(context.Background(), opts); err != nil {
		t.Fatalf("未预期的错误: %v", err)
	}
	if got, _ := os.ReadFile(opts.TargetPath); string(got) != "old" {
		t.Fatalf("无法比较时不应改动文件, 实际 %q", got)
	}
	if !strings.Contains(out.String(), "无法比较") {
		t.Fatalf("输出为 %q", out.String())
	}
}

func TestRunRejectsBadChecksum(t *testing.T) {
	payload := []byte("binary-payload")
	srv := newReleaseServer(t, "v0.2.0", strings.Repeat("0", 64)+"  amane-linux-amd64\n", payload)
	opts := testOptions(t, srv)
	if err := os.WriteFile(opts.TargetPath, []byte("old"), 0o755); err != nil {
		t.Fatalf("准备文件失败: %v", err)
	}

	err := Run(context.Background(), opts)
	if err == nil || !strings.Contains(err.Error(), "校验和") {
		t.Fatalf("期望校验和错误, 实际 %v", err)
	}
	if got, _ := os.ReadFile(opts.TargetPath); string(got) != "old" {
		t.Fatalf("校验失败时不应改动目标文件, 实际 %q", got)
	}
}

func TestRunMissingChecksumEntry(t *testing.T) {
	payload := []byte("binary-payload")
	srv := newReleaseServer(t, "v0.2.0", strings.Repeat("0", 64)+"  other-file\n", payload)
	opts := testOptions(t, srv)

	err := Run(context.Background(), opts)
	if err == nil || !strings.Contains(err.Error(), "没有") {
		t.Fatalf("期望缺少记录错误, 实际 %v", err)
	}
}

// roundTripFunc 用一个函数实现 Transport, 便于记录实际发出的请求.
type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }
