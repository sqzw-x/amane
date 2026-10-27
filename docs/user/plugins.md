# 插件

插件为 Amane 增加新的影片刮削来源或播放源. 本页前半部分面向使用插件的用户, 说明安装、配置与卸载; 后半部分面向插件开发者, 说明目录结构、接口与分发.

## 概览

插件按声明的能力分为两类, 同一个插件可以同时具备两种能力:

| 类型 | 作用 | 生效方式 |
|------|------|----------|
| 影片刮削来源 | 刮削时提供影片元数据, 与内置站点一同参与聚合 | 加入「设置 → 影片刮削」的类型路由后参与刮削 |
| 播放源 | 在影片详情页提供可播放的流, 例如已入库文件或上游地址 | 启用后即出现在详情页的播放源列表 |

Amane 不内置播放源, 详情页的播放功能全部来自播放源插件, 用法见 [播放](playback.md).

Amane 没有官方插件市场. 社区开发的插件登记在 GitHub Issue 的 [eco:project](https://github.com/sqzw-x/amane/issues?q=is%3Aissue+label%3Aeco%3Aproject) 标签下, 由各自作者维护, Amane 项目不对其提供支持保证.

!!! danger "安全提示"
    插件在 Amane 服务进程内运行, 与服务共用同一个 Python 解释器, 没有进程隔离, **可以执行任意代码**, 包括读写服务进程有权访问的任何文件. 只安装来源可信、经过代码审查的插件.

## 安装

插件页位于侧栏「管理 → 插件」. 安装方式有两种:

- **上传 zip**: 点击上传图标, 从本机选择插件 zip 文件.
- **服务器路径**: 在「安装插件」输入框中浏览或填写服务器上已有的插件目录或 zip 文件, 再点击「安装插件」.

安装采用热加载: 成功后插件立即出现在列表中, 不需要重启服务. 正在执行的任务继续使用旧的插件实例完成, 之后的新任务使用新安装的版本. 出于安全考虑, Amane 不提供从网址下载安装插件的功能.

服务器路径受 `AMANE_SAFE_DIRS` 约束, 必须位于安全目录内 (见 [配置参考](configuration.md)). Docker 部署时填写的是容器内路径, 插件文件须先放入已挂载的目录.

### 安装校验

安装前, Amane 在临时副本上导入 `plugin.py` 并校验插件声明, 校验失败时不改动已安装的版本, 界面显示错误原因. 常见错误:

| 错误信息 | 原因 |
|----------|------|
| `只接受 zip 文件` / `只接受插件目录或 zip 文件` | 上传的文件或填写的路径不是 zip, 也不是目录 |
| `不是有效的 zip 文件` | zip 文件损坏或格式不正确 |
| `zip 过大` / `zip 解压后过大` | zip 超过 20 MB, 或解压后总大小超过 50 MB |
| `zip 含有非法路径` | 压缩包内含绝对路径或 `..` |
| `zip 必须在根目录或单一顶层文件夹中包含 plugin.py` | `plugin.py` 不在根目录, 也不在唯一的顶层文件夹内 |
| `目录中必须包含 plugin.py` | 服务器路径指向的目录缺少 `plugin.py` |
| `插件导入失败: ...` | 插件引用了 Amane 未提供的第三方包或模块 |
| `插件加载失败: ...` | 插件代码抛出异常, 或插件声明不合法 (插件 ID、能力、API 版本等) |
| `路径 ... 不在允许的安全目录内` | 服务器路径不在 `AMANE_SAFE_DIRS` 内 |
| `路径不存在: ...` | 服务器路径不存在 |

### 直接放置目录

插件安装后位于数据目录的 `plugins/sources/<插件 ID>/`. 也可以把插件目录直接复制到该位置, 目录名必须与插件 ID 一致, 然后在插件页点击「重新加载」.

服务启动或重新加载时无法加载的插件以红色提示显示在插件页顶部, 内容为目录名与错误原因. 单个插件加载失败不影响其它插件与内置站点.

## 启用与配置

每个插件在插件页显示为一张卡片, 包含名称、版本、插件 ID 与能力标签.

- **启用插件**: 卡片右上角的开关. 禁用后该插件不参与刮削, 也不出现在播放源列表中; 类型路由中的条目可以保留, 刮削时跳过.
- **插件设置**: 插件声明了配置项时, 卡片上出现设置图标. 表单由插件定义的配置模型自动生成, 保存前由插件校验, 校验失败时显示错误信息且不修改当前配置.

插件配置保存在 Amane 的配置文件中. 名称含 `token`、`api_key`、`secret`、`password`、`cookie` 等关键词的配置项, 在任务记录的配置快照中自动脱敏.

## 加入类型路由

影片刮削来源插件安装后不会自动参与刮削. 在「设置 → 影片刮削」的「类型路由」中, 把插件 ID 加入对应内容类型的站点列表, 插件才会在该类型的刮削中被调用; 插件在列表中的位置决定其默认取值顺序. 插件页标题下方的「打开影片刮削设置」链接直达该设置页.

插件声明了支持的内容类型时, 只能加入这些类型的路由. 字段优先级与字段排除同样可以选择插件来源. 类型路由的规则见 [刮削](scraping.md).

播放源插件不写入类型路由.

## 播放源顺序

启用的播放源达到两个及以上时, 插件页出现「播放源顺序」. 拖动排序决定影片详情页中播放源的列出顺序与默认探测顺序; 窄屏设备上使用上移 / 下移按钮调整.

该顺序只保存在当前浏览器中, 不同浏览器与设备之间不同步. 未排过序的播放源排在末尾. 该顺序只影响播放源, 不影响刮削来源的顺序.

## 更新与卸载

- **更新**: 安装同一插件 ID 的新版本, 新版本整体替换旧目录.
- **卸载**: 点击卡片上的「卸载」并确认. 卸载删除插件源码目录 `plugins/sources/<插件 ID>/`, 插件的运行时数据 `plugins/<插件 ID>/` 保留, 重新安装后可以继续使用.

卸载后, 类型路由与插件配置中残留的插件 ID 不影响配置保存与启动, 刮削时跳过.

## 开发插件

!!! info "面向开发者"
    本节面向插件开发者, 要求熟悉 Python 与 Pydantic.

插件是一个纯 Python 包, 运行在 Amane 进程内, 与 Amane 共用同一个 Python 环境. 插件不能自带第三方依赖, 只能导入该环境中已有的模块: Python 标准库、Pydantic 以及 `amane.plugin` 导出的接口. 完整的接口定义与文档字符串见 [`src/amane/plugin/__init__.py`](https://github.com/sqzw-x/amane/blob/main/src/amane/plugin/__init__.py); 下文只说明约定与要点.

- 影片刮削来源: 继承 `FilmSourcePlugin` 并实现 `build()`, 返回 `FilmSourceProvider`.
- 播放源: 继承 `PlaybackPlugin` 并实现 `build_playback()`, 返回 `PlaybackProvider`.
- 同一个类可以同时继承两个基类, 两种能力共用一份配置.

### 目录结构

插件目录必须包含 `plugin.py`, 其中定义名为 `Plugin` 的类:

```text
alice.example/
├── plugin.py      # 定义 Plugin 类
├── parser.py      # 可选: 其它模块
└── ...
```

同目录的其它模块使用相对导入 (`from .parser import parse`). 所有 Amane 类型从 `amane.plugin` 导入, 不要导入 `amane` 的其它模块: 它们不属于插件接口, 随版本变化.

### 插件 ID

插件 ID 是插件的唯一标识, 写在 descriptor 的 `id` 中, 格式为 `namespace.local` (如 `alice.example`):

- 至少两段, 以 `.` 分隔, 可以多于两段 (如 `alice.foo.bar`).
- 每段只包含小写字母、数字、`-` 与 `_`, 且以字母或数字开头.
- 第一段 (命名空间) 不能是保留字 `amane`、`plugin`、`official`、`builtin`, 也不能与内置站点同名.
- 安装后的目录名即插件 ID. 直接放置目录时, 目录名与 `id` 不一致会导致加载失败.

插件 ID 写入类型路由、刮削快照与任务记录. 发布后不允许更改插件 ID: 更改后已刮削影片中的来源记录无法对应到新 ID. 显示名称 `name` 可以随时修改.

### 示例: 影片刮削来源

```python
# plugin.py
from typing import override

from pydantic import BaseModel, ConfigDict

from amane.plugin import (
    ConnectivityOutcome,
    FailureReason,
    FetchOptions,
    FilmSourcePlugin,
    FilmSourceProvider,
    MediaMetadata,
    PluginContext,
    SearchQuery,
    SkipReason,
    SourceCapability,
    SourceDescriptor,
    SourceError,
)


class ExampleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_token: str = ""


class ExampleProvider(FilmSourceProvider):
    def __init__(self, context: PluginContext, config: ExampleConfig) -> None:
        self._http = context.http_client
        self._token = config.api_token

    @override
    async def fetch(self, query: SearchQuery, options: FetchOptions | None = None) -> MediaMetadata | None:
        data = await self._http.get_json(
            f"https://example.test/api/movie/{query.number}",
            headers={"Authorization": f"Bearer {self._token}"} if self._token else None,
        )
        if not isinstance(data, dict):
            raise SourceError(FailureReason.PARSE_ERROR, detail="unexpected response")
        title = data.get("title")
        if not isinstance(title, str) or not title:
            return None  # 请求成功但没有该影片: 未命中, 不是失败
        return MediaMetadata(number=query.number, title=title)

    @override
    async def check_connectivity(self) -> ConnectivityOutcome | None:
        if not self._token:
            return ConnectivityOutcome.skipped(SkipReason.MISSING_CREDENTIAL, detail="api_token")
        return None  # 交给主机探测 descriptor.urls 的第一个地址


class Plugin(FilmSourcePlugin):
    config_model = ExampleConfig

    @classmethod
    @override
    def descriptor(cls) -> SourceDescriptor:
        return SourceDescriptor(
            id="alice.example",
            name="Alice Example",
            version="0.1.0",
            capabilities=frozenset({SourceCapability.FILM_METADATA}),
            urls=("https://example.test",),
        )

    @override
    def build(self, context: PluginContext, config: BaseModel) -> FilmSourceProvider:
        if not isinstance(config, ExampleConfig):
            raise TypeError("unexpected config type")
        return ExampleProvider(context, config)
```

### 示例: 播放源

下例把影片已入库的文件列为播放源, 由 Amane 打开文件并输出码流:

```python
# plugin.py
import asyncio
from pathlib import Path
from typing import override

from pydantic import BaseModel

from amane.plugin import (
    FailureReason,
    FilePlaybackTarget,
    PlaybackMediaFile,
    PlaybackOffer,
    PlaybackPlugin,
    PlaybackProvider,
    PlaybackQuery,
    PluginContext,
    SourceCapability,
    SourceDescriptor,
    SourceError,
)


def _unavailable(file: PlaybackMediaFile) -> str | None:
    """在线程池中执行: stat 是阻塞调用."""
    path = Path(file.path)
    if not path.is_file():
        return "文件已从磁盘消失"
    if path.stat().st_size == 0:
        return "文件为空"
    return None


class LocalProvider(PlaybackProvider):
    @override
    async def probe(self, query: PlaybackQuery) -> tuple[PlaybackOffer, ...]:
        if not query.files:
            raise SourceError(FailureReason.NO_USABLE_METADATA, detail="没有已入库的文件")
        offers: list[PlaybackOffer] = []
        for file in query.files:
            reason = await asyncio.to_thread(_unavailable, file)
            offers.append(
                PlaybackOffer(
                    key=f"file-{file.id}",
                    name=Path(file.path).name,
                    content_type="video/mp4",
                    unavailable=reason,
                )
            )
        return tuple(offers)

    @override
    async def resolve(self, query: PlaybackQuery) -> FilePlaybackTarget | None:
        for file in query.files:
            if query.selected_key in (None, f"file-{file.id}"):
                return FilePlaybackTarget(path=Path(file.path), content_type="video/mp4")
        raise SourceError(FailureReason.NO_USABLE_METADATA, detail="未知的流")


class Plugin(PlaybackPlugin):
    @classmethod
    @override
    def descriptor(cls) -> SourceDescriptor:
        return SourceDescriptor(
            id="alice.localplay",
            name="本地文件",
            version="0.1.0",
            capabilities=frozenset({SourceCapability.PLAYBACK}),
        )

    @override
    def build_playback(self, context: PluginContext, config: BaseModel) -> PlaybackProvider:
        return LocalProvider()
```

### 开发要点

#### 运行环境

| 要点 | 说明 |
|------|------|
| 依赖 | 只能导入 Amane 运行环境中已有的模块: Python 标准库、Pydantic 与 `amane.plugin`. 插件不能声明 pip 依赖或携带原生扩展; 导入不存在的包时安装失败, 报 `插件导入失败`. 桌面版、Docker 与源码运行均提供完整标准库 |
| API 版本 | descriptor 的 `api_version` 默认等于当前的 `PLUGIN_API_VERSION` (`"1"`), 不一致的插件拒绝加载 |
| 加载时机 | 服务启动、安装、卸载、重新加载时实例化 `Plugin`; provider 在首次使用时创建并缓存, 修改插件配置或启停插件后重新创建 |

#### 返回值与错误

| 要点 | 说明 |
|------|------|
| 未命中返回 `None` | 请求成功但没有得到元数据 (或番号不匹配) 属于「没有找到」, 不是失败 |
| 失败抛 `SourceError` | 网络失败、拦截页、需要登录等可分类的失败抛 `SourceError` (`RequestError` 是其子类), 原因取自 `FailureReason`. Amane 记入站点明细, 任务不会因此中止 |
| 不忽略异常 | 不允许 `except Exception` 或把 `RequestError` 转为 `return None`: 失败会被记为「无可用元数据」, 站点明细中看不到真实原因 |
| `detail` 的内容 | 播放源的 `SourceError.detail` 原样显示给用户, 不允许写入上游 URL、密钥或签名参数 |

#### 网络与数据

| 要点 | 说明 |
|------|------|
| 共享客户端 | 网络请求一律经由 `context.http_client`: HTML 用 `get_html` (遇到拦截页抛 `SourceError`), JSON 用 `get_json`, 另有 `get_text`、`get_bytes`、`post_json`. 共享客户端遵循 Amane 的代理、重试与限速设置, 并写入任务记录. 不要自建 HTTP 客户端 |
| `descriptor.urls` | 填写插件访问的站点地址, 用于请求限速, 也是网络检测的默认探测地址 |
| 连通性检测 | 可选实现 `check_connectivity`, 供「网络检测」页探测本来源. 返回 `None` 时 Amane 探测 `descriptor.urls` 的第一个地址. 入口不是该地址 (登录页、需要 token 的 API) 时需实现它; 凭据缺失时返回 `ConnectivityOutcome.skipped(SkipReason.MISSING_CREDENTIAL)`. `detail` 原样显示在界面上, 只写语言中立的补充 (如缺失的配置项名) |
| 数据目录 | `context.data_dir` 是插件专属目录 `{数据目录}/plugins/<插件 ID>/`, 卸载时保留, 适合存放缓存. 插件不允许写入该目录之外的位置 |

#### Descriptor 与行为开关

| 要点 | 说明 |
|------|------|
| 能力 | `capabilities` 缺省为影片元数据. 仅提供播放的插件必须显式声明 `SourceCapability.PLAYBACK`, 否则加载失败. 声明的能力必须与继承的基类一致 |
| 内容类型与字段 | 可选的 `content_types` 限定插件能加入哪些类型路由; `metadata_fields` 限定字段优先级与字段排除中可选择的字段 |
| 默认速率 | 可选的 `rate_limit` (每秒请求数, 0.1 至 100) |
| `traits` | Amane 识别的行为开关, 取值见 `SourceTrait`: `needs_partial`、`multi_language`、`uses_file_hash`. 未识别的取值被忽略, 因此插件可以预先声明较新版本 Amane 才支持的开关 |
| 多语言 | 声明 `multi_language` 后, Amane 按字段语言偏好分别调用插件, `fetch` 经 `options.language` 取得本次的语言 |
| 前序结果 | 声明 `needs_partial` 后, 插件排在聚合的第二段执行, 经只读的 `query.partial_result` 读取前序已确定的单值字段, 以及第一段各来源取得的封面、缩略图、预告片地址. 其中只含第一段的贡献, 不等于最终图片列表的前缀; 为 `None` 表示本次不在第二段 |
| 文件指纹 | 声明 `uses_file_hash` 后, Amane 在刮削前计算关联文件的 oshash, 经 `query.file_hash` 传入; 本次没有关联文件时为 `None` |

#### 元数据字段

| 要点 | 说明 |
|------|------|
| 演员 | `MediaMetadata.actors` 为 `FilmActor` 列表 (`name` 与 `gender`). 也接受字符串列表, 性别视为未识别; `film_actors(names)` 可把名单批量转为 `FilmActor`. 能判定性别时写出 `female` / `male` |
| 时长 | `runtime` 以分钟计, 上游以秒计时须自行换算 |

#### 播放源

| 要点 | 说明 |
|------|------|
| 调用时机 | 用户在详情页切换到该播放源时才调用 `probe`, 打开详情页本身不触发上游请求 |
| `probe` | 列出本来源对该影片能提供的流, 每条流一个 `PlaybackOffer`, 顺序即列表顺序. 有候选但不可播放时仍列出, 以 `unavailable` 说明原因 (只写用户能据以处理的信息, 不写完整路径与上游地址); 整个来源没有可列内容时抛 `SourceError(NO_USABLE_METADATA)`. 探测由用户等待, Amane 不设时限, 但请求挂起时插件须自行抛出 `SourceError` 结束 |
| `key` 与 `name` | `key` 在同一来源与影片内必须稳定且唯一, 更改会使正在播放的地址失效. 列表每行显示为「来源名 · `name`」, `name` 中不要重复来源名 |
| `resolve` | 按 `query.selected_key` 返回用户选中的流; 为 `None` 时由插件自行选择. 每次取流都会调用 |
| 播放目标 | `FilePlaybackTarget`: 已入库文件, 路径必须是 `query.files` 中的文件之一, 由 Amane 打开并输出. `UpstreamPlaybackTarget`: 上游 URL 与仅服务端使用的请求头, 由 Amane 反向代理. `HlsPlaybackTarget`: HLS 清单, 见下一行. 媒体正文一律由 Amane 打开或代理, 插件不直接向浏览器输出字节 |
| HLS | 返回带 locator 的 `HlsPlaybackTarget`; locator 读取清单, 并把清单中的 URI 定位为上游地址, Amane 改写清单并代理分片与密钥. 常规场景可直接使用 `RelativeHlsLocator`. 指向播放列表的 `UpstreamPlaybackTarget` 会被拒绝 |
| 不转码 | Amane 不对码流做实时转码. 浏览器无法直接播放的格式须由上游提供 HLS 清单 |
| `content_type` | 每条 `PlaybackOffer` 的 `content_type` 必须与 `resolve` 返回的目标一致: HLS 用 `application/vnd.apple.mpegurl`, 逐字节码流用 `video/*` 或 `audio/*` |
| 结果有效期 | 播放目标可以声明 `cache_ttl` (秒, 正数), Amane 在该时长内复用这一次解析结果, 上限 300 秒; 不声明则不缓存. 签名 URL 与会话凭据必须声明不超过其实际有效期的值. 修改插件配置会清空该缓存 |
| 字幕 | 在 `PlaybackOffer.subtitles` 中声明轨道; `subtitle` 按轨道 id 返回 WebVTT 正文或上游 VTT 地址, 轨道不存在时返回 `None`. Amane 不读取本机字幕文件, 也不转换字幕格式; 提供本机字幕时由插件读取并转换为 WebVTT |
| 不阻塞事件循环 | `probe`、`resolve`、`subtitle` 在 Amane 的事件循环上调用. 读盘、`stat`、同步 HTTP 等阻塞调用必须经 `asyncio.to_thread` 提交到线程池, 否则整个服务停止响应, 其它来源的请求一并超时 |

### 插件配置

插件配置是一个 Pydantic 模型, 赋值给 `Plugin.config_model`. 插件页根据它的 JSON Schema 自动生成设置表单:

- 使用 `ConfigDict(extra="forbid")` 拒绝未知字段.
- 字段名含 `token`、`api_key`、`secret`、`password`、`cookie` 等关键词时, 在任务记录中自动脱敏.
- 校验失败时界面显示错误信息, 配置不会写入.
- `build()` / `build_playback()` 收到的 `config` 是已校验的模型实例.

不需要配置时省略 `config_model`, 默认为 `EmptyPluginConfig`, 插件卡片不显示设置图标.

## 分发

把插件目录打包为 zip, 保证 `plugin.py` 位于 zip 根目录或唯一的顶层文件夹内:

```bash
cd path/to/parent && zip -r alice.example.zip alice.example -x '*/__pycache__/*' '*/.git/*'
```

不要把 `__pycache__`、`.git` 等无关内容打包进去; zip 不超过 20 MB, 解压后不超过 50 MB.

社区插件登记在 Amane 仓库带 [eco:project](https://github.com/sqzw-x/amane/issues?q=is%3Aissue+label%3Aeco%3Aproject) 标签的 Issue 中. 插件说明中应写明插件 ID、能力、所需配置项与访问的站点, 便于用户在安装前审查.
