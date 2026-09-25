# API 层

> 入口: `src/amane/api/routes/`. 契约在 `api/models/` (与 routes 对齐). 端点签名从源码或 `web/openapi.json` 读; 本文只写划分、约定与易回归点.
> 启动见 [architecture.md](architecture.md), 模型见 [data-model.md](data-model.md), 任务提交见 [task-system.md](task-system.md).

## 包布局

| 路径 | 职责 |
|------|------|
| `app.py` / `deps.py` / `middleware.py` / `spa.py` | HTTP 宿主与 DI |
| `routes/` | 按资源一个模块 |
| `models/` | 与 routes 同名的请求 / 响应 |
| `support/` | `http_cache` / `path_validation` / `task_resolve` / `task_batch`; `path_validation` 的 resolve / exists / is_dir 经由 `@in_thread` |

## 路由划分

全部挂载于 `/api` (`API_PREFIX`). 新端点纳入对应资源模块; 全新资源则新建模块并在 `routes/__init__.py` 注册.

| 模块 | 前缀 | 职责 |
|------|------|------|
| `health` | `/` | 就绪探测 |
| `system` | `/system` | 桌面契约 / 重启 (无监督者 403) / 版本检查 |
| `libraries` | `/libraries` | 库 CRUD; create 可携首刷; 路径模板 schema |
| `webhooks` | `/webhooks` | CloudDrive 回调; 见 [watcher.md](watcher.md) |
| `feeds` | `/feeds` | 源 CRUD + 立即拉取 + 条目历史检索 / 批量操作 / 重刮削; 见 [feeds.md](feeds.md) |
| `media` | `/media` | MediaFile |
| `metadata` | `/metadata` | 番号条目 + merge / crop / facet 筛选 / user-tag / batch / schema |
| `actors` | `/actors` | 演员浏览 (含用户标签筛选)、人物 PATCH、用户标签挂载、刮削; 身份治理经由 facets |
| `facets` | `/facets` | 分类目录与规则 |
| `comments` | `/comments` | 评论修改与删除; 新建经由 metadata |
| `tasks` | `/tasks` | 队列 + `POST /batch` + worker 暂停领队 + 终态 `report` / `record` |
| `schedules` | `/schedules` | cron CRUD + trigger |
| `config` | `/config` | HotSettings + schema |
| `plugins` | `/plugins` | 插件目录、安装 / 卸载 / 热扫描、配置 schema 与启用状态 |
| `playback` | `/playback` | 播放源列表与码流 |
| `files` | `/files` | 目录浏览; 契约见下 |
| `resources` | `/resources` | 本地资源 + `GET /proxy` |
| `agent` | `/agent`, `/saved-queries` | 见 [agent.md](agent.md) |
| `ws` | `/ws` | EventBus 广播 |

OpenAPI 列出参数, 不表达组合语义:

- metadata 同 kind 的筛选: 关联类 AND / 标量类 OR; 跨 kind 始终 AND. `saved_query_id` 与其它筛选项 AND; `data` 实体不可作列表筛选 (400). 关联文件相位筛选与 `has_files` 一样 AND (布尔项 True = 至少一份具备, False = 没有任何一份具备), 列表项带聚合 `file_phase`. 见 [data-model.md](data-model.md) / [agent.md](agent.md).
- GET `/media` 的相位 query 作用在单行列上: 布尔 False 是 `col = false`, 不是 metadata 那种 NOT EXISTS. 未知 `definition` → 422; 相位列不纳入 PATCH.
- 裁切海报基准是 `thumb_urls[0]` **当前本地文件**像素, 不修改库路径海报; locator 见 [data-model.md](data-model.md).
- 注册顺序有约束的三处: `/facets/{kind}/rules` 先于 `/{facet_id}`; `/plugins/reload` 先于 `/plugins/{plugin_id}` (否则 `reload` 被当成插件 ID); `/tasks/batch` 与 `/tasks/worker*` 先于 `/{task_id}` (否则被当成非法整数 id); `/feeds/items` 先于 `/{feed_id}`; `/playback/sources` 先于 `/{source_id}`.
- 播放端点的形状 (流的一行、`available` / `key` / `detail` 的三种组合、Range、HLS 分片与字幕路径、404 / 502 语义) 见 [plugins.md](plugins.md)「播放源」.
- 评论正文先去除首尾空白再校验长度, 全空白与超过 10000 字符均为 422. `updated_at` 晚于 `created_at` 表示正文被编辑过: PATCH 提交与库中一致的正文不写库, 也不刷新 `updated_at`, 前端据此判定「已编辑」; 排序由前端在详情响应上完成, 端点不提供 order 参数.
- `/files`: 路径解析为非严格 (虚拟 / 网络挂载盘无法规范化查询时按字面兜底), 相对 `path` 经 `base` 参数解析 (缺省 = 首个安全目录). 响应含规范 `path` (resolve 后的绝对路径), 前端文件浏览器以它为面包屑的唯一权威形态, 不做分段拼接.

## 依赖注入

端点经由 `Depends` 从 `app.state.runtime` 读取; `deps.py` 预声明 `RuntimeDep` / `RepoDep` / `ConfigDep` / `AgentDep` (`agent_service` 未接入 → 503). Starlette WS 不支持 `Depends`, `ws.py` 手动取 `ws.app.state.runtime`; 插件路由通过 `PluginManagerDep` 读取当前进程内的来源目录. 插件代码不从路由直接暴露 Repository 或 FastAPI 状态.

## 中间件顺序

`create_app` 先 `include_router` 再 `mount_spa` (SPA catch-all 会吞 `/api`), 最后注册 `LoggingMiddleware`. `add_middleware` 后注册者在栈外层 (`insert(0)`), 故 LoggingMiddleware 包住 TokenAuth / CORS / SPA fallback, 401 / 403 直返与内层中间件自身异常也进入请求日志. **新增自定义中间件时 LoggingMiddleware 必须仍为最外层.**

`TokenAuthMiddleware` 与 `LoggingMiddleware` 都是 `BaseHTTPMiddleware`: 它交给下游的是包装过的 `receive`, 必须真正挂起等待才会收到 `http.disconnect`, 因此 `Request.is_disconnected()` (立刻取消式探测) 在本栈内恒为 `False`; 需要感知客户端离开的长响应改用 `DisconnectSignal` (见 [plugins.md](plugins.md)).

## 约定

**错误**: `HTTPException(detail=中文)`. 路径校验位于 `support/path_validation.py` (存在 / 类型 / `safe_dirs` → 400 / 403 / 404; `ALLOW_ALL` 时跳过边界层). `/files` 的失败映射: 不存在 → 404, 不在 `safe_dirs` → 403, 空名单 → 500, `os.scandir` 的 `OSError` (含网络盘挂载失效) → 500 + strerror detail, `PermissionError` → 403. 错误日志统一由 LoggingMiddleware 打点 (见 [observability.md](observability.md)), handler 内不自行打印.

**列表**: `media` / `metadata` / `tasks` / `facets` / `actors` 同构 `offset` + `limit` + `sort_by` + `order`, 响应 `{items, total}`; `sort_by` 是各资源 `*SortField` 枚举, repo 用 enum→Column, 禁止反射列名. `libraries` / `schedules` / `feeds` 全量无分页. `GET /actors` 列表项不填简介 / 别名 / 用户标签 / 源字典 / `raw` (详情仍全量).

**状态码**: 创建 201、任务入队 202、无返回体 204; 空 PATCH / 非法 cron → 422; 任务状态不允许的 report / record → 409.

**资源缓存**: `/resources/{hash}` 与 `/proxy` 因就地超分 URL 不变, 不可 immutable — `Cache-Control: public, no-cache` + `content_hash` ETag. proxy 上游失败 502, 进程内负缓存 15 分钟 (不纳入配置), 同 URL singleflight; 刮削下载不经由该缓存.

**批量**: 不存在的 id 计入 `missing` / `skipped`, 存在的照常处理 (非事务 all-or-nothing). `POST /tasks/batch` 的选择集是 `task_ids` **或**与列表同形的 `status` / `type` (未传则不限): `cancel` 把排队 / 运行中标 `failed` + `error="Cancelled by user"` 而不删行, `delete` 只动终态并清除磁盘产物, `retry` 只对 `failed` 按原 type / payload / priority 再入队并返回新 `task_ids`; 筛选范围与 action 允许状态求交后为空则 `affected=0`. FeedItem 的批量是单一 `POST /feeds/{feed_id}/items/batch`, 一个请求只携带一个 action, 语义见 [feeds.md](feeds.md). 用户标签挂载同样只有批量入口 (`POST /api/metadata/batch/user-tags`, 影片与标签两个维度都是集合, `action` 决定并入或移除): 未知标签 id 属请求级错误返回 404, 不存在的影片 id 计入 `missing`, 已处于目标态的影片计入 `unchanged`. 标签新建也是单一批量入口 (`POST /api/facets/user_tag`): 按名称取回或新建, 已存在的名称直接复用而不报错, 响应与入参同序. 演员侧挂载与影片侧同形 (`POST /api/actors/batch/user-tags`), 筛选参数为 `user_tag_ids`(多值为 AND).

## WebSocket

`/ws` 只接收不发送, 协议层 PING/PONG. 前端入站收口在 `web/src/lib/connection.ts`, 各 store 的消费见 `web/src/stores/`; EventBus 须最先初始化见 [architecture.md](architecture.md).
