# API 层

> 入口: `src/amane/api/routes/`. 契约在 `api/models/` (与 routes 对齐). 端点签名从源码或 `web/openapi.json` 读; 本文只写划分、约定与易回归点.
> 启动见 [architecture.md](architecture.md), 模型见 [data-model.md](data-model.md), 任务提交见 [task-system.md](task-system.md).

## 包布局

HTTP 宿主与 DI 在 `app.py` / `deps.py` / `middleware.py` / `spa.py`; `routes/` 按资源一个模块, `models/` 与 routes 同名, 辅助单元在 `support/` (`path_validation` 的 resolve / exists / is_dir 经由 `@in_thread`).

## 路由划分

全部挂载于 `/api` (`API_PREFIX`); `bootstrap` 自身不设前缀, 路由为 `/agent-guide`, 随全局前缀暴露. 新端点纳入对应资源模块; 全新资源则新建模块并在 `routes/__init__.py` 注册.

| 模块 | 前缀 | 职责 |
|------|------|------|
| `health` | `/` | 就绪探测 |
| `system` | `/system` | 桌面契约 / 重启 (无监督者 403) / 版本检查 |
| `libraries` | `/libraries` | 库 CRUD; create 可携首刷; 路径模板 schema |
| `webhooks` | `/webhooks` | CloudDrive 回调; 见 [watcher.md](watcher.md) |
| `feeds` | `/feeds` | 源 CRUD + 立即拉取 + 条目历史检索 / 批量操作 / 重刮削; 见 [feeds.md](feeds.md) |
| `media` | `/media` | MediaFile + 记录批量删除 |
| `metadata` | `/metadata` | 番号条目 + merge / crop / facet 筛选 / user-tag / batch / schema |
| `actors` | `/actors` | 演员浏览 (含用户标签筛选)、人物 PATCH / 锁、批量性别与批量清空、用户标签挂载; 单条刮削与单条清空已弃用; 身份治理经由 facets |
| `facets` | `/facets` | 分类目录与规则 |
| `comments` | `/comments` | 评论修改与删除; 新建经由 metadata |
| `tasks` | `/tasks` | 队列 + `POST /batch`(操作既有) + `POST /batch/submit`(批量入队) + worker 暂停领队 + 终态 `record` |
| `schedules` | `/schedules` | cron CRUD + trigger |
| `config` | `/config` | HotSettings + schema |
| `plugins` | `/plugins` | 插件目录、安装 / 卸载 / 热扫描、配置 schema 与启用状态 |
| `network` | `/network` | 刮削来源连通性探测 |
| `playback` | `/playback` | 播放源列表与码流 |
| `files` | `/files` | 目录浏览; 契约见下 |
| `resources` | `/resources` | 本地资源 + `GET /proxy` |
| `agent` | `/agent` | 会话 CRUD / 标题 / trace / AG-UI; 见 [agent.md](agent.md) |
| `saved-queries` | `/saved-queries` | 查询预设 CRUD / 结果 / 批量; 见 [agent.md](agent.md) |
| `tools` | `/tools` | 外部智能体的工具索引、单工具入参与派发; 见 [agent.md](agent.md)「外部接入」 |
| `bootstrap` | `/agent-guide` | 免鉴权的接入引导, 固定文本; 豁免见 `middleware.py::_TOKEN_EXEMPT` |
| `ws` | `/ws` | EventBus 广播 |

OpenAPI 列出参数, 不表达组合语义:

- metadata 同 kind 的筛选: 关联类 AND / 标量类 OR; 跨 kind 始终 AND. `saved_query_id` 与其它筛选项 AND; `data` 实体不可作列表筛选 (400). 关联文件相位筛选与 `has_files` 一样 AND (布尔项 True = 至少一份具备, False = 没有任何一份具备), 列表项带聚合 `file_phase`. 见 [data-model.md](data-model.md) / [agent.md](agent.md).
- GET `/media` 的相位 query 作用在单行列上; `uncensored` 与 `content_type` 是例外: 两者都按所属条目判定 (`uncensored` = 条目无码或这一份带无码标记, `content_type` = 条目类型), 未绑定条目的文件只看自身标记、不命中任何类型; 布尔侧按空值安全比较. 未知 `definition` → 422; 相位列不纳入 PATCH.
- 手动裁切基准是源图**当前本地文件**像素 (含就地超分后尺寸): 海报以 `thumb_urls[0]` 为源并替换 `poster_urls`, 演员头像以 `image_urls[0]` 为源并前插保留原图; 库路径海报不受影响. locator 见 [data-model.md](data-model.md).
- `PUT /metadata/{id}/locks` 与 `PUT /actors/{id}/locks` 整体替换锁定字段集合; 锁语义见 [data-model.md](data-model.md)「字段锁定」.
- `GET /facets/{kind}` 的 `favorite` 过滤与 `POST /facets/{kind}/batch` 的收藏写入只在可收藏的分类上成立 (`db/models.py::FAVORITE_FACET_KINDS`; 链接型分类中的 `director` 与 `tag` 同样成立): 传入 `favorite` (与取值无关) 或对不支持的 kind 写收藏都是 400, 其余 kind 恒为 false. 写端点按 `action` 分派, 空 `facet_ids` 与未登记的 `action` 是 422, 取去重后的 `facet_ids`, 不存在的 id 计入 `missing` 而不报 404, 三个计数之和等于去重后的条目数. 见 [data-model.md](data-model.md).
- 注册顺序有约束的五处: `/facets/{kind}/rules` 先于 `/{facet_id}` (否则 `rules` 被当成 facet_id); `/plugins/reload` 先于 `/plugins/{plugin_id}` (否则 `reload` 被当成插件 ID); `/tasks/batch` 与 `/tasks/worker*` 先于 `/{task_id}` (否则被当成非法整数 id); `/feeds/items` 先于 `/{feed_id}`; `/playback/sources` 先于 `/{source_id}`.
- 播放端点的形状 (流的一行、`available` / `key` / `detail` 的三种组合、Range、HLS 分片与字幕路径、404 / 502 语义) 见 [plugins.md](plugins.md)「播放源」.
- `/network/check` 的探测范围 = 当前热配置真正会请求的来源 (各类型路由的并集 + 演员资料来源 / 头像来源), 省略 `source_ids` 即全量. `source_id` 是 `str` 而非 `SiteName` 枚举 (第三方来源是 `namespace.local`, 且来源集合随插件增删变化), 未知值无法用 schema 拒绝, 不存在的 ID 计入条目返回 `SKIPPED`. 逐来源的失败只进条目, 端点始终 200 — 探测结果本身就是响应体; `reason` 只在失败时给出, `skip_reason` 只在未探测时给出, `detail` 是语言中立的补充说明 (界面原样渲染, 不翻译). 探测不进入任务队列, 也不写入站点 outcome (那是刮削任务的记录).
- 评论正文先去除首尾空白再校验长度, 全空白与超过 10000 字符均为 422. `updated_at` 晚于 `created_at` 表示正文被编辑过: PATCH 提交与库中一致的正文不写库, 也不刷新 `updated_at`, 前端据此判定「已编辑」; 排序由前端在详情响应上完成, 端点不提供 order 参数; 正文按 Markdown 存储与返回, 服务端不解析.
- `/files`: 路径解析为非严格 (虚拟 / 网络挂载盘无法规范化查询时按字面兜底), 相对 `path` 经 `base` 参数解析 (缺省 = 首个安全目录). 响应含规范 `path` (resolve 后的绝对路径), 前端文件浏览器以它为面包屑的唯一权威形态, 不做分段拼接.

## 依赖注入

端点经 `Depends` 读取 `app.state.runtime`; `agent_service` 未接入时 `AgentDep` 返回 503. Starlette WS 不支持 `Depends`, `ws.py` 手动读取同一 runtime; 插件路由经 `PluginManagerDep` 读取当前进程内的来源目录. 插件代码不从路由直接暴露 Repository 或 FastAPI 状态.

## 中间件顺序

`create_app` 先 `include_router` 再 `mount_spa` (SPA catch-all 会截获 `/api`), 最后注册 `LoggingMiddleware`. `add_middleware` 后注册者在栈外层 (`insert(0)`), 故 LoggingMiddleware 包住 TokenAuth / CORS / SPA fallback, 401 / 403 直返与内层中间件自身异常也进入请求日志. **新增自定义中间件时 LoggingMiddleware 必须仍为最外层.**

**免鉴权只有两条**, 都在 `middleware.py::_TOKEN_EXEMPT` 里显式登记: `/api/health` 与接入引导 `/api/agent-guide`. 其余 `/api/*` 一律要 token; 非 `/api` 路径 (SPA 静态资源) 不校验. `/api/resources/*` 另接受图片 token (`?token=` 或 Bearer), 该凭证只能读取资源, 见 [config.md](config.md). 接口文档挂在 `/api/docs`, schema 在 `/api/docs/openapi.json`, 同样受校验 —— 它们完整暴露接口结构, 不对匿名开放; 根级 `/docs` / `/redoc` / `/openapi.json` 已不注册. 浏览器无法加 `Authorization`, 只能靠登录下发的 cookie (`path=/api`), 先在 Web 登录再访问; 命令行与智能体用 Bearer.

`TokenAuthMiddleware` 与 `LoggingMiddleware` 都是 `BaseHTTPMiddleware`: 它交给下游的是包装过的 `receive`, 必须真正挂起等待才会收到 `http.disconnect`, 因此 `Request.is_disconnected()` (立刻取消式探测) 在本栈内恒为 `False`; 需要感知客户端离开的长响应改用 `DisconnectSignal` (见 [plugins.md](plugins.md)).

## 约定

**错误**: `HTTPException(detail=中文)`. 路径校验位于 `support/path_validation.py` (存在 / 类型 / `safe_dirs` → 400 / 403 / 404; `ALLOW_ALL` 时跳过边界层). `/files` 的失败映射: 不存在 → 404, 不在 `safe_dirs` → 403, 空名单 → 500, `os.scandir` 的 `OSError` (含网络盘挂载失效) → 500 + strerror detail, `PermissionError` → 403. 错误日志统一由 LoggingMiddleware 打点 (见 [observability.md](observability.md)), handler 内不自行打印.

**列表**: `media` / `metadata` / `tasks` / `facets` / `actors` 同构 `offset` + `limit` + `sort_by` + `order`, 响应 `{items, total}`; `sort_by` 是各资源 `*SortField` 枚举, 显式映射到列或计算表达式 (片库评分按 `scores` 首值, 与响应 `score` 同源), 禁止反射列名. 清理清单的 `cleanup/inventory/nodes` 同样分页, 顺序由树决定, 因此没有排序参数 (见 [task-system.md](task-system.md)). `libraries` / `schedules` / `feeds` 全量无分页. `GET /actors` 列表项不填简介 / 别名 / 用户标签 / 源字典 / `raw` (详情仍全量); `GET /tasks` 与其 `/children` 同样不带 `payload` 与 `result`, 展开单个任务时才取详情.

**状态码**: 创建 201、任务入队 202、无返回体 204; 空 PATCH / 非法 cron → 422; 任务状态不允许的 record → 409.

**资源缓存**: `/resources/{hash}` 与 `/proxy` 因就地超分 URL 不变, 不可 immutable — `Cache-Control: public, no-cache` + `content_hash` ETag. proxy 上游失败 502, 进程内负缓存 15 分钟 (不纳入配置), 同 URL singleflight; 刮削下载不经由该缓存. 图片 token 的 proxy 请求只从缓存读取: 未命中返回 404, 不经过负缓存与 singleflight, 也不发起请求.

- **批量**: 命名分两层 — `batch` 后接**主体** (不接即资源本身), 动作进 body 的 `action` 当且仅当同一主体下的动作共享同一份参数 (`/tasks/batch` 的 cancel / delete / retry, `/facets/{kind}/batch` 的收藏, `/feeds/{id}/items/batch`, 两条 `batch/user-tags` 的 attach / detach); 参数形状不同或只有单动作时动作进路径 (`/metadata/batch/{delete,scrape}`, `/media/batch/delete`, `/actors/batch/{gender,clear-person}`, `/tasks/batch/submit`). 不存在的 id 计入 `missing` 而不报 404, 请求内重复条目计入 `skipped`, 参数级错误 (403 路径越界 / 422 参数互斥) 仍是整请求错误, 其余条目照常处理 (不是 all-or-nothing 事务); 计数口径为「实际处理数 + `missing` (+ `skipped`) = 请求条目数」. 单条写入复用所属批量端点 (单元素列表).
- **批量落库**: 一个批量请求不在服务端逐条提交 — 实体批量写各自一次事务与一次 `IN` 查询; 任务批量入队 (`/tasks/batch/submit`) 逐项与 `POST /tasks` 同形且不设类型白名单, 按 `task_type` 合并为每类一次入队事务, 其 `missing` 只覆盖目标实体不存在的 404, 同番号不同文件各建一条 (逐文件结算不合并). 逐条循环提交不允许, 前端多选一律走批量端点 (见 [frontend.md](frontend.md)). `/tasks/batch` 的选择集是 `task_ids` **或**与列表同形的 `status` / `type` (两者互斥, 未传则不限): `cancel` 把排队 / 运行中标 `failed` 而不删行, `delete` 只动终态并清除磁盘产物, `retry` 只对 `failed` 按原 type / payload / priority 再入队并返回新 `task_ids`, 求交后为空则 `affected=0`. FeedItem 的历史操作是单端点 + 单一 action, 语义见 [feeds.md](feeds.md). 用户标签挂载 (影片 / 演员各一端点) 与标签新建 (`POST /facets/user_tag`: 按名称取回或新建, 响应与入参同序) 都只有批量入口: 未知标签 id 是请求级 404, 不存在的实体计入 `missing`, 已处于目标态计入 `unchanged`.

## WebSocket

`/ws` 只接收不发送, 协议层 PING/PONG. 前端入站处理集中在 `web/src/lib/connection.ts`, 各 store 的消费见 `web/src/stores/`; EventBus 须最先初始化见 [architecture.md](architecture.md).
