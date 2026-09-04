# 来源插件

> 本文记录影片刮削来源插件的边界、发现顺序、配置契约与兼容性要求. 通用爬虫实现见 [crawlers.md](crawlers.md), 配置进程内 rebuild 见 [config.md](config.md).

## 插件边界

插件 API v1 只开放**影片元数据来源**. 插件通过一个窄接口接收 `SearchQuery`, 返回 `MediaMetadata`, 然后进入现有聚合 DAG; 插件不直接访问 Repository、任务 Worker、FastAPI 或前端运行时. `MediaMetadata.actors` 为 `list[FilmActor]`; 仍接受 `list[str]`, 性别为 `unknown`. API 版本号不因此递增. 出演者性别契约见 [crawlers.md](crawlers.md).

插件是可信的进程内纯 Python: `importlib` 从数据目录加载 `plugin.py`, 与主机共用解释器. 没有进程隔离. 插件不能声明自己的 pip 依赖或原生扩展, 只使用主机已提供的 API (经 `amane.plugin` 与 `context.http_client`).

## 发现与身份

插件作者只从 `amane.plugin` 导入类型与契约. 主机实现在 `amane.plugins.*` (发现、落盘安装、Factory), 内部代码不允许导入 `amane.plugin`. 这是导入路径上的分层, 不是运行时沙箱. 包内相对导入与绝对导入约定见 [architecture.md](architecture.md).

第三方来源是 `{cold.data_dir}/plugins/sources/<id>/` 下一棵源码树. 目录名就是来源 ID; 其中必须有 `plugin.py`, 并导出名为 `Plugin` 的 `FilmSourcePlugin` 子类. 同目录其它 `.py` 可作为包内相对导入. 启动、安装、卸载和显式重新扫描时按目录名排序加载; 单个插件加载、descriptor 校验或 API 版本不兼容只使该插件不可用, 不阻断其它来源, 失败写入日志并出现在 `GET /api/plugins` 的 `failures` 里.

官方 / 内置来源使用单段 ID (`javdb`、`dmm`). 第三方来源 ID 必须是 `namespace.local`: 第一段是开发者声明的命名空间, 其后是该命名空间下的来源名, 可再分段 (`alice.javxyz`、`alice.foo.bar`). 命名空间不能是 `amane` / `plugin` / `official` / `builtin`, 也不能是任何内置 `SiteName`. ID 是持久化数据中的稳定 key, 出现在路由、`raw`、`source_urls`、`field_sources`、任务摘要和缓存 key 中; 显示名称不能代替 ID. descriptor 里的 `id` 必须与目录名一致, 否则该目录记为失败. 作者导入 `amane.plugin`, 主机经由 `amane.plugins.*` 与 `/api/plugins`; 来源 ID 本身不带 `plugin.` 前缀.

运行时数据仍在 `{cold.data_dir}/plugins/<id>/` (`PluginContext.data_dir`). 源码树在 `plugins/sources/<id>/`, 卸载只删源码树, 不删运行时数据.

内置来源仍使用原有字符串 ID. 插件来源与内置来源进入同一个 `CrawlerFactory`、HTTP 客户端、Host 限速器、聚合器和任务记录管线.

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

`enabled=false` 只让 Factory 跳过该来源, 不必先从 `content_routes` / `field_priority` 删掉. 路由里的已禁用插件与尚未安装的第三方来源一样: Factory 不将该来源放入 `crawlers` 映射, 聚合执行跳过该节点、沿后续源继续, 不记 unexpected. 配置仍可写入. 更新单个插件配置时, 不会因为路由里还有其它缺失插件而拒绝.

外部插件配置不复用内置 `SiteConfig`. 内置 `site_config` 仍负责内置来源的 cookie、域名和通用站点参数; 插件应在自己的模型中声明所需字段.

插件配置 API:

- `GET /api/plugins`: 列出已发现的外部插件、descriptor、当前配置、源码路径和 JSON Schema.
- `POST /api/plugins`: 安装并进程内重建. `multipart/form-data` 二选一: 字段 `file` (浏览器 zip) 或 `path` (`safe_dirs` 内的插件目录或 zip; `ALLOW_ALL` 时不限目录).
- `POST /api/plugins/reload`: 只重新扫描 `plugins/sources` (手工拷贝之后用). 必须注册在 `/{plugin_id}` 之前.
- `GET /api/plugins/{plugin_id}`: 读取单个插件.
- `PATCH /api/plugins/{plugin_id}`: 更新启用状态和配置字段, 并触发进程内 rebuild.
- `DELETE /api/plugins/{plugin_id}`: 删除源码树并进程内重建目录.

## 网络和运行时

插件通过 `PluginContext` 得到共享 `HttpClient`、`WebClient` 和 `data_dir`. 使用共享客户端是契约的一部分, 确保插件请求遵守 Amane 的代理、重试、Host 限速和任务 HTTP 记录. HTML 用 `http_client.get_html` (拦截页抛 `SourceError`), JSON API 用 `get_json`. `data_dir` 是 `{cold.data_dir}/plugins/<plugin_id>`, 创建 provider 时确保目录存在; 插件不允许写入该目录之外.

`fetch` 未命中返回 `None`; 网络 / 拦截 / 可分类业务失败抛 `SourceError` (含 `RequestError`), 由 `invoke_source` 记入与内置来源同一套 `SiteOutcomeRecord`. 不允许 `except RequestError: return None`, 也不允许裸 `except Exception`. 吞异常时记为 `no_usable_metadata`, 任务不失败.

插件 provider 在 `CrawlerFactory` 中按来源 ID 延迟创建并缓存. 禁用插件不会创建 provider; 构造失败只会使本次来源请求不可用, 并由 Factory 记录异常. 目录替换后 Factory 随 rebuild 重建, 缓存不会跨卸载存活.

## 记录与脱敏

刮削任务的 Hot 配置快照包含 `plugins` section. 插件配置中名称包含 `api_key`、`token`、`secret`、`password`、`cookie`、`credential` 或 `dsn` 的值会在公开记录中脱敏, 明文仅在本地 secrets 快照存在时保留.

插件来源使用与内置来源相同的 `SiteOutcomeRecord` 和 HTTP 记录格式. 任务记录当前保存来源 ID 与插件配置, 不保存 descriptor / version 快照; 插件版本快照和回放兼容性尚未纳入当前 API 版本. 改变插件 ID 会使历史 `raw` 来源失去原有身份, 不允许这样做.

面向社区作者的开发步骤见 [用户文档](../user/plugins.md). 本文只写本仓库主机侧契约.

当前不支持插件自定义任务、数据库迁移、API 路由、React 页面、演员来源、进程隔离或插件自带第三方依赖. 需要这些能力时应先扩展插件 API 版本和对应的权限边界.
