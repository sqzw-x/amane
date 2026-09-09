# 媒体库管理

媒体库 (Library) 是 Amane 管理本地媒体文件的单位. 每个库对应一个磁盘目录, 其中的文件将注册到 Amane, 并支持自动监听此目录下的文件变更、自动刮削新文件, 并通过整理规则组织文件结构.

## 创建媒体库

「媒体库 → 添加」

## 文件监控

每个库选择如何发现新文件. 自动化级别仍决定发现之后是否入库、是否刮削; 整理始终手动触发.

- **本地文件** (默认): 使用操作系统文件事件. 适用于本机磁盘、以及能产生创建事件的挂载.
- **CD2 webhook**: 接收 CloudDrive2 的文件通知 webhook, 不监听挂载路径. Webhook 需 CloudDrive2 会员; 性能好于监听挂载路径.

CD2 webhook 库须同时填写:

- **路径**: 本机可扫描的挂载目录 (如 `/Volumes/115/云下载`, 或 Docker bind 后的路径)
- **CloudDrive 路径**: 库路径对应的 CloudDrive2 内路径 (如 `/115open/云下载`). 不是 `/Volumes/...` 或 Windows 盘符. 多库时按最长前缀匹配. 不允许两个 CD2 webhook 库使用相同或互为前缀的 CloudDrive 路径.

`watcher.use_polling` 只作用于「本地文件」, 不能替代 CD2 webhook. 目录整树复制或离线完成往往只推送结果目录一条 `create`; Amane 会对该子树扫描. 未推送的变更仍可手动扫描.

### CloudDrive2 webhook

文件变更 webhook 是 CloudDrive2 的会员功能. Amane 与 CloudDrive2 无隶属、合作或担保关系; 下列菜单名与配置文件名以 CloudDrive2 当前版本为准.

在 CloudDrive2 网页: **设置 → Webhook → 添加 Webhook**.

也可编辑 CloudDrive2 配置目录中的 `webhook.toml` (或 `Configuration.toml` 里的 `[file_system_watcher]`). 对接 Amane 时必要字段:

| 字段 | 值 |
|------|----|
| `url` | `http(s)://<Amane 主机>/api/webhooks/clouddrive` |
| `method` | `POST` |
| `enabled` | `true` |
| `Authorization` | `Bearer <Amane API Token>` |
| `body` | JSON, 含 `data` 数组; 每项含 `action`、`is_dir`、`source_file`, `rename` 时另有 `destination_file` |

示例:

```toml
[file_system_watcher]
url = "http://<Amane 主机>/api/webhooks/clouddrive"
method = "POST"
enabled = true
body = '''
{
  "data": [
    {
      "action": "{action}",
      "is_dir": "{is_dir}",
      "source_file": "{source_file}",
      "destination_file": "{destination_file}"
    }
  ]
}
'''

[file_system_watcher.headers]
Authorization = "Bearer <Amane API Token>"
```

## 路径模板

路径模板决定整理后文件的存储位置. 模板使用占位符变量:

### 可用占位符 {#placeholders}

| 占位符 | 说明 | 示例值 |
| -------- | ------ | -------- |
| `{number}` | 番号 | `MIDV-123` |
| `{prefix}` | 番号前缀 | `MIDV` |
| `{suffix}` | 番号去掉前缀后的剩余段 | `123` |
| `{title}` | 标题 | `Title Here` |
| `{actor}` | 第一主演 | `Actor1` |
| `{actors}` | 演员 (逗号分隔) | `Actor1,Actor2` |
| `{actress}` | 第一位女演员 | `Actress1` |
| `{actresses}` | 女演员 (逗号分隔) | `Actress1,Actress2` |
| `{studio}` | 制作商 | `Studio Name` |
| `{publisher}` | 发行商 | `Publisher Name` |
| `{series}` | 系列 | `Series Name` |
| `{year}` | 发行年份 | `2024` |
| `{release}` | 发行日期 | `2024-01-15` |
| `{ext}` | 正在放置的文件扩展名 | `mp4` / `srt` |
| `{cd?}` | CD/分集编号 | `1` / `2` / 空 |
| `{sub?}` | 中字标记 | `C` / 空 |
| `{mosaic?}` | 有无码标记 | `uncensored` / `cracked` / `leaked` / 空 |
| `{def?}` | 分辨率标记 | `4K` / `1080p` / `HD` / 空 |
| `{raw_name}` | 源视频文件名 | `A/B.mp4` → `B` |
| `{raw_dir}` | 源文件父目录名 | `A/B/C.mp4` → `B` |
| `{video_dir}` | 整理后视频所在目录 | — |
| `{video_name}` | 整理后视频文件名, 不含扩展名 | `MIDV-123-CD1-C` |
| `{video_relpath}` | 整理后视频相对库根目录的路径 | `Studio/ABC-123/ABC-123.mp4` |
| `{link_dir}` | 链接文件所在目录 | — |
| `{link_name}` | 整理后链接文件名, 不含扩展名 | — |
| `{raw_srt_name}` | 字幕原文件名, 不含扩展名 | `foo.zh.srt` → `foo.zh` |

文件名 `CRACKED` / `-U` / `-UC` 解析为破解; `-UC` 同时识别为中字. 无码标记是 `无码` / `UNCENSORED`.

`{actress}` / `{actresses}` 排除已标为男性的演员; 女性与尚未识别性别的名字保留. 名单为空时输出 `Unknown`.

`{prefix}` / `{suffix}` 从刮削所得 `{number}` 拆出, 不依据源文件名. `MIDV-123` → 前缀 `MIDV`、剩余段 `123`; `MKY-HS-001` → `MKY-HS` / `001`.

未列出范围的占位符在各模板与 STRM 内容模板中均可使用. 下表所列占位符有范围限制; 写在不可用的模板中会得到 `Unknown`.

| 占位符 | 可用范围 |
| -------- | -------- |
| `{video_dir}` `{video_name}` `{video_relpath}` | 链接、附属、字幕、STRM 内容 |
| `{link_dir}` `{link_name}` | 附属、字幕、STRM 内容 |
| `{raw_srt_name}` | 仅字幕 |

### 默认模板

```
视频: {studio}/{number}/{number}[-CD{cd?}][-{sub?}].{ext}
缩略图: {link_dir}/thumb.jpg
海报: {link_dir}/poster.jpg
NFO: {link_dir}/{number}.nfo
预告片: {link_dir}/trailer.mp4
字幕: {link_dir}/{raw_srt_name}.{ext}
```

### 可选组语法

可用 `[...]` 将模板中的一段包裹为组, 组内所有可空占位符全为空时整组省略. 这主要是为了处理分集、中字等可选属性, 例如:

```
{number}[-CD{cd?}][-{mosaic?|cracked=U}{sub?}].{ext}
```

- 源文件 `MIDV-123-U-C-CD2.mp4` → `MIDV-123-CD2-UC.mp4`
- 源文件 `MIDV-123-U.mp4` → `MIDV-123-U.mp4`
- 源文件 `MIDV-123-C.mp4` → `MIDV-123-C.mp4`
- 源文件 `MIDV-123.mp4` → `MIDV-123.mp4` (不会残留 `-`)

双层方括号 `[[...]]` 同样省略逻辑, 但有值时结果会被方括号包裹:

```
{number}[[{def?}]].{ext}
```

- 检测到 4K → `MIDV-123[4K].mp4`
- 未检测到 → `MIDV-123.mp4`

### 值映射

占位符支持 `{name|原值=输出,另一值=输出}` 语法, 将规范值改写成自定义输出:

```
{mosaic?|uncensored=无码,cracked=U,leaked=流出}
{def?|4K=2160p,1080p=FHD}
```

- 未列出的值保持原样 (如 `{mosaic?|cracked=U}`, uncensored / leaked 仍为规范值)
- 可以映射成空串 (配合可选组让某个值不出现在路径中)
- 目录段和文件名段可以分别写映射, 比如目录用规范值便于管理, 文件名用短标记节省字符:

```
{mosaic?}/{number}[-{mosaic?|uncensored=无码,cracked=U,leaked=流出}].{ext}
```

源文件 `MIDV-123-無碼.mp4` → `uncensored/MIDV-123-无码.mp4`

## 链接模板与模式

媒体库支持在库外创建指向库内视频的入口, 适用于网盘挂载等场景:

- **`link_template`**: 链接文件的路径模板 (如 `/本地路径/{number}/{video_name}.{ext}`). 为空则不创建链接, `{link_dir}` / `{link_name}` 分别等于 `{video_dir}` / `{video_name}`.
- **`link_mode`**: 链接类型
  - `strm`: 创建 `.strm` 文件, Emby/Jellyfin 可识别
  - `symlink`: 创建文件系统软链接
- **`strm_content_template`**: STRM 内容模板, 仅 `link_mode=strm` 时生效.

使用网盘库时, 可以将库路径指向挂载盘 (如 `/mnt/cloud`), `link_template` 填本地路径:

- 视频在挂载盘上按模板整理
- 本地创建 strm/软链接 + NFO/海报/字幕等
- 媒体服务器 Emby/Jellyfin 添加本地媒体库即可

`strm_content_template` 用于设置 STRM 文件的内容模板. 默认情况下, STRM 会写入原视频文件的绝对路径,
某些场景需要使用网盘 / OpenList URL, 则可手动设置模板, 例如:

```
https://example.com/{video_relpath} -> https://example.com/ABC-123/ABC-123.mp4
```

### 使用场景

## 整理操作

整理 (Organize) 操作会将媒体文件按路径模板放置到指定位置:

1. 在媒体库页面点击「整理」
2. 系统会根据路径模板计算目标位置
3. 按放置方式 (移动/复制/硬链接/符号链接) 执行
4. 如果配置了链接模板, 在库外创建 strm 文件或软链接
5. 按源文件标记添加封面 / 海报水印

!!! note
    目前整理操作无自动触发途径, 需要手动执行.

## 分集 (CD) 识别

Amane 支持自动识别分集文件名, 目前支持以下几种常见标记:

- `-CD1`, `-CD2` — 标准分集标记
- `-Part1`, `-Part2` — 替代分集标记
- `-A`, `-B` — 字母分集 (`-C` 与中字冲突, 不支持)
- `-1` 到 `-9` — 尾部数字分集

检测到的分集编号通过 `{cd?}` 占位符填入, 在模板中用可选组控制是否出现. 例如默认模板 `{studio}/{number}/{number}[-CD{cd?}].{ext}`:

- 源文件 `MIDV-123-CD2.mp4` → `Studio Name/MIDV-123/MIDV-123-CD2.mp4`
- 源文件 `MIDV-123.mp4` (无分集) → `Studio Name/MIDV-123/MIDV-123.mp4`

## 字幕文件

整理时会把视频**同目录**下的字幕文件一起搬走 (不递归子目录, 字幕本身不入库):

- 扩展名可在库设置中配置, 默认 `.srt` `.ass` `.ssa` `.vtt` `.sub`. 留空则不处理字幕.
- 多个字幕全部带走, 保持原文件名和扩展名, 默认放到整理后视频的同一目录.
- 字幕文件名能解析出番号时, 必须与当前视频番号相同, 并按分集标记配对.
- 解析不出番号时, 按同目录分集规则配对: 有标记的跟当前视频同号; 解析不出分集的跟无分集或 CD1 的视频.

## 预告片跳过

可配置正则, 匹配磁盘上已有的预告片文件名 (含扩展名). 命中文件不作为正片入库.

!!! note
    当媒体库的 `整理时复制` 配置包含预告片时, 使用的是刮削所得预告片, 而不是库中已有的文件. 这是一个已知的不合理行为, 后续会修改.

## 黑名单

可配置一组正则规则, 对文件名 (含扩展名) 进行匹配, 命中文件不入库且整理时移入 `.amane_trash`,此目录在扫描与监控中始终忽略.

!!! note
    此功能主要用于处理广告文件, `.amane_trash` 类似于回收站. 为避免误删 amane 永远不会自动删除此目录, 用户需自行手动清理.

## 小文件过滤

设置最小视频大小 (默认关闭). 小于此值的文件不会被当作正片入库, 整理时移入 `.amane_trash`:

- 只作用于视频扩展名, 图片、NFO、字幕不受此阈值影响
- `.strm` 被特别剔除, 不按大小过滤
- 软链接按目标文件大小判断

## 自动化工作流

### 扫描

扫描是发现媒体文件并注册到数据库的过程:

- 手动扫描: 在库页面点击「扫描」
- 自动扫描: 「本地文件」由操作系统事件触发; 「CD2 webhook」由 webhook 触发

### 刮削

刮削是为已注册的文件获取元数据的过程:

- 手动刮削: 在影片详情页点击「刮削」
- 自动刮削: 文件监控 + 自动化级别为「监控+刮削」时自动触发

### 整理

整理是将文件按路径模板放置到正确位置:

- 目前仅支持手动触发
- 整理时会自动下载缺失的资源 (如海报)

## 多库支持

Amane 支持同时管理多个媒体库, 适用于:

- 不同类型的影片分库存放
- 不同磁盘/分区的媒体
- 测试环境与正式环境分离

!!! warning
    建议不同库的根目录不要重叠 (父子目录关系), 以避免文件归属冲突.
