# 开始使用

## 桌面应用

从 [GitHub Releases](https://github.com/sqzw-x/amane/releases) 下载对应平台的安装包:

- **macOS**: 下载 zip, 解压后将 Amane.app 拖入 Applications. 常驻菜单栏, 一键打开 Web 界面.
- **Windows**: 下载 zip 解压, 运行 `Amane.exe`. 常驻系统托盘, 一键打开 Web 界面.

桌面应用内置服务监督, 崩溃自动重启.

## Docker

推荐使用 Docker Compose, 参考 [compose.yaml](https://github.com/sqzw-x/amane/blob/main/compose.yaml)

## 源码安装

前置依赖:

- [uv](https://github.com/astral-sh/uv) — Python 包管理
- [pnpm](https://pnpm.io/) — 前端包管理
- [just](https://github.com/casey/just) — 任务运行器

```bash
git clone https://github.com/sqzw-x/amane.git
cd amane
just setup   # 同步依赖
just dev     # 启动 API (:8000) + 前端开发服务器
```

## 首次登录

启动服务后, 浏览器访问 `http://localhost:8000` (桌面应用会自动打开).

首次访问需要输入 API Token:

- **桌面应用**: 在菜单栏/托盘图标中点击「复制 Token」, 粘贴到浏览器即可
- **Docker**: 运行 `docker logs amane` 查看启动日志中的 Token
- **源码**: 查看终端输出中的 Token

!!! tip
    Token 仅需输入一次, 浏览器会通过 HttpOnly Cookie 保持登录状态.

## 添加媒体库

登录后, 进入「管理 → 媒体库」页面创建你的第一个媒体库:

1. 点击「添加库」
2. 设置库名称
3. 设置媒体目录路径 (需在 Docker 挂载范围内)
4. 选择文件监控: 本机磁盘用「本地文件」; CloudDrive2 挂载用「CD2 webhook」, 并填写 CloudDrive 路径 (详见 [媒体库管理](libraries.md))
5. 选择自动化级别:
   - **不自动**: 仅手动扫描
   - **仅监控**: 文件变化时自动注册
   - **监控+刮削**: 文件变化时自动注册并刮削
6. 配置路径模板 (可使用默认值)

创建后, 点击「扫描」按钮开始首次扫描, 或等待文件监控自动登记.

## 下一步

- [配置指南](configuration.md) — 了解环境变量和应用配置
- [媒体库管理](libraries.md) — 深入了解库管理与路径模板
- [刮削指南](scraping.md) — 了解多源聚合和内容路由
- [插件](plugins.md) — 安装和开发刮削插件
- [常见问题](faq.md) — 排障与 FAQ
