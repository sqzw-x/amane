# Changelog

## v0.7.0

### ✨ 新功能

- **strm 文件内容支持自定义模板**: 媒体库新增 `strm 内容模板` (仅链接方式为 STRM 时生效), 可把 `.strm` 里的本地挂载路径换成 OpenList 侧路径或 HTTP 直链, 让 MediaWarp 的 AlistStrm / HTTPStrm 正确识别. 留空保持原行为 (写视频绝对路径), 存量库不受影响.
  - 新增占位符 `{video_relpath}` (视频落地路径剔除**库根**前缀后的部分) 与 `{video_path}` (绝对路径). 二者取自视频**实际落地路径**, 自动带上分集后缀 (`-CD1`) 与重名时的 `(1)` 后缀
  - 典型写法: 库根即挂载点时填 `/{video_relpath}`; 库根比挂载点深时补回缺的层级, 如 `/OD/VC/{video_relpath}`; Alist 子目录挂载填 `/OneDrive/{video_relpath}`; HTTPStrm 填 `http://alist:5244/d/{video_relpath}`
  - 改完模板重跑「整理」即可刷新已生成的 strm, 无需先删文件

## v0.5.0

### ✨ 新功能

- **媒体库文件过滤** (#13), 可设置正则规则跳过指定文件; 并在整理时将其移动到 `.amane_trash`, 可用于过滤广告视频等
- **增强演员别名** (#10)
- **路径模板新增 `{mosaic}`, `{definition}`, `{raw_name}`, `{raw_dir}`** (#4 / #24)
- **整理时自动移动字幕** (#9 / #23): 同目录字幕随视频一起整理

### 🐛 修复

- **macOS 15 App 无法启动** (#28)

## v0.4.2

### 🐛 修复

- **演员刮削** (#10): javdb 女优页默认性别、英文页兼容、搜索命中大小写不敏感; Wikidata 改多语言搜索 (ja/zh/en) + AV 关键词/职业判定. 伊藤舞雪、miru 可直接刮取并自动获得性别.
- **文件浏览器**: 挂载盘/跨盘路径兼容与规范路径化 (#8).
- **日志与网络盘**: LoggingMiddleware 置最外层收口请求日志; 网络盘失效不再无日志 500.
- **worker**: `stop()` 终止主循环, 消除在飞 claim 竞态.
- **Docker**: 镜像注入 `AMANE_LOG_DIR` (#5), compose 配置 `AMANE_SAFE_DIRS` (#6).

## v0.4.1

### 🐛 修复

- **文件浏览器跨盘符路径选择报 Internal Server Error** (#1): `os.path.commonpath` 在 Windows 不同盘符 (如 `C:` 与 `D:`) 上抛 `ValueError`, 导致 `/api/files` 浏览其他盘路径时返回 500. 现在跨盘路径视为非后代, 安全检查会跳过该盘并继续校验其余可信目录.
- **Windows 上资源 serve 404**: 资源库 `file_path` 之前按 OS 原生分隔符存储 (Windows 反斜杠), 与按 hash 前缀的查询模式不匹配; 统一存 POSIX (`/`) 分隔符.

### 🧪 测试 / CI

- **CI 测试门禁改为 Linux + Windows 双平台运行**: 此前只在 Ubuntu 上跑, Windows 专属用例 (如跨盘符路径测试) 全部被跳过, 这也是 #1 未被发现的原因.
- `export_openapi` 固定写 LF、新增 `.gitattributes` 统一文本行尾, 修复 Windows 上 `check-openapi` / `check-web` 因 CRLF 导致的整文件误报.
- 路径模板 / 二进制路径测试改为跨平台写法; `TestRepoRoundTrip` 改用独立文件库消除 FeedService 后台 poll 竞态.
