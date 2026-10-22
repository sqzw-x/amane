// Package skill 由工具目录生成智能体 Skill 文件, 并写入目标目录.
//
// 生成物只依赖 GET /api/tools 的响应, 因此与实例版本天然一致.
//
// 只生成一个文件: 工具清单不进 Skill. 清单随实例变化, 写进文件既会过期,
// 又让每次会话都为全部工具付一遍 token; 改为运行时用 `amane catalog` 取索引.
package skill

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/sqzw-x/amane/cli/internal/api"
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

// Render 由目录生成全部 Skill 文件. 输出对同一目录稳定.
func Render(cat *api.Catalog, cliVersion string) map[string][]byte {
	version := strings.TrimSpace(cat.Version)
	if version == "" {
		version = "未知"
	}
	return map[string][]byte{
		MainFile:    []byte(renderMain(cat, version, cliVersion)),
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

func renderMain(cat *api.Catalog, version, cliVersion string) string {
	var b strings.Builder
	b.WriteString("---\n")
	b.WriteString("name: amane\n")
	b.WriteString("description: 通过 amane CLI 管理 Amane 实例 (影片元数据管理服务): 查询与修改番号条目、演员、分类、媒体库、订阅源、定时任务, 执行只读 SQL, 等待任务到终态.\n")
	b.WriteString("---\n\n")
	fmt.Fprintf(&b, "# Amane\n\n本文件由 `amane skill install` 生成, 对应实例版本 %s, CLI 版本 %s. 实例升级后重新运行 `amane skill install`.\n", version, cliVersion)

	b.WriteString("\n## 何时使用\n\n")
	b.WriteString("用户要求管理或查询 Amane 实例中的影片元数据、演员、分类、媒体库、订阅源、定时任务与任务队列时使用. 用户说「用我的 Amane 做什么」也属于此范围.\n")

	b.WriteString("\n## 开始前\n\n")
	b.WriteString("先运行 `amane upgrade`: 它比对 GitHub 上的最新版本, 已是最新时什么都不做. 失败时 (离线、GitHub 不可达等) 不要重试, 也不要停下来, 直接用当前版本继续.\n")

	b.WriteString("\n## 工作方式\n\n")
	b.WriteString("1. 先运行 `amane catalog` 取工具索引, 按名字选择工具, 不要自行猜测工具名. 索引只给一行签名, 足够选出工具.\n")
	b.WriteString("2. 参数不确定时运行 `amane catalog <tool>` 取该工具的完整入参, 不要为其他工具一起取. 嵌套入参写入文件, 再用 `amane call <tool> @args.json` 传入, 避免在 shell 里嵌套引号.\n")
	b.WriteString("3. 写只读 SQL 前先运行 `amane schema` 取库结构.\n")
	b.WriteString("4. 入队类工具返回 `task_id`, 用 `amane wait <task_id>` 阻塞到终态, 不要自己写查询循环; 长任务加 `--timeout` 兜底, 否则会一直等到出结果.\n")
	b.WriteString("5. 退出码 0 表示请求已完成, 业务结果仍看响应体 (工具失败时是 `error` 字段); 非 0 表示这次调用失败, 不要当成完成: 1 本机问题 (配置损坏、目录不可写), 2 用法错误, 3 连接或鉴权, 4 服务端拒绝 (参数不对、工具不存在).\n")
	b.WriteString("6. 连接或鉴权失败时先运行 `amane doctor`, 按它的结论处理, 不要反复试探.\n")

	b.WriteString("\n## 调用约定\n\n")
	b.WriteString("所有操作经由 CLI, 不要自己拼 HTTP 请求. 命令表固定, 不随实例工具变化:\n\n")
	b.WriteString("```sh\n")
	b.WriteString("amane catalog [<tool>]       # 工具索引 / 单个工具的完整入参\n")
	b.WriteString("amane schema                 # 库结构摘要\n")
	b.WriteString("amane call <tool> [@file|-]  # 派发调用\n")
	b.WriteString("amane wait <task-id>         # 轮询到终态\n")
	b.WriteString("amane api <path>             # 直通 GET\n")
	b.WriteString("amane doctor                 # 自检\n")
	b.WriteString("```\n")
	b.WriteString("\n除 `wait` 外, 其余命令都可加 `--timeout <时长>` 放宽单个请求的超时 (默认 15s). 只有慢查询这类耗时才需要.\n")

	b.WriteString("\n## 审批礼仪\n\n")
	b.WriteString("服务端不设审批闸门, 是否执行由你与用户的对话决定. 下列操作执行前必须先向用户复述影响面 (修改什么、涉及多少条、能否撤销), 得到明确同意后才调用:\n\n")
	b.WriteString("- 删除番号条目、演员、演员别名、分类、媒体库、订阅源、定时任务.\n")
	b.WriteString("- 合并分类或番号条目, 以及任何批量修改.\n")
	b.WriteString("- 批量删除或批量修改用户标签、订阅源条目.\n")
	b.WriteString("- 预计耗时较长的查询 (allow_slow 只跳过实例的慢查询闸门, 时限仍由 sql_timeout_ms 决定), 以及任何会全表写入的 SQL.\n")
	b.WriteString("\n用户未答复或答复含糊时不执行. 不要用 `--yes` 之类的开关跳过这一步: CLI 不提供它.\n")

	b.WriteString("\n## 自服务\n\n")
	b.WriteString("很多事不需要接口, 直接读文件更快:\n\n")
	b.WriteString(renderSelfService(cat))

	b.WriteString("\n## 鉴权\n\n")
	b.WriteString("token 由 CLI 保管, 不进入对话. 不要读取、回显或写入 token, 也不要向用户索要 token 后代为输入; 需要登录时请用户自己在终端执行 `amane login --server <地址>` 并粘贴 token.\n")
	b.WriteString("\n持有 token 等同于实例所有者. 不把本 Skill 的调用能力转交给第三方.\n")
	return b.String()
}

func renderSelfService(cat *api.Catalog) string {
	var b strings.Builder
	paths := cat.Paths
	if paths.DataDir != "" {
		fmt.Fprintf(&b, "- 数据目录: `%s`\n", paths.DataDir)
	}
	if paths.LogDir != "" {
		fmt.Fprintf(&b, "- 日志目录: `%s`; 单任务日志、配置快照与出站 HTTP 记录在 `tasks/task-{id}/` 下, 失败排查先读这里\n", paths.LogDir)
	}
	if paths.DBPath != "" {
		fmt.Fprintf(&b, "- 数据库: `%s`; 只读查询优先用目录中的 SQL 探查工具, 它复用沙箱、超时与查询预设\n", paths.DBPath)
	}
	if paths.ConfigPath != "" {
		fmt.Fprintf(&b, "- 配置文件: `%s`; 其中含站点密钥, 读取时不得回显\n", paths.ConfigPath)
	}
	if len(paths.Env) > 0 {
		fmt.Fprintf(&b, "- 决定上述路径的环境变量: %s\n", strings.Join(paths.Env, ", "))
	}
	b.WriteString("- 远端实例无法访问服务端文件系统时, 改用 `amane api /api/tasks/<id>/record` 取任务记录包\n")
	b.WriteString("\n")
	return b.String()
}
