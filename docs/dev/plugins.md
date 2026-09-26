# 来源插件

> 本文记录影片刮削来源与播放源插件的边界、发现顺序、配置契约与兼容性要求. 通用爬虫实现见 [crawlers.md](crawlers.md), 配置进程内 rebuild 见 [config.md](config.md).

## 插件边界

插件 API v1 开放**影片元数据来源**与**播放源**. 二者共用 `{cold.data_dir}/plugins/sources/<id>/` 与 HotSettings `plugins.<id>` 信封; 调用路径、输入输出与 Factory 分离. 插件不直接访问 Repository、任务 Worker、FastAPI 或前端运行时.

影片来源通过一个窄接口接收 `SearchQuery` 并返回 `MediaMetadata`, 随后进入现有聚合 DAG. `MediaMetadata.actors` 为 `list[FilmActor]`, 也接受 `list[str]` (性别 `unknown`), API 版本号不因此递增; 性别契约见 [crawlers.md](crawlers.md).

播放源接收主机装配的当前 Metadata 与关联文件快照, 返回探测结果或由主机执行的播放目标. 浏览器只请求 Amane 同源、已鉴权的固定媒体端点, 上游查找、鉴权、跨域与会话留在后端; HTTP 形状见 [api.md](api.md).

插件是可信的进程内纯 Python: `importlib` 从数据目录加载 `plugin.py`, 与主机共用解释器, 没有进程隔离. 插件不能声明自己的 pip 依赖或原生扩展, 只使用主机已提供的 API (经 `amane.plugin` 与 `context.http_client`). **标准库可以放心 import**: 桌面打包版按平台收集整个标准库 (只排除依赖包外产物的 GUI 与安装器, 见 [desktop.md](desktop.md)), Docker 与源码运行用的是完整标准库. 第三方包与原生扩展不可用: 安装时导入失败的模块以可读的 422 报出, 已放好的目录在发现期进入 `failures`.

## 发现与身份

插件作者只从 `amane.plugin` 导入类型与契约; 主机实现在 `amane.plugins.*` (发现、落盘安装、Factory), 内部代码不允许导入 `amane.plugin`. 这是导入路径上的分层, 不是运行时沙箱.

第三方来源是 `{cold.data_dir}/plugins/sources/<id>/` 下一棵源码树: 目录名就是来源 ID, 其中必须有 `plugin.py` 并导出名为 `Plugin` 的类 (`FilmSourcePlugin`、`PlaybackPlugin`, 或同时继承二者), 同目录其它 `.py` 可作为包内相对导入. 启动、安装、卸载与显式重新扫描时按目录名排序加载; 单个插件加载 / descriptor 校验 / API 版本不兼容只使该插件不可用, 不阻断其它来源, 失败写入日志并出现在 `GET /api/plugins` 的 `failures` 里.

仅声明播放能力的插件必须显式写出 `playback`, 否则能力集合缺省为影片元数据, 纯播放插件在发现期失败. `PLUGIN_API_VERSION` 仍为 `"1"`.

官方 / 内置来源使用单段 ID (`javdb`、`dmm`); 第三方来源 ID 必须是 `namespace.local` (第一段是开发者声明的命名空间, 可再分段), 命名空间不能是 `amane` / `plugin` / `official` / `builtin`, 也不能是任何内置 `SiteName`. ID 是持久化数据中的稳定 key, 出现在路由、`raw`、`source_urls`、`field_sources`、任务摘要和缓存 key 中, **改变插件 ID 会使历史 `raw` 失去原有身份, 不允许这样做**; 显示名称不能代替 ID. descriptor 里的 `id` 必须与目录名一致, 否则该目录记为失败. 运行时数据在 `{cold.data_dir}/plugins/<id>/` (`PluginContext.data_dir`), 卸载只删源码树, 不删运行时数据.

内置影片来源与插件影片来源进入同一个 `CrawlerFactory`、HTTP 客户端、Host 限速器、聚合器和任务记录管线. 仅声明播放能力的插件不进入 `CrawlerFactory`, 也不允许写入 `content_routes` / `field_priority` / `field_blacklist`; 内容路由校验只检查路由里出现的 ID 是否具备影片元数据能力, 配置里只有 `plugins.<id>` 不触发该检查.

## 进程内重建

安装 / 卸载 / 重新扫描 / 启用 / 禁用都不重启进程: 它们共用一把 rebuild 锁, 先处理源码树, 再 `AppRuntime.rebuild()` 换网络栈、Factory 和 Worker 并排空旧 Worker. 安装把一份 zip 解到 `plugins/sources/<id>/` (根目录或单一顶层文件夹里必须有 `plugin.py`), 同 ID 已存在则整棵替换; 把文件夹直接放到该路径后点「重新扫描」效果相同. zip 拒绝路径穿越和过大载荷.

重建会 `invalidate_caches` 并删除 `amane_ext_*` 动态模块, 以便下一轮 `discover()` 执行到新代码; `amane` 本体不会被卸模块, 进程内解释器也无法阻止插件 `import amane.db`. 配置里的第三方来源路由和 `plugins` 段在卸载后可以残留 — 刮削时跳过, 不阻断写入 (见 [config.md](config.md)).

## Descriptor

descriptor 声明来源能力、支持的内容类型、语言、访问 URL、多语言行为和默认速率. 路由校验在启动与配置热更新时执行: 已安装来源须声明影片元数据能力, 且若声明了内容类型集合则必须覆盖所配置的 `ContentType`; 尚未安装的合法第三方来源 ID 可以留在路由里, 只记日志.

`multi_language` 决定聚合器是否按字段语言展开 `(source, language)` 抓取节点, 不允许只在爬虫内部根据配置猜测该行为. 内置影片来源的 descriptor 从对应爬虫 `profile().effective_capabilities()` / `multi_language` 拷贝, 不另维护名单.

## 配置

持久化配置放在 HotSettings 的 `plugins` 字典中, 每个 key 是插件 ID, 值包含 `enabled` 和插件自己的 `config` 对象; 插件通过 `configuration_model()` 提供 Pydantic 校验模型和 JSON Schema. 外部插件配置不复用内置 `SiteConfig` (后者仍负责内置来源的 cookie、域名与通用站点参数).

校验顺序是: 构造候选 HotSettings → 由当前插件目录校验路由和插件配置 → 原子写入 TOML → 重建网络栈、Factory 和 Worker; 校验失败不修改当前配置. `enabled=false` 只让 Factory 跳过该来源, 不必先从路由里删掉, 配置仍可写入; 更新单个插件配置时不会因为路由里还有其它缺失插件而拒绝.

插件配置 API: `GET /api/plugins` (发现结果 / descriptor / 配置 / JSON Schema)、`POST /api/plugins` (安装, `multipart/form-data` 二选一: `file` 为浏览器 zip, `path` 为 `safe_dirs` 内的插件目录或 zip)、`POST /api/plugins/reload` (只重新扫描 `plugins/sources`, 必须注册在 `/{plugin_id}` 之前)、`GET|PATCH|DELETE /api/plugins/{plugin_id}`.

## 播放源

刮削继续使用 `build` 返回影片 provider; 播放使用独立方法 `build_playback`, 避免同一类同时继承两种基类时返回类型冲突. 同一 zip 可以同时声明两种能力, 配置仍只有一份 `plugins.<id>`.

主机在**用户切到某个来源时**才装配查询并调用该来源:

- 播放源列表 (`GET /api/playback/sources`) 不调用插件: 只列出已启用的来源名, 因此打开详情页不产生任何上游请求; 用户切到某个来源时才探测它, 探测结果按「来源 + 条目」缓存一小段时间.
- `probe`: 列出本源在这个条目上能提供的流, 一条流一项: 稳定标识 `key`、来源内的展示名 `name`、媒体类型、是否可按字节寻址, 以及不可播时的原因 `unavailable`. 一个来源可以给出多条流 (多个搜索结果、多个已入库文件), 顺序即列表顺序; 条目里没有一条可播时仍把候选列出来并逐条说明原因. 列表与用户的选择无关, 因此这里拿到的选中项恒为 `None`.
- `probe` 的两种否定回答必须分清: 返回**空元组**表示「这个来源没有内容, 也没什么可解释的」; 抛 `SourceError(NO_USABLE_METADATA, detail=中文原因)` 表示「没有内容, 但原因值得告诉用户」, 列表以 `available=false` 与这条 `detail` 呈现. 两种都不缓存.
- `resolve`: 打开码流前的目标, 按 `query.selected_key` 定位用户选中的流; `selected_key` 为 `None` 表示没有指定, 由插件自己挑一条. 返回 `None` 与 probe 的空元组同义.
- `subtitle`: 按轨道 id 返回 WebVTT 正文或上游 VTT; 缺省 `None`. 主机不转换字幕格式, 也不读取本机字幕文件.
- 上游 / 网络 / 可分类失败抛 `SourceError`, 不允许把失败写成 `None`.
- 三个钩子都在事件循环上被调用, **不允许执行阻塞 I/O** (`stat`、读取文件、同步 HTTP); 需要读盘的钩子用 `asyncio.to_thread` 提交线程池 — 阻塞事件循环会让整个服务端停止推进, 其它来源的探测一并超时.

主机不提供内置播放源: 播放源只来自插件, 本地文件播放由插件声明 `file` 目标实现. 播放目标由主机执行, 插件只声明:

- `file`: 已入库文件. 插件回送快照 `query.files` 里的路径, 主机自行打开并输出. 主机只接受该条目已索引文件之一 (两侧都执行 `resolve()` 后逐条比较, 索引里的符号链接与插件回送的等价形式视为同一个文件, 因此指向库外的符号链接照常可播); 不检查库根与 `safe_dirs`. 路径不在索引中、或索引里的文件已从磁盘消失, 均返回 502 与可读原因; 条目没有已索引文件时一律拒绝. 长度为 0 的文件由插件在探测阶段拒绝, 主机对它发出的任何 Range 都不可满足.
- `upstream`: 上游 URL 与仅服务端使用的请求头. 主机反向代理并转发单段 Range, 密钥不得出现在响应头或重定向 Location; 指向播放列表的 `upstream` 仍拒绝, 清单必须经由 `hls`.
- `hls`: 插件提供 locator. 主机用「包含该 URI 的那份清单」的 base 做 `urljoin` 后再调用 `locate`, 因此 `locate` 收到绝对 URL; 相对 URI (含协议相对 `//host/...`) 解析后必须与该份清单 Origin 相同, 其它 scheme 拒绝. 主机把清单 URI 改写到本机前缀并反向代理分片、密钥与子清单, 清单内不得残留上游 Origin. 主机不自行推导新的 Origin, 只跟随清单里已声明的绝对地址. 无法定位的 URI 只作废自己 (登记一个必定失败的 token, 请求时返回 502 与原因), 清单其余部分照常可播; 密钥标签的 URI 例外 — 缺密钥整份清单都播不了, 直接让整份清单 502.

`key` 由插件声明并保证在同一来源与条目内**稳定且唯一**: 浏览器地址、解析结果缓存、HLS 分片 token 的归属都按它区分, 改动它等于让在播地址失效, 重复则两条流共用同一个地址与缓存桶 (主机丢弃后一条并记日志). 形状是 `^[a-zA-Z0-9._-]{1,64}$`, 整值 `.` 与 `..` 被拒绝 (会被浏览器与服务器归一化掉). 主机不解释 `key` 的含义, 也不核对它是否对应该条目的某个文件 — 认不出来的 `key` 由插件自己以 `SourceError(NO_USABLE_METADATA)` 拒绝. 列表每一行的展示名是 `来源名 · 流的展示名`, 因此 `name` 里不要重复来源名; 主机按来源拼接、原样输出, 不二次排序, 前端默认选中第一条可播的. 来源之间的先后是纯客户端的用户偏好 (插件页维护, 前端据此重排来源列表), 主机不持久化播放源顺序.

`probe` 的 `unavailable` 是**探测时已知不可播** (文件为空、已从磁盘消失), 原因原样展示给用户, 且只写用户能据以行动的信息 (哪个文件怎么了), 不写完整路径与上游地址. 省略它表示探测时未发现不可播, **不是点播保证**: 可播性只是探测瞬间的观测, `resolve` 仍可能以自己的原因 502. `probe.content_type` 必须与随后 `resolve` 的目标种类一致 (HLS 用 `mpegurl`, 逐字节码流用 `video/*` / `audio/*`).

`resolve` 的结果默认不缓存: 主机每次真正取流都调用它, 播放目标上的 `cache_ttl` (秒, 必须为正数) 声明本次结果的可复用时长, 主机按「来源 + 条目 + 所选流」在 `RESOLVE_TTL_MAX_SECONDS` (300 秒) 内复用. 签名 URL 与会话令牌必须声明不超过其实际有效期的值, 声明过长会让主机把已失效的地址继续交给播放器.

**主机不给探测设时限**: 探测发生在用户切到该来源时, 慢就是他在等, 因此插件不必为迁就预算而砍掉候选或返回半截结果. 只有两点要求: 探测要么给出完整结果, 要么整条来源报不可用并说明原因; 一次请求挂住时插件要自己收尾 (抛 `SourceError`) 而不是永久挂住. 抛错时该来源在列表里只剩一行不可用, 以 `detail="探测超时"` / `"上游失败"` / `"探测失败"` 呈现. 需要按候选逐个访问上游的清单只能在 `resolve` 做.

**不允许在 Amane 主机内对码流做实时转码**: 浏览器无法直接播放时由上游提供 HLS 清单, 主机只改写 URI 并代理分片.

**断连不在 `Request` 上探测**: 中间件栈让 `Request.is_disconnected()` 恒为 `False` (原因见 [api.md](api.md)), 长响应用 `playback/disconnect.py` 的 `DisconnectSignal`.

码流 I/O 不复用刮削 `HttpClient` / `WebClient`, 改用独立流式客户端: 不缓冲完整正文, 浏览器断开则取消上游, 每源与全局有出口并发上限, 请求上游时 `Accept-Encoding: identity`, 不跟随重定向, 上游 304 与 416 原样返回, 剥离 hop-by-hop 与 `Set-Cookie`, 任何失败路径都必须归还出口额度. 分片按「非播放列表即放行」处理, 不设媒体类型白名单 — 上游 CDN 普遍伪装分片的扩展名与 `Content-Type`; 响应类型一律中和为 `application/octet-stream` (`text/vtt` 与文本型 AES 密钥保留原类型), 播放列表类型仍拒绝. 分片缓存按上游声明的类型区分: 媒体分片与初始化段可用不可变缓存, 密钥 URI 一律 `no-store`, 其余 4xx / 5xx 不写入不可变缓存.

**token 表与探测缓存跨 rebuild 存活** (所有权在 `AppRuntime`), 只在插件集合变化 (安装 / 卸载 / 重载 / 启停) 时清空 — 播放中修改任意热配置不得让在播 HLS 会话的分片失效; **解析结果缓存相反, 每次 rebuild 都清空**, 因为配置改动可能更换凭据与签名参数. 探测失败与打开失败使用独立的进程内 TTL, 不复用图片代理负缓存, 也不把码流写入 `ResourceStore`.

`SourceError.detail` 会原样进入 502 响应体并展示给终端用户, 不允许在其中写入上游 URL、密钥或签名参数. 路由层的 `source_id` 只限制长度, 第三方 ID 的命名空间规则只在 descriptor 加载期执行.

## 网络和运行时

插件通过 `PluginContext` 得到共享 `HttpClient`、`WebClient` 和 `data_dir`. 使用共享客户端是契约的一部分: 插件请求必须遵守 Amane 的代理、重试、Host 限速和任务 HTTP 记录. HTML 用 `http_client.get_html` (拦截页抛 `SourceError`), JSON API 用 `get_json`. `data_dir` 是 `{cold.data_dir}/plugins/<plugin_id>`, 插件不允许写入该目录之外. 插件短 JSON 仍经由 `context.http_client`; 需要输出本机文件时声明 `file` 目标由主机打开, 插件不自行读盘.

`fetch` 未命中返回 `None`; 网络 / 拦截 / 可分类业务失败抛 `SourceError` (含 `RequestError`), 由 `invoke_source` 记入与内置来源同一套 `SiteOutcomeRecord`. 不允许 `except RequestError: return None`, 也不允许裸 `except Exception` — 吞异常会被记为 `no_usable_metadata`, 任务不失败.

`FilmSourceProvider.check_connectivity` 是可选钩子, 供「网络检测」页探测本源. 返回 `None` = 未声明, 主机按 descriptor 的首个 URL 探测; 入口不是该 URL (登录页 / 需要 token 的 API) 或凭据缺失时覆盖它, 后者返回 `ConnectivityOutcome.skipped(SkipReason.MISSING_CREDENTIAL)`; 抛 `SourceError` 也接受 (主机按异常上的原因与状态码报). `detail` 会被界面原样渲染, 因此只放语言中立的补充 (例如缺失的配置项名), 不写中文句子. 这是向后兼容的增量, 已有插件不实现也能被探测, `PLUGIN_API_VERSION` 不变.

插件 provider 在 `CrawlerFactory` 中按来源 ID 延迟创建并缓存; 禁用插件不会创建 provider, 构造失败只使本次来源请求不可用并由 Factory 记录异常. 目录替换后 Factory 随 rebuild 重建, 缓存不跨卸载存活.

## 记录与脱敏

刮削任务的 Hot 配置快照包含 `plugins` section, 其中的密钥字段按 [observability.md](observability.md) 的脱敏规则处理. 插件来源使用与内置来源相同的 `SiteOutcomeRecord` 和 HTTP 记录格式. 任务记录当前保存来源 ID 与插件配置, 不保存 descriptor / version 快照; 插件版本快照与回放兼容性尚未纳入当前 API 版本.

面向社区作者的开发步骤见 [用户文档](../user/plugins.md), 本文只写本仓库主机侧契约. 当前不支持插件自定义任务、数据库迁移、API 路由、React 页面、演员来源、进程隔离、插件自带第三方依赖; 需要其它能力时应先扩展插件 API 版本和对应的权限边界.
