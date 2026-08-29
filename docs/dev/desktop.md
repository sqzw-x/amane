# 桌面形态: 菜单栏 / 托盘

> 提交: `e8b40a9`
>
> 本文解释桌面形态的进程边界、IPC 契约与打包方式. UI 相关配置 (路径/开关) 走壳进程环境变量, **不进** [config.md](config.md) 的 Cold/Hot 分层.

## 品牌标

单一源 `assets/logo.svg` (蓝色渐变圆角徽标 + 播放字腔字标 A). 改完跑 `just icons`, 提交衍生文件:

- WebUI favicon / 顶栏: `web/public/favicon.svg` (与源文件相同)
- macOS `.app`: `assets/app.icns` → `CFBundleIconFile=AppIcon` (`LSUIElement` 不进 Dock, Finder / 通知仍用这份)
- Windows `Amane.exe` / 托盘: `assets/app.ico` (`ApplicationIcon`; 托盘从 exe 抽同一份, GDI 回退只画渐变徽章 + 实心 A + 播放字腔, 尖角近似)
- macOS 菜单栏: 模板字形 A (只取 alpha, 系统着色). 彩色徽标在菜单栏会糊成色块, 不能当模板用.

`just icons` 需要 `rsvg-convert`; `app.icns` 还需要 macOS `iconutil`. 衍生文件入库, 打包机不必装 librsvg.

## 进程模型

Python 只跑 HTTP (与 Docker / `just start` 同一入口 `amane.server`), 不含监督、不含 UI. 壳是原生进程, 设桌面环境变量、监督 Python、画菜单栏/托盘. UI 与服务之间**只有 HTTP**, 无专用通道.

**macOS** 三个进程. Swift 是 App 入口; 菜单栏是兄弟进程, 不是服务的孩子:

- **应用进程**: `macapp/Sources/Amane` → `Contents/MacOS/Amane` (CFBundleExecutable). Launch Services 登记为 `com.github.sqzw-x.amane` (必须是 NSApplication, 第二次打开才走系统单实例). 本进程设环境、监督 Python、拉起/回收菜单栏. 服务退出码 **0 / 130 / 143** 结束 App; **3** 立刻再拉 Python (UI 继续活着); **126 / 127** exec 失败退出; 其它退避 2s. TERM 时先停两个子进程. Info.plist: `LSUIElement` + `LSMultipleInstancesProhibited`. `AMANE_SUPERVISED=1`.
- **服务进程**: PyInstaller onedir, 冻住 `amane.server`.
- **UI 进程**: 嵌套 `Contents/Resources/AmaneUI.app` (`com.github.sqzw-x.amane.ui`). 独立 bundle id, 避免和主进程抢 NSApplication. 无状态, 只轮询 HTTP. `--watch-parent` 指向**应用进程** PID. `NSStatusItem` 只能在 `applicationDidFinishLaunching` 里创建: 更早碰菜单栏时 WindowServer / CGS 尚未就绪, SkyLight 会断言退出, 菜单栏永远不出现.

macOS 上 UI 不嵌进服务进程: 原生菜单 / 通知; 嵌进 Python 会把签名、公证和崩溃隔离变差. 菜单栏本身是独立 NSApplication (`NSStatusItem`), 不能与 Launch Services 身份共用 bundle id, 因此是嵌套 `AmaneUI.app`.

**Windows** 两个进程. 监督与托盘在同一个 `WinExe` 里 (没有 Launch Services / bundle id 可抢; `NotifyIcon` 必须跟消息循环同进程):

- **应用进程**: `winapp/` → `Amane.exe`. 命名 Mutex `Local\com.github.sqzw-x.amane` 单实例. 隐藏窗口泵消息 + 托盘; 后台线程监督 Python. Python 放进 Job Object (`KILL_ON_JOB_CLOSE`), 任务管理器杀掉壳时服务一起走. 退出码 **0 / 130 / 143 / 0xC000013A** (Windows Ctrl+C) 结束 App; **3** 立刻再拉 Python (托盘还在); `Process.Start` 失败结束 App; 其它退避 2s. `AMANE_SUPERVISED=1`.
- **服务进程**: PyInstaller onedir `onedir/Amane.Server.exe`, 由壳 `CreateNoWindow` 拉起.

Windows 上不要把托盘塞进 Python: `exit 3` 会拆掉图标, PyInstaller 身份更招 Defender. 壳与托盘同进程: 托盘逻辑只是 HTTP 轮询 + 菜单, 不值得再拆一个 exe.

## IPC 契约

| 方向 | 方式 |
|------|------|
| 状态展示 | 每 3s 轮询 `GET /api/system/desktop`, 菜单里显示 "运行中 · v{version}" / "未连接" |
| 打开 Web UI | 系统默认浏览器打开启动时的 base URL (Windows 托盘双击同样打开) |
| 打开数据目录 | `/api/system/desktop` 的 `data_dir`; 未连接时置灰 |
| 检查更新 | `GET /api/system/release`; 有新版本则打开 `html_url`, 否则提示已是最新 |
| 重启服务器 | `POST /api/system/restart` (仅 `supervised`); 菜单直接请求, 不确认 |
| 复制 API Token | 壳拿到的 token 拷入剪贴板; 未传 (关鉴权) 时置灰 |
| 退出 | 菜单「退出」停壳; 壳先停 Python (就绪时走同一条 `POST /api/system/restart` 优雅停机, 因 stopping 不再拉起; 否则 Kill), 再卸托盘 |

bar 的静态信息**不走** `/api/health`: 后者是就绪契约 (Docker healthcheck). `/api/system/desktop` 是 bar 专属 (`version` / `data_dir` / `supervised`).

**本地化**: 菜单字符串按系统 UI 语言 (zh / en), 不跟随前端浏览器语言.

**鉴权**: token 模式与 cookie 见 [config.md](config.md). 壳等 bootstrap 写入 `data_dir/token` 后再带 `Authorization`. 打开 Web UI 只传 base URL.

macOS UI argv (`AmaneUI --base-url http://127.0.0.1:PORT [--token <token>] [--watch-parent [pid]]`):

- `--base-url`: 必传 (dev 可省略, 默认 `http://127.0.0.1:18000`).
- `--token`: 仅用于轮询 `Authorization`. 不进打开 Web UI 的 URL.
- `--watch-parent [pid]`: bundle 传入应用进程 PID; 「退出」对该 PID 发 SIGTERM. 省略时回退 `getppid()`. pid ≤ 1 视为未监视 (`just bar-run` 不传, 「退出」只关 UI).

Windows 无独立 UI 进程, 无这组 argv. `AMANE_UI_ONLY=1` 只开托盘、不拉 Python (`just windows-bar`), 对已有服务轮询; 「退出」只关托盘.

## 生命周期

**macOS**

- **打开 Amane.app** → Launch Services 启动应用进程 → 设环境 → spawn Python → 等 token 文件 → spawn UI.
- **第二次打开** → Launch Services 按 `com.github.sqzw-x.amane` 拦截.
- **UI 意外退出** → 应用进程退避再拉 UI (Python 不受影响).
- **菜单「退出 Amane」** → SIGTERM 应用进程 → 停 Python + UI.
- **菜单「重启服务器」** → `POST /api/system/restart` → Python `exit 3` → 应用进程立刻再拉 Python; UI 还在, 轮询会重新连上.
- **Python 崩溃** (其它非 0) → 应用进程退避再拉; UI 仍在.
- **对 onedir/Amane Force Quit (SIGKILL)** → 当成崩溃再拉. 停 App 用菜单或结束应用进程.

**Windows**

- **打开 Amane.exe** → Mutex; 已有实例则立刻退出 → 设环境 → 托盘 + spawn Python; token 文件就绪后轮询带鉴权.
- **第二次打开** → Mutex, 退出.
- **菜单「退出 Amane」** → stopping, 停 Python, 卸托盘, 结束消息循环.
- **菜单「重启服务器」** → 同 macOS: `exit 3`, 托盘不拆.
- **Python 崩溃** → 退避再拉; 托盘不拆.
- **任务管理器结束 Amane.exe** → Job Object 带走 Python.
- **explorer.exe 重启** → `TaskbarCreated` 后重新 `NIM_ADD`.

Windows 壳是 Per-Monitor V2 (`winapp/app.manifest`). 未声明时系统把 `TrackPopupMenu` 整张位图按缩放拉伸, 高分屏上菜单字发糊. 菜单跟的是**属主 HWND 的 DPI**, 不是光标所在监视器; 隐藏窗口默认在 (0,0), 弹出前必须先移到光标处, 否则主屏 100% + 任务栏在高分屏时仍然发糊.

壳设置的环境变量:

- `AMANE_HOST=127.0.0.1` `AMANE_PORT=18000` (可覆盖; 绑回环, 避免防火墙弹窗)
- `AMANE_DATA_DIR` `AMANE_LOG_DIR` → macOS `~/Library/Application Support/Amane`; Windows `%LOCALAPPDATA%\Amane`
- `AMANE_SAFE_DIRS` — 认证后的调用方是用户本人. 桌面默认 `ALLOW_ALL` (关闭路径边界, 含 UNC / 迟到的网络盘). 想收紧可改成逗号分隔的目录名单. Docker 仍用显式名单 (见 compose `AMANE_SAFE_DIRS=/media`). 文件浏览器在 `ALLOW_ALL` 下相对路径缺省根为 POSIX `/`、Windows `C:\`.
- `AMANE_WEB_DIST` → 包内 `web/dist`
- `AMANE_SUPERVISED=1` `PYDANTIC_DISABLE_PLUGINS=1`
- macOS: `AMANE_UI_BINARY` 覆盖 UI 路径; `AMANE_UI_DISABLED=1` 不拉菜单栏 (传给 Python 以防误拉).
- Windows: `AMANE_BIN` 覆盖服务 exe; `AMANE_UI_ONLY=1` 不拉服务.

## 打包

macOS: `scripts/build_macos_app.sh` (`just macos-app`). 冻住 `amane.server` 为 onedir, 再组装 `.app`. 需要 Swift 工具链.

1. PyInstaller onedir (`src/amane/server.py`)
2. `Amane` → `Contents/MacOS/Amane`; `AmaneUI` 包成 `Contents/Resources/AmaneUI.app`
3. Info.plist: `LSUIElement` + `LSMultipleInstancesProhibited` + `CFBundleIconFile`

Windows: `scripts/build_windows_app.ps1` (`just windows-app`). 必须在 Windows 上跑 (PyInstaller 与 Native AOT 都不能从 macOS 交叉). 需要 .NET 8 SDK + 能链 Native AOT 的 MSVC.

1. PyInstaller onedir, `--name Amane.Server`
2. `dotnet publish -r win-x64` Native AOT `Amane.exe`
3. 目录: `Amane.exe` + `onedir/Amane.Server.exe` + `web/dist`

开发回路: `just dev` 起服务 + `just bar-run` (macOS) / `just windows-bar` (Windows) 只开托盘.
