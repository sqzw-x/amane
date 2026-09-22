# 数据模型

> 表结构、字段类型、便捷属性见 `src/amane/db/models.py`. 本文记录所有权、生命周期、可写字段与仍生效的禁止事项.

## 数据所有权

**SQLite 是唯一数据源**, NFO / 海报 / fanart 等磁盘文件是从 DB 派生的副产物 (兼容 Emby / Jellyfin / Kodi). 重建顺序为 DB → 派生文件, 不反向; 不允许从 NFO 反推 Metadata.

实体边界:

| 实体 | 角色 | 唯一键 |
|------|------|--------|
| `MediaFile` | 磁盘上视频文件的索引 | `path` UNIQUE (**NFC**; 目录列出的 NFD 与 NFC 是同一路径) |
| `Metadata` | 番号级聚合元数据 | `number` UNIQUE (**大小写不敏感**; 存库保留首次写入的原始大小写) |
| `Resource` | URL 级下载缓存 | `url` UNIQUE |
| `Task` | 持久化任务队列 | `id` |
| `Library` | 媒体库: 根目录 + 路径模板 + 整理放置方式 + 自动化级别 | `id` |
| `Feed` | 远程 RSS / Atom 发现源 (间隔与刮削属性按源绑定; 分组是字符串伪路径, 不建目录表) | `url` UNIQUE |
| `FeedItem` | 某源曾见过的条目 (去重 + 历史 + 阅读器正文快照) | `(feed_id, item_key)` UNIQUE |
| `Schedule` | cron 触发器 | `id` |
| `Actor` / `Director` | 人物一等实体 (`Actor` 承载人物元数据; `Director` 预留) | `name` UNIQUE |
| `Tag` / `Studio` / `Publisher` / `Series` | 爬取侧分类目录 | `name` UNIQUE |
| `UserTag` | 用户自定义标签 (与爬取 `Tag` 隔离) | `name` UNIQUE |
| `Comment` | 绑定于 Metadata 的用户评论 | `id` |

`MediaFile` 与 `Metadata` 解耦: **Metadata 是一等公民** (用户直接管理的番号级条目), 有效性不依赖本地文件; `MediaFile` 是磁盘视频的索引, 能对应到某条 Metadata 时以多对一绑定 `metadata_id`. `metadata_id IS NULL` 的文件 (解析失败 / 尚未刮削) 与**没有任何 MediaFile 的 Metadata** (by-number 刮削、只囤元数据) 都是常态, 不是待清理的对象.

文件相位 (`content_type` / `mosaic` / `has_subtitle` / `definition`) 是 **path 的投影**, 只落在 `MediaFile`: 创建与修改 path 时用同一次 `parse_file_info` 回填, 不纳入对外 PATCH; `cd` 只用于 ORGANIZE 分集配对, 不落库. `content_type` 是番号 / 目录的内容类型 (决定刮削路由), `mosaic` 是这份文件的马赛克标记 (有码 / 无码 / 破解 / 流出); 词表未命中时按内容类型兜底 (有码 → `censored`, 无码 → `uncensored`, 国产 / FC2 / 欧美保持空), 已有的破解 / 流出 / 无码标记不覆盖. 无码展示与筛选是 `mosaic=uncensored OR content_type=uncensored`. `ContentType.chinese` 是国产, 不是中字 — 中字只依据 `has_subtitle`. Metadata 列表的角标与筛选经由关联 EXISTS / 页级聚合 (`file_phase`): 任一挂载文件具备即亮, `definition` 取最高档; 没有挂载文件的 Metadata 不命中这些筛选. `{mosaic?}` 输出判定后的 mosaic, `{content_type}` 输出内容类型.

ORGANIZE 复制到库路径的 poster / thumb 在 `watermark.enabled` 时按**源文件** FileInfo 叠 PNG 角标, 不修改 Resource 原图与 fanart; 尺寸与角位见 [config.md](config.md) `watermark`.

## Library 归属

与 Emby Library 概念对齐: 一个 Library = 一个根目录 + 一组路径模板 + 整理放置方式 (`move_mode`) + 自动化级别 (`automation`: none / watch / scrape) + 发现通道 (`ingest`: native / clouddrive) + 跳过规则. `automation` 只控制发现侧 (不监控 / 仅登记 / 登记后自动刮削); `ingest=clouddrive` 必填 `cloud_path` 且不挂 watchdog Observer, 契约见 [watcher.md](watcher.md). **自动整理尚未开放**, 落盘只由手动 ORGANIZE 执行.

**每个 `MediaFile` 必须持久关联到唯一 Library** (`MediaFile.library_id` 非空 FK). 归属在文件**入库时确定一次**, 入口行为一致: watcher 按监控根绑定的 `library_id`, scan 按 payload 自带, 手动 by-number scrape / RSS 发现与文件无关因而无归属. 库目录落盘只由 ORGANIZE 执行 — 读 `media_file.library_id` 取模板与放置方式, 是归属的唯一真值来源; SCRAPE 用 `media_file_id` 只作查询输入与回写关联, 不移动文件.

一库一根目录, 目录不重叠由用户保证 (不强制校验); 不提供一库多目录, 多根须另建子表.

## 多源字段保留策略

| 字段类型 | 存储方式 | 取舍 |
|----------|---------|----------|
| 标量 (`title`, `studio`, `plot`, ...) | 单值, 聚合按字段优先级选首个非空源 | 覆盖大多数场景 |
| 聚合类 URL (`poster_urls`, `thumb_urls`, ...) | list, 按字段站点顺序拼接各站非空值, 物化后按下载成功重排 (见 [task-system.md](task-system.md)) | 下载时顺序尝试, 某站失效不需重新刮削 |
| `extrafanart_urls` | `dict[site, list[url]]` 按站点分组 | 剧照集合是站点特异的, 扁平合并会丢失站点上下文 |
| `scores` | `dict[site, score]` | 不同评分体系 (5 分 vs 100 分) 需保留来源供前端分列展示 |
| `raw` | `{site: {field: value}}` 原始快照 | 支持离线重新聚合与站点级复用 (见 [task-system.md](task-system.md)) |

长文本 (`plot` / 演员 `overview`) 存**纯文本**: 上游的 HTML 片段、HTML 实体、异体空白 (NBSP / 全角空格)、XML 非法字符在入库前由 `utils.text::normalize_long_text` 收成一种表示 — HTML 换行与段落变成 `\n` 与空行. 归一在两处发生且必须幂等: 聚合出口 (`aggregate/engine.py::_fetch_one`, 含快照复用分支) 与落库写入 (`db/repos/metadata.py` 的两个写方法, 覆盖 merge / REST PATCH / Agent 工具). 因此 `raw` 快照、merge 输入与库内值是同一份规范文本; 消费端 (NFO 写出、前端) 不识别 HTML. 存量行在下次写入时被清理.

### `field_sources`

`{field_name: site_name}`, 仅记录**标量字段**的来源; 聚合类字段自带来源结构, 不写入. 用途是调试多源不一致与前端展示来源, 不参与业务逻辑, 重新刮削后被覆盖.

`raw` 的字段名 / 类型必须与当前 `MediaMetadata` 一致 — 站点级复用会把它直接反序列化. 模型改名或改类型时, 结果列与 raw 是两份数据, 需单独的 data migration (见 [database.md](database.md) Autogenerate 盲区).

## 可写字段与 req↔repo 兼容性

更新一条记录时存在三个模型, 字段集呈包含关系: **req model (对外) ⊆ repo 入参 TypedDict (对内) ⊆ DB 列**.

- **DB 列**: ground truth, 全部可持久化字段.
- **repo 入参 TypedDict** (`src/amane/db/repo_types.py`): repo update 方法接受的内部可写字段. 比 DB 列窄 (排除主键 / 时间戳), 但比外部可写字段宽 — 含仅后端可写字段 (`Metadata.raw` / `field_sources` 由刮削写入, `Schedule.next_run` / `last_run` 由调度器维护).
- **req model** (`src/amane/api/models/`): 对外可写字段, 经 `create_partial_model(DBModel, ignore_fields=...)` 从 DB 模型派生. `ignore_fields` 把只读列与仅后端可写字段从模型上**彻底移除**, 阻断外部经 API 越权赋值; 非 DB 列的可写字段 (如 `Actor.aliases` 别名行) 经 `extra_fields` 显式纳入, 同样 partial 化且显式 `null` 被拒.

**可写字段约束** (无运行时反射):

1. repo update 方法**显式逐字段赋值**, 不用 `setattr`; 字段名与类型兼容性由静态类型检查保证, TypedDict 与 DB 列漂移会直接编译期报错.
2. req↔DB 的字段 / 类型兼容性由 `create_partial_model` 的构造保证, 只需验证该函数正确 (`tests/api/test_schema_repo_compat.py`). 手写且必须是某表列字段子集的响应模型用 `@subset_of(..., covariant=)` 在导入时校验.
3. 端点把窄的 req `model_dump` 结果传入宽的 repo 方法; 唯一运行时缝隙 (req 键须 ⊆ TypedDict 键, 否则多余键被 repo 静默丢弃) 由字段纪律测试兜底.

PATCH 三态: **省略键** = 不更新 (`exclude_unset`); **显式值** = 写入; **显式 `null`** 仅当源列本就可空时表示清空. 源列非 Optional 时显式 `null` 由 `create_partial_model` 拒绝 (422). 空 glob 的合法写入是 `[]`.

`create_partial_model` 的 `ignore_fields` 依赖「生成模型不继承源字段」才能真正移除字段, 因此仅对 `table=True` 的 SQLModel 有效; 对普通 `BaseModel` 传 `ignore_fields` 会显式报错, 以免被忽略字段经继承泄漏.

## 删除级联

| 操作 | 级联行为 | 后果 |
|------|---------|------|
| `MediaFile` 删除 | 不级联 Metadata | Metadata 是一等公民; 文件索引消失不影响元数据条目 |
| `Metadata` 删除 | **nullify** `MediaFile.metadata_id`, 状态回 `PENDING` | 应用层级联 (`delete_metadata`); 文件本身保留, 可再刮削 |
| `Library` 删除 | **级联删除** `MediaFile` | 应用层级联 (`delete_library` 先 flush 删子表再删库, 无 ORM relationship). 仅删 DB 索引, 不动磁盘文件; 路由层同时 `remove_library` 停止监控 |
| `Feed` 删除 | **级联删除** `FeedItem` | 应用层级联; 已入队的 SCRAPE / Metadata 不受影响 |
| `Resource` 清理 | CLEANUP 回收未引用 | 扫描全部 Metadata 媒体 URL 字段与 `Actor.image_urls`, 删不被引用的 Resource (文件 + 行). 非 LRU |
| 文件 move / hardlink 后 | 路径仍在本库内则 ORGANIZE 更新 `MediaFile.path`; 已不在本库内且源路径不在磁盘上则删除该行 | 外部直接挪文件不触发更新, 由 watcher 检测. 见 [task-system.md](task-system.md) 落盘执行 |

## Library 整理布局

每个 Library 持有 `move_mode` (move / copy / hardlink / symlink)、一组按资源类型独立的路径模板与整理默认 (`write_nfo` / `copy_resources`), 因此同一进程里各库可以不同. `copy_resources` 与刮削热配置 `scraping.download_resources` 共用 `DownloadableResource` 枚举但互不读写 — 前者控制复制到库路径, 后者控制写入 Resource 目录. ORGANIZE payload 上对应字段为 `None` 时沿用库设置, 非空则只覆盖该次任务.

`trailer_pattern` 只在库上: 对**文件名 (含扩展名)** 做正则搜索, 命中则 REFRESH / TRASH 扫描与 watcher 都不把该文件当正片入库; 空串关闭. `min_file_size` (字节, 默认 0 关闭) 只过滤**扫描视频**: 后缀须属于该次扫描的视频扩展名白名单; 图片 / NFO / 字幕不适用, `.strm` 是路径指针也不参与判定; 软链接跟随目标比较真实体积, 否则已整理的入口会被当作广告. 低于阈值与黑名单同语义: REFRESH / watcher 不入库, TRASH 移入 `.amane_trash`; stat 失败 (含悬空链接) 视为不匹配.

`blacklist_patterns` (正则列表) 与预告片同属「文件名匹配即跳过」, 语义差别在 TRASH:

- 命中文件被 TRASH 移入本库 **`.amane_trash`** (固定保留名, 恒为物理移动, 不受 `move_mode`), 移动后删除其 `MediaFile` 记录; 未纳入索引或不匹配 glob 的文件仍执行回收.
- 预告片只跳过不动 — 它是模板产物, 属于库内容.
- `.amane_trash` 是保留目录: 目录本身与任意深度下级路径在任何扫描 / 监控中都恒被忽略, 否则回收内容会被再次注册; 手动移出则被当作新文件重新入库.
- 跳过正则在扫描 / 监控侧**逐条编译、任一命中即跳过**; 不允许用 `|` 拼接 (用户全局旗标拼在联合式中间会触发 re 的 "global flags not at the start"). 空列表关闭.

分集 (CD) 检测只做在 ORGANIZE 时, 不落库, 写回靠路径模板里的 `{cd?}`. 文件名无分集时, 直接父目录整段为 `CDn` / `PARTn` 也可认. **幂等**: 写出的分集 / 中字 / 马赛克 / 分辨率格式须能被同一检测逻辑反推, 否则二次整理会丢失标记 — 当前只文档约束, 不加验证.

字幕: ORGANIZE 在视频**挪走前**扫描其同目录 (不递归、不入库、不扫描同目录其它视频), 扩展名由库 `subtitle_extensions` 配置; 多个字幕全部搬走不挑主字幕, 放置采用与视频相同的 `move_mode`. 字幕采用同一套 `parse_file_info`, 但只解析**文件名** (`text=` 入参), 否则 `chs.srt` 会被当成番号; 解析出番号时必须与当前视频相同, 再按分集配对, 解析不出时才回退独立目录规则.

`link_template` 为空则不创建链接, 非空时 ORGANIZE 在视频就位后按该模板写一条指向真实视频的入口 (`link_mode=strm` 写 `.strm`, `symlink` 做软链接). 链接必须在库外, 否则 REFRESH 会把入口再扫描为媒体. `.strm` 正文由库级 `strm_content_template` 决定 (空则写一行视频绝对路径); 模板引用 `{video_relpath}` 且整理后路径不在本库内时失败, 不写出错误正文. 默认附属模板用 `{link_dir}`, 因此填链接模板后 NFO / 海报自动跟随链接.

模板语言在 `organize/template.py`, 只约束以下几点: 占位符分相位注入 (`metadata` → file 相位 → `apply_video` → `apply_link`), file 相位未检出是**空串**不是 `Unknown`; 渲染时把 `{title}` / `{actor}` 等分量截到 200 UTF-8 字节 (不切开多字节字符), 但不截断渲染后的路径分量; 可选组 `[...]` 内直接占位符全空则整段丢弃, 有一个非空时其余输出空串. 普通占位符缺失回退 `Unknown`.

**逃逸防护**: 校验对渲染结果做 realpath (跟随符号链接). 相对模板的真实写出路径必须在本库内 (`ALLOW_ALL` 也不例外); 绝对模板 (含展开 `{video_dir}` / `{link_dir}` 后变绝对) 必须位于本库或 `safe_dirs` 内, 否则 `ValueError`. 返回路径与 `{video_dir}` 用字面绝对路径 (折叠 `..`, 不跟随链接), 库内某级目录项指向库外则拒绝. 多盘分存要求目标盘在 `safe_dirs` 内.

## Resource (一等存储, 非缓存)

`Resource.url` UNIQUE, 作**通用 locator key**: 原始外部图用真实外部 URL, 派生裁剪用合成串 `derived:{sha256(src_url)}:crop:{args}`. 手动裁切写入派生 Resource 并替换 `poster_urls`, **不**修改库路径海报 (ORGANIZE 再复制). `file_path` 是相对于 `{data_dir}/resources/` 的两级散列路径.

`meta` (JSON, 默认 `{}`) 在派生 / 被处理资源上记录可逆来源与处理标记: 裁剪记 `{'op':'crop','src':源url,'args':str}`; 任意资源被超分后追加 `{'sr':{tool,model,scale}}` — 超分**就地覆盖**文件 (URL 不变), `'sr' in meta` 即去重依据.

**一等存储, 按引用回收**: Resource 不是 LRU 缓存. 刮削换 URL / 修改裁剪参数后旧条目会留在库里, 直到 CLEANUP 的 `remove_unreferenced_resources` 扫描 Metadata 媒体 URL 字段并删除未引用项 (含派生). 要原始像素须 invalidate 重下 (就地超分后原像素不可恢复). 其它保留规则见 [task-system.md](task-system.md) CLEANUP. `content_hash` (SHA-256) 作完整性校验与 ETag, 约定见 [api.md](api.md).

## 分类索引 (爬取侧投影)

`Metadata` 上的 `actors` / `tags` / `directors` (JSON list) 与 `studio` / `publisher` / `series` (标量) **仍是刮削聚合、NFO、路径模板的真值来源**. 分类实体表 + 关联表是**查询投影**: `upsert_metadata` / `update_metadata` 写入后由 `_sync_metadata_facets` 重建 (按 name get-or-create; list 字段带 `position` 保序).

写入时先清洗 `Metadata.actors` 的 `name(alias1, alias2)` 形式: 展示名留真值, 别名并入对应演员的 `ActorAlias` 行. 每个名字经 `resolve_actor_by_name` 解析 (展示名精确命中 → 别名唯一命中 → 歧义 / 无命中以名字本身为展示名新建实体), 因此站点给的**裸别名**会折到已认定演员, 不再另建重复实体; block 判定在解析前后各执行一次. 影片名单顺序由第一成功源锁定, 其后已抓源按展示名填空性别. 写入时若带 `FilmActor.gender`, 只对 `Actor.gender == unknown` 填空, 不覆盖已有值, 不写入 `field_sources`. `Metadata.actors` 存库始终是展示名, 站点 `raw` 快照保留原始带括号形式.

用户对爬取侧分类的改名 / 合并 / 删除意图落在 `FacetRule` (按 `(kind, source_name)` 唯一), **不**修改投影表本身:

| action | 含义 |
|--------|------|
| `alias` | 源名映射到目标名; **表内保持单跳规范形** (写入时压缩入边, apply 不递归). 演员不使用该规则, 由 `ActorAlias` 行承担 |
| `block` | 源名永久剔除; 指向该名的 alias 入边一并压成 block |

规则在 `_sync_metadata_facets` 之前对六个分类真值字段执行 (不修改 `raw`); 演员只有 block 会命中. 目录 API 的 rename / merge 对非演员写 alias 并修改已有 Metadata, delete 写 block 后从真值剔除再删实体. `user_tag` 与刮削隔离, 硬删且不写入规则表. 名称大小写敏感, 与源站原样一致, 不做模糊合并. `Actor` / `Director` 为一等实体, 无影片关联时**不自动删除**, 用户显式删除时删除实体并写 block. 删 `Metadata` 时清理关联 / 评论 / 用户 tag 挂载, 人物与目录实体保留.

### 演员身份与人物元数据

身份是 **ID 锚定的名字映射**, 不是独立身份图:

| 层 | 角色 |
|----|------|
| `Metadata.actors` | 影片刮削 / NFO 真值 (解析后的展示名) |
| `Actor.id` | 人物宿主 (任务、关联、人物字段); `name` UNIQUE **展示名** |
| `ActorAlias` | ID→名称一对多映射 (查找 / 搜索 / 展示); `(actor_id, name)` 唯一, `name` 列**不全局唯一** |
| `FacetRule(actor, block)` | 名字永久剔除 (演员仅此一种规则) |

展示名**不写入** `ActorAlias` 表 (恒等约束); 切换展示名 = 旧展示名入表 (追加末尾)、被选中的别名行出表、存量 `Metadata.actors` 批量改写为新展示名 — 一次操作, 无需维护映射规则.

`Actor` 另存人物元数据 (`gender` / 生日 / 身材 / 简介 / `image_urls` / `provider_ids` / `source_urls` / `raw`). `gender` 的 `unknown` 视为标量空位, 可被刮削填空或手动修改覆盖; `birthday` 与 `Metadata.release` 同为 `YYYY-MM-DD`; `image_urls[0]` 是主图 (详情 / 头像墙), 用户可编辑次序.

档案刮削**不修改** `Actor.name`: 各站 `ActorMetadata.name` 与 `aliases` 并入别名行并集, 写回时排除与展示名相同的项, 因此站点的中文显示名不会盖掉已认定的展示名. 多站 `source_url` 聚合为 `source_urls` (site→url, 先到先得). 实体 merge 保留 target id: 先把源演员的名字并入 target 别名行, 再把人物字段填空并入. 实体 delete 对展示名与其**独有**别名写 block 行 (被其它演员引用的共享名不写, 避免误伤), 别名行随实体显式删除, 不依赖 SQLite FK pragma.

## 用户注解 (与爬取隔离)

`UserTag` + `MetadataUserTag`、`Comment` 绑定于 Metadata. 刮削路径**绝不触碰**.

## 当前限制

- SQLite + batch mode 修改大表会重建表, 数百万行时耗时不可接受; 个人规模可接受, 超过须切换至 PostgreSQL.
- `Task.payload` / `Task.result` 是 JSON dict, 无 schema 强制 — 由 handler 的 Pydantic 模型在反序列化时校验 (见 [task-system.md](task-system.md)).
- `Schedule.payload` 存 `RoutineSubmission` 的 JSON; 不允许在线修改任务内容, 修改 type / payload 须删除后重建. 详见 [task-system.md](task-system.md).
