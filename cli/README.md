# amane CLI

Amane 实例的外部智能体接口客户端. 它是实例派发端点的薄客户端: 只保管 token、组装入参、折叠轮询与自检, 不认识任何具体工具名. 设计背景见 [docs/dev/agent.md](../../docs/dev/agent.md).

仅依赖 Go 标准库, 交叉编译为单一静态二进制.

## 安装

```sh
# 从 Release 下载并校验 (不要用浏览器打开链接: macOS 会给下载物加隔离属性)
curl -fL -O https://github.com/sqzw-x/amane/releases/latest/download/amane-darwin-arm64
curl -fL -O https://github.com/sqzw-x/amane/releases/latest/download/SHA256SUMS
shasum -a 256 -c SHA256SUMS --ignore-missing
chmod +x amane-darwin-arm64          # 下载物默认没有执行位
mv amane-darwin-arm64 ~/.local/bin/amane

# 或从源码构建 (cli/ 是独立 module)
cd cli && go build -o amane ./cmd/amane
```

产物名按 `amane-{os}-{arch}` 命名, Windows 加 `.exe`. 五个平台: `linux/amd64`、`linux/arm64`、`darwin/amd64`、`darwin/arm64`、`windows/amd64`.

安装位置不归 CLI 管: 放到已有 `PATH` 的目录, 或之后一律用绝对路径调用.

## 登录

```sh
echo <token> | amane login --server <实例地址>   # 桌面版默认 18000, 源码与 Docker 默认 8000
```

token 只从标准输入读取, 不接受命令行参数 (会进 shell 历史与 `ps`). 交互终端下读一行并给出提示, 但终端本身会回显; 想不让它出现在屏幕上就用管道. 权限 0600:

| 平台 | 路径 | 说明 |
|------|------|------|
| macOS | `~/.config/amane/cli.json` | UNIX 约定, 不占用 `~/Library/Application Support` |
| Linux | `$XDG_CONFIG_HOME/amane/cli.json`, 默认 `~/.config/amane/cli.json` | 同上 |
| Windows | `%LOCALAPPDATA%\Amane\cli.json` | 与桌面应用共用数据目录, 与实例的 `config.toml` / `token` 并列 |

token 的取值顺序: profile → 环境变量 `AMANE_TOKEN` → 本机 `token` 文件. 后者依次找 `AMANE_DATA_DIR`、桌面应用的数据目录 (macOS `~/Library/Application Support/Amane`, Windows `%LOCALAPPDATA%\Amane`)、`./data`. 与实例同机时通常无需 `login`: 没有 profile 而本机 `token` 文件可读时, 端口按桌面壳的规则取 — 环境变量 `AMANE_PORT` > 数据目录 `desktop.env` 的 `AMANE_PORT` > 桌面默认 18000 (源码与 Docker 默认 8000). 端口对不上时才需 `amane login --server` 指明地址.

实例地址指向非回环地址且为明文 `http://` 时, 在标准错误给一次警告, 不阻断.

## 常用命令

```sh
amane catalog                          # 工具索引, 按域分组的一行制文本
amane catalog update_metadata          # 单个工具的完整入参
amane catalog --json                   # 索引原文 (JSON)
amane schema                           # 库结构摘要, 写只读 SQL 时参考

amane call update_metadata @args.json        # 入参来自文件
amane call sql_explore -                     # 入参来自标准输入
amane call sql_explore '{"sql":"select 1"}'  # 入参字面量

amane wait 42                          # 轮询任务到终态
amane wait 42 --interval 1s --timeout 10m

amane api tasks/42                     # 直通 GET, 自动补 /api 前缀
amane doctor                           # 自检: CLI、实例、鉴权
amane doctor --json
amane upgrade                          # 比对最新版本, 较新时下载替换
amane upgrade --dry-run                # 只打印判断结果与下载计划
amane skill install                    # 默认写入 ~/.agents/skills/amane
amane skill install --dir ~/my-skills  # 指定父目录, 自动追加 amane
amane skill show                       # 打印 Skill 内容, 不写文件

amane config                           # 当前 profile (token 打码)
amane config use work
amane config list
```

全局选项: `--server <url>`、`--profile <name>`、`--pretty`、`--json`、`--timeout <时长>` (单个请求的超时, 默认 15s; 升级下载同样受它约束, 慢网下自行放宽)、`--version`、`--help`. `catalog` 与 `schema` 默认输出紧凑文本, `--json` 取原文.

`wait` 用 `GET /api/tasks/{id}` 取任务详情 (目录里没有取任务详情的工具), 到达终态输出最后一次响应 JSON. 终态为 `done` 与 `failed`: 服务端 `TaskStatus` 只有 `queued` / `running` / `done` / `failed` 四个取值, 取消的任务同样标成 `failed` (`error` 为「已由用户取消」), 没有 `cancelled`. `wait` 的 `--timeout` 表示轮询截止时间, 默认 0 表示不超时.

`upgrade` 先取 GitHub 上的最新 tag (`releases/latest` 的重定向目标), 与自身版本比较, 确实较新才下载; 已是同版本或本地构建的 `dev` 时不动文件.

Skill 只有 `SKILL.md` 一个文件 (另有 `version` 记录生成时对应的实例版本). 工具清单不在其中: 清单随实例变化, 写进文件既会过期, 又让每次会话都为全部工具付一遍 token; 改由运行时 `amane catalog` 取.

`skill install` 不会降级: 已装版本不低于实例版本时保留不动并说明原因, 只有实例更新时才覆盖. 同一份 Skill 可能同时服务多个实例, 因此以最新版本为准; 这要求 Skill 内容自身保持向后兼容. 版本无法比较时 (例如实例版本为空) 按「没装过」处理.

## 退出码

| 退出码 | 含义 |
|--------|------|
| 0 | 请求完成. 工具返回的业务 `error` 仍是 0, 结果以响应体为准 |
| 1 | 本机问题 (配置损坏、目录不可写、替换自身失败、平台没有产物) |
| 2 | 用法错误 (参数缺失、非法选项、`api` 用了非 GET) |
| 3 | 连接失败或鉴权失败 (网络错误、401、超时)、自检未通过、升级下载或校验失败 |
| 4 | 服务端拒绝了这次请求 (4xx/5xx, 例如参数校验失败、工具或任务不存在) |

数据写标准输出, 诊断与提示写标准错误.

## 开发

```sh
make check              # gofmt 检查 + go vet + go test
make build              # 当前平台的二进制到 dist/amane
make dist VERSION=0.22.1  # 交叉编译五个平台并生成 SHA256SUMS
```

版本号经 `-ldflags "-X main.version=..."` 注入, 默认 `dev`.
