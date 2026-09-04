# 插件

插件是社区开发的扩展包, 可以为 Amane 增加新的刮削数据源. 目前没有官方插件市场, 社区可以自行开发和分发插件.

!!! danger "安全提示"
    插件在 Amane 进程内运行, **可以执行任意代码**. 不要安装未经安全审查的插件.

## 安装插件

在「管理 → 刮削插件」页面, 有两种安装方式:

- **上传 zip**: 选择本地的插件 zip 文件上传
- **选择服务器路径**: 填写服务器上已有的插件目录或 zip 文件路径

安装是热加载的, 成功后插件立即出现在列表中, 不需要重启服务. 出于安全考虑, 不提供远程下载安装的方式.

## 使用插件

### 启用与配置

在「刮削插件」页面可以:

- 控制每个插件的启用/禁用状态
- 配置插件开发者定义的配置项 (如 API 密钥等)

### 加入刮削路由

安装插件后, 需要在「设置 → 影片刮削 → 内容路由」中将插件 ID 添加到对应内容类型的站点列表中, 插件才会在刮削时被调用.

### 更新与卸载

- **更新**: 用新版本覆盖安装即可
- **卸载**: 在插件列表中删除, 卸载后插件数据保留在数据目录中

## 开发插件

!!! info "面向开发者"
    以下内容面向插件开发者.

插件本质上是一个 Python 包, 通过继承 `FilmSourcePlugin` 声明身份和能力, 并实现 `build()` 构造刮削 Provider. 插件运行时与 Amane 在同一进程中, 共享 Python 环境.

### 目录结构

插件必须包含一个 `plugin.py` 文件, 其中包含名为 `Plugin` 的 `FilmSourcePlugin` 子类:

```
my_plugin/
  plugin.py
  utils.py
  ...
```

多模块时使用相对导入.

### 插件 ID

ID 是插件的唯一标识, 格式为 `namespace.local` (如 `alice.example`):

- 至少两段, 多段也合法 (如 `alice.foo.bar`)
- 每段只包含小写字母 / 数字 / `-` / `_`, 且必须以字母或数字开头
- namespace 不能是保留字 (`amane` / `plugin` / `official` / `builtin`), 也不能与内置站点重名

### 示例

```python
# plugin.py
from typing import override

from pydantic import BaseModel, ConfigDict

from amane.plugin import (
    FetchOptions,
    FilmSourcePlugin,
    FilmSourceProvider,
    MediaMetadata,
    PluginContext,
    SearchQuery,
    SourceCapability,
    SourceDescriptor,
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
        if not query.number:
            return None
        data = await self._http.get_json(
            "https://example.test/api/movie",
            headers={"Authorization": f"Bearer {self._token}"} if self._token else None,
        )
        if not isinstance(data, dict):
            return None
        title = data.get("title")
        if not isinstance(title, str) or not title:
            return None
        return MediaMetadata(number=query.number, title=title)


class Plugin(FilmSourcePlugin):
    config_model = ExampleConfig

    @classmethod
    @override
    def descriptor(cls) -> SourceDescriptor:
        return SourceDescriptor(
            id="alice.example",
            name="Alice Example",
            version="0.1.0",
            capabilities=frozenset({SourceCapability.FILM_METADATA.value}),
            urls=("https://example.test",),
        )

    @override
    def build(self, context: PluginContext, config: BaseModel) -> FilmSourceProvider:
        if not isinstance(config, ExampleConfig):
            raise TypeError("unexpected config type")
        return ExampleProvider(context, config)
```

### 开发要点

- **未命中返回 `None`**: 请求成功但没有得到元数据 (或番号不匹配), 这是「没有找到」, 不是失败
- **网络失败抛 `SourceError`**: 交给 Amane 分类记录, 任务不会崩溃, 报告里能看到原因. 不要 `except Exception` 吞掉异常
- **网络请求走 `context.http_client`**: 共享 Amane 的代理、重试、限速, 并记入任务记录, 不要自建客户端
- **`descriptor.urls`** 填插件需访问的站点, 会用于请求限速
- **落盘写 `context.data_dir`**: 插件自己的 `{data_dir}/plugins/<id>/` 目录, 卸载时保留, 适合放缓存
- **多语言支持**: descriptor 声明 `multi_language=True`, fetch 通过 `options.language` 获取当前语言
- **出演者**: `actors` 为 `FilmActor` 列表 (`name` + `gender`). 仍可传入字符串列表, 性别视为未识别. 名单能判定性别时写出 `female` / `male`

### 插件配置

插件配置是 Pydantic 模型 (`config_model`), 界面根据 JSON Schema 自动渲染表单:

- 用 `extra="forbid"` 防止未知字段
- 含 `token` / `api_key` / `secret` 等关键词的字段在任务快照中自动脱敏
- 校验失败时界面直接显示错误消息

不需要自定义配置时, 不必设置 `config_model`.

### 分发

将代码打包成 zip, 保证根目录或单一顶层目录内可找到 `plugin.py`. 不要将 `__pycache__` 或 `.git` 等无关内容打包进去.
