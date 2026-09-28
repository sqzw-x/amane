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

写操作工具按域分组为 `Capability`, **不做延迟载入**: 每个请求都声明全部工具. 兼容端点 (DeepSeek 等) 没有「声明了但不开放」的通道, 逐次载入只能通过修改 `tools` 实现, 而工具定义渲染在 system 之后、对话之前, 每载入一次即重读整段对话前缀; 全量声明的固定前缀恒定不变, 首轮即命中缓存. 不经由 HTTP 自调用. `AgentDeps.bridge` 提供库路径边界 / watcher / 取消运行中任务.

| `id` | 覆盖 |
|------|------|
| `metadata-ops` | 修改字段、合并、user tag、刮削入队、删除 |
| `actor-ops` | 演员人物字段、别名行 (查询 / 解析 / 增删)、展示名切换、演员刮削入队 |
| `facet-identity` | rename / merge / delete / 规则列表与删除 |
| `library-ops` | 库 CRUD、REFRESH 入队 |
| `feed-ops` | 源 CRUD、立即拉取、FeedItem 历史批量操作 |
| `schedule-ops` | CLEANUP / UPSCALE / R18_IMPORT / RESCRAPE 定时 CRUD 与触发 |
| `task-ops` | 统一提交 / 取消 / 重试 (入队, 不代为运行) |

任务与定时任务的入参 (即 `submit_task` / `create_schedule` 的 `submission`) **不随工具签名内联**: 联合体体积远大于其余工具定义, 改为 `get_task_submission_schema` / `get_routine_submission_schema` 按需返回, 与提交时的校验共用同一个 `TypeAdapter`; 校验失败返回出错字段 (最多三条)、可用类型与该类型的字段定义, 模型无需再次试探形状.

指令只保留域特有约束. 全局约定 (id 一律取自 `sql_explore` / `sql_deliver`、破坏性操作批准、失败返回 `{error}`、入参先取 schema) 集中在系统提示, 不逐工具重复; capability id 不再对模型可见, 指令中不得引用.

### 返回值

工具返回值是模型上下文的一部分, 每个 token 都要付钱, 因此只回**下一步需要的观测**:

- 成功且无后续依赖 → `tools.py::TOOL_OK` (纯回执); 创建 → 新对象 id (键名用入参口径, 如 `feed_id`); 批量 → 计数 (`affected` / `missing` / `submitted` / `skipped`).
- 不回入参与工具名回显、时间戳、耗时 (`elapsed_ms`)、以及能由返回值本身算出的字段 — `total` 只在分页列表里回, 因为它是翻页的依据.
- 列表工具只回识别与筛选所需字段 (id / 名称 / 归属 / 状态 / 计数), 细节留给 `get_*` 或 `sql_explore`; `get_*` 回该行当前状态, 是详情视图.
- 失败只回 `{"error": ...}` (多字段出错另附 `errors`, 最多三条), 文案须自足且可行动: 点名出错输入, 并给出下一步. 创建后立即触发的外呼失败属部分成功: 仍回新 id, 原因单列 `poll_error`, 不并入 `error`.
- 大块结果保持「列名 + 行数组」结构, 列名只回一次, 不按行重复键名.

执行前只修改实际调用与落入 `messages.json` 的 `tool_name`: `__` 最后一段恰好是当前可调用名时裁成该段; 名字已在可调用集合里或后缀对不上则原样交给框架 (未知工具仍 `ModelRetry`). 工具卡片仍可能显示模型原始名.

`actor-ops` 的别名工具对应别名模型 (见 [data-model.md](data-model.md) 演员身份): 别名是一对多行, `resolve_actor_name` 多命中即歧义, 应交由用户决定; `set_actor_display_name` 与 `facet-identity.rename_facet(kind=actor)` 等价, 二者任一即可, 不允许重复调用. `PATCH /config` **不**暴露为工具.

`feed-ops` 只通过 `FeedService.poll_one` 触发远程拉取; 新条目是否入队 SCRAPE 由 Feed 的 `auto_enqueue` 决定, Agent 不在工具内解析 RSS 或直接运行刮削. 删除源或删除条目历史须批准. 条目 `scrape` 沿用该 Feed 当前配置, 语义见 [feeds.md](feeds.md).

`schedule-ops` 创建时只接受 `RoutineSubmission`; 更新只允许 `name` / `cron` / `enabled`, 任务类型或 payload 变化须删除后重建. `trigger_schedule` 只把 `next_run` 设为当前时间, 实际 Task 由 `CronScheduler` 下一次 tick 创建, 不是同步执行.

**批准流**: SQL 非法或 SQLite 运行时错误 → 工具返回 `error` 字符串, **不**升格为回合错误打断整轮. 慢查询 (`allow_slow`) 与破坏性写 (metadata / facet merge·delete·删规则 / 删库) 在工具体内 `raise ApprovalRequired`; 适配器把 `DeferredToolRequests` 映射成 AG-UI 中断 (`RUN_FINISHED.outcome.interrupts`), 中断 `id` 为 `int-{tool_call_id}`, 文案取自工具给的 metadata. 一次 `resume[]` 必须回答**全部**打开的中断, 故前端逐项暂存决定、集齐后整批提交, 批量批准即全部置 approve. 批准 → 工具体再次进入且 `tool_call_approved=True`; 拒绝 → `ToolDenied`. 模型只看见普通 tool return, **无**「用户已批准…」类旁白; 刷新后用回放行末尾的中断还原待批状态。

## 会话数据

会话是**用户数据**, 落 Cold `data_dir`, **不**纳入 `log_dir`: `{data_dir}/agent/sessions/{session_id}/`

| 文件 | 角色 |
|------|------|
| `messages.json` | **权威** LLM `message_history`; 供 prompt cache 前缀一致 |
| `events.jsonl` | UI 回放行 (单调 `seq`): AG-UI 事件 + 气泡 / 工具 / usage; 供页面重建 |
| `meta.json` | 附属文件 (`turn_running`、会话 `thinking` 覆盖等) |

`agent_sessions` 表只做索引. 删会话清理目录与未 persist 的 Saved Query. 标题由 `POST /agent/sessions/{id}/title` 按首条用户输入生成, 与回合并行执行 (前端在发出首条消息时另行请求, 不等回合结束); 未配置模型、请求失败或超时回退首条输入截断, 标题不进模型上下文. 进行中的回合任务登记在 `AgentService`, 取消与删会话据此终止; `messages.json` 是唯一的模型上下文来源, `ResultCache` 独立 TTL.

## 对话通道

| 通道 | 用途 |
|------|------|
| REST | 会话 CRUD、标题生成、`trace`、Saved Query list / get / patch / delete / result |
| **AG-UI** | `POST /agent/sessions/{id}/agui` 启动后台回合并订阅; `GET .../agui/events` 只跟随回放行; `POST .../agui/cancel` 终止 |
| `/ws` | 任务日志等广播 — **不**承载对话 |

回合在服务端跑完: **客户端断连不取消**, 只有显式 `cancel` 才终止任务并落 `cancelled` 行. 事件先落盘再分发给订阅者; 每条 AG-UI 事件同时摊成回放行, 页面据此重建气泡 (协议本身没有历史回放), 模型上下文只认 `messages.json`. 页面重挂载 (刷新 / 切页) 后本地流已断, 由 `GET .../agui/events` 续上: 它只跟随回放行 (**不**启动回合, 故进行中的回合也不 409), 先整段回放再跟随新行, 发的是回放行本身而非 POST 通道的 AG-UI 投影, 流关闭即回合已结束.

`RUN_FINISHED.usage` 由端点补 — 协议有这个字段而官方适配器不填; 客户端解析器会丢弃该字段, 页面从回放行取同一份数据. 逐请求用量另成 `request_usage` 行, 在回合末尾成批补, 用 `after_tool_call` 标出该次请求的落点 (页面据此把用量印在这次请求产出的内容之后), 耗时由 `new_messages()` 响应与其请求的时间戳算出. 客户端重放整段会话时, 服务端按**用户输入**切分并裁掉已入库的部分 — 工具回合在两侧的消息分组不同, 逐条比对全部消息必然错位.

## Saved Query

权威是存下的 **SQL + 实体类型** (`metadata` | `actor` | `data`). Browse / 下载时 **Live 重新运行** (命中内存缓存则跳过); **无**后端 Snapshot, 要留当时行集由前端下载.

呈现规则: `metadata` / `actor` 交付双呈现 (`/meta|/actors?saved_query_id=` 筛选深链 + 数据页); `data` (含全部探查视图) 只渲染数据页, 作列表筛选会 400. 结果缓存 (`ResultCache`) 对列**无感**, 只存 `columns + rows`; `id` 列抽取仅发生在 metadata / actor 交付时作为契约校验 (缺列报错), 结果不写入缓存.

交付先绑定会话, `persisted=true` 后与会话解耦; 删会话清理未 persist 预设, **已 persist 保留**. 列表带 `saved_query_id` 时与其它筛选项 **AND**: 预设 SQL 包成 `id IN (SELECT id FROM (…))` 子查询嵌入, 不预物化 id 列表.

## 运行时

`AgentService` 挂载于 `AppRuntime`: `rebuild` 按 `hot.agent` 重建工厂, **不清除** ResultCache; bootstrap 装配 `bridge` (safe_dirs / watcher / 动态 Worker 取消 / `FeedService.poll_one`).

上游协议由 `hot.agent.api_type` 选择, 模型构造与翻译共用 `llm/model.py::build_model`; `base_url` / `api_key` / `model` 原样交给对应 Provider (Anthropic 需填 Anthropic 端点, 无隐式改写). 身份与规则经 agent `instructions` 注入而**不是** `system_prompt`: Responses 协议把 agent instructions 放在 API 顶层 `instructions` 字段, 服务端插在 `input` 之前; `system_prompt` 会变成 `input` 里的 system 消息, 于是各 capability 的注意事项反而排在身份定位之前. 思考强度: 全局 `hot.agent.thinking` 为默认 (`None` = 不传), 会话覆盖在 `meta.json`; 每回合经由 `model_settings` 注入 thinking 与 `hot.agent.max_tokens`. 运行使用无上限的 `UsageLimits`.
