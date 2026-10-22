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
	force   bool
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
	file      string
	fileSet   bool
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
		case "--force":
			if hasVal {
				return nil, usagef("选项 %s 不接受值", name)
			}
			p.force = true
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
		case "--file", "-f":
			v, err := takeValue()
			if err != nil {
				return nil, err
			}
			p.file = v
			p.fileSet = true
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
			return nil, usagef("不接受在命令行传 token; token 从标准输入读取, 或由终端提示粘贴: amane login --server <地址> --profile <名字>")
		default:
			return nil, usagef("未知选项 %s; 用 amane --help 查看全部选项", name)
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

// 子命令清单: validate 据此报错, 各命令的 switch 据此分派.
var (
	skillSubcommands  = []string{"install", "show"}
	configSubcommands = []string{"list", "delete"}
)

// checkSubcommand 校验子命令. required 为真时缺子命令也报错, 并在错误里列出可用的.
func checkSubcommand(cmd string, args, available []string, required bool) error {
	sub := ""
	if len(args) > 0 {
		sub = args[0]
	}
	if sub == "" {
		if required {
			return subcommandError(cmd, "", available...)
		}
		return nil
	}
	for _, name := range available {
		if name == sub {
			return nil
		}
	}
	return subcommandError(cmd, sub, available...)
}

// validate 校验命令与选项的组合, 以及位置参数个数.
func (p *parsed) validate() error {
	switch p.cmd {
	case "catalog":
		if err := p.expectArgs(0, 1, ""); err != nil {
			return err
		}
	case "doctor", "upgrade", "schema":
		if err := p.expectArgs(0, 0, ""); err != nil {
			return err
		}
	case "login":
		if err := p.expectArgs(0, 0, ""); err != nil {
			return err
		}
		// 名字与地址都必须显式给出: 猜一个 default 会把别的实例的凭据覆盖掉.
		if strings.TrimSpace(p.server) == "" {
			return usagef("缺少 --server: amane login --server <地址> --profile <名字>")
		}
		if strings.TrimSpace(p.profile) == "" {
			return usagef("缺少 --profile: amane login --server <地址> --profile <名字>")
		}
	case "call":
		// 工具名是必需的; 忘了名字时 catalog 索引比一整屏用法有用.
		if len(p.args) < 1 {
			return usagef("缺少 <tool>; 可用工具见 amane catalog")
		}
		if err := p.expectArgs(1, 2, "<tool>"); err != nil {
			return err
		}
	case "wait":
		if err := p.expectArgs(1, 1, "<task-id>"); err != nil {
			return err
		}
	case "api":
		if err := p.expectArgs(1, 1, "<path>"); err != nil {
			return err
		}
	case "config":
		if err := p.expectArgs(0, 2, ""); err != nil {
			return err
		}
		if err := checkSubcommand("config", p.args, configSubcommands, false); err != nil {
			return err
		}
	case "skill":
		if err := p.expectArgs(0, 2, ""); err != nil {
			return err
		}
		if err := checkSubcommand("skill", p.args, skillSubcommands, true); err != nil {
			return err
		}
	default:
		return usagef("未知命令 %q; 用 amane --help 查看命令表", p.cmd)
	}

	if p.fileSet && p.cmd != "call" {
		return usagef("--file 只用于 call")
	}
	if p.fileSet && len(p.args) > 1 {
		return usagef("入参要么用 --file, 要么用位置参数, 不要同时给")
	}
	if p.force && p.cmd != "login" {
		return usagef("--force 只用于 login")
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
	// 放在最后: 选项本身写错时先报那个, 免得一律被「缺 profile」挡住.
	if strings.TrimSpace(p.profile) == "" && needsProfile(p.cmd) {
		return usagef("缺少 --profile; 已有 profile 见 amane config list")
	}
	return nil
}

// expectArgs 校验位置参数个数. want 是第一个位置参数的名字, 如 "<task-id>".
func (p *parsed) expectArgs(min, max int, want string) error {
	if len(p.args) < min {
		return usagef("缺少 %s", want)
	}
	if len(p.args) > max {
		return usagef("%s 接受 %d-%d 个位置参数, 收到 %d 个", p.cmd, min, max, len(p.args))
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

命令 (除 upgrade 与 config list/delete 外都要带 --profile):
  catalog [<tool>]                打印工具索引; 指定工具时打印其完整入参
  schema                          打印库结构摘要, 编写只读 SQL 时参考
  call <tool> [--file <路径>|-|JSON]  派发工具调用; 入参来自文件、标准输入或 JSON 字面量
  wait <task-id>                  轮询任务到终态并输出最后一次响应
  api <path>                      直通 GET 请求, 自动补 /api 前缀
  doctor                          自检: CLI、实例可达性、token、版本
  upgrade [--dry-run]             比对最新版本, 较新时下载并替换自身
  login --server <url> --profile <名字> [--force]
                                  写入一份 profile; 终端下 token 不回显
  config [list|delete <名字>]     列出或删除 profile
  config --profile <名字>         查看某个 profile
  skill install [--dir <path>]    生成 Skill 文件并写入目录
  skill show                      打印 Skill 内容, 不写文件

全局选项:
  --server <url>      覆盖实例地址 (login 时必填)
  --profile <name>    实例 profile; 所有实例操作必填
  --force             与 login 同用, 覆盖同名 profile
  --pretty            缩进输出 JSON
  --json              以 JSON 输出原文; catalog 与 schema 默认输出文本
  --timeout <时长>    单请求超时, 省略时 15s (须为正数); wait 中为轮询截止时间, 省略表示不设限
  --version           打印版本
  --help              打印本帮助

doctor 选项:
  --json              以 JSON 输出自检结果 (默认逐条文本)

wait 选项:
  --interval <时长>   轮询间隔, 默认 2s

call 选项:
  --file, -f <路径>   从文件读入参, 与位置参数二选一

api 选项:
  --method <方法>     只允许 GET

upgrade 选项:
  --dry-run           只打印将要执行的操作

skill install 选项:
  --dir <path>        安装到的父目录, 省略时为 ~/.agents/skills

退出码:
  0  请求完成 (含工具返回的业务 error, 结果以响应体为准)
  1  本机问题: 配置损坏、目录不可写、平台没有产物
  2  用法错误
  3  连接失败、鉴权失败或超时
  4  服务端拒绝了这次请求 (4xx/5xx)
`

// needsProfile 判断命令是否必须显式指定 profile.
//
// 所有操作实例的命令都要: 多个智能体可能同时操作不同实例, 缺省选一个「当前 profile」
// 会让漏写参数的命令打到别人那台实例上. config 与 upgrade 不碰实例, 不在此列.
func needsProfile(cmd string) bool {
	switch cmd {
	case "catalog", "schema", "call", "wait", "api", "doctor", "login", "skill":
		return true
	}
	return false
}
