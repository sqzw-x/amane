// Package skill 生成智能体 Skill 文件, 并写入目标目录.
//
// 内容只与 CLI 自身有关: 命令、退出码与审批礼仪都不随实例变化, 所以生成时不连接实例,
// 版本也跟随 CLI. 实例相关的位置 (数据目录、日志) 由 `amane catalog --json` 运行时取.
//
// 只生成 SKILL.md 与 version 两个文件; 工具清单不进 Skill, 清单随实例变化, 写进文件既会过期,
// 又让每次会话都为全部工具付一遍 token; 改为运行时用 `amane catalog` 取索引.
package skill

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// 生成的文件名.
const (
	MainFile    = "SKILL.md"
	VersionFile = "version"
)

// DirName 是 Skill 在智能体 Skill 目录下的子目录名.
const DirName = "amane"

// DefaultParent 是默认的 Skill 父目录, 相对用户主目录.
var DefaultParent = filepath.Join(".agents", "skills")

// DefaultDir 返回默认安装位置. 父目录不必预先存在, 安装时创建.
func DefaultDir(home string) string {
	return filepath.Join(home, DefaultParent, DirName)
}

// ResolveTarget 把用户给的目录规整为 Skill 自身所在的目录.
//
// 用户通常给父目录 (多个 Skill 并列的那一层), 因此末尾不是 DirName 时补一层;
// 已经指向 DirName 时原样返回, 重复执行不会多套一层.
func ResolveTarget(dir string) string {
	cleaned := filepath.Clean(dir)
	if filepath.Base(cleaned) == DirName {
		return cleaned
	}
	return filepath.Join(cleaned, DirName)
}

// Render 生成全部 Skill 文件. 输出对同一 CLI 版本稳定.
func Render(cliVersion string) map[string][]byte {
	version := strings.TrimSpace(cliVersion)
	if version == "" {
		version = "未知"
	}
	return map[string][]byte{
		MainFile:    []byte(renderMain(version)),
		VersionFile: []byte(version + "\n"),
	}
}

// Install 写入全部文件, 覆盖同名文件. 目录不存在时创建.
func Install(dir string, files map[string][]byte) error {
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return fmt.Errorf("创建 Skill 目录失败: %w", err)
	}
	for name, data := range files {
		path := filepath.Join(dir, name)
		if err := os.WriteFile(path, data, 0o644); err != nil {
			return fmt.Errorf("写入 %s 失败: %w", path, err)
		}
	}
	return nil
}

// InstalledVersion 读取已安装 Skill 的版本; 未安装时返回错误.
func InstalledVersion(dir string) (string, error) {
	data, err := os.ReadFile(filepath.Join(dir, VersionFile))
	if err != nil {
		return "", fmt.Errorf("未安装")
	}
	version := strings.TrimSpace(string(data))
	if version == "" {
		return "", fmt.Errorf("版本文件为空")
	}
	return version, nil
}

// skillDescription 是 frontmatter 里的描述.
const skillDescription = "通过 amane CLI 管理 Amane 实例 (影片元数据管理服务): 查询与修改番号条目、演员、分类、媒体库、订阅源、定时任务, 执行只读 SQL, 等待任务到终态."

// yamlQuote 写成 YAML 双引号标量. 描述里的 ": " 会让未加引号的值解析失败, 而解析失败
// 的 frontmatter 会让部分 Agent 工具直接忽略整个 Skill.
func yamlQuote(s string) string {
	var b strings.Builder
	b.WriteByte('"')
	for _, r := range s {
		switch r {
		case '"':
			b.WriteString(`\"`)
		case '\\':
			b.WriteString(`\\`)
		case '\n':
			b.WriteString(`\n`)
		default:
			b.WriteRune(r)
		}
	}
	b.WriteByte('"')
	return b.String()
}

func renderMain(version string) string {
	const pf = " --profile <name>"
	var b strings.Builder
	b.WriteString("---\n")
	b.WriteString("name: amane\n")
	b.WriteString("description: " + yamlQuote(skillDescription) + "\n")
	b.WriteString("---\n\n")
	fmt.Fprintf(&b, "# Amane\n\n本文件由 `amane skill install` 生成, 对应 CLI 版本 %s. CLI 升级后重新运行同一条命令.\n", version)
	b.WriteString("本文件不绑定具体实例: 下文命令里的 `<name>` 是 profile 名, `amane config list` 列出本机有哪些, 选你在操作的那一份填进去.\n")

	b.WriteString("\n## 何时使用\n\n")
	b.WriteString("用户要求管理或查询 Amane 实例中的影片元数据、演员、分类、媒体库、订阅源、定时任务与任务队列时使用. 用户说「用我的 Amane 做什么」也属于此范围.\n")

	b.WriteString("\n## 开始前\n\n")
	b.WriteString("先确认要操作哪份 profile: 运行 `amane config list`. 本机有多份而用户没说清时, 停下来问用户, 不要猜; 一份都没有时, 问清实例地址, 按「鉴权」一节让用户自己登录.\n")
	b.WriteString("再运行 `amane upgrade`: 它比对 GitHub 上的最新版本, 已是最新时什么都不做. 失败时 (离线、GitHub 不可达等) 不要重试, 也不要停下来, 直接用当前版本继续.\n")

	b.WriteString("\n## 工作方式\n\n")
	fmt.Fprintf(&b, "1. 先运行 `amane catalog%s` 取工具索引, 按名字选择工具, 不要自行猜测工具名. 索引只给一行签名, 足够选出工具.\n", pf)
	fmt.Fprintf(&b, "2. 参数不确定时运行 `amane catalog <tool>%s` 取该工具的完整入参, 不要为其他工具一起取. 嵌套入参写入文件, 再用 `amane call <tool> --file <路径>%s` 传入, 避免在 shell 里嵌套引号.\n", pf, pf)
	fmt.Fprintf(&b, "3. 写只读 SQL 前先运行 `amane schema%s` 取库结构.\n", pf)
	fmt.Fprintf(&b, "4. 入队类工具返回 `task_id`, 用 `amane wait <task_id>%s` 阻塞到终态, 不要自己写查询循环; 长任务加 `--timeout` 兜底, 否则会一直等到出结果.\n", pf)
	b.WriteString("5. 退出码 0 表示请求已完成, 业务结果仍看响应体 (工具失败时是 `error` 字段); 非 0 表示这次调用失败, 不要当成完成: 1 本机问题 (配置损坏、目录不可写), 2 用法错误, 3 连接或鉴权, 4 服务端拒绝 (参数不对、工具不存在).\n")
	fmt.Fprintf(&b, "6. 连接或鉴权失败时先运行 `amane doctor%s`, 按它的结论处理, 不要反复试探.\n", pf)

	b.WriteString("\n## 调用约定\n\n")
	b.WriteString("CLI 用 profile 区分实例: 一份 profile 记一个实例的地址与 token, `amane config list` 列出本机全部.\n")
	b.WriteString("所有操作经由 CLI, 不要自己拼 HTTP 请求. 每条命令都带 `--profile <name>`, 换成你正在操作的那一份:\n\n")
	b.WriteString("```sh\n")
	fmt.Fprintf(&b, "amane catalog [<tool>]%s       # 工具索引 / 单个工具的完整入参\n", pf)
	fmt.Fprintf(&b, "amane schema%s                 # 库结构摘要\n", pf)
	fmt.Fprintf(&b, "amane call <tool> --file <路径>%s  # 派发调用\n", pf)
	fmt.Fprintf(&b, "amane wait <task-id>%s         # 轮询到终态\n", pf)
	fmt.Fprintf(&b, "amane api <path> [--method <方法>] [--file <路径>|-]%s  # 直通 REST, 工具做不到时用\n", pf)
	fmt.Fprintf(&b, "amane doctor%s                 # 自检\n", pf)
	b.WriteString("```\n")
	b.WriteString("\n`--timeout` 在 `wait` 上是轮询截止时间, 在其余命令上是单次请求超时 (默认 15s). 只有慢查询这类耗时才需要.\n")

	b.WriteString("\n## REST 兜底\n\n")
	b.WriteString("工具目录是主路径: 目录里有等价工具时不要用 REST, 工具做不到时才直通. 形状先看 OpenAPI, 只取要用的那一段:\n\n")
	b.WriteString("```sh\n")
	fmt.Fprintf(&b, "amane api /docs/openapi.json%s | jq '.paths | keys'\n", pf)
	fmt.Fprintf(&b, "amane api /docs/openapi.json%s | jq '.paths[\"/api/comments/{comment_id}\"]'\n", pf)
	b.WriteString("```\n")
	b.WriteString("\nREST 供实例自己的界面使用, 不为智能体设计: 不保证兼容性与易用性, 形状随版本变化, 也不压缩体积. 整份文档数百 KB, 不要整份读进上下文.\n")
	b.WriteString("写操作照常受「审批礼仪」约束; 经 REST 的写入不进工具审计, 事后只有请求记录里的方法与路径.\n")

	b.WriteString("\n## 审批礼仪\n\n")
	b.WriteString("服务端不设审批闸门, 是否执行由你与用户的对话决定. 下列操作执行前必须先向用户复述影响面 (修改什么、涉及多少条、能否撤销), 得到明确同意后才调用:\n\n")
	b.WriteString("- 删除番号条目、演员、演员别名、分类、媒体库、订阅源、定时任务.\n")
	b.WriteString("- 合并分类或番号条目, 以及任何批量修改.\n")
	b.WriteString("- 批量删除或批量修改用户标签、订阅源条目.\n")
	b.WriteString("- 预计耗时较长的查询 (allow_slow 只跳过实例的慢查询闸门, 时限仍由 sql_timeout_ms 决定), 以及任何预计会全表扫描的 SQL.\n")
	b.WriteString("- 经 REST 的删除、合并与批量修改与工具调用同等对待, 不因绕开工具而放宽.\n")
	b.WriteString("\n用户未答复或答复含糊时不执行. 不要用 `--yes` 之类的开关跳过这一步: CLI 不提供它.\n")

	b.WriteString("\n## 自服务\n\n")
	b.WriteString("实例与本机同机时, 直接读文件比走接口快:\n\n")
	b.WriteString("- 位置由实例给出: `amane catalog --json --profile <name>` 的 `paths` 里有数据目录、日志目录、数据库与配置文件\n")
	b.WriteString("- 单任务日志、配置快照与出站 HTTP 记录在日志目录的 `tasks/task-{id}/` 下, 失败排查先读这里\n")
	b.WriteString("- 数据库: 只读查询优先用目录中的 SQL 探查工具, 它复用沙箱、超时与查询预设\n")
	b.WriteString("- 配置文件含站点密钥, 读取时不得回显\n")
	b.WriteString("- 远端实例无法访问服务端文件系统时, 改用 `amane api /api/tasks/<id>/record --profile <name>` 取任务记录包\n")

	b.WriteString("\n## 鉴权\n\n")
	b.WriteString("token 由 CLI 保管, 不进入对话. 不要读取、回显或写入 token, 也不要向用户索要 token 后代为输入; 需要登录时请用户自己在终端执行 `amane login --server <实例地址> --profile <name>` 并粘贴 token.\n")
	b.WriteString("\n持有 token 等同于实例所有者. 不把本 Skill 的调用能力转交给第三方.\n")
	return b.String()
}
