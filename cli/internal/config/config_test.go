package config

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func envFrom(m map[string]string) func(string) string {
	return func(k string) string { return m[k] }
}

func TestNormalizeServer(t *testing.T) {
	cases := []struct {
		name    string
		in      string
		want    string
		wantErr bool
	}{
		{name: "补全 scheme", in: "127.0.0.1:8000", want: "http://127.0.0.1:8000"},
		{name: "去掉末尾斜杠", in: "http://127.0.0.1:8000/", want: "http://127.0.0.1:8000"},
		{name: "保留路径前缀", in: "https://example.com/amane/", want: "https://example.com/amane"},
		{name: "丢弃查询串", in: "http://127.0.0.1:8000?x=1", want: "http://127.0.0.1:8000"},
		{name: "空地址", in: "  ", wantErr: true},
		{name: "不支持的 scheme", in: "ftp://example.com", wantErr: true},
		{name: "缺少主机", in: "http://", wantErr: true},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got, err := NormalizeServer(c.in)
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

func TestPlaintextWarning(t *testing.T) {
	cases := []struct {
		server string
		want   bool
	}{
		{server: "http://127.0.0.1:8000", want: false},
		{server: "http://127.1.2.3:8000", want: false},
		{server: "http://localhost:8000", want: false},
		{server: "http://[::1]:8000", want: false},
		{server: "https://example.com", want: false},
		{server: "http://192.168.1.10:8000", want: true},
		{server: "http://amane.example.com", want: true},
	}
	for _, c := range cases {
		if got := PlaintextWarning(c.server); got != c.want {
			t.Errorf("%s: 得到 %v, 期望 %v", c.server, got, c.want)
		}
	}
}

func TestMask(t *testing.T) {
	cases := []struct {
		token string
		want  string
	}{
		{token: "", want: ""},
		{token: "short", want: "****"},
		{token: "0123456789abcde", want: "****"},
		{token: "0123456789abcdef", want: "0123****cdef"},
		{token: "abcdefghijklmnopqrstuvwxyz", want: "abcd****wxyz"},
	}
	for _, c := range cases {
		if got := Mask(c.token); got != c.want {
			t.Errorf("%q: 得到 %q, 期望 %q", c.token, got, c.want)
		}
	}
}

func TestResolveTokenPriority(t *testing.T) {
	dir := t.TempDir()
	tokenFile := filepath.Join(dir, "token")
	if err := os.WriteFile(tokenFile, []byte("from-file\n"), 0o600); err != nil {
		t.Fatalf("准备 token 文件失败: %v", err)
	}
	env := envFrom(map[string]string{EnvToken: "from-env", EnvDataDir: dir})

	cases := []struct {
		name       string
		profile    Profile
		env        func(string) string
		wantToken  string
		wantSource string
	}{
		{name: "profile 优先", profile: Profile{Token: "from-profile"}, env: env, wantToken: "from-profile", wantSource: "配置文件"},
		{name: "环境变量其次", env: env, wantToken: "from-env", wantSource: "环境变量 AMANE_TOKEN"},
		{name: "数据目录文件最后", env: envFrom(map[string]string{EnvDataDir: dir}), wantToken: "from-file", wantSource: "本机 token 文件 " + tokenFile},
		{name: "都没有", env: envFrom(map[string]string{EnvDataDir: t.TempDir()}), wantToken: "", wantSource: ""},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			gotToken, gotSource := ResolveToken(c.profile, c.env)
			if gotToken != c.wantToken || gotSource != c.wantSource {
				t.Fatalf("得到 (%q, %q), 期望 (%q, %q)", gotToken, gotSource, c.wantToken, c.wantSource)
			}
		})
	}
}

func TestLocalTokenFiles(t *testing.T) {
	t.Run("显式数据目录优先, ./data 兜底", func(t *testing.T) {
		got := LocalTokenFiles(envFrom(map[string]string{EnvDataDir: "/srv/amane"}))
		if len(got) < 2 {
			t.Fatalf("候选太少: %v", got)
		}
		if got[0] != filepath.Join("/srv/amane", "token") {
			t.Fatalf("首项应为显式数据目录, 得到 %q", got[0])
		}
		last := got[len(got)-1]
		if last != filepath.Join(".", "data", "token") {
			t.Fatalf("末项应为 ./data/token, 得到 %q", last)
		}
	})

	t.Run("桌面默认目录在候选里", func(t *testing.T) {
		// 同机场景下桌面应用的数据目录不在 ./data, 漏掉它会让用户被迫手动登录.
		if runtime.GOOS != "darwin" && runtime.GOOS != "windows" {
			t.Skip("该平台没有桌面应用")
		}
		home := t.TempDir()
		got := LocalTokenFiles(envFrom(map[string]string{"HOME": home, "LOCALAPPDATA": home}))
		if len(got) != 2 {
			t.Fatalf("期望 桌面目录 + ./data 两项, 得到 %v", got)
		}
		if filepath.Base(filepath.Dir(got[0])) != "Amane" {
			t.Fatalf("首项应为桌面数据目录, 得到 %q", got[0])
		}
	})
}

func TestLoadMissingReturnsEmpty(t *testing.T) {
	f, err := Load(filepath.Join(t.TempDir(), "cli.json"))
	if err != nil {
		t.Fatalf("未预期的错误: %v", err)
	}
	if len(f.Profiles) != 0 {
		t.Fatalf("得到 %+v, 期望空配置", f)
	}
}

func TestLoadInvalidJSON(t *testing.T) {
	path := filepath.Join(t.TempDir(), "cli.json")
	if err := os.WriteFile(path, []byte("{"), 0o600); err != nil {
		t.Fatalf("准备文件失败: %v", err)
	}
	if _, err := Load(path); err == nil {
		t.Fatal("期望解析失败, 实际成功")
	}
}

func TestSaveRoundTrip(t *testing.T) {
	path := filepath.Join(t.TempDir(), "nested", "cli.json")
	f := &File{Profiles: map[string]Profile{
		"work": {Server: "http://192.168.1.2:8000", Token: "0123456789abcdef"},
	}}
	if err := f.Save(path); err != nil {
		t.Fatalf("写入失败: %v", err)
	}
	if runtime.GOOS != "windows" {
		info, err := os.Stat(path)
		if err != nil {
			t.Fatalf("读取权限失败: %v", err)
		}
		if mode := info.Mode().Perm(); mode != 0o600 {
			t.Fatalf("权限为 %o, 期望 600", mode)
		}
	}
	got, err := Load(path)
	if err != nil {
		t.Fatalf("读取失败: %v", err)
	}
	if got.Profiles["work"].Token != "0123456789abcdef" {
		t.Fatalf("往返不一致: %+v", got)
	}
}

func TestDirForPlatform(t *testing.T) {
	home := func() (string, error) { return "/home/u", nil }
	cfgDir := func() (string, error) { return "/home/u/.config", nil }

	cases := []struct {
		name   string
		goos   string
		getenv func(string) string
		want   string
	}{
		{
			name:   "windows 与桌面应用同目录",
			goos:   "windows",
			getenv: func(k string) string { return map[string]string{"LOCALAPPDATA": `C:\Users\u\AppData\Local`}[k] },
			want:   filepath.Join(`C:\Users\u\AppData\Local`, "Amane"),
		},
		{
			name:   "darwin 走 .config 而非 Application Support",
			goos:   "darwin",
			getenv: func(string) string { return "" },
			want:   filepath.Join("/home/u", ".config", "amane"),
		},
		{
			name:   "linux 跟随 XDG",
			goos:   "linux",
			getenv: func(string) string { return "" },
			want:   filepath.Join("/home/u/.config", "amane"),
		},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got, err := dirFor(c.goos, c.getenv, home, cfgDir)
			if err != nil {
				t.Fatalf("未预期的错误: %v", err)
			}
			if got != c.want {
				t.Fatalf("得到 %q, 期望 %q", got, c.want)
			}
		})
	}
}

func TestDirForWindowsWithoutLocalAppData(t *testing.T) {
	_, err := dirFor("windows", func(string) string { return "" },
		func() (string, error) { return "/home/u", nil },
		func() (string, error) { return "/home/u/.config", nil })
	if err == nil {
		t.Fatal("缺少 LOCALAPPDATA 时应报错")
	}
}

func TestLocalToken(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "token"), []byte("  abc123\n"), 0o600); err != nil {
		t.Fatalf("准备 token 失败: %v", err)
	}
	empty := t.TempDir()
	if err := os.WriteFile(filepath.Join(empty, "token"), nil, 0o600); err != nil {
		t.Fatalf("准备空 token 失败: %v", err)
	}

	cases := []struct {
		name string
		env  map[string]string
		want string
	}{
		{name: "取自 AMANE_DATA_DIR", env: map[string]string{"AMANE_DATA_DIR": dir}, want: "abc123"},
		{name: "跳过空 token 文件", env: map[string]string{"AMANE_DATA_DIR": empty}, want: ""},
		{name: "都没有", env: map[string]string{}, want: ""},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			token, path := LocalToken(func(k string) string { return c.env[k] })
			if token != c.want {
				t.Fatalf("token 为 %q, 期望 %q", token, c.want)
			}
			if token != "" && path == "" {
				t.Fatalf("取得 token 时应给出文件路径")
			}
		})
	}
}

func TestIsLoopback(t *testing.T) {
	cases := map[string]bool{
		"http://127.0.0.1:18000":    true,
		"http://localhost:8000":     true,
		"http://[::1]:8000":         true,
		"http://127.0.0.2:8000":     true,
		"http://LOCALHOST:8000":     true,
		"http://192.168.1.5:8000":   false,
		"https://amane.example.com": false,
	}
	for server, want := range cases {
		if got := IsLoopback(server); got != want {
			t.Errorf("IsLoopback(%q) = %v, 期望 %v", server, got, want)
		}
	}
}
