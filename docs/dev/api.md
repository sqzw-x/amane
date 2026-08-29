# API 层

> 提交: `ecbc041`
>
> 入口: `src/amane/api/routes/`. 契约在 `api/models/` (与 routes 对齐). 端点签名从源码或 `web/openapi.json` 读 (`just generate`); 本文只写划分、约定与易错点.
> 启动见 [architecture.md](architecture.md), 模型见 [data-model.md](data-model.md), 任务提交见 [task-system.md](task-system.md).

## 包布局

| 路径 | 职责 |
|------|------|
| `app.py` / `deps.py` / `middleware.py` / `spa.py` | HTTP 宿主与 DI |
| `routes/` | 按资源一个模块 |
| `models/` | 与 routes 同名的请求/响应 |
| `support/` | `http_cache` / `path_validation` / `task_resolve` / `task_batch` |

## 路由划分

全部挂 `/api` (`API_PREFIX`). 新端点进对应资源模块; 全新资源则新建模块并在 `routes/__init__.py` 注册.

| 模块 | 前缀 | 职责 |
|------|------|------|
| `health` | `/` | 就绪探测 |
| `system` | `/system` | 桌面契约 / 重启 (无监督者 403) / 版本检查 |
| `libraries` | `/libraries` | 库 CRUD; create 可携首刷; 路径模板 schema |
| `feeds` | `/feeds` | RSS/Atom 源 CRUD + 立即拉取 + 跨源/单源条目历史检索/批量操作/重刮削; 见 [feeds.md](feeds.md) |
| `media` | `/media` | MediaFile |
| `metadata` | `/metadata` | 番号条目 + merge / crop / facet 筛选 / user-tag / batch / schema |
| `actors` | `/actors` | 演员浏览、人物 PATCH、刮削; 身份治理走 facets |
| `facets` | `/facets` | 分类目录与规则; `POST /user_tag` 创建用户标签 |
| `comments` | `/comments` | 评论改删; 新建挂 metadata; 列表随详情 |
| `tasks` | `/tasks` | 队列; `POST /batch` (`cancel`/`delete`/`retry`); worker 暂停领队; 终态 `report` / `record` |
| `schedules` | `/schedules` | cron CRUD + trigger |
| `config` | `/config` | HotSettings + schema |
| `plugins` | `/plugins` | 外部影片来源插件目录、安装/卸载/热扫描、配置 schema 与启用状态 |
| `files` | `/files` | 目录浏览 (`?path=`) |
| `resources` | `/resources` | 本地资源 + `GET /proxy` |
| `agent` | `/agent`, `/saved-queries` | 见 [agent.md](agent.md) |
| `ws` | `/ws` | EventBus 广播 |

OpenAPI 列得出参数, 列不出组合语义:

- metadata 同 kind: 关联类 AND / 标量类 OR; 跨 kind 始终 AND. `saved_query_id` 与其它筛选项 AND; `data` 实体不可作列表筛选 (400). 关联文件相位筛选 (`has_subtitle` / `uncensored` / `mosaic` / `definition` / `content_type`) 与 `has_files` 一样 AND; 布尔项 True=至少一份具备, False=没有任何一份具备; `uncensored` 是 mosaic 标记或片种无码. 列表项带聚合 `file_phase`. 见 [data-model.md](data-model.md) / [agent.md](agent.md).
- GET `/media` 同一组相位 query 作用在单行列上: 布尔 False 是 `col = false`, 不是 metadata 那种 NOT EXISTS. 未知 `definition` → 422. 相位列不进 PATCH.
- 裁切海报基准是 `thumb_urls[0]` **当前本地文件**像素; 不改库路径海报 (ORGANIZE 再复制). locator 见 [data-model.md](data-model.md).
- `/facets/{kind}/rules` 须注册在 `/{facet_id}` 之前. 写规则语义见 [data-model.md](data-model.md).
- `/plugins/reload` 须注册在 `/plugins/{plugin_id}` 之前, 否则 `reload` 会被当成插件 ID. 安装/卸载契约见 [plugins.md](plugins.md).
- `/tasks/batch` 与 `/tasks/worker*` 须注册在 `/{task_id}` 之前, 否则会被当成非法整数 id.

## 依赖注入

端点经 `Depends` 从 `app.state.runtime` 取. `deps.py` 预声明 `RuntimeDep` / `RepoDep` / `ConfigDep` / `AgentDep` (`agent_service` 未挂载 → 503).

Starlette WS 不支持 `Depends`, `ws.py` 手动取 `ws.app.state.runtime`. 插件路由通过 `PluginManagerDep` 读取当前进程内的来源目录；插件代码不从路由直接暴露 Repository 或 FastAPI 状态.

## 挂载顺序

`create_app`: 先 `include_router` 再 `mount_spa` (SPA catch-all 会吞 `/api`), 最后注册 `LoggingMiddleware`. `add_middleware` 后注册者在栈外层 (insert(0)), 故 LoggingMiddleware 包住 TokenAuth / CORS / SPA fallback — 401/403 直返与内层中间件自身异常也进请求日志; 新端点无需关心中间件顺序, 但新增自定义中间件时必须保证 LoggingMiddleware 仍在最外层.

## 约定

**错误**: `HTTPException(detail=中文)`. 路径校验收口 `support/path_validation.py` (存在 / 类型 / `safe_dirs` → 400/403/404; `safe_dirs is None` 即 `ALLOW_ALL` 时跳过边界层). `/files`: 路径解析为非严格 (`utils/path.py` 对虚拟/网络挂载盘无法规范化查询时按字面兜底), 相对 `path` 经 `base` 参数解析 (缺省 = 首个安全目录; `ALLOW_ALL` 时 POSIX `/`、Windows `C:\`), 不存在 → 404, 不在 `safe_dirs` → 403; 空名单 (已配置但无可用根) → 500; `os.scandir` 的 `OSError` (含网络盘挂载失效, macOS errno 6) → 500 + strerror detail; `PermissionError` → 403. 响应含规范 `path` (resolve 后的绝对路径, as_posix 且清 `\\?\` 前缀), 前端文件浏览器以它为面包屑的唯一权威形态, 不做分段拼接. 错误日志统一由 LoggingMiddleware 打点 (见 [observability.md](observability.md)), handler 内不自行 logger 打印.

**列表**: `media` / `metadata` / `tasks` / `facets` / `actors` 同构 `offset`+`limit`+`sort_by`+`order`, 响应 `{items, total}`. `sort_by` 是各资源 `*SortField`, repo 用 enum→Column, 禁止反射列名. `libraries` / `schedules` / `feeds` 全量无分页; `GET /feeds/items` 与 `GET /feeds/{id}/items` 分页. `GET /feeds/items` 必须注册在 `/{feed_id}` 之前, 否则 `items` 会被当成非法整数 id. `GET /actors` 列表项不填简介/别名/源字典/`raw` (详情 `GET /actors/{id}` 仍全量).

**状态码**: 创建 201、任务入队 202、无返回体 204. 空 PATCH / 非法 cron → 422; 任务状态不允许的 report/record → 409. `POST /tasks/batch` 对不匹配该 action 的行计入 `skipped` (不 409).

**资源缓存**: `/resources/{hash}` 与 `/proxy` 因就地超分 URL 不变, 不可 immutable — `Cache-Control: public, no-cache` + `content_hash` ETag. proxy 上游失败 502, 进程内负缓存 15 分钟 (不进配置), 同 URL singleflight; 刮削下载不走此缓存.

**批量**: 不存在的 id 计入 `missing`/`skipped`, 存在的照常处理 (非事务 all-or-nothing).

`POST /tasks/batch` 选择集互斥: `task_ids` **或** 与列表同形的 `status`/`type` (未传则不限). `cancel` 把排队/运行中标 `failed` + `error="Cancelled by user"`, 不删行; `delete` 只动终态并清磁盘产物; `retry` 只对 `failed` 按原 type/payload/priority 再入队 (返回新 `task_ids`). 筛选范围与 action 允许状态求交后为空 → `affected=0`. Worker 暂停是进程内标志, 不写 HotSettings; 只停 `claim_next_task`, 已认领的继续跑. 见 [task-system.md](task-system.md).

FeedItem 的批量命令是单一 `POST /feeds/{feed_id}/items/batch`: 一个请求只携带一个 action (`ignore` / `unignore` / `delete` / `scrape`), 跨 Feed ID 计入 `missing`. `scrape` 从 Feed 表读取当前刮削配置并批量创建 SCRAPE Task; 无番号计入 `skipped`, 重复番号只创建一个任务. 详细状态语义见 [feeds.md](feeds.md). 源本身没有 batch 端点; 管理页循环 `POST /feeds/{id}/poll` / `PATCH` / `DELETE`.

## WebSocket

`/ws` 只收不发, 协议层 PING/PONG. 事件分发见 [frontend.md](frontend.md); EventBus 须最先初始化见 [architecture.md](architecture.md).
