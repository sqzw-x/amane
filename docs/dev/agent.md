# 助理 Agent

> 提交: `e711e93`
>
> 入口: `src/amane/agent/`. 本文只解释边界与契约; 字段与签名去源码.
> 配置见 [config.md](config.md), 列表接入见 [api.md](api.md), 翻译 LLM 见 [llm.md](llm.md), 前端 IA 见 [frontend.md](frontend.md).

## 定位

**助理 Agent** 是内部对会话式读写助手的称呼; 产品面只称 **Amane** (首页对话), 不向用户暴露 Agent / SQL 等实现词.

能力: 只读 SQL 探查库; 交付结果落为可引用的 **Saved Query**, 再被 Browse / 下载消费; 写变更只走封装领域工具 (Repository / Handler 语义), **禁止**裸 DML/DDL. 与 `llm/` 翻译管线并行, **不共用** Hot section. 产品入口 `/` 的交互壳见 [frontend.md](frontend.md).

## 工具面

读面始终在场 (`FunctionToolset`):

| 工具 | 契约 |
|------|------|
| `sql_explore` | 中间推理; 默认只回样例. `create_view=true` 物化会话内 SavedQuery 供 `inspect_result` 翻页 — 视图是**纯行数组**, 无 entity / `id` 列要求, **不**进交付芯片 |
| `sql_deliver` | 给人看的结果 → `saved_query` + 内存结果缓存. `entity=metadata\|actor` 交付须含主键列 `id`, 可作片库/演员筛选并深链; 省略 entity (或 `data`) 交付任意只读结果, 只走数据页 |
| `inspect_result` | 按 `saved_query_id` 窥行 (交付或探查视图) |

写面按域 `Capability(defer_loading=True)`, 模型先 `load_capability` 再调工具; 不经 HTTP 自调用. `AgentDeps.bridge` 提供库路径边界 / watcher / 取消运行中任务.

执行前只改实际调用与落入 `messages.json` 的 `tool_name`: 当前不可调用、且 `__` 最后一段恰好是当前可调用名时, 裁成该段 (`rename_facet__rename_facet` → `rename_facet`). 名字已在可调用集合里、或后缀对不上, 一律原样交给框架 (未知工具仍 `ModelRetry`). 流式 SSE 徽章仍可能显示模型原始名.

| `id` | 覆盖 |
|------|------|
| `metadata-ops` | 改字段、合并、user tag、刮削入队、删除 |
| `actor-ops` | 演员人物字段、别名行 (查询/解析/增删)、展示名切换、演员刮削入队 |
| `facet-identity` | rename / merge / delete / 规则列表与删除 |
| `library-ops` | 库 CRUD、REFRESH 入队 |
| `feed-ops` | RSS/Atom 源 CRUD、立即拉取、FeedItem 历史批量操作 |
| `schedule-ops` | CLEANUP / UPSCALE / R18_IMPORT / RESCRAPE 定时 CRUD 与触发 |
| `task-ops` | 统一提交 / 取消 / 重试 (入队, 不代跑) |

`actor-ops` 的别名工具对应新别名模型 (见 [data-model.md](data-model.md) 演员身份): 别名是一对多行 (同别名可属多个演员, `resolve_actor_name` 多命中即歧义, 应交由用户决定); `set_actor_display_name` = 展示名切换 (旧名入别名行, 存量影片真值改写), 与 `facet-identity.rename_facet(kind=actor)` 等价 — 二者任一即可, 不要重复调用.

`PATCH /config` **不**暴露为工具.

`feed-ops` 只通过 `FeedService.poll_one` 触发远程拉取; 拉取发现的新条目是否入队 `SCRAPE` 由 Feed 的 `auto_enqueue` 决定, Agent 不在工具内解析 RSS 或直接运行刮削. FeedItem 的 `ignore` / `unignore` / `scrape` 为批量操作; 删除源或删除条目历史须批准. 条目 `scrape` 沿用该 Feed 当前 `content_type` / `use_cache`, 同批番号大小写不敏感去重, 无番号计入 `skipped`.

`schedule-ops` 创建时只接受 `RoutineSubmission` (`cleanup` / `upscale` / `r18_import` / `rescrape`); 更新只允许 `name` / `cron` / `enabled`, 任务类型或 payload 变化须删除后重建. `trigger_schedule` 只把 `next_run` 设为当前时间, 实际 Task 由 `CronScheduler` 下一次 tick 创建, 不是同步执行. 删除 Schedule 须批准.

SQL 非法或 SQLite 运行时错误 → 工具返回 `error` 字符串, **不**升格为 SSE `error` 打断整轮. 慢查询 (`allow_slow`) 与破坏性写 (metadata / facet merge·delete·删规则 / 删库) 在工具体内 `raise ApprovalRequired`; 回合产出 `DeferredToolRequests`, 服务端写成 SSE `needs_approval` (`approval_id` = `tool_call_id`, `sql` 字段承载确认文案). 控件挂在对应工具徽章旁 (批准 / 拒绝 / 批量批准). **批量批准**一次提交同工具全部待批 id. **单次批准**先前端暂存, 待同工具再无 pending 时再一次 `approve/stream` (`approval_ids`) 回灌 (与模型并行调用对齐); 发新消息前会冲掉暂存. 拒绝立即请求, 若同工具因此清空 pending 则顺带冲暂存. 前端收集待批时只扫**最后一条用户消息之后**的气泡 (避免历史回合残留 pending id); 服务端对已不在 `_pending` 的 id 跳过而非整批失败.

批准 / 拒绝以 `deferred_tool_results` 续跑 (`user_prompt=None`): 批准 → 工具体再次进入且 `tool_call_approved=True` 后正常 `return`; 拒绝 → `ToolDenied`. 模型只看见普通 tool return, **无**「用户已批准…」类 follow-up 旁白. 回放仍跳过旧 events 里带 `hidden` 或历史文案的内部 user_message.

## 会话数据

会话是**用户数据**, 落 Cold `data_dir`, **不**进 `log_dir`:

`{data_dir}/agent/sessions/{session_id}/`

| 文件 | 角色 |
|------|------|
| `messages.json` | **权威** LLM `message_history`; 供 prompt cache 前缀一致 |
| `events.jsonl` | UI 事件流 (单调 `seq`); 回放气泡 / 工具 / usage; SSE 续订 |
| `meta.json` | 侧车 (`turn_running`、会话 `thinking` 覆盖等) |

`agent_sessions` 表只做索引. 删会话清理目录与未 persist 的 Saved Query.

进程内 history / pending 有 TTL+LRU; 逐出后从 `messages.json` 装回. `ResultCache` 独立 TTL.

## 对话通道

| 通道 | 用途 |
|------|------|
| REST | 会话 CRUD、`cancel`、`trace`、Saved Query list/get/patch/delete/result |
| **SSE** | `messages/stream`、`approve/stream` 启动后台回合并订阅; `events/stream?after=` 续订 |
| `/ws` | 任务日志等广播 — **不**承载对话 |

回合在服务端跑完: **客户端断连不取消**. 显式 `cancel` 才 `task.cancel()`, 落盘 `cancelled` 并把已生成片段写回 `messages.json`. 事件先落盘再推订阅者. UI 从 events 重建气泡; 模型上下文只认 `messages.json`.

## Saved Query

权威是存下的 **SQL + 实体类型** (`metadata` | `actor` | `data`). Browse / 下载时 **Live 重跑** (命中内存缓存则跳过). **无**后端 Snapshot; 要留当时行集 → 前端下载.

呈现规则: `metadata` / `actor` 交付双呈现 — `/meta|/actors?saved_query_id=` 筛选深链 + 数据页; `data` (含全部探查视图) 只渲染数据页 `/saved-queries/{id}`, 作列表筛选会 400. 数据页渲染 `GET /saved-queries/{id}/result` 的全列行数组 (服务端分页). 结果缓存 (`ResultCache`) 对列**无感**, 只存 `columns + rows`; `id` 列抽取仅发生在 `sql_deliver` 的 metadata/actor 交付时, 作为契约校验 (缺列报错, 保证列表子查询嵌入可用), 结果不入缓存.

交付先挂会话; `persisted=true` 后与会话解耦. 删会话清理未 persist 预设; **已 persist 保留**.

列表带 `saved_query_id` 时与其它筛选项 **AND**: 预设 SQL 包成 `id IN (SELECT id FROM (…))` 子查询嵌入 (不预物化 id 列表).

## 运行时

`AgentService` 挂在 `AppRuntime`: `rebuild` 按 `hot.agent` 重建工厂并裁剪 history 热缓存, **不清** ResultCache. bootstrap 装配 `bridge` (safe_dirs / watcher / 动态 Worker 取消 / FeedService.poll_one).

上游协议由 `hot.agent.api_type` 选择 (`runtime.build_model`): `chat` → Chat Completions; `response` → Responses; `anthropic` → Anthropic Messages. `base_url` / `api_key` / `model` 原样交给对应 Provider (Anthropic 需填 Anthropic 端点, 无隐式改写).

思考强度: 全局 `hot.agent.thinking` 为默认 (`None` = 不传); 会话覆盖在 `meta.json`. 每回合经 `model_settings` 注入 (含高 `max_tokens`); 运行使用无上限的 `UsageLimits`.
