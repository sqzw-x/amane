# 桌面形态: 菜单栏 / 托盘

> 本文记录桌面形态的进程边界、IPC 契约与打包方式. 桌面形态的环境变量由壳的设置文件给出, 不纳入 [config.md](config.md) 的 Cold/Hot 分层.

## 品牌标

单一源 `assets/logo.svg`; 修改后执行 `just icons` 并提交衍生文件: WebUI favicon (`web/public/favicon.svg`)、macOS `assets/app.icns`、Windows `assets/app.ico` (托盘从 exe 抽同一份)、macOS 菜单栏模板字形 (只取 alpha 由系统着色 — 彩色徽标在菜单栏会糊成色块). `just icons` 需要 `rsvg-convert` 与 macOS `iconutil`; 衍生文件入库, 打包机不必装 librsvg.

## 进程模型

Python 只运行 HTTP (与 Docker / `just start` 同一入口 `amane.server`), 不含监督、不含 UI. 壳是原生进程, 求解桌面环境变量并注入 Python 子进程、监督 Python、绘制菜单栏 / 托盘. UI 与服务之间**只有 HTTP**, 无专用通道.

**macOS** 三个进程, Swift 是 App 入口, 菜单栏是兄弟进程而非服务的孩子:

- **应用进程**: `macapp/Sources/Amane` → `Contents/MacOS/Amane`; Launch Services 登记为 `com.github.sqzw-x.amane` (必须是 NSApplication, 第二次打开才走系统单实例). 本进程设置环境、监督 Python、启动与回收菜单栏. 服务退出码 **0 / 130 / 143** 结束 App, **3** 立刻再次启动 Python (UI 继续活着), **126 / 127** exec 失败退出, 其它退避 2s; TERM 时先停两个子进程. Info.plist 含 `LSUIElement` + `LSMultipleInstancesProhibited`, 并设置 `AMANE_SUPERVISED=1`.
- **服务进程**: PyInstaller onedir, 入口与导入约束见 [architecture.md](architecture.md).
- **UI 进程**: 嵌套 `Contents/Resources/AmaneUI.app` (`com.github.sqzw-x.amane.ui`). 独立 bundle id 以免和主进程抢 NSApplication; 无状态, 只轮询 HTTP; `--watch-parent` 指向**应用进程** PID. `NSStatusItem` 只能在 `applicationDidFinishLaunching` 里创建 — 更早碰菜单栏时 WindowServer / CGS 尚未就绪, SkyLight 会断言退出.

UI 不嵌进服务进程: 原生菜单 / 通知需要它独立, 嵌进 Python 会把签名、公证和崩溃隔离变差.

**Windows** 两个进程; 监督与托盘在同一个 `WinExe` 里 (没有 Launch Services / bundle id 可抢, `NotifyIcon` 必须跟消息循环同进程):

- **应用进程**: `winapp/` → `Amane.exe`. 命名 Mutex `Local\com.github.sqzw-x.amane` 单实例; 隐藏窗口泵消息 + 托盘, 后台线程监督 Python. Python 放入 Job Object (`KILL_ON_JOB_CLOSE`), 任务管理器杀掉壳时服务一起结束. 退出码 **0 / 130 / 143 / 0xC000013A** 结束 App, **3** 立刻再次启动 Python (托盘还在), `Process.Start` 失败结束 App, 其它退避 2s; 设置 `AMANE_SUPERVISED=1`.
- **服务进程**: PyInstaller onedir `onedir/Amane.Server.exe`, 由壳 `CreateNoWindow` 启动.

Windows 壳是 Per-Monitor V2 (`winapp/app.manifest`): 未声明时系统把 `TrackPopupMenu` 整张位图按缩放拉伸, 高分屏上菜单字发糊; 菜单跟的是**属主 HWND 的 DPI**, 因此弹出前必须先把隐藏窗口移到光标处.

## IPC 契约

| 方向 | 方式 |
|------|------|
| 状态展示 | 每 3s 轮询 `GET /api/system/desktop`, 菜单里显示 "运行中 · v{version}" / "未连接" |
| 打开 Web UI | 系统默认浏览器打开启动时的 base URL |
| 打开数据目录 | `/api/system/desktop` 的 `data_dir`; 未连接时置灰 |
| 检查更新 | `GET /api/system/release`; 有新版本则打开 `html_url` |
| 重启服务器 | `POST /api/system/restart` (仅 `supervised`), 菜单直接请求不确认 |
| 复制 API Token | 壳拿到的 token 拷入剪贴板; 未传 (关鉴权) 时置灰 |
| 退出 | 停壳; 壳先停 Python (就绪时经同一条 `POST /api/system/restart` 优雅停机, 因 stopping 不再再次启动; 否则 Kill), 再卸托盘 |

bar 的静态信息**不走** `/api/health` — 后者是就绪契约 (Docker healthcheck); `/api/system/desktop` 是 bar 专属. 菜单字符串按系统 UI 语言 (zh / en), 不跟随前端浏览器语言. 壳等 bootstrap 写入 `data_dir/token` 后再带 `Authorization`.

macOS UI argv (`AmaneUI --base-url http://127.0.0.1:PORT [--token <token>] [--watch-parent [pid]]`): `--base-url` 必传; `--token` 仅用于轮询 `Authorization`, 不进打开 Web UI 的 URL; `--watch-parent` 省略时回退 `getppid()`, pid ≤ 1 视为未监视. Windows 无独立 UI 进程与这组 argv; `AMANE_UI_ONLY=1` 只开托盘、不启动 Python, 对已有服务轮询.

## 生命周期

**macOS**: 打开 App → 设置环境 → spawn Python → 等 token 文件 → spawn UI; 第二次打开由 Launch Services 拦截. UI 意外退出或 Python 崩溃 (其它非 0) 时应用进程退避后再次启动, 另一方不受影响. 菜单「重启服务器」→ Python `exit 3` → 立刻再次启动 Python, UI 还在并重新连上. 对 onedir / Amane Force Quit (SIGKILL) 当成崩溃再次启动, 停 App 用菜单或结束应用进程.

**Windows**: 打开 exe 时先抢 Mutex, 已有实例则立刻退出. 菜单「退出」置 stopping、停 Python、卸托盘、结束消息循环; 「重启服务器」同 macOS. Python 崩溃退避后再次启动, 托盘不拆; 任务管理器结束 `Amane.exe` 时 Job Object 结束 Python; explorer.exe 重启后收到 `TaskbarCreated` 再 `NIM_ADD`.

## 桌面设置文件

壳注入的环境变量来自数据目录旁的 `desktop.env` (macOS `~/Library/Application Support/Amane/desktop.env`, Windows `%LOCALAPPDATA%\Amane\desktop.env`), 每行 `KEY=VALUE`, 首次启动写入注释模板. 该路径取默认数据目录, 不随 `AMANE_DATA_DIR` 变动 — 读取它必须早于确定数据目录.

- **优先级**: 真实环境变量 > 设置文件 > 壳内置默认值; 空值按未设置处理.
- **可写入的键**: `AMANE_HOST` (默认 `127.0.0.1`, 绑定回环以避免防火墙弹窗)、`AMANE_PORT`、`AMANE_DATA_DIR` / `AMANE_LOG_DIR` (默认数据目录及其 `logs`)、`AMANE_SAFE_DIRS` (默认 `ALLOW_ALL` 关闭边界校验)、`AMANE_TOKEN`.
- **生效时机**: 壳在每次启动 Python 前重新求解, 因此修改后经菜单「重启服务器」即生效. `AMANE_HOST` / `AMANE_PORT` 变化会改变 UI 兄弟进程的 `--base-url`, macOS 壳须同时重建该进程; Windows 的轮询地址由壳自身重算, 无需重建.
- **非法键**: 白名单之外的键在启动时提示一次并忽略, 服务仍以内置默认值启动.
- **同步要求**: 两个壳各自解析同一契约 (文件名、键列表、模板、解析规则), 新增环境变量时必须同步两侧白名单与模板.

壳自行设置且不允许经设置文件改写: `AMANE_SUPERVISED=1`、`PYDANTIC_DISABLE_PLUGINS=1`、`AMANE_WEB_DIST`, 以及 macOS 的 `AMANE_UI_BINARY` / `AMANE_UI_DISABLED=1` 与 Windows 的 `AMANE_BIN` / `AMANE_UI_ONLY=1` (后两组仅供开发回路).

`AMANE_SAFE_DIRS` 收紧为目录名单时按逗号分隔; Docker 仍用显式名单. 文件浏览器在 `ALLOW_ALL` 下相对路径缺省根为 POSIX `/`、Windows `C:\`, 认证后的调用方视为用户本人 (含 UNC 与迟到的网络盘).

## 打包

macOS: `scripts/build_macos_app.sh` (`just macos-app`), 需要 Swift 工具链 — PyInstaller 打成 onedir 后再组装 `.app`, `AmaneUI` 包成 `Contents/Resources/AmaneUI.app`, Info.plist 补 `LSUIElement` / `LSMultipleInstancesProhibited` / `CFBundleIconFile`. Windows: `scripts/build_windows_app.ps1` (`just windows-app`), **必须在 Windows 上运行** (PyInstaller 与 Native AOT 都不能从 macOS 交叉), 需要 .NET 8 SDK + 能链 Native AOT 的 MSVC.

两边 PyInstaller 都要 `--add-data` 打进 `amane/db/migrations` 与 `amane/media/watermarks` (Docker wheel 靠 hatch `force-include`), 并按平台收集整个标准库 (`scripts/stdlib_modules.py` 列出顶层模块, 只排除依赖包外产物的 `tkinter` / `turtle` / `idlelib` / `turtledemo` / `ensurepip`, 构建脚本为每个名字加 `--collect-submodules`). **插件是运行时从数据目录动态加载的**, PyInstaller 的静态导入图看不见它们引用什么; 不整包收集就会出现「插件在 `just dev` 与 Docker 里能用, 装进桌面版报 `ModuleNotFoundError`」.

开发回路: `just dev` 起服务 + `just bar-run` (macOS) / `just windows-bar` (Windows) 只开托盘.
