# 术语表

> 符号 ↔ 中文用词对照. 用于统一注释、`docs/dev/`、提交说明与 PR 的用词, 并给 i18n 与 `docs/user/` 提供面向用户的定名.
> 用语风格见 [writing.md](writing.md).

列的含义:

| 列 | 服务对象 | 规则 |
|----|---------|------|
| 符号 | 源码 | 与代码逐字一致; 描述其它术语时用它指代 |
| 开发中文 | 注释、`docs/dev/`、提交说明、PR、与维护者对话 | 实现概念在此层用词 |
| 用户中文 | i18n、`docs/user/` | 面向用户, 不出现实现词 (Agent / SQL / facet / worker / Provider 等) |
| 说明 | — | 只在概念不直白时给出 |

约定:

- `—` 表示该层不使用此词 (与上一层同词, 或该层不出现).
- 每格只有一个词; 同格出现多个词即视为未收敛.
- 说明栏写该概念的一句话判据 (供理解代码用); 机制细节见对应文档, 不在此复述.
- 两层用词不同是**有意**的: 用户层按直觉与惯例取名, 不反向污染实现层; 反向亦然. 例外与豁免见「一词多义」与「豁免」.
- 同一事物只在本表出现一次; 一个概念在两层都出现时, 两层各自统一, 不混用 (用户文案里写「来源」, 开发文档里就不要再拿它指 `Feed`).
- 新增符号前先在本表搜索它是不是既有符号的同义写法 (例: 新的「列表」概念应并入既有那一行, 而不是另起一个词); 新造词只在既有词确实指别的东西时使用.

## 一词多义

中文词比符号少, 以下词必须按下表分工, 不得互换:

| 中文词 | 允许指 | 不允许指 |
|--------|--------|----------|
| 条目 | `FeedItem`、列表项、目录项 | `Metadata` (称番号条目或影片) |
| 分类 | `facet` (分类实体与分类页) | `ContentType` (内容类型)、`classify` (归类) |
| 来源 | 刮削来源 (`source`: 站点与插件); 「播放来源」是完整词, 不简称为来源 | `Feed` (订阅源)、日志 `source` 字段 (不译) |
| 回收 | `TRASH` (无效文件移入 `.amane_trash`) | `Resource` 的淘汰 (称清理) |
| 黑名单 | `blacklist_patterns` (写「文件黑名单正则」) 与清理清单的命中原因 (写「文件黑名单」) | `FacetRuleAction.BLOCK` (写剔除)、`field_blacklist` (写字段排除) |
| 落盘 | `ORGANIZE` 写入库路径 | 一般的持久化 (称写入磁盘、落库) |
| 扫描 | `REFRESH` (库文件增删) | 插件重新发现 (写重新加载)、界面数据刷新与 `updated_at` 刷新 (写刷新) |
| 中字 | `has_subtitle` (影片带中文字幕) | 字幕文件 (`subtitle`) |
| 入库 | `MediaFile` 登记 (状态与动作同词) | 写入数据库 (写落库) |
| 库 | `Library` (媒体库) | 数据库 (写 DB 或数据库) |
| 关联文件 | `connections` / `has_files` / `file_count` 三处同词, 靠位置区分 | 其它列表 |
| 类型 | `ContentType` (内容类型, 可简称类型)、`SavedQuery.entity` 与表单枚举 | 泛指的类别 (必须带限定词)、`FacetKind` (分类) |
| 能力 | 来源声明的能力 (`SourceCapability`) | 助理的工具组 (`Capability`), 写「工具组」 |
| 提供者 | 插件返回的来源对象 (`provider`) | 上游模型服务 (`provider`, 用户层写「供应商」) |
| 上游 | LLM 上游、上游接口、上游 CDN 等非播放语境 | 播放链路的用户可见文案 (写「播放来源」) |
| 厂商 | 组件与包的发行方 (WebView 厂商包版本、厂商自带 WebView) | `Studio` (写「制作商」) |
| 出口 | — (该词废弃) | 网络出站写「出站」, 播放并发写「通道」, 数据产出写「输出」 |
| 体积 | — (该词废弃) | 一律写「大小」(文件大小 / 视频大小 / 图片大小) |
| 抓取 | — (该词废弃) | 一律写「获取」 |

## 核心实体

| 符号 | 开发中文 | 用户中文 | 说明 |
|------|---------|---------|------|
| `Metadata` | 番号条目 | 影片 | 番号级聚合元数据; 是中心, 不依赖本地文件. 「元数据」只指数据本身 (元数据缓存) |
| `MediaFile` | 媒体文件 | 媒体文件 | 磁盘视频的索引; 必须归属唯一 `Library` |
| `Resource` | 资源 | 资源 | 一等存储, 按引用回收 |
| `Library` | 媒体库 | 媒体库 | 一个根目录 + 路径模板 + 放置方式 + 自动化级别的单位 |
| `Actor` | 演员 | 演员 | 人物一等实体; `name` 即展示名 |
| `ActorAlias` | 别名行 | 别名 | 展示名不写入本表 |
| `Director` | 导演 | 导演 | 预留实体 |
| `Tag` | 标签 | 标签 | 爬取侧分类实体 (`FacetKind.TAG`) |
| `Studio` | 片商 | 制作商 | メーカー; 与 `Publisher` 成对 |
| `Publisher` | 发行商 | 发行商 | レーベル |
| `Series` | 系列 | 系列 | 爬取侧分类实体 |
| `UserTag` | 用户标签 | 用户标签 | 与爬取侧 `Tag` 隔离, 硬删且不写入规则表 |
| `Comment` | 评论 | 评论 | 绑定于 `Metadata` |
| `FacetRule` | 分类规则 | 规则 / 用户规则 | `alias` 改名 / `block` 剔除; 不修改投影表 |
| `FacetKind` | 分类种类 | 分类 | 分类页的分栏依据 |
| `Feed` | 订阅源 | 订阅源 | 远程 RSS / Atom 发现源; 单写「源」会与刮削来源混 |
| `FeedItem` | 条目 | 历史条目 | 含正文快照与忽略 / 已读状态 |
| `Schedule` | 定时任务 | 定时任务 | cron 触发器; 与 `Task` 不是一回事 |
| `Task` | 任务 | 任务 | 持久化队列的一行 |
| `TaskLink` | 后继边 | 子任务 | 父子边真值; 后继由 `TaskResult.followups` 产生 |
| `AgentSession` | 会话 | 会话 | 索引表; 完整 trace 落盘, 不在表内 |
| `SavedQuery` | 查询预设 | 查询预设 | 权威是 SQL, 结果仅内存缓存 |
| `WriteMode` | 写入策略 | — | `AUTO` 跳过锁定列 / `MANUAL` 无视锁并回写锁集合 |
| `locked_fields` | 字段锁 | 锁定 | 字段级锁集合, 没有条目级开关 |
| `field_sources` | 字段来源 | 来源 | 标量字段取自哪个站点; 空不代表刮削失败 |
| `scores` | 评分体系 | 评分 | `{site: score}`, 保留来源供分列展示 |
| `MetadataField` / `ActorField` | 可锁字段 | 锁定字段 | 字段级锁的取值全集 |
| `projection` | 查询投影 | — | 分类实体与关联表是 `Metadata` 真值的投影 |
| `metadata_id` | 归属绑定 | — | `MediaFile` → `Metadata` 的多对一; 为空是常态 |
| `has_files` / `file_count` | 挂载文件 | 关联文件 | 与详情内的文件列表 (`connections`) 同词, 靠位置区分 |
| `raw` | 原始快照 | 刮削来源快照 | 各站原始数据, 供离线重新聚合. 两层有意分层 |
| `external_ids` | 站点 ID | 外部 ID | `{site: id}` |
| `source_urls` | 来源链接 | 来源链接 | `{site: url}` |
| `merge` | 合并 | 合并 | 分类 / 演员身份 / 多源字段的合并 |
| `Alembic revision` | 迁移脚本 | — | 结构演进的唯一入口; 禁止手写 revision ID |
| `autogenerate` | 自动生成 | — | 列改名 / 索引 / JSON 结构是盲区, 须手写迁移 |
| `NFC` | 规范化路径 | — | 路径身份一律按 NFC 存储与比较 |
| `crop` | 裁切 | 裁切 | 从 `thumb` 派生 `poster`, 或裁演员头像 |

## 内容识别

| 符号 | 开发中文 | 用户中文 | 说明 |
|------|---------|---------|------|
| `number` | 番号 | 番号 | 大小写不敏感唯一; 存库保留首次写入的大小写 |
| `prefix` / `suffix` | 番号前缀 / 后缀 | 番号前缀 / 番号剩余段 | 路径模板占位符 `{prefix}` / `{suffix}` |
| `parse_file_info` | 路径解析 | — | 从路径或自由文本解析番号与文件相位, 纯函数 |
| `ContentType` | 内容类型 | 内容类型 | 决定刮削路由; 取值 有码 / 无码 / 国产 / 欧美 / FC2 / 素人 / 动漫 |
| `Mosaic` | 马赛克标记 | 马赛克标记 | 值取 有码 / 无码标记 / 破解 / 流出; 与 `ContentType` 正交 |
| `has_subtitle` | 中字 | 中字 | 只依据文件名; 与 `ContentType.CHINESE` (国产) 无关 |
| `definition` | 分辨率 | 分辨率 | 文件名检测的档位; 占位符 `{def?}` 同词. 图像像素量的「分辨率」是另一义 |
| `cd` | 分集 | 分集 | 只在 `ORGANIZE` 配对, 不落库 |
| `overview` | 人物简介 | 简介 | 演员档案文本; 与影片的 `plot` (剧情) 不同物 |
| `FileInfo` / `FilePhase` | 文件相位 | — | 文件相位的解析结果与持久化投影 |
| `FilePhaseSummary` | 相位聚合 | — | 多文件相位; 任一具备即为真, `definition` 取最高档 |
| `oshash` | 文件指纹 | — | 只在 `SCRAPE` 按需计算 |
| `subtitle` | 字幕 | 字幕 | 字幕文件; 与 `has_subtitle` 的「中字」区分 |

## 刮削与来源

| 符号 | 开发中文 | 用户中文 | 说明 |
|------|---------|---------|------|
| `scrape` | 刮削 | 刮削 | 联网取元数据; 与 `ORGANIZE` 落盘互不包含 |
| `source` | 来源 | 来源 | 站点与第三方插件的统称 |
| `site` / `SiteName` | 站点 | 站点 / 来源 | 内置来源; 站点配置处称站点, 来源列表处称来源 |
| `crawler` | 爬虫 | 刮削插件 | 内置来源的实现; 用户层不出现「爬虫」 |
| `plugin` / `provider` | 插件 / 提供者 | 插件 | `plugin/` 是公开接口, `plugins/` 是主机实现 |
| `FilmSourcePlugin` / `FilmSourceProvider` | 影片来源插件 / 提供者 | 影片刮削插件 | 前者被继承, 后者是运行时契约 |
| `PluginContext` | 插件上下文 | — | 注入 `source_id` / `http_client` / `web_client` / `data_dir` |
| `PLUGIN_API_VERSION` | 插件 API 版本 | — | 描述符版本不匹配即该插件不可用 |
| `source_id` | 来源 ID | — | 路由 / `raw` / `field_sources` / 缓存键共用的稳定身份, 不等同显示名 |
| `CrawlerFactory` | 工厂 | — | 按来源 ID 延迟创建并缓存爬虫与插件提供者 |
| `registry` | 注册表 | — | 影片与演员两张, 决定站点角色与默认优先级 |
| 双料站 | 双料站 | — | 同一 `SiteName` 在两张注册表各有一个类 |
| `Crawler` / `_search` / `_scrape` | 爬虫 / 检索 / 详情解析 | 刮削插件 | Template Method: 番号 → URL → `MediaMetadata` |
| `CrawlerProfile` | 档案 | — | 内置来源的声明性事实; 与 `profile_sites` 的「资料来源」同字不同物 |
| `ActorFetcher` | 演员获取器 | — | 工厂下游依赖的结构性协议 |
| `HttpClient` / `for_source` | 爬虫 HTTP 封装 / 来源视图 | — | 浏览器策略与同源 Referer 在此生效 |
| `SourceError` / `RequestError` | 来源错误 / 请求错误 | — | 都进站点结果, 不视为整任务崩溃 |
| `SourceKind` | 来源类别 | 影片 / 演员 / 插件 | 网络检测页的分组 |
| `ConnectivityOutcome` | 探测结论 | 可访问 / 不可访问 / 无法探测 | 三态; 未探测不算失败, 不给耗时 |
| `MULTI_LANGUAGE_SITES` | 多语言站 | — | 聚合只对它展开 `(site, lang)` 节点 |
| `SearchQuery` | 检索入参 | — | 番号 / 路径 / 指纹 / 内容类型 |
| `partial_result` | 前序聚合结果 | 前序结果 | 段间注入的只读标量聚合 |
| `AggregatedMetadata` | 聚合结果 | — | 聚合输出; URL / 评分 / 站外 ID 为多源结构 |
| `descriptor` / `SourceDescriptor` | 描述符 | — | 来源能力声明; 刮削只读描述符, 不读实例 profile |
| `capabilities` | 能力声明 | — | 影片元数据 / 演员档案 / 演员头像 |
| `traits` / `SourceTrait` | 行为开关 | — | `needs_partial` 第二段 / `multi_language` 按语言展开 / `uses_file_hash` 刮削前取指纹 |
| `aggregate` | 聚合 | — | 字段级多源合并; 字段分两类, 见下两行 |
| `SCALAR_FIELDS` | 单源字段 | — | 沿链取首个非空值; 含列表型 (演员 / 标签 / 导演), 整份列表取首个非空, 不跨来源合并 |
| 多源字段 | 多源字段 | — | URL / 评分 / 剧照; 全部来源返回后按链拼接 |
| `ContentRoutes` | 类型路由 | 类型路由 | 按 `ContentType` 的有序站点链, 是资格真值 |
| `field_priority` | 字段优先级 | 字段优先级 | 稀疏例外; 与该类型路由求交后前置 |
| `field_blacklist` | 字段排除 | 字段排除 | 该字段不采用的站点 |
| `build_graph` / `FetchNode` | 获取图 / 节点 | — | 站点 + 语言唯一确定一个节点 |
| `NEEDS_PARTIAL` | 第二段 | — | 依赖前序标量聚合的来源延后执行 |
| `use_cache` / `CacheKind` | 缓存开关 | 使用缓存 | `metadata` 复用站点快照, `trans` 复用译文 |
| `cache_key` | 缓存键 | — | `site` 或 `site:lang` |
| `FailureReason` | 失败原因 | 失败原因 | 拦截 / 超时 / 限速等, 进任务摘要 |
| `SKIPPED` / `SkipReason` | 未探测 | 无法探测 | 网络检测的第三种结果, 不是失败 |
| `classify_block` | 拦截判定 | 人机验证 / 拦截 | Cloudflare challenge 等模式表 |
| `BrowserMode` | 浏览器策略 | 浏览器策略 | `off` / `auto` / `always` |
| `BrowserBackendName` | 浏览器后端 | 浏览器后端 | `patchright` / `camoufox` / `solver` |
| `check_connectivity` | 连通性探测 | 网络检测 | 逐来源探测点, 与刮削共用一条 HTTP 通道 |
| `official_routes` | 片商路由 | 制作商路由 | 按番号前缀指定片商站 |
| `rate_limit` | 限速 | 速率限制 | 按 Host 或按站点 |
| `site_config` | 站点配置 | 站点配置 | 内置来源的 cookie / 域名 / 通用参数 |
| `r18dev` | r18 只读镜像 | r18.dev 数据源 | 外部 PostgreSQL dump; 不纳入 Alembic |

## 媒体库与文件整理

| 符号 | 开发中文 | 用户中文 | 说明 |
|------|---------|---------|------|
| `REFRESH` | 扫描 | 扫描 | 注册与移除索引; 首刷 `initial_refresh` 不另立新词. en 保持 `Refresh` |
| `ORGANIZE` | 整理 | 整理 | 按路径模板落盘 |
| `TRASH` | 回收 | 回收 | 无效文件移入 `.amane_trash` |
| `CLEANUP` | 清理 | 清理 | 删失效索引与未引用 `Resource` |
| `UPSCALE` / `sr` | 超分 | 超分 | 就地覆盖低质图 |
| `SrPreset` | 超分预设 | 超分模型 | 一个工具 + 模型 + 倍率 + 降噪的组合 |
| `blacklist_patterns` | 文件黑名单正则 | 文件黑名单正则 | 文件名匹配即视为垃圾文件; 清理清单的原因标签写「文件黑名单」 |
| `trailer_pattern` | 预告片正则 | 预告片文件名正则 | 命中则跳过扫描与监控 |
| `min_file_size` | 最小视频大小 | 最小视频大小 | 低于阈值与文件黑名单同语义 |
| `move_mode` / `MoveMode` | 整理方式 | 整理方式 | `move` 移动 / `copy` 复制 / `hardlink` 硬链接 / `symlink` 符号链接 |
| `symlink` | 符号链接 | 符号链接 | 与 `hardlink` 成对; `move_mode` 与 `link_mode` 共用同一实现 |
| `link_mode` | 链接方式 | 链接方式 | `strm` / `symlink` |
| `link_template` | 链接模板 | 链接路径模板 | 为空则不创建链接 |
| `strm` | STRM | STRM 文件 / strm | 写绝对路径的入口文件 |
| `template` / `placeholder` | 路径模板 / 占位符 | 路径模板 / 占位符 | 占位符按相位注入 |
| 可选组 / 值映射 | 可选组 / 值映射 | 可选组 / 值映射 | `[...]` 内占位符全空则整段丢弃; 值映射限定有闭合取值的占位符 |
| `write_nfo` | 写 NFO | 写入 NFO | 整理时是否写 NFO |
| `recursive` / `patterns` | 递归 / glob | 递归扫描 / 匹配模式 | 逗号分隔的 glob 命中才算媒体 |
| `subtitle_extensions` | 字幕扩展名 | 字幕扩展名 | 整理时同目录发现字幕的后缀白名单 |
| `initial_refresh` | 创建时首刷 | 创建后立即扫描 | 建库后是否入队一次 `REFRESH` |
| `safe_dirs` | 安全目录 | — | 绝对模板的允许写出范围 |
| `watermark` / `WatermarkKind` | 角标 | 水印 | 封面副本叠加的 PNG |
| `crop_poster` / `poster_ratio` | 海报裁切 | 裁切海报 / 海报裁切比例 | 从 `thumb` 派生 `poster` |
| `thumb` | 封面 | 封面 | 竖图; 与水印、裁剪的输入 |
| `poster` | 海报 | 海报 | 由 `thumb` 裁出或自来源取得 |
| `extrafanart` | 剧照 | 剧照 | 按站点分组, 禁止扁平合并 |
| `fanart` | 背景图 / fanart | 背景图 | 磁盘派生文件; 不在 `DownloadableResource` 内 |
| `trailer` | 预告片 | 预告片 | |
| `nfo` | NFO | NFO | 从 DB 派生的副产物, 不反向 |
| `DownloadableResource` | 附属资源 | 资源 | `thumb` / `poster` / `extrafanart` / `trailer` |
| `download_resources` / `copy_resources` | 下载资源 / 复制资源 | 刮削时下载 / 整理时复制 | 前者写入 `Resource`, 后者复制到库路径 |
| `materialize_images` | 资源物化 | 下载 | 聚合后把 URL 落成 `Resource` |
| `automation` | 自动化级别 | 自动化 | 档位 `none` 关闭 / `watch` 仅入库 / `scrape` 入库并刮削 |
| `ingest` / `LibraryIngest` | 发现通道 | 文件发现方式 | `native` 本地文件 / `clouddrive` CD2 webhook. 监视器 (`watcher`) 才算「文件监控」 |
| `watcher` / `WatcherService` | 监视器 | 文件监控 | 进程级, `use_polling` 只作用于 `native` 库 |
| `use_polling` / `debounce_seconds` / `media_extensions` | 轮询模式 / 防抖窗口 / 媒体扩展名 | 轮询模式 / 防抖窗口 / 媒体扩展名 | 进程启动时注入, 不随 `rebuild` 更新 |
| `link` | 链接 | 链接 | 库外指向真实视频的链接文件 (strm 或符号链接) |
| `cloud_path` | CloudDrive 虚拟路径 | CloudDrive 路径 | 与宿主挂载路径不是一回事 |
| `register_media_file` | 文件注册 | 入库 | 只写路径, 不算指纹 |
| `classify` / `LibraryScan` | 归类 | — | 单路径判定为 跳过 / 回收 / 媒体; 不写「分类」以免与 `facet` 混 |
| `LibraryFileKind` | 归类结果 | — | `media` 媒体 / `skip` 跳过 / `unwanted` 回收 |
| `.amane_trash` | 回收目录 | `.amane_trash` | 保留目录, 任意深度恒被忽略 |
| `add` / `remove` | 新增 / 移除 | 扫描新文件 / 清理失效记录 | `REFRESH` 的两个扫描模式 |
| `正片` | 正片 | 正片 | 与预告片、垃圾文件相对 |

## 任务、调度与可观测

| 符号 | 开发中文 | 用户中文 | 说明 |
|------|---------|---------|------|
| `TaskType` | 任务类型 | 任务类型 | 即时任务的全部取值 |
| `RoutineType` | 例行任务类型 | — | 定时任务只接受 `cleanup` / `upscale` / `r18_import` / `rescrape` |
| `TaskStatus` | 任务状态 | 排队中 / 运行中 / 已完成 / 失败 | `queued` / `running` / `done` / `failed` |
| `MediaFileStatus` | 文件状态 | 待处理 / 已刮削 / 失败 / 跳过 | `pending` / `scraped` / `failed` / `skip` |
| `followups` | 后继 | 子任务 | 唯一的子任务产生途径, 随完成事务原子落库 |
| `link_key` | 后继语义键 | 子任务 | 父节点内区分后继的键. 用户层的「子任务」指父子任务这一关系, 不是键值本身 |
| `child_count` / `child_status` | 后继计数 / 后继状态 | 子任务 | 折叠节点据此显示 |
| `enqueue` | 入队 | 提交任务 | |
| `claim` | 认领 | — | 归属边界是认领开始时刻 |
| `worker` | Worker | 任务引擎 | 用户层不出现「Worker」 |
| `concurrency` | 并发上限 | 并发数 | 过高会触发反爬 |
| `pause` / `resume` | 暂停 / 恢复 | 暂停 / 恢复 | 进程内状态, 不写配置 |
| `retire` / `drain` | 退役 / 排空 | — | 配置重建时旧 Worker 的去向, 不可逆 |
| `cancel` | 取消 | 取消 | 文件操作中取消只能延后生效 |
| `retry` | 重试 | 重试 | 克隆为无根裸任务, 不继承原链 |
| `RESCRAPE` | 重新刮削 | 重新刮削 | 滚动重刮, 复用站点快照. 与影片页的「重新刮削」按钮不同物 (后者产生 `SCRAPE`) |
| `ACTOR_SCRAPE` | 演员刮削 | 演员刮削 | 抓人物档案与头像; 可由影片刮削链式触发 |
| `actor_scraping` / `profile_sites` / `image_sites` | 演员刮削 / 资料来源 / 头像来源 | 演员刮削 / 演员资料优先级 / 头像来源优先级 | 头像优先于资料来源附图 |
| `R18_IMPORT` | 导入 | R18 导入 | 下载 dump 并重建只读库 |
| `force` | 强制 | 强制 | 忽略既有记录重新执行 (`r18_import` 的 `force` 忽略数据包版本) |
| `TaskSubmission` / `RoutineSubmission` | 即时提交 / 例行提交 | 提交任务 | 带 `type` 判别式的联合; 定时只接受后者 |
| `TaskHandler` | 处理器 | — | payload / result 泛型契约 |
| `report_progress` | 进度上报 | 进度 | 经 `EventBus` 发 `task.progress`; 不调用时静默忽略 |
| `fail_all_running_tasks` | 清扫 | — | 关闭时单次把遗留运行中任务标为失败 |
| `CronScheduler` | cron 触发器 | — | 每 60 秒扫描启用的 `Schedule` |
| `cron` / `next_run` / `last_run` | cron 表达式 / 下次触发 / 上次运行 | 运行时间 / 下次执行 / 从未执行 | 由调度器维护, 外部只读 |
| `Schedule.cron` | cron 表达式 | 运行时间 | 定时任务的触发表达式字段 |
| `trigger` | 手动触发 | 立即触发 | 把下次触发置为当前, 由下一 tick 执行 |
| `FeedService` | 远程发现 | 订阅 | 与 `Schedule` 并列的第三个循环 |
| `Feed.group` | 分组伪路径 | 分组 | 用 `/` 分段建树; 库内不存目录实体 |
| `item_key` | 条目身份 | — | guid → link → title; 决定去重 |
| `auto_enqueue` | 自动入队 | 自动刮削新条目 | 只对新条目且解析出番号时生效; 反向写法「不自动刮削」 |
| `ignore_keywords` | 忽略关键词 | 忽略关键词 | 只匹配标题与番号, 不匹配正文 |
| `ignored_at` / `read_at` | 已忽略 / 已读 | 已忽略 / 已读 | 与条目去重正交 |
| `FeedItemState` / `FeedItemReadState` | 忽略视图 / 已读视图 | 未忽略 / 已忽略 / 全部; 未读 / 已读 / 全部 | 两个「全部」维度不同 |
| `next_fetch_at` / `etag` / `last_modified` | 到期时间 / 304 缓存 | — | `enabled` 只控制这一轮扫描 |
| `number_pattern` | 番号正则 | 番号正则 | 一旦设置即不再回退内置规则 |
| `poll` | 拉取 | 立即拉取 | 单源间隔驱动, 失败同间隔重试 |
| `EventBus` | 事件总线 | — | 进程内事件分发; 失效连接在发送失败时移除 |
| `/ws` | WebSocket 通道 | — | 单向只读, 握手 cookie 鉴权 |
| `EventType` | 事件名 | — | 保持英文 (`task.started` / `file.discovered` / `log`), 不译也不进用户文案 |
| `Recorder` | 任务记录 | 任务记录 | 单任务的顶层门面 |
| `app.log` / `request.log` / `task.log` | 应用日志 / 请求日志 / 任务日志 | 日志 | 三流日志 |
| `source` (日志) | 来源 | 来源 | 取 logger 名末段 (`worker` / `translator` / 站点名), 属日志字段, 不译 |
| `report` / `summary.json` | 摘要 | 摘要 | 从 `SiteOutcomeRecord` 投影; 不读 `http/` |
| `result` | 结果 | 结果 | handler 返回值; 与 `report` 不同物 |
| `SiteOutcomeKind` | 站点结果类别 | 成功 / 失败 / 缓存 | `ok` / `failed` / `cache_hit`; 同站多次上报取更差 |
| `invoke_source` | 来源记账 | — | 唯一写站点结果的写入位置 |
| `eligible_sites` | 资格站 | — | 与 `sites_queried` (实际请求过) 不同 |
| `manifest.json` / `http/` | 记录清单 / HTTP 记录 | — | 出站 HTTP 只落 `http/`, 不是站点结果 |
| `debug_capture` | 调试捕获 | 调试捕获 | 打开则所有任务留 HTTP 正文, 否则只留失败任务 |
| `include_secrets` | 明文密钥 | — | 导出时换成真实密钥快照 |
| `ReplayWebClient` | 离线回放 | — | 按 method + url 匹配 `http/`, 无命中即 replay miss |
| `outcome` | 站点结果 | — | 只对刮削类任务有意义 |
| `replay` | 回放 | — | 界面展示只认回放行 |
| `trace` | 回放行 | — | 后端定形的展示事实 |
| `export` / `record.zip` | 导出 | 任务记录 | 仅终态可导出 |
| `redact` | 脱敏 | — | 导出时排除密钥 |

## 助理与大模型

| 符号 | 开发中文 | 用户中文 | 说明 |
|------|---------|---------|------|
| `agent` | 助理 | 智能体 | 导航写 Amane, 其余位置写「智能体」; 对话里不出现 Agent / SQL (查询预设页的 SQL 保留) |
| `AgentService` | 助理服务 | — | 挂在 `AppRuntime`; 持有工厂、进行中回合与 `ResultCache` |
| `AgentRuntimeBridge` | 桥 | — | 库路径边界 / 监视器 / 取消任务 / 拉取订阅 |
| `SessionStore` / `events.jsonl` / `meta.json` | 会话存储 / 事件行 / 会话附属文件 | — | `events.jsonl` 是回放的唯一来源 |
| `readonly SQL sandbox` / `allow_slow` | 只读沙箱 / 慢查询 | 超时 | 未批准不执行; 批准后取消超时上限 |
| `ResultCache` | 结果缓存 | 结果缓存 | 命中要求条目 SQL 与预设行一致 |
| `SubmissionSpec` | 按需披露 | 参数 | 任务与定时入参二级披露; schema 与校验同源 |
| `AgentSessionStatus` | 会话状态 | 状态 | `awaiting_approval` 表示存在未决批准 |
| `session` | 会话 | 会话 | 用户数据, 写入 Cold `data_dir` |
| `turn` | 回合 | — | 服务端跑完, 客户端断连不取消 |
| `interrupt` | 中断 | 待批准 | 一次 `resume` 必须回答全部中断 |
| `ApprovalRequired` | 批准流 | 批准 / 拒绝 | 慢查询与破坏性写 |
| `tool` | 工具 | 工具 | 工具名与文案分离 |
| `Capability` | 能力 | — | 按域分组的写工具; 每个请求全量声明 |
| `sql_explore` | 探查 | — | 中间推理, 默认只回样例 |
| `sql_deliver` | 交付 | — | 结果写成 `SavedQuery` 并进内存缓存 |
| `inspect_result` | 按 id 取行 | — | 按 `saved_query_id` 翻页 |
| `AG-UI` | 事件流 | — | 对话经 AG-UI, 不经 `/ws` |
| `messages.json` | 模型上下文 | — | 唯一真值; 展示另认回放行 |
| `preset` / `persist` | 预设 / 保留 | 预设 / 保留 | 未保留的预设随会话删除 |
| `instructions` | 身份与规则 | — | 与 `system_prompt` 在协议层位置不同 |
| `system_prompt` | 系统提示词 | 系统提示词 | 用户可覆盖 |
| `thinking` | 思考强度 | 思考强度 | 会话可覆盖全局默认 |
| `usage` | 用量 | 请求 / 输入 / 输出 | 缓存读写作单列 |
| `translator` / `trans` | 翻译 / 译文 | 翻译 | 简繁转换不走大模型 |
| `Translator` / `TranslationCache` | 翻译面 / 译文缓存 | 翻译缓存 | 独立 `translations.db`; 键含提示词指纹 |
| `translate_fields` / `field_prompts` | 翻译字段 / 字段提示词 | 翻译字段 / 字段提示词 | 提示词只替换指令与字段说明, 输出约束固定 |
| `needs_llm_translation` | 跨语系 | — | 只有跨语系才调用大模型 |
| `zhconv` | 简繁转换 | 简繁转换 | 幂等且不进译文缓存 |
| `ApiType` | 上游协议 | API 类型 | `chat` / `response` / `anthropic` |
| `api_key` | 密钥 | API 密钥 | 上游凭据; 与 Amane 自身的 token 区分 |

## 播放、网络与配置

| 符号 | 开发中文 | 用户中文 | 说明 |
|------|---------|---------|------|
| `playback` | 播放 | 播放 | 反代与 HLS 清单改写; 不实时转码 |
| `PlaybackProvider` / `source` | 播放源 | 播放源 | 插件实现; 顺序在插件页维护 |
| `PlaybackOffer` / `PlaybackTarget` | 播放目标 | 播放源 / 码流 | 探测返回可选项, 解析返回主机据此反代的地址 |
| `probe` / `resolve` / `subtitle` | 探测 / 解析 / 字幕轨 | — | 播放源的三个钩子 |
| `HlsLocator` | HLS 定位 | — | 清单与密钥 URI 的相对改写 |
| `cache_ttl` | 探测缓存 | — | 有上限约束 |
| `StreamClient` / `UpstreamGate` | 播放通道 / 通道额度 | 播放通道繁忙 | 全局与每来源并发上限, 等不到即 503 |
| `PlaybackFactory` / `PlaybackState` | 播放工厂 / 播放状态 | — | 跨 `rebuild` 存活: HLS token 表与探测缓存 |
| `HlsUriMap` | 分片 token 表 | — | 清单内 URI 登记为短 token, 不可定位也登记 |
| `ListedSource` | 列表行 | 码流 | 一行 = 一条流或一条不可用记录 |
| `BrowserPool` | 浏览器池 | — | 按来源复用 context 以保留挑战会话 |
| `Patchright` / `Camoufox` / `Solver` | 本地引擎 / solver | Patchright / Camoufox / Solver 服务 | 前两者是可选依赖, 不随打包分发 |
| `RateLimiters` | 限速器 | 速率限制 | 全局 > 站点 > 默认; 浏览器渲染复用同一许可 |
| `HttpExchangeRecorder` | HTTP 录制 | — | 与任务 `Recorder` 不同物 |
| `ConfigManager` | 配置管理器 | — | TOML 原子写; `preview` 只校验不落盘 |
| `data_dir` | 数据目录 | 数据目录 | 派生 DB / TOML / resources / token 路径 |
| `x-frozen-keys` / `x-hidden` | 冻结键 / 隐藏字段 | — | schema 扩展: 前者禁止 UI 加 key, 后者不进表单 |
| `max_retries` | 重试次数 | 最大重试次数 | 值 = 首次请求之外的重试次数; `0` 表示只发一次请求 |
| `stream` | 码流 | 码流 | 流列表按需加载 |
| `probe` / `resolve` | 探测 / 解析 | — | 播放源的两个钩子 |
| `hls` | HLS | HLS | 清单改写 |
| `proxy` | 代理 | 代理 | HTTP / SOCKS5 |
| `WebClient` | 出站 HTTP 通道 | — | 唯一出站通道; 限速器必须先于它构造 |
| `ColdSettings` | 冷配置 | 环境变量 | `AMANE_*`, 修改须重启 |
| `HotSettings` | 热配置 | 应用配置 | TOML, 经 `PATCH /api/config` 写入 |
| `rebuild` | 重建 | — | 不重启进程替换依赖热配置的对象链 |
| `AppRuntime` | 组合根 | — | 拥有启停顺序; HTTP 与 CLI / 回放共用 |
| `AppSession` | 运行会话 | — | 进程级运行时容器; 与 `AgentSession` (对话) 不同物 |
| `token` | Token | Token | Amane 自身的访问凭据; 上游凭据才是「密钥」 |

## 界面、桌面与发布

| 符号 | 开发中文 | 用户中文 | 说明 |
|------|---------|---------|------|
| `facet` | 分类 | 分类 | 用户层不出现 facet |
| `catalog` | 分类目录页 | 分类 | `/catalog`; 按 `FacetKind` 分栏 |
| `meta` | 片库 | 片库 | `/meta`, `Metadata` 的列表页; 页面内不称「元数据」 |
| `view` | 视图 | 视图 | 海报 / 列表 / 词云 |
| `FileBrowser` | 文件浏览器 | 浏览 | 复用 `/api/files` |
| `LibraryPicker` | 库选择器 | 媒体库 | 表单里的 `x-widget: LibraryPicker` |
| `entity` | 交付目标 | 类型 | `SavedQuery` 的三种交付: `metadata` 片库 / `actor` 演员 / `data` 数据 |
| `kind` | 种类 | 分类 | 分类页的维度参数; 与「类型」(`ContentType`) 不是一回事 |
| `view.cloud` | 芯片云 | 词云 | 按 `count` 缩放字号; en 作 Cloud |
| `sidecar` | 附属资源 | 附属资源模板 | 库路径模板配套产出的 NFO / 图片; 不指辅助进程 |
| `shell` | 壳 | — | Android / 桌面外壳; 用户层不出现「壳」 |
| `ShellBridge` | 壳桥 | — | 壳向页面暴露的动作接口, 不接受参数 |
| `desktop.env` | 桌面设置文件 | 设置文件 | 壳启动服务前读取的 `KEY=VALUE` 文件 |
| `supervised` | 监督者在场 | — | 为假时重启端点返回 403 |
| `menubar` / `tray` | 菜单栏 / 托盘 | 菜单栏 / 托盘 | 桌面形态; Windows 侧不写「任务栏」 |
| `WebView` | WebView | 系统 WebView | Android 壳不携带内核, 版本由用户设备决定 |
| `MIN_CHROMIUM_MAJOR` | 运行期下限 | Chromium 版本 | 只取自 UA 的 `Chrome/<版本>`; WebView 包版本与它不同物 |
| `onedir` | 打包 | — | PyInstaller 目录式产物 |
| `Native AOT` | 原生编译 | — | Windows 壳; 与 PyInstaller 均不能从 macOS 交叉编译 |
| `app-*` / `v*` | APP 发布线 / 本体发布线 | 版本 | 两条独立 tag 线; 「本体」指服务端与桌面端 |
| `bump` / tag | 发版 | 版本 | 只用 `just bump`, 不手改版本或手打 tag |
