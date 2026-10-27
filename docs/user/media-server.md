# 对接 Emby / Jellyfin

Amane 与 Emby / Jellyfin 分工互补: 媒体服务器管理视频文件, 负责播放与串流; Amane 负责多源刮削与元数据管理. 两者通过本地文件衔接: Amane 在 [整理](organize.md) 时为每部影片写出 NFO、图片与字幕, 媒体服务器读取这些文件即可显示元数据. 本页面向使用媒体服务器的用户, 说明 Amane 写出的文件布局、推荐的媒体库配置, 以及网盘场景下的链接与 STRM 用法.

Amane 不调用媒体服务器的接口: 整理完成后, 由媒体服务器自身的文件监控或媒体库扫描发现新文件与元数据变化.

## 写出的文件

使用默认模板整理一部带外挂字幕的影片后, 目录结构如下:

```text
<库根>/
└── Studio Name/
    └── MIDV-123/
        ├── MIDV-123-C.mp4        视频
        ├── MIDV-123-C.nfo        NFO
        ├── MIDV-123.zh.srt       字幕 (保留原文件名)
        ├── poster.jpg            海报
        ├── thumb.jpg             封面
        ├── fanart.jpg            背景图 (内容同封面)
        └── extrafanart/          剧照
            ├── 1.jpg
            └── 2.jpg
```

| 文件 | 来源 | 由谁决定 |
|------|------|---------|
| NFO | 影片元数据 | 「写入 NFO」开关与 NFO 模板 |
| `poster.jpg` | 刮削的海报, 或由封面裁切 | 「整理时复制」与海报模板 |
| `thumb.jpg` / `fanart.jpg` | 刮削的封面 | 「整理时复制」与封面、背景图模板 |
| `extrafanart/` | 刮削的剧照 | 「整理时复制」与剧照模板 |
| `trailer.mp4` | 刮削的预告片 | 「整理时复制」(新建媒体库时默认不勾选) 与预告片模板 |
| 字幕 | 视频同目录的外挂字幕 | 字幕扩展名与字幕模板 |

开启 [水印](organize.md#watermark) 后, 海报与封面带有中字、无码、分辨率等角标. 各文件的路径均可通过模板修改, 语法见 [整理与路径模板](organize.md#placeholders).

### NFO {#nfo}

NFO 为 Kodi 格式, 根元素为 `<movie>`, 内容固定, 不提供自定义模板. NFO 模板只决定文件路径, 默认 `{link_dir}/{video_name}.nfo` 与视频同名, 以便媒体服务器按文件名匹配.

| 元素 | 内容 |
|------|------|
| `<title>` `<originaltitle>` `<sorttitle>` | 番号 + 空格 + 标题, 如 `MIDV-123 Title Here` |
| `<num>` | 番号 |
| `<plot>` `<outline>` | 剧情简介 (纯文本) |
| `<premiered>` `<releasedate>` `<year>` | 发行日期与年份 |
| `<actor>` | 每位演员一项, 含 `<name>` |
| `<studio>` `<maker>` | 制作商 |
| `<publisher>` `<label>` | 发行商 |
| `<series>` `<set>` | 系列 |
| `<tag>` `<genre>` | 标签, 两个元素同时写出 |
| `<director>` | 导演 |
| `<rating>` `<criticrating>` | 评分, 后者为前者的 10 倍 |
| `<runtime>` | 时长 |
| `<mpaa>` | 固定为 `JP-18+` |
| `<来源标识id>` | 各刮削来源的作品 ID, 元素名为来源标识后接 `id` |

标题已包含番号, 媒体服务器中按标题排序即按番号排序. 分集影片的每个分集各有一份 NFO, 图片默认共用.

## 推荐配置

=== "本地磁盘"

    视频与元数据在同一目录, 媒体服务器直接读取 Amane 的媒体库目录.

    | 设置 | 推荐值 |
    |------|--------|
    | 整理方式 | 移动 |
    | 视频路径模板 | 默认 `{studio}/{number}/{number}[-CD{cd?}][-{sub?}].{ext}` |
    | 链接路径模板 | 留空 |
    | 写入 NFO | 开启 |
    | 整理时复制 | 封面、海报、剧照 |
    | 自动整理 | 确认模板结果后开启 |

    媒体服务器的媒体库指向 Amane 的库根目录.

=== "网盘挂载"

    视频留在网盘挂载目录中整理, NFO、图片与字幕写入本地的链接目录, 媒体服务器只读取本地目录. 详见 [网盘场景](#cloud).

    | 设置 | 推荐值 |
    |------|--------|
    | 路径 | 网盘挂载目录, 如 `/mnt/cloud/av` |
    | 文件发现方式 | CloudDrive2 挂载使用「CD2 webhook」 |
    | 整理方式 | 移动 |
    | 链接路径模板 | `/local/emby/{studio}/{number}/{video_name}.{ext}` |
    | 链接方式 | STRM 文件 |
    | 写入 NFO | 开启 |

    媒体服务器的媒体库指向链接目录的根, 如 `/local/emby`.

在媒体服务器侧:

- 媒体库内容类型选择电影.
- 启用 NFO 作为本地元数据来源. 如需保留 Amane 写出的元数据, 关闭在线元数据下载与图片下载, 避免被覆盖.
- 媒体服务器未自动发现变化时, 手动扫描该媒体库.

!!! tip "每部影片一个目录"
    封面、海报、背景图的默认文件名固定. 多部影片位于同一目录时会互相覆盖, 因此推荐保留默认模板中的 `{number}` 目录层.

## 网盘场景 {#cloud}

网盘挂载的读取延迟高, 且媒体服务器扫描挂载目录会产生大量请求. 设置链接路径模板后, 视频在网盘中整理, 其余文件写在本地:

```text
/mnt/cloud/av/Studio Name/MIDV-123/MIDV-123.mp4     视频 (网盘)
/local/emby/Studio Name/MIDV-123/MIDV-123.strm      STRM (本地)
/local/emby/Studio Name/MIDV-123/MIDV-123.nfo
/local/emby/Studio Name/MIDV-123/poster.jpg
...
```

- 默认附属资源模板以 `{link_dir}` 为根, NFO、图片与字幕自动写入链接目录.
- 链接文件名以 `{video_name}` 结尾时, 与默认 NFO 文件名一致. 链接文件名与视频文件名不同时, 将 NFO 模板改为 `{link_dir}/{link_name}.nfo`.
- 链接路径必须位于库外. Docker 部署中, 链接目录还须映射进容器, 并位于 `AMANE_SAFE_DIRS` 允许的范围内 (见 [配置参考](configuration.md)).
- 在 Amane 中删除视频不会删除链接目录中的文件, 需要自行清理.

链接方式与路径的关系:

| 链接方式 | 链接内容 | 媒体服务器的要求 |
|---------|---------|----------------|
| STRM 文件, 内容模板留空 | 视频在 Amane 中的绝对路径 | 能以同一路径访问视频 |
| STRM 文件, 内容模板为 URL | 视频的网络地址 | 能访问该地址 |
| 符号链接 | 指向视频绝对路径的符号链接 | 能以同一路径访问视频 |

Amane 与媒体服务器分别运行在不同容器中时, 两者看到的挂载路径往往不同, 此时推荐使用 URL 形式的 STRM.

### STRM 指向 URL

网盘可经 HTTP 访问 (如 OpenList 等目录服务) 时, 在「STRM 内容模板」中填写 URL 模板, 用 `{video_relpath}` 引用视频相对库根的路径:

```text
https://files.example.com/av/{video_relpath}
```

视频整理为 `Studio Name/MIDV-123/MIDV-123.mp4` 时, STRM 内容为 `https://files.example.com/av/Studio Name/MIDV-123/MIDV-123.mp4`.

- URL 前缀应对应 Amane 媒体库根目录在该服务中的路径.
- 占位符的值原样写入, 不进行 URL 编码.
- 使用 `{video_relpath}` 时, 视频路径模板必须将视频整理在本库内.

语法细节见 [链接与 STRM](organize.md#links).

## 图片 Token

Amane 的图片资源地址 (`/api/resources/...`) 需要鉴权. 浏览器通过登录后的 Cookie 访问; 媒体服务器插件、脚本等无法附加请求头的客户端, 使用图片 Token 访问:

```text
http://<Amane 主机>:<端口>/api/resources/<资源 ID>?token=<图片 Token>
```

- 图片 Token 只能读取图片资源, 不能调用其它接口. 通过代理地址访问时, 只返回已缓存的图片.
- 图片 Token 默认自动生成, 可在启动日志或数据目录的 `image_token` 文件中查看; 通过环境变量 `AMANE_IMAGE_TOKEN` 指定或关闭.
- Token 会出现在 URL 中, 并可能记录在媒体服务器数据库与反向代理日志里.

整理写出的本地图片文件由媒体服务器直接读取, 不需要图片 Token. 详细说明见 [配置参考](configuration.md).
