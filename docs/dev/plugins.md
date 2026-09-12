# 来源插件

> 本文记录影片刮削来源插件的边界、发现顺序、配置契约与兼容性要求. 通用爬虫实现见 [crawlers.md](crawlers.md), 配置进程内 rebuild 见 [config.md](config.md).

## 插件边界

插件 API v1 开放**影片元数据来源**与**播放源**. 二者共用 `{cold.data_dir}/plugins/sources/<id>/` 与 HotSettings `plugins.<id>` 信封; 调用路径、输入输出与 Factory 分离. 插件不直接访问 Repository、任务 Worker、FastAPI 或前端运行时.

影片来源通过一个窄接口接收 `SearchQuery`, 返回 `MediaMetadata`, 然后进入现有聚合 DAG. `MediaMetadata.actors` 为 `list[FilmActor]`; 仍接受 `list[str]`, 性别为 `unknown`. API 版本号不因此递增. 出演者性别契约见 [crawlers.md](crawlers.md).

播放源接收主机装配的当前 Metadata 与关联文件快照, 返回探测结果或由主机执行的播放目标. 浏览器只请求 Amane 同源、已鉴权的固定媒体端点; 上游查找、鉴权、跨域与会话留在后端. HTTP 形状见 [api.md](api.md).

插件是可信的进程内纯 Python: `importlib` 从数据目录加载 `plugin.py`, 与主机共用解释器. 没有进程隔离. 插件不能声明自己的 pip 依赖或原生扩展, 只使用主机已提供的 API (经 `amane.plugin` 与 `context.http_client`).

## 发现与身份

插件作者只从 `amane.plugin` 导入类型与契约. 主机实现在 `amane.plugins.*` (发现、落盘安装、Factory), 内部代码不允许导入 `amane.plugin`. 这是导入路径上的分层, 不是运行时沙箱. 包内相对导入与绝对导入约定见 [architecture.md](architecture.md).

第三方来源是 `{cold.data_dir}/plugins/sources/<id>/` 下一棵源码树. 目录名就是来源 ID; 其中必须有 `plugin.py`, 并导出名为 `Plugin` 的类: `FilmSourcePlugin`、`PlaybackPlugin`, 或同时继承二者. 同目录其它 `.py` 可作为包内相对导入. 启动、安装、卸载和显式重新扫描时按目录名排序加载; 单个插件加载、descriptor 校验或 API 版本不兼容只使该插件不可用, 不阻断其它来源, 失败写入日志并出现在 `GET /api/plugins` 的 `failures` 里.

仅声明播放能力的插件必须显式写出 `playback`. descriptor 的能力集合缺省为影片元数据, 缺省值会使纯播放插件在发现期失败. 仅影片元数据的 drop-in 行为不变. `PLUGIN_API_VERSION` 仍为 `"1"`.

官方 / 内置来源使用单段 ID (`javdb`、`dmm`). 第三方来源 ID 必须是 `namespace.local`: 第一段是开发者声明的命名空间, 其后是该命名空间下的来源名, 可再分段 (`alice.javxyz`、`alice.foo.bar`). 命名空间不能是 `amane` / `plugin` / `official` / `builtin`, 也不能是任何内置 `SiteName`. ID 是持久化数据中的稳定 key, 出现在路由、`raw`、`source_urls`、`field_sources`、任务摘要和缓存 key 中; 显示名称不能代替 ID. descriptor 里的 `id` 必须与目录名一致, 否则该目录记为失败. 作者导入 `amane.plugin`, 主机经由 `amane.plugins.*` 与 `/api/plugins`; 来源 ID 本身不带 `plugin.` 前缀.

运行时数据仍在 `{cold.data_dir}/plugins/<id>/` (`PluginContext.data_dir`). 源码树在 `plugins/sources/<id>/`, 卸载只删源码树, 不删运行时数据.

内置影片来源仍使用原有字符串 ID, 与插件影片来源进入同一个 `CrawlerFactory`、HTTP 客户端、Host 限速器、聚合器和任务记录管线. 仅声明播放能力的插件不进入 `CrawlerFactory`, 也不允许写入 `content_routes` / `field_priority` / `field_blacklist`. 内容路由校验只检查路由里出现的 ID 是否具备影片元数据能力; 配置里只有 `plugins.<id>` 不触发该检查.

## 进程内重建

安装 / 卸载 / 重新扫描 / 启用 / 禁用都不重启进程. 它们共用一把 rebuild 锁: 先处理源码树, 再 `AppRuntime.rebuild()` 换网络栈、Factory 和 Worker, 并排空旧 Worker.

安装把一份 zip 解到 `plugins/sources/<id>/` (根目录或单一顶层文件夹里必须有 `plugin.py`); 同 ID 已存在则整棵替换. 把文件夹直接放到该路径后点「重新扫描」效果相同. 卸载删除对应源码目录. zip 拒绝路径穿越和过大载荷. Docker、源码运行和桌面打包进程采用同一条落盘路径.

进程内重建会 `invalidate_caches` 并删除 `amane_ext_*` 动态模块, 以便下一轮 `discover()` 能执行到新代码. `amane` 本体不会被卸模块. 进程内解释器无法阻止插件 `import amane.db`.

配置里的第三方来源路由和 `plugins` 段在卸载后可以残留; 刮削时跳过, 不阻断写入. 见 [config.md](config.md).

## Descriptor

插件 descriptor 声明来源能力、支持的内容类型、语言、访问 URL、多语言行为和默认速率. 路由校验在启动和配置热更新时执行: 已安装来源须声明影片元数据能力, 且若声明了内容类型集合则必须覆盖所配置的 `ContentType`. 尚未安装的第三方来源 ID (合法的 `namespace.local`) 可以留在路由里, 只记日志, 不阻断启动或写入.

`multi_language` 决定聚合器是否按字段语言展开 `(source, language)` 抓取节点. 不允许只在爬虫内部根据配置猜测该行为. 内置影片来源的 descriptor 从对应爬虫 `profile().effective_capabilities()` / `multi_language` 拷贝, 不另维护名单.

## 配置

持久化配置放在 HotSettings 的 `plugins` 字典中, 每个 key 是插件 ID, 值包含 `enabled` 和插件自己的 `config` 对象. 插件通过 `configuration_model()` 提供 Pydantic 校验模型和 JSON Schema.

配置校验顺序是: 构造候选 HotSettings → 由当前插件目录校验路由和插件配置 → 原子写入 TOML → 重建网络栈、Factory 和 Worker. 校验失败不会修改当前配置.

`enabled=false` 只让 Factory 跳过该来源, 不必先从 `content_routes` / `field_priority` / `field_blacklist` 删掉. 路由里的已禁用插件与尚未安装的第三方来源一样: Factory 不将该来源放入 `crawlers` 映射, 聚合执行跳过该节点、沿后续源继续, 不记 unexpected. 配置仍可写入. 更新单个插件配置时, 不会因为路由里还有其它缺失插件而拒绝.

外部插件配置不复用内置 `SiteConfig`. 内置 `site_config` 仍负责内置来源的 cookie、域名和通用站点参数; 插件应在自己的模型中声明所需字段.

插件配置 API:

- `GET /api/plugins`: 列出已发现的外部插件、descriptor、当前配置、源码路径和 JSON Schema.
- `POST /api/plugins`: 安装并进程内重建. `multipart/form-data` 二选一: 字段 `file` (浏览器 zip) 或 `path` (`safe_dirs` 内的插件目录或 zip; `ALLOW_ALL` 时不限目录).
- `POST /api/plugins/reload`: 只重新扫描 `plugins/sources` (手工拷贝之后用). 必须注册在 `/{plugin_id}` 之前.
- `GET /api/plugins/{plugin_id}`: 读取单个插件.
- `PATCH /api/plugins/{plugin_id}`: 更新启用状态和配置字段, 并触发进程内 rebuild.
- `DELETE /api/plugins/{plugin_id}`: 删除源码树并进程内重建目录.

## 播放源

刮削继续使用 `build` 返回影片 provider. 播放使用独立方法 `build_playback`, 避免同一类同时继承两种基类时返回类型冲突. 同一 zip 可以同时声明两种能力, 配置仍只有一份 `plugins.<id>`.

主机装配查询后调用:

- `probe`: 是否可播、展示名、媒体类型、是否可按字节寻址. `None` 表示本源没有对应流, 不带原因; 条目没有可播流而原因值得告诉用户时抛 `SourceError(NO_USABLE_METADATA, detail=中文原因)`, 列表以 `available=false` 与这条 `detail` 呈现, 名字仍取 descriptor 的展示名. 原因会原样展示, 只写用户能据以行动的信息 (哪个文件怎么了), 不写完整路径与上游地址.
- `resolve`: 打开码流前的目标. `None` 与 probe 相同语义.
- `subtitle`: 按轨道 id 返回 WebVTT 正文或上游 VTT. 缺省 `None`.
- 上游 / 网络 / 可分类失败抛 `SourceError`. 不允许把失败写成 `None`.
- 三个钩子都在事件循环上被调用, 不允许执行阻塞 I/O (`stat`、读取文件、同步 HTTP). 需要读盘的钩子用 `asyncio.to_thread` 提交到线程池: 阻塞事件循环会让整个服务端停止推进, 其它来源的探测一并超时. 探测预算由主机计时, 无法中断已经进入事件循环的阻塞调用.

主机不提供内置播放源: 播放源只来自插件. 本地文件播放由插件声明 `file` 目标实现, 主机只打开条目已索引的文件.

播放目标由主机执行, 插件只声明:

- `file`: 已入库文件. 插件回送快照 `query.files` 里的路径, 主机自行打开并输出. 主机只接受该条目已索引文件之一: 两侧都执行 `resolve()` 后逐条比较, 索引里的符号链接与插件回送的等价形式视为同一个文件, 因此指向库外的符号链接照常可播 — 打开的就是索引里的那条路径. 不检查库根、`safe_dirs` 与解析目标 (那是文件浏览器与路径模板的配置). 路径不在该条目索引中时返回 502 与可读原因, 这是插件侧的失败; 索引里的文件已从磁盘消失同样返回 502. 条目没有已索引文件时一律拒绝. 主机按单段 Range 输出 (206), 不可满足的范围返回 416 (响应体带 `detail`, 空文件与越界范围分开说明), 多段 Range 返回 400; 客户端断开后停止读取, 不再继续消耗网盘流量. 长度为 0 的文件由插件在探测阶段拒绝: 主机对它发出的任何 Range 都不可满足, 放行只会把失败推迟到播放时.
- `upstream`: 上游 URL 与仅服务端使用的请求头. 主机反向代理, 转发单段 Range, 密钥不得出现在响应头或重定向 Location. 指向播放列表的 `upstream` 仍拒绝; 清单必须经由 `hls`.
- `hls`: 插件提供 locator. 主机用「包含该 URI 的那份清单」的 base 做 `urljoin` 后再调用 `locate`, 因此 `locate` 收到绝对 URL. 相对 URI (含协议相对 `//host/...`) 解析后必须与该份清单 Origin 相同; 清单内已写出的绝对 `http`/`https` URL 由插件承担, 主机仍会代理. 其它 scheme 拒绝. 主机把清单 URI 改写到本机前缀并反向代理分片、密钥与子清单. 清单内不得残留上游 Origin. 主机不自行推导新的 Origin, 只跟随清单里已声明的绝对地址: 需要跨源时写绝对 URL, 协议相对形式一律拒绝. 无法定位的 URI 只作废自己: 主机在原位置登记一个必定失败的 token 并记录原因, 清单其余部分照常可播, 请求该 token 时返回 502 与该原因; 该 token 与普通 token 一样绑定来源 / 条目 / 文件, 其它来源或条目请求一律 404. 密钥标签 (`#EXT-X-KEY` / `#EXT-X-SESSION-KEY`) 的 URI 例外: 缺密钥整份清单都播不了, 它在改写阶段直接让整份清单 502.

`probe.content_type` 必须与随后 `resolve` 的目标种类一致: HLS 用 `mpegurl`, 逐字节码流用 `video/*` / `audio/*`. 列表 `href` 按探测类型指向清单或码流; 不一致时前端会按错误方式初始化, 清单端点对非 HLS 目标返回 502.

`resolve` 的结果默认不缓存: 主机每次真正取流都调用插件的 `resolve` (逐字节码流是每个 HTTP 请求一次, 清单是每次取清单一次). 播放目标上的 `cache_ttl` (秒, 必须为正数) 声明本次结果的可复用时长, 主机按「来源 + 条目 + 所选文件」在这段时间内复用, 宿主上限 `RESOLVE_TTL_MAX_SECONDS` (300 秒) 截断过长的声明. 不声明即不缓存, 与没有这个字段时完全一致. 签名 URL 与会话令牌必须声明不超过其实际有效期的值: 声明过长会让主机把已失效的地址继续交给播放器, 表现为播放失败; 声明短了只多解析一次. 只有成功解析出的目标进这条缓存, `SourceError` 与 `None` 仍走打开失败与探测的负缓存.

探测预算 2 秒. 超时由主机标记为不可用, 列表以 `available=false` 与 `detail="探测超时"` 呈现, 不是插件返回 `None`. 网络失败抛 `SourceError`. `NO_USABLE_METADATA` 与 `None` 走同一条探测负缓存, 原因跟着缓存保留; 其余 `SourceError` 归为「上游失败」并单独记负缓存. `probe` 内访问网络可能耗尽预算; 短探测、把取流留到 `resolve`.

探测结果可附带 WebVTT 轨道. 插件通过 `subtitle` 返回 VTT 正文或上游 VTT 地址; 正文由插件自行准备, 主机不转换字幕格式, 也不读取本机字幕文件. 上游字幕只接受 `text/vtt` (及缺省类型).

**不允许在 Amane 主机内对码流做实时转码.** 浏览器无法直接播放时, 由上游提供 HLS 清单; 主机只改写 URI 并代理分片.

**断连不在 `Request` 上探测**: 本项目的中间件栈让 `Request.is_disconnected()` 恒为 `False` (原因见 [api.md](api.md)). 响应在自己的 `__call__` 里启动 `DisconnectSignal` (`playback/disconnect.py`) 的等待任务, 本机文件读循环与上游正文生成器只查这个标记, 客户端离开后不再读取、不再拉取上游. 中间件已缓冲的分块仍会送出, 但读取不会越过当前这一块.

码流 I/O 不复用刮削 `HttpClient` / `WebClient`. 反向代理使用独立流式客户端: 禁止缓冲完整正文, 浏览器断开则取消上游, 每源与全局有出口并发上限 (满载时短等待后 503), 请求上游时 `Accept-Encoding: identity`, 禁止跟随 301/302/303/307/308, 上游 304 与 416 原样返回 (304 不写 `immutable`), 其余 4xx/5xx 不得写入不可变缓存, 剥离 hop-by-hop 与 `Set-Cookie`. 上游声明非 identity 的 `Content-Encoding` 时丢弃 `Content-Length` — 主机转发的是 httpx 解码后的正文, 该值不再成立. 畸形上游 URL 归为 502, 且任何失败路径都必须归还出口额度. token 表与探测缓存跨 rebuild 存活 (所有权在 `AppRuntime`), 只在插件集合变化 (安装 / 卸载 / 重载 / 启停) 时清空 — 播放中修改任意热配置不得让在播 HLS 会话的分片失效. 解析结果缓存相反, 每次 rebuild 都清空: 配置改动可能更换凭据与签名参数, 旧目标不再可信; 于是「改热配置不中断在播会话」与「改热配置后重新解析」并存, `cache_ttl` 只在同一份配置内生效. 被替换的流式客户端等在途请求结束后关闭 (30 秒兜底), 分片读超时 30 秒, 避免卡死的上游长期占用出口额度. 单个长响应 (非 HLS 的上游码流, 浏览器一次 Range 拉完整段) 在 rebuild 后最多再续 30 秒, 之后由播放器重新发起 Range 请求; token 跨 rebuild 存活, 重连可直接成功. HLS 分片请求短, 不受影响. 播放响应带 `X-Content-Type-Options: nosniff`. 探测失败与打开失败使用独立的进程内 TTL, 不复用图片代理负缓存, 也不把码流写入 `ResourceStore`. `probe` 返回 `None` 与探测失败分条缓存.

清单内分片按「非播放列表即放行」处理, 不设媒体类型白名单: 上游 CDN 普遍伪装分片的扩展名与 `Content-Type`, 按类型拒绝会让整条流无法播放. 转发时响应类型一律中和为 `application/octet-stream`, 只有 `text/vtt` (清单内字幕分片) 与 `text/plain` (文本型 AES 密钥的常见默认类型) 保留原类型; 与 `nosniff` 一起, 上游即使返回可执行类型也不会被浏览器按该类型处理. 播放列表类型仍拒绝. 分片缓存按用途区分, 判定依据是上游声明的类型: 媒体分片与初始化段可用不可变缓存; 来自 `#EXT-X-KEY` / `#EXT-X-SESSION-KEY` 的 URI 一律 `no-store` (同一 URI 的密钥内容会轮换), 其余文本类用 `no-cache`. `SourceError.detail` 会原样进入 502 响应体并展示给终端用户, 不允许在其中写入上游 URL、密钥或签名参数.

`RelativeHlsLocator` 的 `http_client` 是刮削客户端 (跟随刮削侧重定向、重试与任务 HTTP 记录). 正式插件应传入已读取的 `playlist_text`; 分片由主机代理.

`file` 目标由主机输出, 响应不写 `Content-Disposition: attachment` — 浏览器会因此下载而不是播放.

路由层的 `source_id` 只限制长度, 不重复校验字符集: 第三方 ID 的命名空间规则只在 descriptor 加载期执行.

插件短 JSON (如查询媒体库条目) 仍经由 `context.http_client`. 需要输出本机文件时声明 `file` 目标, 由主机打开; 插件不自行读盘, 也不把文件正文交给主机.

## 网络和运行时

插件通过 `PluginContext` 得到共享 `HttpClient`、`WebClient` 和 `data_dir`. 使用共享客户端是契约的一部分, 确保插件请求遵守 Amane 的代理、重试、Host 限速和任务 HTTP 记录. HTML 用 `http_client.get_html` (拦截页抛 `SourceError`), JSON API 用 `get_json`. `data_dir` 是 `{cold.data_dir}/plugins/<plugin_id>`, 创建 provider 时确保目录存在; 插件不允许写入该目录之外.

`fetch` 未命中返回 `None`; 网络 / 拦截 / 可分类业务失败抛 `SourceError` (含 `RequestError`), 由 `invoke_source` 记入与内置来源同一套 `SiteOutcomeRecord`. 不允许 `except RequestError: return None`, 也不允许裸 `except Exception`. 吞异常时记为 `no_usable_metadata`, 任务不失败.

插件 provider 在 `CrawlerFactory` 中按来源 ID 延迟创建并缓存. 禁用插件不会创建 provider; 构造失败只会使本次来源请求不可用, 并由 Factory 记录异常. 目录替换后 Factory 随 rebuild 重建, 缓存不会跨卸载存活.

## 记录与脱敏

刮削任务的 Hot 配置快照包含 `plugins` section. 插件配置中名称包含 `api_key`、`token`、`secret`、`password`、`cookie`、`credential` 或 `dsn` 的值会在公开记录中脱敏, 明文仅在本地 secrets 快照存在时保留.

插件来源使用与内置来源相同的 `SiteOutcomeRecord` 和 HTTP 记录格式. 任务记录当前保存来源 ID 与插件配置, 不保存 descriptor / version 快照; 插件版本快照和回放兼容性尚未纳入当前 API 版本. 改变插件 ID 会使历史 `raw` 来源失去原有身份, 不允许这样做.

面向社区作者的开发步骤见 [用户文档](../user/plugins.md). 本文只写本仓库主机侧契约.

当前不支持插件自定义任务、数据库迁移、API 路由、React 页面、演员来源、进程隔离、插件自带第三方依赖. 不允许主机实时转码. 需要其它能力时应先扩展插件 API 版本和对应的权限边界.
