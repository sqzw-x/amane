package app

import (
	"strconv"
	"strings"
	"time"

	"github.com/sqzw-x/amane/cli/internal/wait"
)

// parsed 是一次命令行的解析结果.
type parsed struct {
	cmd  string
	args []string

	server  string
	profile string
	pretty  bool
	jsonOut bool
	version bool
	help    bool

	timeout     time.Duration
	timeoutSet  bool
	interval    time.Duration
	intervalSet bool

	method    string
	methodSet bool
	dir       string
	dirSet    bool
	dryRun    bool
}

// parseArgs 解析全部参数. 选项可出现在命令前后, 位置参数保持顺序.
func parseArgs(args []string) (*parsed, error) {
	p := &parsed{timeout: defaultHTTPTimeout, interval: wait.DefaultInterval}
	var positional []string

	for i := 0; i < len(args); i++ {
		arg := args[i]
		if arg == "-" || !strings.HasPrefix(arg, "-") {
			positional = append(positional, arg)
			continue
		}
		name, val, hasVal := strings.Cut(arg, "=")
		takeValue := func() (string, error) {
			if hasVal {
				if val == "" {
					return "", usagef("选项 %s 缺少值", name)
				}
				return val, nil
			}
			if i+1 >= len(args) {
				return "", usagef("选项 %s 缺少值", name)
			}
			i++
			return args[i], nil
		}

		switch name {
		case "--server":
			v, err := takeValue()
			if err != nil {
				return nil, err
			}
			p.server = v
		case "--profile":
			v, err := takeValue()
			if err != nil {
				return nil, err
			}
			p.profile = v
		case "--timeout":
			v, err := takeValue()
			if err != nil {
				return nil, err
			}
			d, err := parseDuration(v)
			if err != nil {
				return nil, usagef("--timeout 需要时长, 例如 30s: %v", err)
			}
			p.timeout = d
			p.timeoutSet = true
		case "--interval":
			v, err := takeValue()
			if err != nil {
				return nil, err
			}
			d, err := parseDuration(v)
			if err != nil {
				return nil, usagef("--interval 需要时长, 例如 2s: %v", err)
			}
			p.interval = d
			p.intervalSet = true
		case "--method", "-X":
			v, err := takeValue()
			if err != nil {
				return nil, err
			}
			p.method = v
			p.methodSet = true
		case "--dir":
			v, err := takeValue()
			if err != nil {
				return nil, err
			}
			p.dir = v
			p.dirSet = true
		case "--pretty":
			if hasVal {
				return nil, usagef("选项 %s 不接受值", name)
			}
			p.pretty = true
		case "--json":
			if hasVal {
				return nil, usagef("选项 %s 不接受值", name)
			}
			p.jsonOut = true
		case "--dry-run":
			if hasVal {
				return nil, usagef("选项 %s 不接受值", name)
			}
			p.dryRun = true
		case "--version":
			p.version = true
		case "--help", "-h":
			p.help = true
		case "--token":
			return nil, usagef("不接受在命令行传 token; token 从标准输入读取: echo <token> | amane login --server <地址>")
		default:
			return nil, usagef("未知选项 %s", name)
		}
	}

	if len(positional) > 0 {
		p.cmd = positional[0]
		p.args = positional[1:]
	}
	return p, nil
}

// parseDuration 解析时长. 纯数字按秒处理.
func parseDuration(v string) (time.Duration, error) {
	if n, err := strconv.Atoi(v); err == nil {
		return time.Duration(n) * time.Second, nil
	}
	return time.ParseDuration(v)
}

// validate 校验命令与选项的组合, 以及位置参数个数.
func (p *parsed) validate() error {
	switch p.cmd {
	case "catalog":
		if err := p.expectArgs(0, 1); err != nil {
			return err
		}
	case "doctor", "upgrade", "login", "schema":
		if err := p.expectArgs(0, 0); err != nil {
			return err
		}
	case "call":
		if err := p.expectArgs(1, 2); err != nil {
			return err
		}
	case "wait", "api":
		if err := p.expectArgs(1, 1); err != nil {
			return err
		}
	case "config":
		if err := p.expectArgs(0, 2); err != nil {
			return err
		}
	case "skill":
		if err := p.expectArgs(1, 2); err != nil {
			return err
		}
	default:
		return usagef("未知命令 %q", p.cmd)
	}

	if p.intervalSet && p.cmd != "wait" {
		return usagef("--interval 只用于 wait")
	}
	if p.methodSet && p.cmd != "api" {
		return usagef("--method 只用于 api")
	}
	if p.methodSet && !strings.EqualFold(p.method, "GET") {
		return usagef("api 只允许 GET, 收到 %s", p.method)
	}
	// http.Client 里 0 与负值都表示不超时, 与本项目「每个请求都有超时」的约定相反.
	if p.timeoutSet && p.timeout <= 0 {
		return usagef("--timeout 须为正数, 收到 %s", p.timeout)
	}
	if p.dryRun && p.cmd != "upgrade" {
		return usagef("--dry-run 只用于 upgrade")
	}
	if p.dirSet && !(p.cmd == "skill" && p.arg(0) == "install") {
		return usagef("--dir 只用于 skill install")
	}
	return nil
}

// expectArgs 校验位置参数个数.
func (p *parsed) expectArgs(min, max int) error {
	if len(p.args) < min {
		return usagef("%s 缺少参数, 见 amane --help", p.cmd)
	}
	if len(p.args) > max {
		return usagef("%s 接受 %d 到 %d 个位置参数, 收到 %d 个", p.cmd, min, max, len(p.args))
	}
	return nil
}

func (p *parsed) arg(i int) string {
	if i < len(p.args) {
		return p.args[i]
	}
	return ""
}

// usageText 是帮助文本, 输出到 stdout.
const usageText = `amane — Amane 实例的外部智能体接口客户端

用法:
  amane [全局选项] <命令> [参数]

命令:
  catalog [<tool>]             打印工具索引; 指定工具时打印其完整入参
  schema                       打印库结构摘要, 编写只读 SQL 时参考
  call <tool> [@文件|-|JSON]    派发工具调用; 入参来自文件、标准输入或 JSON 字面量
  wait <task-id>               轮询任务到终态并输出最后一次响应
  api <path>                   直通 GET 请求, 自动补 /api 前缀
  doctor                       自检: CLI、实例可达性、token
  upgrade [--dry-run]          比对最新版本, 较新时下载并替换自身
  login --server <url>         从标准输入读取 token 并写入配置文件
  config [use <profile>|list]  查看当前配置, 切换或列出 profile
  skill install [--dir <path>] 生成 Skill 文件并写入目录
  skill show                   打印 Skill 内容, 不写文件

全局选项:
  --server <url>      覆盖实例地址
  --profile <name>    使用指定 profile
  --pretty            缩进输出 JSON
  --json              以 JSON 输出原文; catalog 与 schema 默认输出文本
  --timeout <时长>    HTTP 超时, 默认 30s; wait 中为轮询截止时间, 默认 0 (不超时)
  --version           打印版本
  --help              打印本帮助

doctor 选项:
  --json              以 JSON 输出自检结果 (默认逐条文本)

wait 选项:
  --interval <时长>   轮询间隔, 默认 2s

api 选项:
  --method <方法>     只允许 GET

upgrade 选项:
  --dry-run           只打印将要执行的操作

skill install 选项:
  --dir <path>        安装到的父目录, 省略时为 ~/.agents/skills

退出码:
  0  请求完成 (含工具返回的业务 error, 结果以响应体为准)
  2  用法错误
  3  连接失败或鉴权失败
`
