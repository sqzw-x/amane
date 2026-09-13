# 可观测性

> 入口: `src/amane/observability/` — `logging.py` (进程级三流) + `recorder.py` (单任务 Recorder).
> 任务系统见 [task-system.md](task-system.md).

## Recorder

`Recorder` 是任务可观测的顶层门面 (`recorder.py`):

- 创建 `{log_dir}/tasks/task-{id}/`, 安装带 `TaskIdFilter` 的 `task.log`
- 缓冲出站 HTTP, 按策略落盘 `http/`
- 写入配置快照 / scrape 摘要 / manifest
- **叙事 API** (`info` / `warning` / …) 内部经由 structlog, 与进程级日志同一管线

scrape 等 handler 只调用 `current()`; 其它代码仍可直接使用 `logger`, Worker `begin` 后同样写入同一 `task.log`. WebClient 经由 ContextVar 透明接入 HTTP.

Worker 只 `Recorder.begin` / `finalize`.

## 三流日志 (进程级)

`src/amane/observability/logging.py` 把 structlog 同时输出到三处:

| 流 | 文件 | 内容 |
|----|------|------|
| 应用日志 | `app.log` | 服务整体: 启动、配置变更、调度 |
| 请求日志 | `request.log` | HTTP 请求: 路径、状态码、耗时 |
| 任务日志 | `logs/tasks/task-{id}/task.log` | 单任务全生命周期 (Recorder 安装) |

另有一条 EventBus `log` event 流转到前端 Logs 页.

**请求日志打点** (`api/middleware.py`): `LoggingMiddleware` 注册为**最外层**用户中间件 (add_middleware insert(0), 见 [api.md](api.md)) — 每个请求一条 `request completed` (info); 端点和内层中间件 (TokenAuth/CORS 等) 的**未捕获异常**在 Starlette 500 兜底前记 `request failed` (error, 含 traceback) 再重抛. 手动 `HTTPException` 在 `ExceptionMiddleware` (Logging 内层) 被转成响应, 只打 `request completed` + 状态码, 不会重复打 `request failed`. 降噪路径 (`/api/ws`, `/favicon.ico`, `/api/system/desktop` — 后者是菜单栏 bar 每 3s 轮询, 见 [desktop.md](desktop.md)) 降为 debug, 默认 INFO 不落盘, DEBUG 下可恢复. handler 不自行打点请求结果, 统一由 LoggingMiddleware 记录.

**打点约定**: Worker 在 `task started` 时打印 payload 全量. scrape 等 handler 用 `current().debug/info/...`. Recorder 活跃时, WebClient 对最终失败的 `request failed` 降为 debug — 结构化失败以 `http exchange` + `http/index.jsonl` 为准.

## Per-task 隔离

`ContextVar(task_id)` + 每 handler 一个 `TaskIdFilter`. `asyncio.create_task` 自动复制上下文.

**生命周期**: 删除任务时同步删除整个 `task-{id}/`. 文件删除失败仅告警, 不阻断记录删除. `Recorder.begin` 在 mkdir 后删除目录里已有的 `summary.json`: SQLite 可复用已删除任务的 id, 新任务不得沿用同 id 目录里残留的刮削摘要.

## 任务目录布局

```text
task-{id}/
  manifest.json
  task.json
  config.hot.json          # 任务相关 Hot 切片 (已脱敏)
  .secrets.hot.json        # 仅当确有密钥时写入
  summary.json             # 任务摘要: 资格站 / 调度顺序 / 站点结果表
  raw_cache.json           # 可选
  http/index.jsonl
  http/bodies/{seq}.*
  task.log
```

导出: `GET /api/tasks/{id}/record?include_secrets=false` → `task-{id}-record.zip`. 仅终态可导出.

UI 摘要: `GET /api/tasks/{id}/report` 从 DB (`headline` / `metadata_id` / `actor_id`) + `summary.json` **投影**面向界面的 `headline` / 站点 outcomes / 详情跳转 — outcomes 与 summary.json 是**同一结构** (`SiteOutcomeRecord`) 的不同序列化, 报告不读取 `http/`、不解析任何文本. `metadata_id` 来自 SCRAPE 的 `task.result`; `actor_id` 来自 ACTOR_SCRAPE 的 `result` (缺则 payload). 非终态 400. 站点 outcomes **只对** SCRAPE / ACTOR_SCRAPE 有意义; 其它类型即使目录里有 `summary.json` 也投影为空. 出站 HTTP (含 ORGANIZE 缺资源时的 `acquire`) 只写入 `http/`, **不是**站点 outcome.

### 分层

| 问题 | 位置 |
|------|--------|
| 番号 / 终态错误 / payload | `task.json` |
| 资格站 / 调度顺序 / 站点结果 (含失败原因) | `summary.json` |
| 每次出站 HTTP (刮削 / Feed / ORGANIZE acquire) | `http/` |
| Hot 切片 | `config.hot.json` |
| 过程叙事 | `task.log` |

Feed 失败记在源的 `last_error`, 不写入站点 outcome 表.

### 站点结果单一导出

站点级结果 (成功 / 失败 / 缓存命中) 是任务事实的一手结构化数据. `Recorder.record_site_outcome` 是落盘 API; **来源 fetch 的记账只发生在** `invoke_source` (`observability/source.py`):

- 有数据 → OK; `None` → `no_usable_metadata`; `SourceError` → 异常上的 `reason` / `http_status` / `detail`; 其它 `Exception` → `unexpected` (继续其它源). 响应结构不符合读模型属 `parse_error`, 必须抛 `SourceError` 并带字段路径 (`net/errors.py::parse_detail`); 返回 `None` 会被记成 `no_usable_metadata`, 具体原因随之丢失
- 影片 `_fetch_one` 与演员 Handler 均经由它. 爬虫和插件不 import Recorder
- HTML 拦截由 `HttpClient.get_html` 抛出 `SourceError`; HTTP 失败由 `WebClient` 抛出 `RequestError` (`SourceError` 子类, 构造时 `classify_request_error`)
- 同站点多次上报按语义合并: outcome 取更差 (`cache_hit < ok < failed`); 已写入的 `reason` / `http_status` / `detail` 不被后续兜底覆盖
- `FailureReason` 在 `net/errors.py` (封闭枚举, 与前端 i18n 对应); 自定义句子只写入 `detail`

**新增字段时只修改一处**: 扩展 `SiteOutcomeRecord` 字段 → 上游上报点补参数 → summary.json / report / 前端同步获得, 无需跨层拼接字符串.

SCRAPE 的 `config.hot.json` 含 `scraping` / `network` / `llm` / `r18` / `plugins`. 插件配置按字段名中的 `api_key` / `token` / `secret` / `password` / `cookie` / `credential` / `dsn` 规则脱敏.

### 落盘时机

| 内容 | 时机 |
|------|------|
| `task.json` + `config.hot.json` (+ 条件 `.secrets`) | `begin` |
| HTTP body | 失败时; 或 `logging.debug_capture=true` |
| `summary.json` / `manifest.json` | `finalize` |
| 图片 `get_bytes` / `download` | 只记 meta |

`logging.debug_capture` 位于 `logging` 段. Worker 对所有任务类型在 `finalize` 时读取该开关, 不是刮削专属.

### 脱敏

默认脱敏 (`manifest.redacted=true`), 占位符 `***`. HTTP 索引不收录 Cookie/Authorization. 无密钥时不写 `.secrets.hot.json`.

### 回放 CLI

```bash
just repro path/to/task-N
just repro path/to/record.zip --online
uv run python -m amane.observability path/to/record.zip
```

有 `http/` 且未 `--online` → Offline (`ReplayWebClient` + `ScrapeHandler`); 否则 Online. v1 仅 `type=scrape`. 回放 CLI 接受 `record_version` 1 与 2; 回放只读取 `http/` 与 `task.json`, 不依赖站点结果结构化字段.

### 非目标 (v1)

ORGANIZE/REFRESH 全量回放、浏览器 HAR、回放写生产 DB / 移动用户媒体.

## 排障速查

| 症状 | 排查 |
|------|------|
| 无 task.log | worker 未 claim 或 Recorder 未 begin |
| 日志串任务 | `create_task` 是否传播 context |
| 前端 Logs 不更新 | WS / EventBus 启动顺序 |
| 离线回放 | 导出后 `just repro <path>` |
