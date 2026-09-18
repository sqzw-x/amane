# 助理 Agent

> 入口: `src/amane/agent/`. 本文只记录边界与契约; 字段与签名去源码.
> 配置见 [config.md](config.md), 列表接入见 [api.md](api.md), 翻译 LLM 见 [llm.md](llm.md), 前端 IA 见 [frontend.md](frontend.md).

## 定位

**助理 Agent** 是内部对会话式读写助手的称呼; 产品面只称 **Amane** (首页对话), 不向用户暴露 Agent / SQL 等实现词.

能力: 只读 SQL 探查库; 交付结果写入可引用的 **Saved Query** 再被 Browse / 下载消费; 写变更只经由封装领域工具 (Repository / Handler 语义), **禁止**裸 DML / DDL. 与 `llm/` 翻译管线并行, **不共用** Hot section.

## 工具

只读工具始终可用:

| 工具 | 契约 |
|------|------|
| `sql_explore` | 中间推理; 默认只回样例. `create_view=true` 物化会话内 SavedQuery 供 `inspect_result` 翻页 — 视图是**纯行数组**, 无 entity / `id` 列要求, **不**纳入交付芯片 |
| `sql_deliver` | 面向用户的结果 → `saved_query` + 内存结果缓存. `entity=metadata\|actor` 交付须含主键列 `id`, 可作片库 / 演员筛选并深链; 省略 entity (或 `data`) 交付任意只读结果, 只进入数据页 |
| `inspect_result` | 按 `saved_query_id` 窥行 (交付或探查视图) |

写操作工具按域分组为 `Capability`, **不做延迟载入**: 每个请求都声明全部工具. 兼容端点 (DeepSeek 等) 没有"声明了但不开放"的通道, 逐次载入只能通过修改 `tools` 实现, 而工具定义渲染在 system 之后、对话之前, 每载入一次即重读整段对话前缀; 全量声明的固定前缀实测 8302 token (system 6.9k 字符 / 指令 1.3k 字符 / 46 个工具 21.6k 字符), 恒定不变故首轮即命中缓存. 不经由 HTTP 自调用. `AgentDeps.bridge` 提供库路径边界 / watcher / 取消运行中任务.

| `id` | 覆盖 |
|------|------|
| `metadata-ops` | 修改字段、合并、user tag、刮削入队、删除 |
| `actor-ops` | 演员人物字段、别名行 (查询 / 解析 / 增删)、展示名切换、演员刮削入队 |
| `facet-identity` | rename / merge / delete / 规则列表与删除 |
| `library-ops` | 库 CRUD、REFRESH 入队 |
| `feed-ops` | 源 CRUD、立即拉取、FeedItem 历史批量操作 |
| `schedule-ops` | CLEANUP / UPSCALE / R18_IMPORT / RESCRAPE 定时 CRUD 与触发 |
| `task-ops` | 统一提交 / 取消 / 重试 (入队, 不代为运行) |

任务与定时任务的入参 (即 `submit_task` / `create_schedule` 的 `submission`) **不随工具签名内联**: 联合体体积远大于其余工具定义, 改为 `get_task_submission_schema` / `get_routine_submission_schema` 按需返回, 与提交时的校验共用同一个 `TypeAdapter`; 校验失败以 `{error, errors, types, schema}` 返回出错字段 (最多三条)、可用类型与该类型的字段定义, 模型无需再次试探形状.

指令只保留域特有约束. 全局约定 (id 一律取自 `sql_explore` / `sql_deliver`、破坏性操作批准、失败返回 `{error}`、入参先取 schema) 集中在系统提示, 不逐工具重复; capability id 不再对模型可见, 指令中不得引用.

### 返回值

工具返回值是模型上下文的一部分, 每个 token 都要付钱, 因此只回**下一步需要的观测**:

- 成功且无后续依赖 → `tools.py::TOOL_OK` (纯回执); 创建 → 新对象 id (键名用入参口径, 如 `feed_id`); 批量 → 计数 (`affected` / `missing` / `submitted` / `skipped`).
- 不回入参与工具名回显、时间戳、耗时 (`elapsed_ms`)、以及能由返回值本身算出的字段 — `total` 只在分页列表里回, 因为它是翻页的依据.
- 列表工具只回识别与筛选所需字段 (id / 名称 / 归属 / 状态 / 计数), 细节留给 `get_*` 或 `sql_explore`; `get_*` 回该行当前状态, 是详情视图.
- 失败只回 `{"error": ...}` (多字段出错另附 `errors`, 最多三条), 文案须自足且可行动: 点名出错输入, 并给出下一步 (可写字段清单、可用类型及其字段定义). 创建后立即触发的外呼失败属部分成功: 仍回新 id, 原因单列 `poll_error`, 不并入 `error`.
- 大块结果保持"列名 + 行数组"结构, 列名只回一次, 不按行重复键名.

执行前只修改实际调用与落入 `messages.json` 的 `tool_name`: `__` 最后一段恰好是当前可调用名时裁成该段; 名字已在可调用集合里或后缀对不上则原样交给框架 (未知工具仍 `ModelRetry`). 流式 SSE 徽章仍可能显示模型原始名.

`actor-ops` 的别名工具对应别名模型 (见 [data-model.md](data-model.md) 演员身份): 别名是一对多行, `resolve_actor_name` 多命中即歧义, 应交由用户决定; `set_actor_display_name` 与 `facet-identity.rename_facet(kind=actor)` 等价, 二者任一即可, 不允许重复调用. `PATCH /config` **不**暴露为工具.

`feed-ops` 只通过 `FeedService.poll_one` 触发远程拉取; 新条目是否入队 SCRAPE 由 Feed 的 `auto_enqueue` 决定, Agent 不在工具内解析 RSS 或直接运行刮削. 删除源或删除条目历史须批准. 条目 `scrape` 沿用该 Feed 当前配置, 语义见 [feeds.md](feeds.md).

`schedule-ops` 创建时只接受 `RoutineSubmission`; 更新只允许 `name` / `cron` / `enabled`, 任务类型或 payload 变化须删除后重建. `trigger_schedule` 只把 `next_run` 设为当前时间, 实际 Task 由 `CronScheduler` 下一次 tick 创建, 不是同步执行.

**批准流**: SQL 非法或 SQLite 运行时错误 → 工具返回 `error` 字符串, **不**升格为 SSE `error` 打断整轮. 慢查询 (`allow_slow`) 与破坏性写 (metadata / facet merge·delete·删规则 / 删库) 在工具体内 `raise ApprovalRequired`, 回合产出 `DeferredToolRequests`, 服务端写成 SSE `needs_approval` (`approval_id` = `tool_call_id`). **批量批准**一次提交同工具全部待批 id; **单次批准**先前端暂存, 待同工具再无 pending 时再一次回灌 (与模型并行调用对齐), 发新消息前清除暂存. 前端收集待批时只扫描**最后一条用户消息之后**的气泡, 服务端对已不在 `_pending` 的 id 跳过而非整批失败. 批准 / 拒绝以 `deferred_tool_results` 继续运行 (`user_prompt=None`): 批准 → 工具体再次进入且 `tool_call_approved=True`; 拒绝 → `ToolDenied`. 模型只看见普通 tool return, **无**「用户已批准…」类旁白; 回放跳过带 `hidden` 的历史内部 user_message.

## 会话数据

会话是**用户数据**, 落 Cold `data_dir`, **不**纳入 `log_dir`: `{data_dir}/agent/sessions/{session_id}/`

| 文件 | 角色 |
|------|------|
| `messages.json` | **权威** LLM `message_history`; 供 prompt cache 前缀一致 |
| `events.jsonl` | UI 事件流 (单调 `seq`); 回放气泡 / 工具 / usage; SSE 续订 |
| `meta.json` | 附属文件 (`turn_running`、会话 `thinking` 覆盖等) |

`agent_sessions` 表只做索引. 删会话清理目录与未 persist 的 Saved Query. 进程内 history / pending 有 TTL + LRU, 逐出后从 `messages.json` 重新装入; `ResultCache` 独立 TTL.

## 对话通道

| 通道 | 用途 |
|------|------|
| REST | 会话 CRUD、`cancel`、`trace`、Saved Query list / get / patch / delete / result |
| **SSE** | `messages/stream`、`approve/stream` 启动后台回合并订阅; `events/stream?after=` 续订 |
| `/ws` | 任务日志等广播 — **不**承载对话 |

回合在服务端运行完毕: **客户端断连不取消**; 显式 `cancel` 才 `task.cancel()`, 落盘 `cancelled` 并把已生成片段写回 `messages.json`. 事件先落盘再推订阅者; UI 从 events 重建气泡, 模型上下文只认 `messages.json`.

## Saved Query

权威是存下的 **SQL + 实体类型** (`metadata` | `actor` | `data`). Browse / 下载时 **Live 重新运行** (命中内存缓存则跳过); **无**后端 Snapshot, 要留当时行集由前端下载.

呈现规则: `metadata` / `actor` 交付双呈现 (`/meta|/actors?saved_query_id=` 筛选深链 + 数据页); `data` (含全部探查视图) 只渲染数据页, 作列表筛选会 400. 结果缓存 (`ResultCache`) 对列**无感**, 只存 `columns + rows`; `id` 列抽取仅发生在 metadata / actor 交付时作为契约校验 (缺列报错), 结果不写入缓存.

交付先绑定会话, `persisted=true` 后与会话解耦; 删会话清理未 persist 预设, **已 persist 保留**. 列表带 `saved_query_id` 时与其它筛选项 **AND**: 预设 SQL 包成 `id IN (SELECT id FROM (…))` 子查询嵌入, 不预物化 id 列表.

## 运行时

`AgentService` 挂载于 `AppRuntime`: `rebuild` 按 `hot.agent` 重建工厂并裁剪 history 热缓存, **不清除** ResultCache; bootstrap 装配 `bridge` (safe_dirs / watcher / 动态 Worker 取消 / `FeedService.poll_one`).

上游协议由 `hot.agent.api_type` 选择, 模型构造与翻译共用 `llm/model.py::build_model`; `base_url` / `api_key` / `model` 原样交给对应 Provider (Anthropic 需填 Anthropic 端点, 无隐式改写). 身份与规则经 agent `instructions` 注入而**不是** `system_prompt`: Responses 协议把 agent instructions 放在 API 顶层 `instructions` 字段, 服务端插在 `input` 之前; `system_prompt` 会变成 `input` 里的 system 消息, 于是各 capability 的注意事项反而排在身份定位之前. Responses 模型对非 OpenAI 自家端点经 `llm/model.py::_responses_profile` 关闭 `additional_tools` 追加通道: 该通道只有 OpenAI 实现, 兼容端点静默丢弃, 中途追加的工具就到不了模型. 工具面现已全量声明, 无追加产生者; 该 profile 保留以备重新引入延迟载入. 思考强度: 全局 `hot.agent.thinking` 为默认 (`None` = 不传), 会话覆盖在 `meta.json`; 每回合经由 `model_settings` 注入 thinking 与 `hot.agent.max_tokens` (默认 128000, 避免提供商默认过小导致 length 截断). 运行使用无上限的 `UsageLimits`.
