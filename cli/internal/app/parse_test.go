package app

import (
	"strings"
	"testing"
	"time"
)

func TestParseArgs(t *testing.T) {
	cases := []struct {
		name  string
		args  []string
		check func(t *testing.T, p *parsed)
	}{
		{
			name: "命令与位置参数",
			args: []string{"call", "sql_explore", "@args.json"},
			check: func(t *testing.T, p *parsed) {
				if p.cmd != "call" || len(p.args) != 2 || p.args[1] != "@args.json" {
					t.Fatalf("解析错误: %+v", p)
				}
			},
		},
		{
			name: "选项在命令前",
			args: []string{"--server", "http://127.0.0.1:8000", "--pretty", "catalog"},
			check: func(t *testing.T, p *parsed) {
				if p.cmd != "catalog" || p.server != "http://127.0.0.1:8000" || !p.pretty {
					t.Fatalf("解析错误: %+v", p)
				}
			},
		},
		{
			name: "等号形式",
			args: []string{"--profile=work", "catalog"},
			check: func(t *testing.T, p *parsed) {
				if p.profile != "work" {
					t.Fatalf("profile 为 %q", p.profile)
				}
			},
		},
		{
			name: "连字符是位置参数",
			args: []string{"call", "sql_explore", "-"},
			check: func(t *testing.T, p *parsed) {
				if len(p.args) != 2 || p.args[1] != "-" {
					t.Fatalf("解析错误: %+v", p)
				}
			},
		},
		{
			name: "wait 的间隔与截止",
			args: []string{"wait", "7", "--interval", "500ms", "--timeout", "10m"},
			check: func(t *testing.T, p *parsed) {
				if p.interval != 500*time.Millisecond || !p.intervalSet {
					t.Fatalf("interval 为 %v", p.interval)
				}
				if p.timeout != 10*time.Minute || !p.timeoutSet {
					t.Fatalf("timeout 为 %v", p.timeout)
				}
			},
		},
		{
			name: "纯数字时长按秒",
			args: []string{"wait", "7", "--interval", "5"},
			check: func(t *testing.T, p *parsed) {
				if p.interval != 5*time.Second {
					t.Fatalf("interval 为 %v", p.interval)
				}
			},
		},
		{
			name: "api 的方法统一大写",
			args: []string{"api", "tasks/1", "--method", "post", "--profile", "default"},
			check: func(t *testing.T, p *parsed) {
				if err := p.validate(); err != nil {
					t.Fatalf("校验失败: %v", err)
				}
				if !p.methodSet || p.method != "POST" {
					t.Fatalf("method 为 %q", p.method)
				}
			},
		},
		{
			name: "默认超时",
			args: []string{"catalog"},
			check: func(t *testing.T, p *parsed) {
				if p.timeout != defaultHTTPTimeout || p.timeoutSet {
					t.Fatalf("timeout 为 %v", p.timeout)
				}
			},
		},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			p, err := parseArgs(c.args)
			if err != nil {
				t.Fatalf("未预期的错误: %v", err)
			}
			c.check(t, p)
		})
	}
}

func TestParseArgsErrors(t *testing.T) {
	cases := []struct {
		name string
		args []string
		want string
	}{
		{name: "未知选项", args: []string{"catalog", "--nope"}, want: "未知选项"},
		{name: "选项缺少值", args: []string{"catalog", "--server"}, want: "缺少值"},
		{name: "拒绝命令行 token", args: []string{"login", "--token", "abc"}, want: "不接受在命令行传 token"},
		{name: "时长非法", args: []string{"catalog", "--timeout", "abc"}, want: "--timeout 需要时长"},
		{name: "布尔选项带值", args: []string{"catalog", "--pretty=1"}, want: "不接受值"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			_, err := parseArgs(c.args)
			if err == nil {
				t.Fatal("期望报错, 实际成功")
			}
			if !strings.Contains(err.Error(), c.want) {
				t.Fatalf("错误信息为 %q, 期望包含 %q", err, c.want)
			}
		})
	}
}

func TestValidateErrors(t *testing.T) {
	cases := []struct {
		name string
		args []string
		want string
	}{
		{name: "未知命令", args: []string{"frobnicate"}, want: "未知命令"},
		{name: "call 缺少工具名", args: []string{"call"}, want: "缺少 <tool>"},
		{name: "wait 缺少 task-id", args: []string{"wait"}, want: "缺少 <task-id>"},
		{name: "api 缺少 path", args: []string{"api"}, want: "缺少 <path>"},
		{name: "skill 缺少子命令", args: []string{"skill"}, want: "skill 缺少子命令; 可用: install, show"},
		{name: "skill 未知子命令", args: []string{"skill", "get"}, want: `skill 未知子命令 "get"; 可用: install, show`},
		{name: "config 缺少子命令", args: []string{"config"}, want: "config 缺少子命令; 可用: list, delete"},
		{name: "config 未知子命令", args: []string{"config", "foo"}, want: `config 未知子命令 "foo"; 可用: list, delete`},
		{name: "config 不接受 profile", args: []string{"config", "list", "--profile", "work"}, want: "config 不接受 --profile"},
		{name: "call 参数过多", args: []string{"call", "a", "b", "c"}, want: "位置参数"},
		{name: "api 未知方法", args: []string{"api", "x", "--method", "TRACE"}, want: "未知方法"},
		{name: "interval 只用于 wait", args: []string{"catalog", "--interval", "1s"}, want: "--interval 只用于 wait"},
		{name: "method 只用于 api", args: []string{"catalog", "--method", "GET"}, want: "--method 只用于 api"},
		{name: "dry-run 只用于 upgrade", args: []string{"catalog", "--dry-run"}, want: "--dry-run 只用于 upgrade"},
		{name: "dir 只用于 skill install", args: []string{"skill", "show", "--dir", "/tmp/x"}, want: "--dir 只用于 skill install"},
		{name: "doctor 不接受位置参数", args: []string{"doctor", "x"}, want: "位置参数"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			p, err := parseArgs(c.args)
			if err != nil {
				t.Fatalf("解析失败: %v", err)
			}
			if err := p.validate(); err == nil {
				t.Fatal("期望报错, 实际成功")
			} else if !strings.Contains(err.Error(), c.want) {
				t.Fatalf("错误信息为 %q, 期望包含 %q", err, c.want)
			}
		})
	}
}

func TestExitCodeUsage(t *testing.T) {
	cases := []struct {
		name string
		args []string
	}{
		{name: "缺少命令", args: nil},
		{name: "未知命令", args: []string{"frobnicate"}},
		{name: "未知选项", args: []string{"catalog", "--nope"}},
		{name: "api 未知方法", args: []string{"api", "tasks/1", "--method", "HEAD"}},
		{name: "call 缺入参且 stdin 是终端", args: []string{"call", "sql_explore"}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			h := newHarness(t, "http://127.0.0.1:1")
			if code := h.run(c.args...); code != ExitUsage {
				t.Fatalf("退出码为 %d, 期望 %d", code, ExitUsage)
			}
			if h.out() != "" {
				t.Fatalf("用法错误不应写标准输出: %q", h.out())
			}
		})
	}
}

func TestVersionAndHelp(t *testing.T) {
	h := newHarness(t, "")
	if code := h.run("--version"); code != ExitOK {
		t.Fatalf("退出码为 %d", code)
	}
	if want := "amane 0.1.0 (linux/amd64)"; !strings.Contains(h.out(), want) {
		t.Fatalf("输出为 %q, 期望包含 %q", h.out(), want)
	}

	h = newHarness(t, "")
	if code := h.run("--help"); code != ExitOK {
		t.Fatalf("退出码为 %d", code)
	}
	for _, want := range []string{"catalog", "call", "wait", "api", "doctor", "upgrade", "login", "skill"} {
		if !strings.Contains(h.out(), want) {
			t.Errorf("帮助缺少 %q", want)
		}
	}
}

func TestDurationParsing(t *testing.T) {
	cases := map[string]time.Duration{
		"0":   0,
		"30":  30 * time.Second,
		"30s": 30 * time.Second,
		"2m":  2 * time.Minute,
	}
	for in, want := range cases {
		got, err := parseDuration(in)
		if err != nil {
			t.Errorf("%q: 未预期的错误: %v", in, err)
			continue
		}
		if got != want {
			t.Errorf("%q: 得到 %v, 期望 %v", in, got, want)
		}
	}
	if _, err := parseDuration("x"); err == nil {
		t.Error("非法时长应报错")
	}
}
