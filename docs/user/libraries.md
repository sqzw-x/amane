# 媒体库管理

媒体库 (Library) 是 Amane 管理本地媒体文件的单位. 每个库对应一个磁盘目录, 其中的文件将注册到 Amane, 并支持自动监听此目录下的文件变更、自动刮削新文件, 并通过整理规则组织文件结构.

## 创建媒体库

「媒体库 → 添加」

## 路径模板

路径模板决定整理后文件的存储位置. 模板使用占位符变量:

### 常用占位符

| 占位符 | 说明 | 示例值 |
|--------|------|--------|
| `{number}` | 番号 | `MIDV-123` |
| `{title}` | 标题 | `Title Here` |
| `{actor}` | 第一主演 | `Actor1` |
| `{actors}` | 演员 (逗号分隔) | `Actor1, Actor2` |
| `{studio}` | 制作商 | `Studio Name` |
| `{publisher}` | 发行商 | `Publisher Name` |
| `{series}` | 系列 | `Series Name` |
| `{year}` | 发行年份 | `2024` |
| `{release}` | 发行日期 | `2024-01-15` |
| `{ext}` | 正在放置的文件扩展名 (不含点); 字幕模板里是该字幕的扩展名 | `mp4` / `srt` |
| `{mosaic}` | 马赛克类型, 来自源文件路径 | `uncensored` / `cracked` / `censored` |
| `{definition}` | 分辨率, 来自源文件名 | `4K` / `1080p` / `HD` |
| `{raw_name}` | 源视频文件名，不含扩展名 | `A/B.mp4` → `B` |
| `{raw_dir}` | 源文件父目录名 | `A/B/C.mp4` → `B` |
| `{video_dir}` | 按模板渲染后目标文件所在目录的路径，仅可用于附属资源模板 | — |
| `{link_dir}` | 链接文件渲染后的父目录 (无链接时等于 video_dir) | — |
| `{raw_srt_name}` | 字幕原文件名，不含扩展名，仅字幕模板 | `foo.zh.srt` → `foo.zh` |
| `{video_path}` | 整理后视频的绝对路径，仅 strm 内容模板 | `/test/OD/VC/MIDV-123/MIDV-123.mp4` |
| `{video_relpath}` | 整理后视频路径**自动剔除库根前缀**后的部分 (POSIX 分隔符，无前导 `/`)，仅 strm 内容模板 | 库根 `/test` 时 → `OD/VC/MIDV-123/MIDV-123.mp4` |

### 默认模板

```
视频: {studio}/{number}/{number}.{ext}
缩略图: {link_dir}/thumb.jpg
海报: {link_dir}/poster.jpg
NFO: {link_dir}/{number}.nfo
字幕: {link_dir}/{raw_srt_name}.{ext}
```

### 示例

假设番号为 `MIDV-123`, 标题为 `Sample Title`:

```
{number}/{number}.mp4          → MIDV-123/MIDV-123.mp4
{studio}/{number}/{title}.mp4  → Studio Name/MIDV-123/Sample Title.mp4
{studio}/{number}/{number}-{mosaic}-{definition}.{ext}  → Studio Name/MIDV-123/MIDV-123-uncensored-4K.mp4
```

!!! warning
    `{definition}` 只会从文件名中的 `-4K`/`-HD` 等解析, 无法获取时会变为 `Unknown`.
    若只把清晰度放在目录段 (如 `{definition}/{number}.mp4`),
    则后续整理时该字段会无法获取而变为 `Unknown`. 因此若要用 `{definition}` 最好在文件名中同时记录,
    (如 `{number}-{definition}.{ext}`).

## 链接模板与模式

媒体库支持在库外创建指向库内视频的入口, 适用于网盘挂载等场景:

- **`link_template`**: 链接文件的路径模板 (如 `/本地路径/{number}/{number}`). 为空则不创建链接, `{link_dir}` 等于 `{video_dir}`.
- **`link_mode`**: 链接类型
  - `strm`: 创建 `.strm` 文件 (内容默认为视频绝对路径), Emby/Jellyfin 可识别
  - `symlink`: 创建文件系统软链接
- **`strm_content_template`**: `.strm` 文件的内容模板 (单行), 仅 `link_mode=strm` 生效. 为空则写视频绝对路径.
  `{video_relpath}` 会自动剔除库根前缀, 库根比挂载点深时请在模板里补回缺的层级 (见下).

### 使用场景

网盘库整理时, 库路径指向挂载盘 (如 `/mnt/cloud`), `link_template` 填本地路径 (需在 `safe_dirs` 内):

- 视频在挂载盘上按模板改名
- 本地出现 strm 文件或软链接 + NFO/海报
- 媒体服务器扫描本地路径即可

### strm 内容: 对齐 OpenList / MediaWarp

默认写的是**本地**绝对路径. 用 rclone 挂载 OpenList 时, 这条路径与 OpenList 上的文件路径差一个挂载前缀,
MediaWarp 的 **AlistStrm** 认不出来. 填 `strm_content_template` 换掉这个前缀:

**`{video_relpath}` = 视频落地路径自动剔除「库根」之后的部分.** 剔掉的是**库根**, 不是挂载点 —— 两者相同时直接可用,
库根更深时少掉的层级要自己在模板里补:

| 库根 (媒体库的「路径」) | `video_template` | 视频落地 | strm 内容模板 | strm 内容 |
|---|---|---|---|---|
| `/test` (= 挂载点) | `OD/VC/{number}/{number}.{ext}` | `/test/OD/VC/MIDV-123/MIDV-123.mp4` | `/{video_relpath}` | `/OD/VC/MIDV-123/MIDV-123.mp4` |
| `/test/OD/VC` (比挂载点深) | `{number}/{number}.{ext}` | 同上 | `/OD/VC/{video_relpath}` | `/OD/VC/MIDV-123/MIDV-123.mp4` |
| `/test` + Alist 子目录挂载 | `OD/VC/{number}/{number}.{ext}` | 同上 | `/OneDrive/{video_relpath}` | `/OneDrive/OD/VC/MIDV-123/MIDV-123.mp4` |
| `/test` + HTTPStrm 直链 | `OD/VC/{number}/{number}.{ext}` | 同上 | `http://alist:5244/d/{video_relpath}` | `http://alist:5244/d/OD/VC/MIDV-123/MIDV-123.mp4` |

!!! warning "库根比挂载点深时前缀会连带被剔掉"
    库根 `/test/OD/VC` 时, `OD/VC` 属于库根的一部分, `{video_relpath}` 只剩 `MIDV-123/MIDV-123.mp4`,
    strm 内容会变成 `/MIDV-123/MIDV-123.mp4` —— OpenList 上找不到.
    模板前面补 `/OD/VC/` 即可, 无需改库根 (改库根会连带影响扫描范围, 且 `video_template` 也得跟着改).

!!! tip
    要拼进文件名时, 优先用 `{video_relpath}` 而不是手写 `/OD/VC/{number}/{number}.{ext}`.
    `{video_relpath}` 取自视频**实际落地路径**, 自动带上分集后缀 (`-CD1`) 与重名时的 `(1)` 后缀;
    手写元数据占位符会丢掉这两者, 让 strm 指向不存在的文件.

!!! note
    改完模板重跑「整理」即可刷新已生成的 strm — 内容不同会直接覆盖重写, 不需要先删文件.
    模板不能含换行 (strm 是一行路径), 保存时会拒绝.
    若 `video_template` 用绝对路径把视频放到了库根之外 (多盘分存), `{video_relpath}` 无解,
    该文件整理会记失败并给出明确错误, 而不是写出一个错的 strm.

!!! note
    链接路径必须在库根之外, 否则会被扫描为新文件. `{link_dir}` 占位符在有链接时指向链接父目录, 默认的 NFO/海报模板已改用 `{link_dir}`, 因此填写链接模板后附属文件自动跟随, 无需修改其他模板.

## 整理操作

整理 (Organize) 操作会将媒体文件按路径模板放置到指定位置:

1. 在媒体库页面点击「整理」
2. 系统会根据路径模板计算目标位置
3. 按放置方式 (移动/复制/硬链接/符号链接) 执行
4. 如果配置了链接模板, 在库外创建 strm 文件或软链接

!!! note
    整理操作不会自动触发, 需要手动执行. 可以通过任务系统排队整理任务.

## 分集 (CD) 后缀

Amane 支持自动识别分集文件并添加后缀:

- `-CD1`, `-CD2` — 标准分集标记
- `-Part1`, `-Part2` — 替代分集标记
- `-A`, `-B` — 字母分集
- `-1` 到 `-9` — 尾部数字分集

分集后缀模板默认为 `-CD{cd}`, 可在库设置中自定义.

## 字幕文件

整理时会把视频**同目录**下的字幕文件一起搬走 (不递归子目录, 字幕本身不入库):

- 扩展名可在库设置中配置, 默认 `.srt` `.ass` `.ssa` `.vtt` `.sub`. 留空则不处理字幕.
- 多个字幕全部带走, 保持原文件名和扩展名, 默认放到整理后视频的同一目录.
- 按字幕文件名上的分集标记与当前视频配对; 解析不出的跟无分集或 CD1 的视频.

## 预告片跳过

库可配置正则表达式匹配预告片文件, 匹配的文件不会被当作正片处理:

- 默认模式: `trailer.mp4`
- 仅对文件名 (含扩展名) 匹配

## 小视频过滤

库可设置最小视频体积 (默认关闭). 小于阈值的文件不会被当作正片入库, 整理时移入库根 `.amane_trash`:

- 只作用于扫描用的视频扩展名 (与监控/扫描配置同一套, 默认 `.mp4` `.mkv` 等)
- 图片、NFO、字幕不受此阈值影响
- `.strm` 是路径指针, 不按体积过滤
- 软链接按目标文件体积判断, 不是链接本身那几个字节
- 0 表示关闭

## 自动化工作流

### 扫描

扫描是发现媒体文件并注册到数据库的过程:

- 手动扫描: 在库页面点击「扫描」
- 自动扫描: 文件监控检测到变化时自动触发

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
