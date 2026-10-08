# 任务系统

> Payload 结构与 handler 步骤见源码 (`src/amane/handlers/`, `src/amane/scheduler/worker.py`). 本文记录任务职责边界、入队互斥、后继契约与仍生效的禁止事项.
> 数据所有权见 [data-model.md](data-model.md), 启动顺序见 [architecture.md](architecture.md), 日志隔离见 [observability.md](observability.md).

## 任务职责边界

元数据是中心, 文件系统是派生. 影片侧主任务互不内嵌:

| 任务 | 职责 | 排除范围 |
| ------ | ------ | ------ |
| `REFRESH` | 扫描增删、注册 MediaFile、fan-out SCRAPE (`use_cache` 原样转发); 整库范围时顺带写入清理清单 | 移动文件、写 NFO |
| `SCRAPE` | 联网聚合 → DB → Resource; `media_file_id` 只作查询输入 (番号 / oshash) 与回写关联 | 库内移动 / NFO |
| `ORGANIZE` | 范围内已有 Metadata 的 MediaFile 按路径模板落盘, 并删除本次腾空的目录; 缺资源时 `acquire` 可出站 HTTP | 扫描磁盘、运行爬虫、修改 Metadata |
| `SCAN_INVALID` | 遍历库产出清理清单 (文件黑名单 / 小于最小视频大小 / 残留目录 / 空目录), 写入进程内清单存放 | 一切写操作 |
| `DELETE` | 按清单标识删除文件与目录, 删对应索引, 按需剪枝 | 整库重新扫描、重新生成清单、写 NFO、回收 Resource |

`CLEANUP` / `UPSCALE` 扫描 DB / Resource; `ACTOR_SCRAPE` 刮人物; `R18_IMPORT` 导入 dump. 上述类型均不执行影片落盘.

不允许 ScrapeHandler 或 Watcher 提交 ORGANIZE / DELETE — Watcher 只注册文件并入队 SCRAPE; 完整的扫描、刮削与落盘须提交 REFRESH, 再提交 ORGANIZE. ORGANIZE 可用 `priority=-1` 跟在刮削之后, 但该优先级不使 ORGANIZE 等待刮削完成: 当时尚未刮削完成的文件会被跳过, 须再次运行 ORGANIZE.

ORGANIZE 只读取范围内的 `MediaFile` 行: 缺省为该库全部索引, 显式 `path` 按前缀过滤, `media_file_ids` 为勾选快照 (含其它库的 id 则 422); 后两者同时给出则 422. 库根必须是已存在的目录, 否则失败; `media_file_ids` 未给出且 path 为子目录时该子目录也必须存在 — 避免网络盘掉线时把选中行当失效索引删掉. 无 Metadata 的行与命中文件黑名单 / 最小视频大小 / 预告片规则的行跳过落盘; 路径落在 `.amane_trash` 内的行删除索引. SCAN_INVALID 的 `path` 同样可限定子目录. 整理默认与预告片跳过正则见 [data-model.md](data-model.md).

入队互斥在 `create_task`: queued / running 的 ACTOR_SCRAPE 按 `payload.actor_id` 复用已有行; ORGANIZE 与 DELETE 每次提交都新建行, 禁止复用, 同库的二者共用 `LibraryTaskLocks` 在执行期串行 (都在同一棵树上动文件). 不同库 / 不同演员仍并行. API、Agent、链式入队、retry 都经由 `create_task`, Worker 不按类型加锁; 终态之后允许再入队; 互斥不比较 payload 其它字段. SQLite 默认 DEFERRED 事务里两个 session 的 SELECT 都会在写锁前看到空表, 因此检查与插入须在 Repository 上串行化 (单进程).

## 任务图 (TaskLink)

后继任务只经 `TaskResult.followups` 进入完成事务:

- **动态后继**: handler 在执行期间才确定后继数量与 payload, 经 `TaskResult.followups` 返回. REFRESH→SCRAPE、RESCRAPE→SCRAPE / ACTOR_SCRAPE、SCRAPE→ACTOR_SCRAPE 都是这一形态.
- **统一完成事务**: worker 成功路径调用 `Repository.complete_task_with_followups` — 一个事务内完成父任务、创建子任务 (复用 `create_task` 的入队互斥)、写 `TaskLink` 边; 父完成与子创建原子, 失败路径不产生后继. 它与 `create_task` 同一把入队锁串行化, 因此两个父任务并发派生同一互斥键时只会复用同一行.
- **`TaskLink`** 是父子边真值: `(parent_task_id, key)` 唯一, `key` 须在父节点内区分后继 (fan-out 带实体 id); 完成事务对同 key 只留第一条. 删除任务时清理其边, 不删除另一端任务.
- **链聚合**: `tasks.root_task_id` 记录链根 (根指向自己), 一棵链一次 `list_tasks_by_root` 取回. 任务列表默认只显示链根, `child_count` / `child_status` 是直接后继的数量与状态分布, 折叠节点据此显示.
- **筛选与 roots_only 正交**: `GET /tasks` 带 status / type 筛选时在 SQL 中匹配**全部**任务 (含子任务), 再 `DISTINCT COALESCE(root_task_id, id)` 还原链根行, 因此「父已 DONE、子排队中」时仍能看到父根行. 裸任务 (root 为空) 按自身 id 精确匹配.
- **删除保护**: 待删集合里存在**不在该集合内的后裔**的节点跳过; 「清除已完成」遇到父 DONE、子有成有败时保留父节点作为链根.
- **重试为独立再次运行**: `retry_tasks` 克隆为**无根裸任务**, 不继承原任务链归属, 完成后自成新链.
- **树视图 API**: `GET /tasks/{id}/children` 返回直接子任务 (含出边 `link_key`); `GET /tasks?root_task_id=` 取整链. 批量操作须同时失效列表与 children (见 [frontend.md](frontend.md)).
- 静态 continuation / on_failure / 多父 join 尚未实现, 见 `docs/roadmap.md`.

## REFRESH 组合开关

`RefreshPayload` 的 `scan` / `scrape` / `use_cache` 取值语义见 `handlers/models.py`; 落盘另交 ORGANIZE.

扫描遍历经由 `scan_inventory` 的显式递归 (`@in_thread`, 一次分类为跳过 / 无效 / 媒体, 顺带产出清理清单与读错误计数), 与库内索引的差集在 Python 计算. 不允许将整棵树的路径放入 SQL `IN` / `NOT IN` — 按批拆分时 `NOT IN` 会把其它批里真实存在的文件误判为失效; 仅 `remove` 时对库内记录 `exists`, 不遍历磁盘树. fan-out 必须 `list_media_files(..., limit=None)`, 默认 50 是列表分页不是批量任务上限. `MediaFile.path` 的写入、按路径查找、有效 / 失效集合差一律 NFC, 从库内路径打开 / 判断存在 / 落盘必须经 `existing_disk_path`.

文件注册 (watcher 与 REFRESH 共用 `register_media_file`) 只写路径, 不计算 oshash; 指纹只在 SCRAPE 时按需计算 (本次可用来源中有声明 `uses_file_hash` trait 且 `oshash` 为空), 失败留 `None`, 不阻断刮削. REFRESH 仅在指定 library 下运行, 提交不接受裸 path; 不入库只刮削由 `ScrapeSubmission` 的 by-number 纯查询路径表达.

## 站点级复用

SCRAPE **没有**「缓存命中即整体跳过爬取」的快速返回 — 完全不联网的纯整理由 ORGANIZE 承担. 它总是进入聚合, 但当 `CacheKind.metadata ∈ use_cache` 时把 `Metadata.raw` 作为 `cache` 传入, 请求某站前按 `cache_key` (`site` 或 `site:lang`) 查快照, 命中即还原并跳过爬虫调用, 未命中的站点按获取图请求. 不含 `metadata` 时全部站点强制重爬; 不含 `trans` 时跳过译文缓存读取 (仍写入), 见 [llm.md](llm.md). 复用与新结果统一写入 `fetched`, 输出 `raw` 为两者合并; 快照含非法字段时降级为正常 fetch.

## 字段级多源聚合

`aggregate` (`src/amane/aggregate/`) 先把优先级配置编译成**获取图** (`build_graph`), 再分两段执行 (`execute_graph`):

- **建图**: handler 先把 `content_routes[type]`、稀疏 `field_priority` 与稀疏 `field_blacklist` 编成每字段站点链 (见 [config.md](config.md)); `content_routes` 是该类型资格真值, 被全部字段黑名单的站不产生节点. 站点 + 语言唯一确定一个 `FetchNode` (`cache_key`); 站点在任一字段上需要语言时统一用带语言节点.
- **执行**: 未声明依赖的节点并发请求; 声明 `SourceTrait.NEEDS_PARTIAL` 的来源 (按来源目录的 `traits` 判定) 在第二段并发, 段间注入只读的标量聚合 (`partial_result`, 深拷贝). 节点不因标量已满足而跳过. `crawlers` 映射是可用集合: 禁用插件 / 未安装第三方 / 构造失败都不在其中, 图节点直接跳过并沿链继续, 不调用 `invoke_source` (因此不会记成 unexpected).
- **取值**: 标量沿链取第一个非空值 (判定为真值, `0` / 空串 / 空列表都算空), 空值继续回退, 链上仍有未执行节点时中断该字段; 只有非空值写入 `field_sources`. 聚合类字段 (URL / score / extrafanart) 在全部请求结束后按该字段 `field_chains` 拼接, 不按返回先后排列; 某站未返回或该字段为空则跳过. 标量可以全空: 只要有来源返回结果, 任务仍成功. 落库跳过锁定字段, 见 [data-model.md](data-model.md).

## TaskHandler 契约

`src/amane/handlers/protocol.py::TaskHandler[P, R]` 用泛型固定 payload / result 类型. 入队 `model_dump(mode="json")` 序列化为 JSON, 出队 `model_validate(raw)` 还原并校验; payload 字段带默认值时旧 task 出队不会 KeyError, 字段约束 (range / enum) 在反序列化阶段拒绝并直接 `fail_task`.

**约束**: 不允许重命名已持久化的 payload 字段, 否则队列中的旧 dict 无法还原; 新增字段必须带默认值.

结果侧: 每个结果模型自带 `type` 字面量, 单条任务的结果按该字段判别 (联合定义在 `api/models/tasks.py`, 前端按 `result.type` 收窄). 结果由本进程按同一模型写入, 读不出来时不迁移也不猜字段: 详情响应里置空. 列表与子任务响应不带 `payload` 与 `result`, 展开单个任务时才取详情. 落库前统一经 `TaskResult.as_dict` 按 JSON 模式 dump, 结果模型的字段因此只允许 JSON 原生类型. 失败任务同样落库载荷 (`fail_task` / `fail_running_task` 都接 `result`): 刮削与演员刮削攒下站点明细后失败时带上它, 崩溃与取消则由 `TaskHandler.failure_result` 补交, 界面据此解释失败原因. 新增结果模型须同时补 `tests/handlers/test_protocol.py` 的用例与 `api/models/tasks.py` 的联合 (漏了联合, 该类型的详情会静默置空).

### 进度上报

Worker 在 `handle()` 前注入 `report_progress` 回调, 经 EventBus 发 `task.progress` (`{task_id, current, total, message}`); 前端写 `web/src/stores/progress.ts`. **契约**: `total > 0` 时前端按 `current/total` 显示百分比, 未上报则回退 indeterminate, Handler 不调用时静默忽略.

### 站点结果上报

SCRAPE 与 ACTOR_SCRAPE 的每个站点结果经 `invoke_source` 写入任务摘要 (契约见 [observability.md](observability.md)「站点结果单一导出」). HTTP / 拦截失败带 `SourceError` 上的 `FailureReason` 与 HTTP 状态; 未命中是 `None` → `no_usable_metadata`; 意外异常记 `unexpected` 后继续其它源.

## 共享单元

handler 之间复用的阶段逻辑, 不是一条可跳步的总管线:

| 单元 | 位置 | 复用方 | 职责 |
| ------ | ------ | -------- | ------ |
| `LibraryScan` | `library/scan.py` | REFRESH / watcher / ORGANIZE / SCAN_INVALID | 单路径分类 (跳过 / 无效 / 媒体), `unwanted_kind` 给出无效原因; 规则常量与校验在 `library/rules.py` |
| `scan_library` | `handlers/_common.py` | clouddrive 巡检 | 库目录遍历; `@in_thread` 包装 glob / stat. 入库扫描与清理清单改走 `library/cleanup/inventory.py::scan_inventory` 的显式递归 (能收集读错误并产出目录条目) |
| `LibraryTaskLocks` | `handlers/_common.py` | ORGANIZE / DELETE | `build_handlers` 构造一份注入两端, 同库执行期串行; 测试里未注入时各 handler 自建, 互不共享 |
| `finalize_media_file` | `handlers/_common.py` | SCRAPE (缓存 / 主路径) | 标记 SCRAPED + 关联 Metadata |
| `apply_file_operations` | `handlers/file.py` | ORGANIZE | 读取 MediaFile→读取 Library→渲染路径→执行 file ops; 库路径 I/O 经 `@in_thread` |

库路径 (含 FUSE / NAS) 与用户浏览路径上的磁盘调用不允许在事件循环上执行: 整段同步 I/O 用 `@in_thread`, 调用方 `await fn(...)`; 已在工作线程内 (例如 `place_subtitles` 里再 `execute_organize`) 用 `.sync`, 不允许再次进入线程池. Watchdog 的 `stat` 在 observer 线程, 不经过事件循环.

`_common.py` 只放置无 `execute_file_operations` 依赖的轻量单元 (纯函数, 依赖全参数注入); `apply_file_operations` 因封装 `execute_file_operations` 而与之相邻置于 `file.py`, 避免循环导入. 前置条件不满足时返回 `None` 表示跳过. 图片下载统一经 `ResourceStore` (强制注入).

## 落盘执行

`execute_file_operations` 是落盘执行单元, 仅 ORGANIZE 经 `apply_file_operations` 调用. 不变量:

- **整理 = 复制到库路径**: 优先用 Resource 已有文件, 缺失才现场 `acquire`; 复制哪些类型由 `Library.copy_resources` (或 payload 覆盖) 决定 (见 [data-model.md](data-model.md)).
- **封面角标**: `watermark.enabled` 时 poster / thumb 副本按源文件 FileInfo 叠 PNG; Resource 原图与 fanart 不修改.
- **海报缺失**: 按 `scraping.crop_poster` 从已落盘 thumb 裁剪兜底.
- **已就位**: 源与模板 dest 已是同一文件 (含硬链同一 inode) 时视为成功, 不做任何改动.
- **目标占用**: dest 上已是别的文件时跳过该文件, 不改名、不覆盖、不移动, 视频与字幕一视同仁; 调用方按冲突记账, 冲突条目 (源路径 + 被占用的目标路径; 字幕冲突的 `path` 记字幕源路径, 不记视频源路径) 写进任务结果供界面显示, 且 `conflicts` 恒为 `conflicted` 条数的前缀 (差额只留在任务日志里). 冲突不自动解决, 未落盘的行留在源路径, 由用户自行处理. 存在性与占用判定都经 `existing_disk_path` (跟随符号链接): 目标位置是断链符号链接时按空闲处理, MOVE 会替换它, SYMLINK 因 EEXIST 记失败. 判定与落盘之间还有窗口, MOVE / COPY 期间目标被外部写入仍会覆盖, 这条保证是尽力而为.
- **链接**: `link_template` 非空时视频就位后在库外写 STRM 或符号链接, 指向这次整理后的路径; `MediaFile.path` 仍是真实视频. 链接写入失败时, 目标路径仍在本库内则回写 path, 任务记失败以便重试补链接.
- **索引写回**: 整理后路径仍在本库内则更新 `MediaFile.path`; 已不在本库内且源路径不在磁盘上则删除该行. 不改 `library_id`, 不写其它库的行. 目标路径已被本库另一行占用时删除本行, 占用行缺少刮削字段则补上.
- **失效索引**: 落盘前只对本次读到的行探活 (path 不存在或不在本库内则删除), 范围外的行不读取、不探活. 占用判定只看磁盘, 索引里有另一行指向同一路径不构成冲突.
- **腾空目录**: payload 的 `prune_empty_dirs` (默认开) 为真且整理方式为移动时, 落盘后删除本次移动腾空的祖先目录. 自底向上, 只把 `ENOTEMPTY` 当作非空, 库根与 `.amane_trash` 不删; 复制 / 硬链接 / 符号链接不移走源文件, 不产生腾空目录. 删除与剪枝共用 `library/delete.py` 的执行单元 (字面路径边界, 不跟随符号链接, 不跨越挂载点).

## 清理清单

清单有两个产出方: `SCAN_INVALID` 的全库只读遍历, 与 `REFRESH` 整库扫描的顺带产出 (同一趟遍历, 媒体命中用于增删), 两者产出的条目集合相同. 回收目录展开由只读接口产出回收目录来源清单, 走同一套删除. `DELETE` 按清单执行, 清单是它唯一的路径来源:

- **进程内**: 存放挂在 `AppRuntime.inventory_store`, 按库与来源分组 (规则 / 回收目录 / 选中项展开各一组, 打开回收目录因此不挤掉正在复核的选中项清单) 保留最近四份, 24 小时过期; 不落库, 重启即丢. 库路径修改或库删除时丢弃该库清单.
- **残留目录**: 整棵子树只含附属文件 (NFO / 图片 / 字幕)、祖先的直接子项里没有媒体的目录, 登记为一条 `orphan` 容器条目 (`InventoryEntry.expandable`); 判定规则见 `library/cleanup/orphan.py::OrphanScan.verdict`. 容器自身不是删除目标 (执行时跳过, 删空后由剪枝回收目录), 子树内容一律登记为普通条目, 系统产物 (操作系统与同步工具生成) 带 `noise` 标记; 子树里本来就会成为条目的空子目录与命中文件黑名单的文件同样保留. 容器在父级只算一次 (条目数与大小由子项汇总). 库根与扫描范围目录不登记为条目, 它们直接子项里的附属文件按普通条目登记, 条件只按本层算 (库内别处有正片与这一层是不是残留无关). 判定为候选但没有登记的目录只按未识别文件一种计数 (`BlockedDirs.unexplained`), 与「读不到」的 `skipped_*` 分开.
- **触顶只丢条目**: 条目上限触顶后遍历照常走完, 只是不登记条目并记下未纳入的候选数. 同一趟遍历要给 `REFRESH` 收集媒体命中, 提前收工会让 `add + remove` 把磁盘上还在的文件当成失效; 截断与未纳入数在状态接口上可见.
- **执行集合 ⊆ 清单**: `DELETE` 携带清单标识与排除项 / 纳入项; 标识不存在、已执行、库不一致或库根已变更即失败, 不重新扫描、不重新生成清单. 两者都按路径分量匹配, 相对路径按清单库根解释, 不匹配任何条目的忽略; 互为祖先时按最深的一条判定 (同深时纳入优先) — 面板用「排除一个目录 + 纳入其中一项」表达「保留这个目录, 但删掉里面的某一项」, 用户刚点的那条总是更深, 只按「纳入优先」判会让再深一层的取消勾选失效. 清单在取到同库锁之后、动第一个目标之前即置为已执行: 半执行的快照留在面板上只会显示一批磁盘上已经没有的条目, 重跑它既不会恢复已删的也不会补上没删的; 预检失败 (标识不存在 / 库不一致 / 库根变更 / 抢不到锁) 不置位, 一次都没动盘时重试仍然合理. 已执行的清单在面板侧等同于不存在, 而按标识查找仍返回它, 供执行侧区分「不存在」与「已执行过」.
- **会变的事实在执行前复验**: 残留条目与空目录条目描述的事实 (目录里没有正片 / 目录是空的) 都会在清单的 24 小时有效期里失效, 因此执行前就地重算: 谓词复用 `library/cleanup/orphan.py`, 遍历另写在 `handlers/delete.py::_reverify_subtree`, 两处的判定顺序必须一致; 冷却期不参与复验. 残留条目按宿主容器的整棵子树加每一级祖先, 容器下的条目共用一次子树复验; 空目录条目只确认目录仍为空 (执行侧删目录是递归的). 不通过的条目不进删除集合, 按失败记账并单独计数 (`DeleteResult.reverify_rejected`); 目标已经不在磁盘上不算复验拒绝, 交给执行侧按「已不存在」记账. 只复验磁盘事实: 大小判定与库设置的变化不在其中, 设置改了应当重扫. 这是 `DELETE` 唯一会重算规则的路径, 其余条目只复验存在性与边界.
- **展开提示**: 选中项展开把未能纳入的项作为码与参数 (`kind` / `path` / `count` / `detail`) 返回, 文案由面板按界面语言给出; 截断与未纳入数只走响应的 `truncated` / `dropped`.
- **边界**: 目标须在清单记录的库根内, 没有例外 — 库外的一切 (链接模式下写在链接树的 strm 与产物) 既不进清单也不删除; 库根自身与 `.amane_trash` 自身 (含递归途中的该子树) 拒绝. 库内同时存在指向库外的符号链接时, 边界判定用字面路径, 不解析符号链接. 目录链接 (Windows 的 junction 与挂载点) 是唯一的例外形态, 三处单独处理: 遍历与选中项展开不进入 (宿主目录因此不判定为残留, 只计入未登记候选); 执行前对非链接目标复核真实路径, 真实路径在库根外即拒绝; 扫描之后才出现在残留子树里的按复验拒绝.
- **面板读取**: 状态接口只给状态与范围, 节点一律走 `inventory/nodes` 按 `offset`/`limit` 取一页 (顶层与下钻同一接口, 回收目录用响应里的 `path` 展开, 同一打开动作的重复请求复用上一次展开) — 一份清单可能有上万条候选, 整份下发会卡住面板. 清单内容生成后不变, 树因此只构建一次 (`library/cleanup/inventory.py::inventory_tree`). 节点路径一律 `/` 分隔, 面板按它做分量匹配; 折叠系统产物时 `has_children` 按过滤后的子节点算, 否则面板会给出展开后为空的行. 遍历设置与当前库配置不一致的清单按不存在处理: 它只覆盖了库的一部分, 面板不该照整库渲染. `will_be_empty` 由扫描自底向上算出, 而不是面板按清单条目推断 (有效媒体不是条目); 面板还要确认该子树里没有被排除的条目, 否则标记预告的是一个不会发生的结果.
- **不加库列**: 清单与剪枝开关都只存在于任务 payload, 没有需要从库里取真值的读取路径.

## 刮削期资源物化

`ScrapeHandler` 在聚合后、`upsert_metadata` 前调用 `materialize_images` (`src/amane/media/pipeline.py`) — 二进制写入 Resource 目录的主路径, 与是否整理无关:

- **`scraping.download_resources`** 控制本步下载哪些类型 (thumb / poster / extrafanart / trailer).
- **URL 重排契约**: 被下载类型的 URL 列表按本次下载成功 (含缓存命中) 稳定分区 — 成功者保序前置, 失败者保序沉底. 死 URL 不再占据首位, 但保留在尾部, 来源恢复后下次物化可重新尝试. 未选中下载的类型无成败信息, 保持聚合优先级原序; extrafanart 为站点分组 dict, 不重排. `raw` 快照保持站点原始数据.
- 裁剪海报 → 按 `scraping.poster_ratio` 从 thumb 右侧裁切; 超分就地覆盖. 失败不阻断刮削.

手动裁切复用同一派生通道 (`op=crop`, `args=box:…`), 见 [data-model.md](data-model.md).

## 图像超分

超分只在两处发生, serve 永不触发: scrape 期急切 (`sr.enabled` 时对低质本地副本) 与 UPSCALE 任务 (扫描 Resource, 补 `'sr' not in meta` 的低质图). **就地覆盖**: 不产生新 URL, 直接覆盖磁盘文件并在 `meta` 打 `'sr'`, 前端零感知; 预设只暴露两个, 屏蔽工具 / 模型 / 倍率; 二进制按需下到 `{data_dir}/tools/`.

## Worker 并发

并发上限 `worker.concurrency`. 上限是经验值: curl_cffi 浏览器指纹 + 多站点并发下过高易触发反爬.

### 暂停

进程内 `_paused`, 不写入 HotSettings. 暂停只停 `claim_next_task`, 循环仍在, 已认领的继续运行, 入队不受影响. `_rebuild()` 把 pause 复制到新 worker, 避免 PATCH 配置时意外恢复领队. 与退役不同: 退役不可逆且主循环退出.

### 取消

`AsyncWorker.cancel_task(task_id)` 通过给运行中的 asyncio task 注入 `CancelledError`; `AppRuntime.cancel_task` 依次尝试当前与退役 worker, 批量接口与助理 bridge 都经由它.

| 场景 | 安全性 |
| ------ | ------ |
| HTTP 请求中 | 安全 (curl_cffi 断连) |
| 文件 move / hardlink 中 | **不安全** (shutil 不响应 CancelledError) |
| DB 写入中 | 安全 (session 退出时回滚) |

文件操作中取消只能等操作完成后才能真正生效. 两个已知窗口: claim 的 commit 到登记之间取消不可达 (回退的状态条件写入不覆盖已终态, 任务仍可能执行); 登记后、首次调度前被取消由 done callback 补写终态.

### 配置变更与退役

配置变更不取消运行中任务. `_apply_rebuild_unlocked()` 构建新 worker 后调用旧 worker 的 `retire()`: 同步置 `is_main=False` 并唤醒轮询, 主循环退出且不再认领; 已认领任务继续运行; 新 worker 立即开始认领. 退役 worker 由后台 `drain()` 处理: 先等主循环退出 (认领结算并登记), 再等活跃任务清零, 顺序不可交换; 清零后释放其持有的 r18 句柄与浏览器池. 连续变更时多个退役 worker 可以并存.

归属边界是**认领开始时刻**: 变更时尚未结算的那次认领仍属于旧 worker, 每次退役至多带走一个旧配置任务; PATCH 返回后开始的认领属于新 worker. 过渡期总并发为新旧 worker 上限之和; 长期挂起的任务会延迟退役 worker 的资源释放.

### 关闭

`AppRuntime.stop_workers()` 顺序: 对全部 worker (当前与退役中的) `retire()` → 逐个有界等待主循环退出 (超时 `MAIN_LOOP_STOP_TIMEOUT` 后取消主循环) → 统一 `shutdown_active()` (`worker.shutdown_timeout` 是等待活跃任务自然完成的秒数, `0` 表示立即超时并 cancel) → 单次 `fail_all_running_tasks()` → await 后台释放任务. 

清扫必须只在全部 worker 处置完成后执行一次: `fail_all_running_tasks` 是全库操作, 逐个 worker 清扫会把其它 worker 的运行中任务标为失败, 其完成事务随后因状态不再是 RUNNING 而静默丢弃. 等待主循环退出须在清扫之前, 否则认领尚未提交的行可能晚于清扫提交而永久 RUNNING; 该行也可能因取消落在事务提交等待中而无法被清扫覆盖, 由人工取消处置. `stop_workers(closing=False)` 供测试夹具使用: 不置关闭态, 其余步骤相同.

## 即时提交与定时提交

**即时** (`POST /tasks`): 接收 `TaskSubmission` (含全部即时 type), 经 `resolve_submission` 得到 `(TaskType, Payload)` 后建 Task. REFRESH / ORGANIZE / SCAN_INVALID 只接受 `library_id`, resolve 时由 library 派生 `path` (submission 可显式覆盖) 与 `recursive` / `patterns`; ORGANIZE 还可带 `media_file_ids`, 与显式 `path` 互斥且不含扫描字段. SCRAPE 采用 number / media_id, 二者可同时提交; **`content_type` 可空**, 为空时仅 media_id 按文件路径解析, 有 number 时按番号推断. 覆盖只作用于这一次 `POST /tasks`. ACTOR_SCRAPE 采用 `actor_id`.

**定时** (`Schedule`): 仅接受 `RoutineSubmission` (`cleanup` / `upscale` / `r18_import` / `rescrape`). 触发时由 `CronScheduler` 用对应 Payload 的 `model_validate` 入队; 编辑只修改 name / cron / enabled, 修改任务类型或 payload 须删除后重建 (见 [api.md](api.md)).

## ACTOR_SCRAPE

`ActorScrapeHandler` 按 `HotSettings.actor_scraping` 的资料来源 / 头像来源顺序获取 (见 [config.md](config.md)), **先按 `Actor.gender` 与各站 `profile().genders` 过滤** (`unknown` 只请求同时覆盖两性的站; 被裁站不发 HTTP、不消费其 raw 缓存). 站点内按查找名首命中; 聚合是标量填空 (含 `gender`, `unknown` 当空) + 头像优先, 无影片字段 DAG. `use_cache` 与影片同型: 含 `metadata` 时按**已允许**站复用 `Actor.raw` 跳过爬虫 (非法快照降级为重爬). 写回时再与库内已有人物字段填空合并, 避免冲掉已填值.

**链式自动触发**: `actor_scraping.auto_scrape` 开启 (默认) 时, 影片 SCRAPE 成功后在 `ScrapeHandler` 末尾按清洗解析后的 `meta.actors` 查询 Actor 实体, **`Actor.raw` 非空 (已刮过) 则跳过**, 其余以 **`priority=-1`** 入队 (不抢占影片任务优先级); 同 `actor_id` 已有 queued / running 时复用入队互斥. 链式块内异常只记录 warning, 不阻断刮削主流程.

## CLEANUP 悬空引用回收

Metadata 是一等公民, CLEANUP **从不**因「无关联 MediaFile」删除 Metadata. 两个独立开关: `remove_missing_files` 删路径在磁盘上不存在的 MediaFile 索引行; `remove_unreferenced_resources` 删不被任何 Metadata / Actor 媒体 URL 字段引用的 Resource. 存活引用从 `poster_urls` / `thumb_urls` / `trailer_urls` / `extrafanart_urls` 与 `Actor.image_urls` 收集, 匹配规则见 [data-model.md](data-model.md) Resource 一节.

## 调度器与监控

- `CronScheduler`: 每 60s 扫描启用的 `Schedule`, 按 `RoutineType` 入队.
- `WatcherService`: 文件系统事件 + CloudDrive webhook; 契约见 [watcher.md](watcher.md).
- `FeedService`: 远程 RSS / Atom 发现, 按每源 `next_fetch_at` 到期拉取; `auto_enqueue` 时入队 by-number SCRAPE. **不是** Schedule / Routine, 契约见 [feeds.md](feeds.md).

三者分属独立循环 (秒级反应、分钟级 routine、每源间隔的远程拉取), 合并会互相拖高 latency.

**UPSCALE 例行任务**: 扫描全部 `Resource`, 对低质且未超分的就地超分, `limit` 限单次批量.

**RESCRAPE (滚动补刮)**: 与 `RefreshHandler` 同构的 fan-out — 批量任务只选目标并下发既有刮削. `targets` (`metadata` / `actor`) 每个已选项各自取一批旧条目, 以 `priority=-1` 入队非 force 任务 (见 `handlers/rescrape.py`). 复用 per-site raw 快照仅补缺失站点, 聚合阶段重放当前配置, 因此同时承担「配置变更后再次运行生效」; 它与 SCRAPE 成功后的链式 ACTOR_SCRAPE 正交 (链式跳过 `Actor.raw` 已非空的演员).

Watcher 的 `automation` 三档都不自动 ORGANIZE / DELETE, 归属随事件携带, 见 [watcher.md](watcher.md) / [data-model.md](data-model.md).
