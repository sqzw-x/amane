# 任务系统

> Payload 结构与 handler 步骤见源码 (`src/amane/handlers/`, `src/amane/scheduler/worker.py`). 本文记录任务职责边界、入队互斥、后继契约与仍生效的禁止事项.
> 数据所有权见 [data-model.md](data-model.md), 启动顺序见 [architecture.md](architecture.md), 日志隔离见 [observability.md](observability.md).

## 任务职责边界

元数据是中心, 文件系统是派生. 影片侧三种主任务互不内嵌:

| 任务 | 职责 | 排除范围 |
| ------ | ------ | ------ |
| `REFRESH` | 扫描增删、注册 MediaFile、fan-out SCRAPE (`use_cache` 原样转发) | 移动文件、写 NFO |
| `SCRAPE` | 联网聚合 → DB → Resource; `media_file_id` 只作查询输入 (番号 / oshash) 与回写关联 | 库内移动 / NFO |
| `ORGANIZE` | 路径模板 + Library.`move_mode` + 库级整理默认 (payload 可覆盖); 缺资源时 `acquire` 可出站 HTTP | 运行爬虫、修改 Metadata、记录站点结果 |

`CLEANUP` / `UPSCALE` 扫描 DB / Resource; `ACTOR_SCRAPE` 刮人物; `R18_IMPORT` 导入 dump. 上述类型均不执行影片落盘.

不允许 ScrapeHandler 或 Watcher 提交 ORGANIZE. Watcher 只注册文件并入队 SCRAPE. 完整的扫描、刮削与落盘须提交两个任务 (REFRESH + ORGANIZE); 媒体库 UI 将扫描与整理分为独立按钮. ORGANIZE 可用 `priority=-1` 跟在刮削之后; 该优先级不使 ORGANIZE 等待刮削完成: 当时尚未刮削完成的文件会被跳过, 须再次运行 ORGANIZE.

整理默认 (`write_nfo` / `copy_resources`) 与预告片跳过正则在 Library 上, 见 [data-model.md](data-model.md). ORGANIZE payload 对应字段为 `None` 时沿用库设置.

入队互斥在 `create_task`. queued / running 的 ORGANIZE 按 payload.`library_id`、ACTOR_SCRAPE 按 payload.`actor_id` 复用已有行, 不新建. API、Agent、链式入队、retry 都经由该入口, Worker 不按类型加锁. 不同库 / 不同演员仍并行. 终态 (DONE / FAILED) 之后允许再入队. 互斥不比较 payload 其它字段 (`write_nfo` / `use_cache` 不同仍复用). SQLite 默认 DEFERRED 事务里, 两个 session 的 SELECT 都会在写锁前看到空表, 因此检查与插入须在 Repository 上串行化 (单进程).

## 任务图 (TaskLink)

任务完成后产生的后继由任务图层描述与实例化. 后继任务只经 `TaskResult.followups` 进入完成事务:

- **动态后继**: handler 在执行期间才确定后继数量与 payload, 经 `TaskResult.followups` 返回 (`FollowupTask` = key + 类型 + 已完成绑定的 payload + priority). REFRESH→SCRAPE、RESCRAPE→SCRAPE、SCRAPE→ACTOR_SCRAPE 都是这一形态.
- **统一完成事务**: worker 成功路径调用 `Repository.complete_task_with_followups` — 一个事务内完成父任务、创建子任务 (复用 `create_task` 的入队互斥)、写 `TaskLink` 边. 父完成与子创建原子; 失败路径不产生后继. 完成事务与 `create_task` 同一把入队锁串行化 — 两个父任务并发派生同一互斥键 (如同 actor) 时只会复用同一行.
- **`TaskLink`** 是父子边真值: `(parent_task_id, key)` 唯一. `key` 须在父节点内区分后继 (fan-out 带实体 id, 如 `scrape:{media_file_id}` / `actor-scrape:{actor_id}`). 完成事务对同 key 只留第一条. 删除任务时清理其边, 不删除另一端任务.
- **链聚合**: `tasks.root_task_id` 记录链根 (根指向自己, 在完成事务内写入), 一棵链一次 `list_tasks_by_root` 取回. 任务列表默认只显示链根 (子任务按需展开). `child_count` / `child_status` 是直接后继的数量与状态分布 — 折叠节点据此显示数量、失败 / 运行数, 不必先展开.
- **筛选与 roots_only 正交**: `GET /tasks` 带 status / type 筛选时在 SQL 中匹配**全部**任务 (含子任务), 再 `DISTINCT COALESCE(root_task_id, id)` 还原链根行 — 因此「父已 DONE、子 SCRAPE 排队中」时 `status=queued` / `type=scrape` 仍能看到父根行. 裸任务 (root 为空) 按自身 id 精确匹配, 不会误扩到其它裸任务.
- **删除保护**: 待删集合里存在**不在该集合内的后裔**的节点跳过 (含孙任务、菱形多父). 整棵匹配子树可一次删除; 「清除已完成」遇到父 DONE、子有成有败时保留父节点作为链根, 只删除无剩余后裔的 DONE 叶子. 删除节点时清理其边, 不删除集合外的另一端任务.
- **重试为独立再次运行**: `retry_tasks` 克隆为**无根裸任务**, 不继承原任务链归属 — 有链任务失败重试后克隆在顶层列表可见, 原 FAILED 行留在原链上, 克隆自身无父无子, 完成后自成新链. 不与原链的后继冲突.
- **树视图 API**: `GET /tasks/{id}/children` 返回直接子任务 (`TaskChildResponse`, 含出边 `link_key`; `limit` / `offset`, `total` 为出边总数不受本页截断). `GET /tasks?root_task_id=` 取整链. 前端 `web/src/components/task/task-tree.tsx` 嵌套列表树: 点击整行展开 / 收起该节点 (子任务与详情同一动作). 完成 / 失败事件与批量操作经 `invalidateTaskQueries` 同时失效列表与 children (hey-api query key 是 `[{ _id }]`, 不允许写成 `["getTaskChildren"]`).
- 静态 continuation / on_failure / 多父 join 尚未实现, 见 `docs/plan/task-graph.md`.

源与模板 dest 已是同一文件时的碰撞规则见 [落盘执行](#落盘执行).

## REFRESH 组合开关

`RefreshPayload` (`handlers/models.py`) 将扫描与刮削拆分为独立开关:

| 字段 | 含义 |
| ------ | ------ |
| `scan` | `add` 注册新文件 / `remove` 删失效记录, 可组合 |
| `scrape` | 对指定 `MediaFileStatus` 派生 SCRAPE 任务 |
| `use_cache` | `set[CacheKind]`: 含 `metadata` 复用 per-site raw; 含 `trans` 复用译文缓存; 空集 = 全强制刷新 |

字段组合: `scan={"add"}, scrape=set()` → 仅注册不刮削; `scan={"add"}, scrape={"pending"}` → 注册 + 刮削; `scan={"remove"}` → 仅删除失效记录. 落盘另交 ORGANIZE.

扫描遍历经由 `scan_library` (`@in_thread` glob / stat, 一次分类为跳过 / 归档 / 媒体), 与库内索引的差集在 Python 计算 (`list_media_files` 一次全部读取). 不允许将整棵树的路径放入 SQL `IN` / `NOT IN`: `NOT IN` 按批拆分会把其它批里真实存在的文件误判为失效. 仅 `remove` 时对库内记录 `exists`, 不遍历磁盘树. fan-out 必须 `list_media_files(..., limit=None)`: 默认 50 是 `GET /media` 的列表分页, 不是批量任务上限.

文件注册 (watcher 发现与 REFRESH 扫描共用 `register_media_file`) 只写路径, 不计算 oshash. 指纹只在 SCRAPE 时按需计算: 本次实例化的爬虫 `profile().uses_file_hash` (ThePornDB) 且 `MediaFile.oshash` 为空, 才 `ensure_oshash`; 失败留 `None`, 不阻断刮削.

REFRESH 仅在指定 library 下运行, 提交不接受裸 path. 不入库只刮削由 `ScrapeSubmission` 的 by-number 纯查询路径表达, 与 REFRESH 正交.

## 站点级复用

SCRAPE **没有**「缓存命中即整体跳过爬取」的快速返回 — 完全不联网的纯整理由 ORGANIZE 任务承担. SCRAPE 总是进入聚合, 但**逐站复用**既有数据: 当 `CacheKind.metadata ∈ use_cache` 时, `ScrapeHandler` 取出 `Metadata.raw` 作为 `cache` 传入 `aggregate`. `_fetch_one` 在请求某站前先按 `cache_key` (`site` 或 `site:lang`) 查询快照 — 命中即还原、跳过爬虫调用, 仅缺失 / 失败站点真正发起请求.

- `use_cache` 不含 `metadata` 时忽略既有 `raw`, 全部站点强制重爬.
- `use_cache` 不含 `trans` 时跳过译文缓存读取 (仍写入), 强制重译. 详见 [llm.md](llm.md).
- 复用与新结果统一写入 `fetched`, 输出 `raw` 为两者合并. 快照含非法字段时降级为正常 fetch (见 `_fetch_one`).

## 字段级多源聚合

`aggregate` (`src/amane/aggregate/`) 不取并集全爬再挑值, 而是先把优先级配置编译成**静态抓取图** (`build_graph`), 再按波次执行 (`execute_graph`):

- **建图** (`build_graph` → `compute_waves`): handler 先把 `content_routes[type]` 与稀疏 `field_priority` 编成每字段站点链 (`compile_priority`: prefer ∩ route 前置, 其余 route 保序). `content_routes` 是该类型资格真值, 不在表内的站不会被请求. 站点 + 语言唯一确定一个 `FetchNode` (`cache_key`), 节点按拓扑分层为**波次** (层内可并行). 每个字段沿优先级链回填 `covers` 与 `fallback` 边.
- **执行** (`execute_graph`): 逐波推进, 每波只激活仍有未满足字段且尚未请求的节点, `asyncio.gather` 并发抓取. `crawlers` 映射是可用集合: 禁用插件 / 未安装第三方 / 构造失败都不在其中, 图节点直接跳过并沿 fallback 继续, 不调用 `invoke_source` (因此不会记成 unexpected). 波后只定值标量 (满足即短路); 后波 `partial` 只携带已定标量. URL / score / extrafanart 在全部请求结束后按该字段 `field_chains` 拼接, 不按返回先后排列. 某站未返回或该字段为空则跳过, 不把后面的站提到前面.
- **多语言合并**: 若某字段需 (site, lang) 而另一字段仅需 (site, None), `compute_waves` 合并为一次带语言请求.

后果: f1=[s3,s2,s1]、f2 / f3=[s1,s2,s3] 全成功时只请求 s1, s3; 若 s1 失败则沿 fallback 由 s2 接替.

## TaskHandler 契约

`src/amane/handlers/protocol.py::TaskHandler[P, R]` 用泛型固定 payload / result 类型. 序列化约束:

- 入队 `payload.model_dump()` 序列化为 JSON, 出队 `model_validate(raw)` 还原并校验.
- payload 字段带默认值时, 旧 task 出队不会 KeyError.
- 字段约束 (range / enum) 在反序列化阶段拒绝, 失败直接 `fail_task`.

**约束**: 不允许重命名已持久化的 payload 字段, 否则队列中的旧 dict 无法还原. 新增字段必须带默认值.

### 进度上报

Worker 在 `handle()` 前注入 `report_progress` 回调, 经 EventBus 发 `task.progress` (`{task_id, current, total, message}`); 前端写 `stores/progress.ts` 渲染 determinate 条 (见 [frontend.md](frontend.md)).

**契约**: `total > 0` 时前端按 `current/total` 显示百分比; 未上报则 running 态回退 indeterminate. Handler 不调用时静默忽略.

**SCRAPE**: 分母 = 标量字段数 + 2 (`materialize` / `persist`). 聚合按波次上报已满足标量字段数 (URL / score / extrafanart 只累积, 不计入); message 为当波站点 `cache_key`. 抓取结束后抬到标量满分, 再执行后两步至 `done`.

**ORGANIZE**: 失效索引按本库 MediaFile 条数 (`prune`). 一次遍历的结果在内存中分类后分别归档 (`trash`) 与落盘 (message 为文件名). glob 进行中 `total=0`. 空目录以 1/1 `done` 结束.

其它任务类型不调用则静默忽略.

### 站点结果上报

SCRAPE 与 ACTOR_SCRAPE 的每个站点结果经 `invoke_source` 写入任务摘要 (契约见 [observability.md](observability.md)「站点结果单一导出」). HTTP / 拦截失败带 `SourceError` 上的 `FailureReason` 与 HTTP 状态; 未命中是 `None` → `no_usable_metadata`; 意外异常记 `unexpected` 后继续其它源.

## 共享单元

handler 之间复用的阶段逻辑, 不是一条可跳步的总管线:

| 单元 | 位置 | 复用方 | 职责 |
| ------ | ------ | -------- | ------ |
| `LibraryScan` | `library/scan.py` | REFRESH / ORGANIZE / watcher | 单路径分类 (跳过 / 归档 / 媒体); 规则常量与校验在 `library/rules.py`; watcher 只调用 `classify` |
| `scan_library` | `handlers/_common.py` | REFRESH / ORGANIZE | 库目录遍历; `@in_thread` 包装 glob / stat |
| `finalize_media_file` | `handlers/_common.py` | SCRAPE (缓存 / 主路径) | 标记 SCRAPED + 关联 Metadata |
| `apply_file_operations` | `handlers/file.py` | ORGANIZE | 读取 MediaFile→读取 Library→渲染路径→执行 file ops; 库路径 I/O 经 `@in_thread` |

库路径 (含 FUSE / NAS) 与用户浏览路径上的磁盘调用不允许在事件循环上执行, 见 [architecture.md](architecture.md). 整段同步 I/O 用 `@in_thread`, 调用方 `await fn(...)`; 已在工作线程内 (例如 `place_subtitles` 里再 `execute_organize`) 用 `.sync`, 不允许再次进入线程池. Watchdog 的 `stat` 在 observer 线程, 不经过事件循环. Resource / `data_dir` 由进程自己管理, 同步读写.

`_common.py` 只放置无 `execute_file_operations` 依赖的轻量单元 (纯函数, 依赖全参数注入). `apply_file_operations` 因封装 `execute_file_operations` 而与之相邻置于 `file.py`, 避免循环导入. 前置条件不满足时返回 `None` 表示跳过. 图片下载统一经 `ResourceStore` (强制注入).

## 落盘执行

`execute_file_operations` 是落盘执行单元, 仅 ORGANIZE 经 `apply_file_operations` 调用. 不变量:

- **整理 = 复制到库路径**: 优先用 Resource 已有文件; 缺失才现场 `acquire`. 复制哪些类型由 Library.`copy_resources` (或 ORGANIZE payload 覆盖) 决定, 不由 `scraping.download_resources` 控制.
- **封面角标**: `watermark.enabled` 时 poster / thumb 副本按源文件 FileInfo 叠 PNG; 大小 / 四角见 Hot `watermark`. Resource 原图与 fanart 不修改. 用户 PNG 覆盖见 [data-model.md](data-model.md).
- **海报缺失**: 按 `scraping.crop_poster` 从已落盘 thumb 裁剪兜底.
- **已就位**: 源与模板 dest 已是同一文件 (含硬链同一 inode) 时视为成功, 不追加 `(1)`; 碰撞改名只用于 dest 被另一文件占用.
- **链接入口**: `link_template` 非空时, 视频就位后在库根之外写 strm 或软链接, 指向这次的 dest. `link_mode=strm` 时正文按 `strm_content_template` 渲染 (空则绝对路径). `MediaFile.path` 仍是真实视频. 链接写入失败时 dest 仍回写 (视频已搬家), 任务记失败以便重试补链接.
- **失效索引**: ORGANIZE 落盘前按库删除 path 在磁盘上不存在的 MediaFile. 碰撞改名只检查磁盘; 不先删除磁盘上已不存在、仍保留在库中的 MediaFile 记录, dest(1) 会在 UNIQUE 上与库内旧 path 冲突.

## 刮削期资源物化

`ScrapeHandler` 在聚合后、`upsert_metadata` 前调用 `materialize_images` (`src/amane/media/pipeline.py`). 这是二进制写入 Resource 目录的主路径 (与是否整理无关):

- **`scraping.download_resources`**: 多选枚举, 控制本步下载哪些类型 (thumb / poster / extrafanart / trailer).
- **URL 重排契约**: 被下载类型的 URL 列表按本次下载成功 (含 Resource 缓存命中) 稳定分区 — 成功者保序前置、失败者保序沉底. 死 URL (来源站失效) 不再占据首位 (前端主图 / ORGANIZE 顺序尝试都受益), 但保留在尾部, 来源恢复后下次物化可重新尝试. 未选中下载的类型无成败信息, 保持聚合优先级原序; extrafanart 为站点分组 dict, 不重排. `raw` 快照保持站点原始数据, 重刮时重聚合出优先级序后再重排.
- 裁剪海报 → 按 `scraping.poster_ratio` 从 thumb 右侧裁切, 记内部相对 URL; 超分就地覆盖 (URL 不变). 失败不阻断刮削.
- serve 端 (`routes/resources.py`) 按 url hash 检索记录并返回文件.

手动裁切 (`manual_crop_poster` / `POST .../crop-poster`) 复用同一派生通道 (`op=crop`, `args=box:…`), 与刮削期右侧比裁切共用 Resource 语义; 见 [data-model.md](data-model.md).

## 图像超分

超分只在两处发生, serve 永不触发: scrape 期急切 (`sr.enabled` 时 `materialize_images` 对低质本地副本) 与 UPSCALE 任务 (扫描 Resource, 补 `'sr' not in meta` 的低质图).

**就地覆盖**: 不产生新 URL, 直接覆盖磁盘文件并在 `meta` 打 `'sr'`. URL / metadata 不变, 前端零感知; 去重依据 `'sr' in meta`. 阈值纯函数 `needs_upscale` (`max_dim_threshold` / `max_bytes_threshold`); 视频永不超分.

预设只暴露两个, 屏蔽工具 / 模型 / 倍率: 默认 `waifu-photo-2x` (快、膨胀小 — 源图分辨率已足够时补一档); `realesr-photo-4x` 给明显低质图. 二进制按需下到 `{data_dir}/tools/`.

## Worker 并发

并发上限 `worker.concurrency` (默认 10, 校验 1-64). 上限是经验值: curl_cffi 浏览器指纹 + 多站点并发下, 过高易触发反爬.

### 暂停

进程内 `_paused`, 不写入 HotSettings. 暂停只停 `claim_next_task`; 循环仍在, 已认领的继续运行, 入队不受影响. `rebuild()` 把 pause 复制到新 worker, 避免 PATCH 配置时意外恢复领队. 与 `stop()` 不同: stop 排空 / 取消活跃任务并标僵尸 RUNNING 为失败.

### 取消

`AsyncWorker.cancel_task(task_id)` 通过给运行中的 asyncio task 注入 `CancelledError`. 取消时机:

| 场景 | 安全性 |
| ------ | -------- |
| HTTP 请求中 | 安全 (curl_cffi 断连) |
| 文件 move / hardlink 中 | **不安全** (shutil 不响应 CancelledError) |
| DB 写入中 | 安全 (session 退出时回滚) |

文件操作中取消只能等操作完成后才能真正生效.

### 关闭

`stop()` 先置 `_running=False` 并**取消主循环 task**, 再处置活跃任务: `worker.shutdown_timeout` (默认 `0`) 等待活跃任务自然完成的秒数; `0` 表示立即超时并 cancel 活跃任务. 需要排空再退出时调大该值 (上限 120). 主循环若不被终止, 停在 DB 往返中的 claim 会在 stop() 返回后认领**之后**入队的任务. API 测试停 worker 必须在此语义下才不竞态.

## 即时提交与定时提交

两条路径的对象与后果不同.

**即时** (`POST /tasks`): 接收 `TaskSubmission` (含全部即时 `type`, 含 `actor_scrape` / `rescrape`), 经 `src/amane/api/support/task_resolve.py::resolve_submission` 得到 `(TaskType, Payload)` 后建 Task. REFRESH / ORGANIZE 只接受 `library_id`, resolve 时由 library 派生 `path` / `recursive` / `patterns` (submission 可显式覆盖); SCRAPE 采用 number / media_id, 二者可同时提交. **`content_type` 可空**: 为空时仅 media_id 按文件路径解析、有 number 时按番号推断 (显式给定则覆盖). **`payload.number` 是否经过 `parse_file_info` 重写**取决于进路 (只按路径会改写, 手填 `number` 原样, 与 media_id 同时填写时仍原样), 爬虫必须同时接受两种入参, 见 [crawlers.md](crawlers.md) 番号入参. 覆盖只作用于这一次 `POST /tasks`; 库表一键刮削与 REFRESH 仍按路径解析. `ACTOR_SCRAPE` 采用 `actor_id` (亦可通过 `POST /actors/{id}/scrape`).

**定时** (`Schedule`): 仅接受 `RoutineSubmission` (`cleanup` / `upscale` / `r18_import` / `rescrape`). 创建时把 submission 的 `model_dump()` 原样写入 `Schedule.payload`; cron / trigger 触发时由 `CronScheduler._execute_task` 从 dict 构造对应 Payload 再建 Task. 编辑只修改 name / cron / enabled; 修改任务内容须删除后重建.

## ACTOR_SCRAPE

`ActorScrapeHandler` 按 `HotSettings.actor_scraping` 的档案站 / 头像站顺序抓取 (配置契约见 [config.md](config.md)), **先按 `Actor.gender` 与各站 `profile().genders` 过滤** (`female` / `male` 须在覆盖内; `unknown` 只请求同时覆盖两性的站, 避免请求仅覆盖女性的站点; 被裁站不发 HTTP、不消费其 raw 缓存). 站点内按查找名首命中; 聚合是标量填空 (含 `gender`, `unknown` 当空) + 头像优先 (无影片字段 DAG). **`use_cache` 与影片同型** (`CacheKind.metadata` / `trans`): 含 `metadata` 时按**已允许**站复用 `Actor.raw` 快照跳过爬虫 (非法快照降级为重爬); `trans` 预留演员译文, 接入前无行为差. 空集 = 全站强制重爬. 写回时再与库内已有人物字段填空合并, 避免冲掉已填值. 可选 `download_images` 经 ResourceStore `acquire` 缓存头像 (URL 仍存远端 locator). 爬虫实例见 `CrawlerFactory.get_actor*`; gFriends 注入 `data_dir` / `gfriends_repo`.

**链式自动触发**: `actor_scraping.auto_scrape` 开启 (默认) 时, 影片 `SCRAPE` 成功后在 `ScrapeHandler` 末尾 (`finalize_media_file` 之后) 按 `meta.actors` (清洗解析后的展示名) 查询 Actor 实体, **`Actor.raw` 非空 (已刮过) 则跳过**, 其余以 **`priority=-1`** 入队 `ACTOR_SCRAPE` (低于默认 0, 批量 REFRESH 产生的演员任务不抢占影片任务优先级). 同 `actor_id` 已有 queued / running 时复用 `create_task` 的入队互斥, 不为同一头像 URL 并行新建两条. 链式块内异常只记录 warning, 不阻断刮削主流程; 失败路径 (无数据 / 无爬虫) 不进入该后继入口, 不产生链式任务.

## CLEANUP 悬空引用回收

Metadata 是一等公民, CLEANUP **从不**因「无关联 MediaFile」删除 Metadata. 两个独立开关:

| 开关 | 对象 |
|------|--------|
| `remove_missing_files` | 路径在磁盘上不存在的 MediaFile 索引行 |
| `remove_unreferenced_resources` | 不被任何 Metadata / Actor 媒体 URL 字段引用的 Resource (外部 URL 或 `/api/resources/{hash}`) |

存活引用从 Metadata 的 `poster_urls` / `thumb_urls` / `trailer_urls` / `extrafanart_urls` 与 `Actor.image_urls` 收集; 与 Resource 的匹配见 [data-model.md](data-model.md) Resource 一节.

## 调度器与监控

- `CronScheduler`: 每 60s 扫描一次启用的 `Schedule`, 按 `RoutineType` (`CLEANUP` / `UPSCALE` / `R18_IMPORT` / `RESCRAPE`) 入队 (`scheduler/cron.py::_execute_task`).
- `WatcherService`: 文件系统事件 + 防抖.
- `FeedService`: 远程 RSS / Atom 发现, 按每源 `next_fetch_at` 到期拉取; `auto_enqueue` 时入队 by-number SCRAPE. **不是** Schedule / Routine. 契约见 [feeds.md](feeds.md).

**UPSCALE 例行任务**: 扫描全部 `Resource`, 对低质且未超分的就地超分; `limit` 限单次批量. 与 scrape 期急切双轨, 见上节.

**RESCRAPE (元数据级滚动补刮)**: 与 `RefreshHandler` 同构的 fan-out — 批量任务只选目标并下发 SCRAPE, 重活经由既有 SCRAPE. 按 `updated_at ASC` 取最久未更新的 `limit` 条 Metadata (可选 `min_age_days` 门槛), 逐条以 `priority=-1` 入队非 force SCRAPE: 复用 per-site raw 快照仅补缺失站点, 聚合阶段重放当前配置 — 因此同时承担「配置变更后再次运行生效」. **content_type 不存表, 运行时推断**: `infer_content_type` — 有挂载文件传路径 (路径关键词 → 番号), 无文件只传番号文本 (未命中则欧美; 路径关键词类如里番 / 欧美目录名在无文件时不可推断).

Watcher、Cron 与 Feed 分属独立循环: 秒级反应、分钟级 routine、每源间隔的远程拉取. 合并到同一循环会互相拖高 latency, 或把 HTTP / RSS 纳入 cron.py.

`watcher.use_polling` 在 NAS / Docker Desktop / WSL2 等 inotify 不可靠场景下打开. `debounce_seconds` 防止大文件写入途中提前刮削. 这三项 HotSettings 在进程启动时注入 WatcherService, 修改 TOML 后须重启才生效 (见 [config.md](config.md)); Library 级 `automation` / 路径 / `trailer_pattern` 则由 libraries 路由热增删监控根. `automation=none` 不监控; `watch` 只登记; `scrape` 登记后入队 SCRAPE. 三者都不自动 ORGANIZE.

**归属随事件携带**: 每个监控根的 `_Handler` 绑定 `library_id`; 文件事件回调带上来源库, 新文件以此入库 (见 [data-model.md](data-model.md) Library 归属).
