# 安装与快速开始

本页说明如何在桌面、Docker 或源码环境中安装 Amane, 完成首次登录, 并在几分钟内刮削第一部影片. 适用于首次部署的用户.

## 选择安装方式 {#platforms}

| 方式 | 适用场景 | 获取 |
|------|---------|------|
| :material-apple: macOS 桌面版 | 在个人电脑上使用 | [Releases](https://github.com/sqzw-x/amane/releases) 中的 `Amane-v*-macos.app.zip`, 需 macOS 13 及以上 |
| :material-microsoft-windows: Windows 桌面版 | 同上 | [Releases](https://github.com/sqzw-x/amane/releases) 中的 `Amane-v*-windows-x64.zip`, 仅 x64 |
| :material-docker: Docker | NAS、家庭服务器等长期运行的主机 | 镜像 `ghcr.io/sqzw-x/amane`, 支持 `linux/amd64` 与 `linux/arm64` |
| :material-android: Android 客户端 | 在手机或电视盒上访问已部署的服务 | [Releases](https://github.com/sqzw-x/amane/releases) 中 `app-` 开头的发布, 见 [客户端](clients.md#android) |
| :material-source-branch: 源码 | 开发插件或参与开发 | 本仓库, 需要 uv、pnpm 与 just |

桌面版与 Docker 运行的是同一个服务, 功能相同; 区别在于桌面版常驻菜单栏或托盘, 并自动重启异常退出的服务. Android 客户端不包含服务, 必须连接一台已部署的 Amane.

!!! tip "推荐做法"
    需要全天运行 (如定时任务与订阅) 时选择 Docker; 只在个人电脑上偶尔使用时选择桌面版. 两者都可以再用浏览器或 Android 客户端从其它设备访问, 见 [局域网与远程访问](clients.md#remote).

## 安装 {#install}

=== "macOS"

    1. 下载 `Amane-v*-macos.app.zip`, 解压后将 `Amane.app` 拖入「应用程序」.
    2. 打开 Amane. 应用不在 Dock 中显示, 菜单栏出现 Amane 图标.
    3. 点击菜单栏图标 → 「打开 Web UI」, 浏览器打开 `http://127.0.0.1:18000`.

    数据目录为 `~/Library/Application Support/Amane`. 菜单项、端口与设置文件见 [客户端](clients.md#desktop).

=== "Windows"

    1. 下载 `Amane-v*-windows-x64.zip` 并解压到任意目录. 保持解压后的目录结构, `Amane.exe` 依赖同目录下的 `onedir` 与 `web` 文件夹.
    2. 运行 `Amane.exe`. 系统托盘出现 Amane 图标.
    3. 双击托盘图标, 或右击后选择「打开 Web UI」, 浏览器打开 `http://127.0.0.1:18000`.

    数据目录为 `%LOCALAPPDATA%\Amane`. 菜单项、端口与设置文件见 [客户端](clients.md#desktop).

=== "Docker"

    部署以仓库中的 [`compose.yaml`](https://github.com/sqzw-x/amane/blob/main/compose.yaml) 为模板, 各项的作用见文件内注释与下方说明.

    1. 新建部署目录, 下载模板并创建数据目录与 `.env` 文件, 使容器以当前用户身份运行:

        ```bash
        curl -fsSLO https://raw.githubusercontent.com/sqzw-x/amane/main/compose.yaml
        mkdir -p data
        printf 'UID=%s\nGID=%s\n' "$(id -u)" "$(id -g)" > .env
        ```

    2. 编辑 `compose.yaml` 中的媒体目录: 需要使用 [媒体库](libraries.md) 时, 将 `/path/to/media` 替换为宿主机上的媒体目录, 有多个媒体目录时逐个映射, 并把容器内路径全部写入 `AMANE_SAFE_DIRS`; 只使用元数据时, 删除媒体目录与 `AMANE_SAFE_DIRS` 两行.
    3. 启动并读取 API Token:

        ```bash
        docker compose up -d
        docker logs amane 2>&1 | grep "api token"
        ```

    4. 浏览器访问 `http://<主机地址>:8000`.

    !!! note "镜像标签"
        `latest` 跟随最新版本; 另有 `0.23.0`、`0.23`、`0` 形式的版本标签, 需要固定版本时使用.

    ??? note "compose 中各项的作用"
        | 配置 | 作用 |
        |------|------|
        | `user` | 容器内进程的 UID/GID. 与宿主机用户不一致时, 写入 `data` 与媒体目录的文件属主不同, 整理与删除可能因权限失败 |
        | `/data` 卷 | 数据库、配置、图片缓存、插件与日志, 必须持久化 |
        | `/media` 卷 | 可选, 媒体目录. 创建媒体库时填写的是容器内路径 (如 `/media/JAV`) |
        | `AMANE_SAFE_DIRS` | 可选, 文件浏览器与媒体库路径允许的范围; 未设置时不能创建媒体库, 见 [配置参考](configuration.md#safe-dirs) |
        | `AMANE_SUPERVISED=1` | 启用页面中的「重启服务器」. 服务以退出方式重启, 依赖 `restart` 策略再次拉起容器, 两者必须同时设置 |
        | `healthcheck` | 模板中的就绪检查访问 `/api/health`, 该路径免鉴权 |

    升级时执行 `docker compose pull && docker compose up -d`. 数据库结构在启动时自动迁移, 迁移前在数据目录写入备份, 见 [数据目录](configuration.md#data-dir).

=== "源码"

    前置依赖: [uv](https://github.com/astral-sh/uv)、[pnpm](https://pnpm.io/)、[just](https://github.com/casey/just). Python 版本由 uv 按项目要求 (3.14 及以上) 自动准备.

    ```bash
    git clone https://github.com/sqzw-x/amane.git
    cd amane
    just sync    # 同步 Python 与前端依赖
    just start   # 构建前端并启动服务
    ```

    服务监听 `http://127.0.0.1:8000`, 数据写入仓库根目录的 `data/`, 日志写入 `logs/`. 修改监听地址与端口需设置真实环境变量, 如 `AMANE_HOST=0.0.0.0 AMANE_PORT=8080 just start`.

    !!! warning "仓库自带的 `.env.dev` 关闭了鉴权"
        仓库根目录的 `.env.dev` 设置了 `AMANE_TOKEN=off` 与 `AMANE_SAFE_DIRS=ALLOW_ALL`, 以便本机开发. 服务需要被其它设备访问时, 在仓库根目录创建 `.env` 并写入 `AMANE_TOKEN=` (留空表示自动生成), `.env` 的取值优先于 `.env.dev`. 加载规则见 [配置参考](configuration.md#env-files).

    `just dev` 同时启动带热重载的 API (`:8000`) 与 Vite 开发服务器, 仅用于开发; `just --list` 列出全部命令.

## 首次登录 {#login}

服务默认启用 API Token 鉴权. 首次打开页面时显示「需要登录」, 将 Token 粘贴到输入框即可.

| 安装方式 | Token 获取方式 |
|---------|---------------|
| 桌面版 | 菜单栏 / 托盘图标 → 「复制 API Token」 |
| Docker | `docker logs amane` 中 `api token auth enabled` 一行的 `token` 字段, 或数据目录的 `token` 文件 |
| 源码 | 终端输出的同一行日志, 或 `data/token` 文件 (`.env.dev` 关闭鉴权时无需登录) |

登录成功后, 服务向浏览器写入 HttpOnly Cookie, 有效期 30 天; Token 不写入浏览器存储, 也不出现在地址栏. 每个浏览器与每个访问地址各自登录一次.

Token 在首次启动时随机生成并保存在数据目录的 `token` 文件中, 重启后沿用. 更换 Token、关闭鉴权与图片 Token 见 [配置参考](configuration.md#token).

!!! danger
    持有 Token 即拥有服务的全部权限, 包括浏览与删除媒体文件. 不要把 Token 写入公开的配置或截图.

## 五分钟上手 {#quickstart}

1. **安装并启动服务.** 按 [安装](#install) 中对应平台的步骤操作.
2. **登录.** 打开 Web 界面, 粘贴 API Token, 见 [首次登录](#login).
3. **刮削一个番号.** 进入「运行 → 任务」, 点击「提交任务」, 类型选择「刮削」, 填写番号 (如 `MIDV-123`) 后提交. 内容类型可以留空, 由番号推断. 部分站点需要代理或登录, 刮削失败时参考 [刮削](scraping.md#troubleshooting) 与 [网络与站点访问](network.md).
4. **在片库查看.** 进入「浏览 → 片库」, 打开刚刮削的影片, 查看各字段与图片; 「对比来源」列出各站点的原始结果, 可逐字段改选. 浏览、编辑与筛选见 [片库与浏览](browse.md).
5. **扩大范围.** 按需选择番号来源:
    - 「任务 → 批量刮削」用模板一次生成一组番号, 见 [任务与定时任务](tasks.md).
    - 添加 RSS / Atom 订阅源, 自动刮削新发布的番号, 见 [订阅](feeds.md).
    - 配置 [智能体](agent.md) 后, 用自然语言描述要刮削或修订的内容.
6. **(可选) 关联本地文件.** 已有本地视频时, 在「管理 → 媒体库」中添加目录, 由扫描与文件监控从文件名识别番号并刮削, 见 [媒体库](libraries.md). 需要供 Emby / Jellyfin 使用时, 再按 [整理与路径模板](organize.md) 写出 NFO 与图片, 见 [对接 Emby / Jellyfin](media-server.md).

!!! tip "影片与媒体库"
    片库 (「浏览 → 片库」) 列出的是影片, 即按番号刮削得到的元数据条目; 媒体库 (「管理 → 媒体库」) 是磁盘上的目录与其中的文件, 是可选的番号来源. 一部影片可以关联零个或多个媒体文件.

## 下一步 {#next}

- [客户端](clients.md): 桌面菜单栏 / 托盘、Android 客户端、局域网与远程访问.
- [配置参考](configuration.md): 环境变量、数据目录与设置页各分区.
- [智能体](agent.md): 在 Amane 首页用自然语言查询与管理片库.
- [常见问题](faq.md): 按症状排查.
