# 数据模型

> 提交: `162e14f`
>
> 表结构、字段类型、便捷属性都在 `src/amane/db/models.py`. 本文只解释**为什么**这么建模、所有权关系、生命周期与已知陷阱.

## 数据所有权

**SQLite 是唯一数据源**, NFO/海报/fanart 等磁盘文件都是从 DB 派生的副产物 (兼容 Emby/Jellyfin/Kodi). 重建顺序: DB → 派生文件, 不反向 — 不要写"从 NFO 反推 metadata"的逻辑.

实体边界:

| 实体 | 角色 | 唯一键 |
|------|------|--------|
| `MediaFile` | 磁盘上视频文件的索引 | `path` UNIQUE |
| `Metadata` | 番号级聚合元数据 | `number` UNIQUE (**大小写不敏感**; 存库保留首次写入的原始大小写) |
| `Resource` | URL 级下载缓存 | `url` UNIQUE |
| `Task` | 持久化任务队列 | `id` |
| `Library` | 媒体库: 单根目录 + 路径模板 + 整理放置方式 + 自动化级别 | `id` |
| `Feed` | 远程 RSS/Atom 发现源 (间隔与刮削属性按源绑定; 分组是字符串伪路径, 不建目录表) | `url` UNIQUE |
| `FeedItem` | 某源曾见过的条目 (去重 + 历史 + 阅读器正文快照) | `(feed_id, item_key)` UNIQUE |
| `Schedule` | cron 触发器 | `id` |
| `Actor` / `Director` | 人物一等实体 (`Actor` 承载人物元数据; `Director` 预留) | `name` UNIQUE |
| `Tag` / `Studio` / `Publisher` / `Series` | 爬取侧分类目录 | `name` UNIQUE |
| `UserTag` | 用户自定义标签 (与爬取 `Tag` 隔离) | `name` UNIQUE |
| `Comment` | 挂在 Metadata 上的用户评论 | `id` |

`MediaFile` 与 `Metadata` 解耦: **Metadata 是一等公民** (用户直接管理的番号级条目), 有效性不依赖本地文件. `MediaFile` 是磁盘视频的索引; 当文件能对应到某条 Metadata 时挂上 `metadata_id` (多对一). 也可以存在 `metadata_id IS NULL` 的文件 (解析失败 / 尚未刮削), 以及**没有任何 MediaFile 的 Metadata** (by-number 刮削、只囤元数据等) — 二者都是常态, 不是待清理的"孤儿".

`Metadata.number` 的唯一约束与 `get_metadata_by_number` / `upsert_metadata` 查重均忽略大小写; 命中已有行时不改写库内 `number` 字符串 (保留首次写入的大小写). 新建时按调用方传入原样落库.

## Library 归属 (强关系)

与 Emby Library 概念对齐: 一个 Library = 一个根目录 + 一组路径模板 + 整理放置方式 (`move_mode`) + 自动化级别 (`automation`: none / watch / scrape) + `trailer_pattern`. 库始终有效, `automation` 只控制发现侧: 不监控、仅登记、或登记后自动刮削. **自动整理尚未开放**, 落盘只由手动 ORGANIZE. **每个 `MediaFile` 必须持久关联到唯一 Library** (`MediaFile.library_id` 非空 FK). 库目录落盘只由 ORGANIZE 执行 —— 读 `media_file.library_id` 取模板与放置方式, 是归属的唯一真值来源. SCRAPE 用 `media_file_id` 只作查询输入 (番号 / oshash) 与刮削后回写关联, 不移动文件.

**强 FK 的意义**: 归属在文件**入库时确定一次**, 同一文件经任何入口行为一致:

- watcher: 每个监控根绑定 `library_id`, 文件事件携带来源库 (见 `scheduler/watcher.py` 的 `_Handler.library_id`).
- scan: 任务在某个 library 下运行, payload 自带 `library_id`.
- 手动 by-number scrape / RSS 发现: 与文件无关的纯查询, 无归属.

**1:1 设计** (一库一根目录) 与 **不重叠**由用户保证 (不强制校验): 正常不会在父子目录各建一个 library. 后续若需一库多目录, 需引入 LibraryFolder 子表.

## 多源字段保留策略

| 字段类型 | 存储方式 | 设计动机 |
|----------|---------|----------|
| 标量 (`title`, `studio`, `plot`, ...) | 单值, 聚合按字段优先级选首个非空源 | 简单, 覆盖大多数场景 |
| URL (`poster_urls`, `thumb_urls`, ...) | list, 聚合按优先级, 物化后按下载成功重排 (见 [task-system.md](task-system.md)) | 下载时顺序尝试, 某站失效不需重新刮削 |
| `extrafanart_urls` | `dict[site, list[url]]` 按站点分组 | 剧照集合是站点特异的, 扁平合并丢上下文 |
| `scores` | `dict[site, score]` | 不同评分体系 (5分 vs 100分), 保留来源让前端分别展示 |
| `raw` | `{site: {field: value}}` 原始快照 | 离线重新聚合: 改了优先级/翻译规则不需重爬; 也作 per-site 复用来源 (见 [task-system.md](task-system.md) 站点级复用) |

### `field_sources`

`{field_name: site_name}` 仅记录**标量字段**的来源. URL/extrafanart/scores 自带来源结构, 不写入. 用途: 调试多源不一致 + 前端展示来源. 不参与业务逻辑, 重新刮削后被覆盖.

`raw` 的字段名/类型必须与当前 `MediaMetadata` 一致 — 它会被站点级复用直接反序列化. 模型改名或改类型时, 结果列与 raw 是两份数据, 需单独的 data migration (见 [database.md](database.md) Autogenerate 盲区).

## 可写面与 req↔repo 兼容性

更新一条记录时存在三个模型, 字段集呈包含关系: **req model (对外) ⊆ repo 入参 TypedDict (对内) ⊆ DB 列**.

- **DB 列**: ground truth, 全部可持久化字段.
- **repo 入参 TypedDict** (`MetadataFields` / `MediaFileUpdates` / `LibraryUpdates` / `ScheduleUpdates`, 在 `src/amane/db/repo_types.py`): repo update 方法接受的内部可写面. 比 DB 列窄 (排除主键/时间戳), 但比对外面宽 —— 含仅后端可写字段 (如 `Metadata.raw`/`field_sources` 由刮削写入, `Schedule.next_run`/`last_run` 由调度器维护).
- **req model** (`src/amane/api/models/`): 对外可写面, 经 `create_partial_model(DBModel, ignore_fields=...)` 从 DB 模型派生. `ignore_fields` 排除只读列与仅后端可写字段, 从模型上**彻底移除**这些字段, 阻断外部经 API 越权赋值 (如 POST `id`/`raw`).

**安全性如何保证** (无运行时反射):

1. repo update 方法**显式逐字段赋值** (`if "x" in updates: obj.x = updates["x"]`), 不用 `setattr`. 字段名拼写与类型兼容性由静态类型检查保证 —— TypedDict 与 DB 列若漂移会直接编译期报错.
2. req model 由 DB 模型派生, 故 req↔DB 的字段/类型兼容性由 `create_partial_model` 的构造保证, 只需验证该函数正确 (`tests/api/test_schema_repo_compat.py`). 该文件 `TestRepoRoundTrip` 用独立文件库, 不经 api/conftest 的 `repo` (源自 app lifespan): lifespan 启动的 `FeedService` 会并发 poll 测试新建的 Feed, 其随机 `next_fetch_at` 是历史时刻 (立即到期), 拉取失败后时间列被覆盖为当前时间 — 与 DB 往返断言竞态. 序列化保真与后台服务无关, 必须隔离.
3. 端点把窄的 req `model_dump` 结果传入宽的 repo 方法; 二者同源派生, 转换天然安全. 唯一运行时缝隙 (req 键须 ⊆ TypedDict 键, 否则多余键被 repo 静默丢弃) 由字段纪律测试兜底.

PATCH 三态: **省略键** = 不更新 (`exclude_unset`); **显式值** = 写入; **显式 `null`** 仅当源列本就可空时表示清空. 源列非 Optional (如 `Library.patterns: list[str]`) 时显式 `null` 由 `create_partial_model` 拒绝 (422). 空 glob 的合法写入是 `[]`.

`create_partial_model` 的 `ignore_fields` 依赖 "生成模型不继承源字段" 才能真正移除字段, 因此仅对 `table=True` 的 SQLModel 有效 (派生时基类换为 `SQLModel`, 断开 `InstrumentedAttribute` 继承); 对普通 `BaseModel` 传 `ignore_fields` 会显式报错, 以免被忽略字段经继承泄漏. 源字段上的 Annotated 校验器 (如 `AfterValidator`) 会带到 PATCH 模型; 未提供的字段保持 `None`, 不跑源校验.

## 删除级联

| 操作 | 级联行为 | 注意 |
|------|---------|------|
| `MediaFile` 删除 | 不级联 Metadata | Metadata 是一等公民; 文件索引消失不影响元数据条目 |
| `Metadata` 删除 | **nullify** `MediaFile.metadata_id`, 状态回 `PENDING` | 应用层级联 (`delete_metadata`); 文件本身保留, 可再刮削 |
| `Library` 删除 | **级联删除** `MediaFile` | 应用层级联 (`delete_library` 先 flush 删子表再删库, 无 ORM relationship). 仅删 DB 索引, 不动磁盘文件. 路由层同时 `remove_library` 停止监控 |
| `Feed` 删除 | **级联删除** `FeedItem` | 应用层级联 (`delete_feed`). 已入队的 SCRAPE / Metadata 不受影响 |
| `Resource` 清理 | CLEANUP 回收未引用 | 扫全部 Metadata 的媒体 URL 字段, 删不被引用的 Resource (文件+行). 非 LRU |
| 文件 move/hardlink 后 | `MediaFile.path` 由 ORGANIZE handler 更新 | 外部直接挪文件不触发更新, 由 watcher 检测. ORGANIZE 落盘前会清掉本库磁盘已不存在的索引, 避免碰撞名被幽灵行占用 |

## Library 整理布局

每个 Library 持有整理时的放置方式 (`move_mode`: move / copy / hardlink / symlink)、一组路径模板 (`video_template`, `thumb_template`, `nfo_template`, ...)、以及整理默认 (`write_nfo`、`copy_resources`). 放置方式与默认按库区分, 同一进程里各库可以不同. `copy_resources` 与刮削热配置 `scraping.download_resources` 共用 `DownloadableResource` 枚举, 但互不读写 — 刮削控制进 Resource 目录, 整理控制复制到库路径. ORGANIZE payload 上对应字段为 `None` 时沿用库设置, 非空则只覆盖该次任务.

`trailer_pattern` 只在库上: 对**文件名 (含扩展名)** 做正则搜索, 命中则 REFRESH / ORGANIZE 扫描与 watcher 都不把该文件当正片入库. 空串关闭跳过. 非法正则在写入时拒绝 (422). 默认与预告片模板文件名 `{video_dir}/trailer.mp4` 对齐.

分集 (CD) 后缀: `cd_suffix_template` (默认 `-CD{cd}`) 只在**视频文件名**上生效 — ORGANIZE 时从源文件名 `parse_file_info` 检测分集 (识别 `-CD{n}` / `-PART{n}` / `-A` / `-B` / 尾部位数 `-1`–`-9`; `-0` 无意义, 零填充与两位尾数 (`-01` / `-10`) 会与合法番号撞车, 均不识别), 非 None 且模板非空则在扩展名前追加渲染结果; 空串关闭. 裸数字识别与番号提取一致: `MIDV-123-1` 的番号仍是 `MIDV-123`, 尾部 `-1`–`-9` 本就是分集语义. 模板只允许恰一个 `{cd}` 占位符, 不允许路径分隔符 (写入时 422). 侧车模板基于 `{video_dir}` (父目录), 不受 CD 后缀影响. **幂等约束**: 渲染后的格式须保持可被同一检测逻辑反推 (如 `-CD1`, `-Part1`), 否则该文件二次整理会因检测不到分集而丢失后缀 — 当前只文档约束, 不加验证. 检测只做在 ORGANIZE 时, 不落库.

模板**故意按资源类型独立**, 而不是一个 `output_dir` + 后缀拼接 — 用户场景包括: NAS 多盘分存、字幕集中备份、NFO 同目录 vs 集中目录.

模板渲染在 `organize/path_templates.py::resolve_paths`. 占位符分相位: metadata 来自 Metadata 字段; `{dir}` / `{dir_path}` (源文件目录名 / 完整路径) 与 `{mosaic}` / `{definition}` (`parse_file_info` 从源路径检测) 只在 ORGANIZE 时注入, 不落库 (与 CD 检测同一约定); `{video_dir}` 为视频渲染后的父目录. `{mosaic}` 无标记时按 content_type 兜底为 `censored` (永不 `Unknown` — 有码/无码是全域语义, 保证目录名稳定), `{definition}` 无命中回退 `Unknown` (与普通占位符一致). **幂等约束** (与 CD 后缀同一条): 文件相位标记必须保留在渲染后的文件名段 — 若模板只把标记放目录段, 二次整理会因文件名不再含标记而按默认值重排. 占位符相位与默认值由同模块导出, 经 `GET /api/libraries/path-template-schema` 下发, 前端不硬编码变量表.

普通占位符缺失回退 `Unknown`. `{dir}` / `{dir_path}` 无 `source_path` 时为空串 — **空变量放模板首段** (如 `{dir}/...`) 会让结果以 `/` 开头被当成绝对路径.

**逃逸防护**: 相对模板必须是 library 根的后代; 绝对模板 (含展开 `{video_dir}` / `{dir_path}` 后变绝对) 必须落在 library 根或 `safe_dirs` 下, 否则 `ValueError`. 多盘分存要求目标盘在 `safe_dirs` 内.

## Resource (一等存储, 非缓存)

`Resource.url` UNIQUE, 作**通用 locator key**:
- 原始外部图: `url` = 真实外部 URL (dedup 天然), `meta` 默认为 `{}`.
- 派生裁剪: `url` = 合成串 `derived:{sha256(src_url)}:crop:{args}` (两层 hash, `resource_store.derived_locator`).
  `args` 两种形态共存于同一 `op=crop`: 自动右侧比 (如 `0.7000`) 与手动像素框 (`box:L,T,R,B`, 相对源图**当前**文件像素, 含就地超分后; right/bottom 不含).
  外部不存在, metadata 以 `/api/resources/{url_hash}` 引用. 手动裁切写入派生 Resource 并替换 `poster_urls`, **不**改库路径海报 (ORGANIZE 再复制).

`file_path` 是相对于 `{data_dir}/resources/` 的两级散列路径 (`a3/a3f1c2....jpg`) — 文件名即 `sha256(url)[:16]`.

`meta` (JSON, 默认 `{}`) 在派生/被处理资源上记录可逆来源 + 处理标记:
- 裁剪: `{'op':'crop','src':源url,'args':str}` (`args` 即 locator 中的参数串).
- 任意资源被超分后追加 `{'sr':{tool,model,scale}}` — 超分**就地覆盖**文件 (URL 不变), `'sr' in meta` 即去重依据.

**一等存储, 按引用回收**: Resource 不是 LRU 缓存. 刮削换 URL / 改裁剪参数后, 旧条目会留在库里直到 CLEANUP 的 `remove_unreferenced_resources` 扫 Metadata 媒体 URL 字段并删除未引用项 (含派生). 要原始像素须 invalidate 重下 (就地超分丢弃了原像素). CLEANUP 另可删磁盘失效的 MediaFile 索引; **从不因缺少 MediaFile 而删 Metadata**.

`content_hash` (SHA-256) 完整性校验, 就地超分后更新; 作 ETag 的约定见 [api.md](api.md).

## 分类索引 (爬取侧投影)

`Metadata` 上的 `actors` / `tags` / `directors` (JSON list) 与 `studio` / `publisher` / `series` (标量) **仍是刮削聚合、NFO、路径模板的真值来源**. 分类实体表 + 关联表是**查询投影**: `upsert_metadata` / `update_metadata` 写入后由 `_sync_metadata_facets` 重建 (按 name get-or-create; list 字段带 `position` 保序).

写入时先清洗 `Metadata.actors` 的 `name(alias1, alias2)` 形式 (`clean_actor_names`, 纯拆分器 `split_actor_aliases`): 规范名留真值, 别名并入对应 `Actor.aliases`. 落袋目标经 actor 规则单跳解析 — 规范名被 alias 规则映射时别名随目标实体走, 被 block 时不落袋, 因此不留孤儿实体. `Metadata.actors` 存库始终是规范名; 站点 `raw` 快照保留原始带括号形式, 重刮时重新清洗.

用户对爬取侧分类的改名 / 合并 / 删除意图落在 `FacetRule` (按 `(kind, source_name)` 唯一), **不**改投影表本身:

| action | 含义 |
|--------|------|
| `alias` | 源名映射到目标名; **表内保持单跳规范形** (写入时压缩入边, apply 不递归) |
| `block` | 源名永久剔除; 指向该名的 alias 入边一并压成 block |

`upsert_metadata` / `update_metadata` 在 `_sync_metadata_facets` 之前对六个分类真值字段跑规则 (不改 `raw`). 目录 API: rename/merge 写 alias 并改已有 Metadata; delete 写 block、从真值剔除后删实体. `user_tag` 与刮削隔离, 硬删且不进规则表.

- 名称大小写敏感, 与源站原样一致, 不做模糊合并.
- `Actor` / `Director` 为一等实体: 无影片关联时**不自动删除**. 用户显式删除时实体删除并拉黑.
- 删 `Metadata` 时清理关联/评论/用户 tag 挂载; 人物与目录实体本身保留.

### 演员身份与人物元数据

身份是**别名映射 + 合并**, 不是独立身份图. 四层分工:

| 层 | 角色 |
|----|------|
| `Metadata.actors` | 影片刮削/NFO 真值 (经规则后的规范名) |
| `FacetRule` | 用户认定的跨名映射 (日/罗/中/旧艺名); rename/merge/alias/block |
| `Actor.id` | 人物宿主 (任务、关联、人物字段); `name` UNIQUE 规范名 |
| `Actor.aliases` | 別名袋 (查找/展示): 档案刮削与影片演员名清洗入袋; **不**承担跨名映射 |

`Actor` 另存人物元数据 (`gender` / 生日/身材/简介/`image_urls`/`provider_ids`/`source_urls`/`raw` 等). **`gender`**: `female` / `male` / `unknown` (默认); `unknown` 视为标量空位, 可被刮削填空或手改覆盖. **`birthday`** 与影片 **`Metadata.release`** 同为日历日字符串 **`YYYY-MM-DD`** (爬虫与 `PATCH` 写入前规范化; 源站 ISO 日期时间只保留日). **`image_urls[0]` 为主图** (详情/头像墙); 列表与各站 `raw` 并存, 用户可编辑规范列表次序.

刮削查找键 (有序去重): `name` → 入边 alias 的 `source_name` → `Actor.aliases`; 站内首命中即用. 档案刮削**不改** `Actor.name`: 各站 `ActorMetadata.name` 与 `aliases` 一并入別名袋, 写回时丢掉与规范名相同的项 (站点中文显示名如 javdb `筧純` 不会盖掉已认定的 `鷲尾めい`). 多站 `source_url` 聚合为 `source_urls` (site→url, 先到先得), 与影片 `Metadata.source_urls` 同形. 实体 merge 删源前须把人物字段填空并入 target (`merge_person_fields_into_target`: 标量填空; aliases/image_urls/provider_ids/raw 并集), 保留 target id. 刮削发请求前按 `Actor.gender` 与站点性别覆盖裁站 (见 [crawlers.md](crawlers.md) / [task-system.md](task-system.md)). CLEANUP 扫 Resource 存活引用时, 除 Metadata 媒体 URL 外还计入 `Actor.image_urls`.

## 用户注解 (与爬取隔离)

`UserTag` + `MetadataUserTag`、`Comment` 挂在 Metadata 上. 刮削路径**绝不触碰**.

## 已知限制

- SQLite + batch mode 改大表会重建表 → 数百万行会卡. 个人规模可接受, 超过需切 PostgreSQL.
- `Task.payload` / `Task.result` 是 JSON dict, 无 schema 强制 — 由 handler 的 Pydantic 模型在反序列化时校验 (见 [task-system.md](task-system.md)).
- `Schedule.payload` 存的是 `RoutineSubmission` 的 JSON (`cleanup` / `upscale` / `r18_import` / `rescrape`); cron 触发时再构造对应 Payload 入队. 不支持在线改任务内容 — 改 type/payload 需删除后重建. 详见 [task-system.md](task-system.md).
