# Android 壳

> 本文记录 Android 端的进程边界、鉴权契约、平台功能取舍与打包方式.
> 桌面形态 (macOS 菜单栏 / Windows 托盘) 见 [desktop.md](desktop.md).

## 进程模型

Android 端是**远程客户端**, 不是桌面壳的同类: 服务端 (FastAPI + SQLite + 运行期加载的 Python 插件) 无法打包进 APK, 也没有必要 — 媒体库、Emby 集成与任务系统都在服务器上.

`androidapp/` 是一个 Kotlin WebView 壳, 只有一个 Activity 家族加一个设置页:

| 组件 | 职责 |
|------|------|
| `BrowserActivity` | WebView 宿主: 工具栏菜单、下载、自定义视图 (全屏视频)、画中画、窗口 inset |
| `MainActivity` | `BrowserActivity` 的 `singleTask` 入口 |
| `PopupActivity` | `window.open` 的目标窗口, 每个弹窗一个实例 |
| `SetupActivity` | 服务器地址列表与首次登录 |
| `ServerStore` / `ServerUrl.kt` | 地址持久化与归一化 (`ServerUrlTest` 覆盖) |

## origin 契约

**WebView 顶层加载的必须是服务端自身 origin** (`scheme://host[:port]`), 不允许把 SPA 打包进 APK 再指向远端 API:

- 鉴权是 HttpOnly cookie (`middleware.py::TokenAuthMiddleware`), 服务端 CORS 显式关闭了凭据 (`api/app.py` 的 `allow_credentials=False`), 跨源带凭据的请求不可能通过.
- 子资源请求无法携带 `Authorization`: 图片代理 (`<img>`)、WebSocket 握手、SSE、播放的 Range 请求都只能依赖 cookie. 换成自带资源的本地 origin 后, 这几条链路全部返回 401.

由此推出: 前端不需要运行期 API 地址 (`VITE_API_URL` 仍是构建期变量), 也不需要为壳做条件分支.

## 登录

首次连接的 token 只在 `SetupActivity` 里用于向 `/api/system/desktop` 发一次 Bearer 请求; 中间件随之下发 `amane_token` cookie, 壳把响应头里的 `Set-Cookie` 手工写入 `CookieManager` (原生请求的响应头 WebView 不可见), 之后一切与浏览器一致. token 不落盘, 登录态就是 WebView 的 cookie 罐. cookie 失效时由 SPA 自己的登录门接管.

服务端关闭鉴权 (`AMANE_TOKEN=off`) 或用户留空 token 时, 探活返回 401 也直接打开页面.

## 平台功能

| 场景 | 处理方式 |
|------|---------|
| 主文档加载失败 | 原生错误页 (重试 / 换服务器), 不显示 WebView 自带的错误页 — 局域网服务器关机会经常遇到 |
| 下载 (`Content-Disposition: attachment`) | `DownloadManager`; 它在独立进程, 不共享 cookie 罐, 因此显式写入 `Cookie` 请求头 |
| `window.open` | 附件交给下载监听器 (任务记录导出即此类), 真页面才另起 `PopupActivity` |
| 全屏视频 | `onShowCustomView` 的自定义视图; 返回键先请求页面退出全屏, 超时未退出则按原生方式收起 |
| 按 Home 键 | 全屏视频转画中画 (`PictureInPictureParams` 的宽高取自自定义视图) |
| 站外链接 | 交给系统浏览器, 不留在 WebView 内 |
| 浅色/深色 | `WebSettingsCompat.setAlgorithmicDarkeningAllowed`, 让 `prefers-color-scheme` 跟随系统 |

窗口 inset 以原生 padding 施加在容器上, 页面不使用 `env(safe-area-inset-*)`: WebView 的视口因此等于安全区, SPA 既有的 `100dvh` 高度计算 (`web/src/components/layout/app-shell-metrics.ts`) 无需改动.

`usesCleartextTraffic="true"` 是刻意的: 网络策略不能按用户在运行时填写的地址放开明文, 而自建服务默认是 `http://<host>:8000`. 非局域网部署应自备 HTTPS 反代.

## WebView 运行期

壳不携带浏览器内核, 页面运行在设备自带的 WebView 上, 版本由用户设备决定. 前端因此声明一个运行期下限 (Chromium 108 / Safari 16.4, 见 `web/vite.config.ts` 的 `MODERN_TARGETS`), 兼容由 `@vitejs/plugin-legacy` 承担: `modernTargets` 同时充当语法目标与 `@babel/preset-env` 的收集目标, polyfill 从 core-js 按 bundle 的实际使用自动挑选, 不需要维护方法清单. `renderLegacyChunks` 关闭 — 下限内核都支持 ESM, 不需要 SystemJS 包.

下限由样式表决定, 不由 JS 决定: 构建产物用到 `dvh` (108) / `:has()` 与 `@container` (105) / `color-mix()` (111), 而 CSS 不能由 core-js 补. 把下限声明得低于样式表的真实下限, 只会把「明确报错」换成「能渲染但残缺」; 下调下限必须同时处理 CSS. 低于下限的设备须更新「Android System WebView」.

`build.target` 只降语法: 内建方法 (`Array.prototype.toSorted` 等) 不会被降级, 缺失时只能由 polyfill 提供 — 因此「降低构建目标」不能替代这里的配置.

## 打包与分发

```
just android-app                     # → dist/Amane-<version>-android.apk
```

`scripts/build_android_app.sh` 从 `amane.version` 取版本写入 `versionName`, 并按 semver 推导单调递增的 `versionCode` (Android 拒绝降级覆盖安装). `androidapp/keystore.properties` 存在时构建 `assembleRelease` (签名密钥相对 `androidapp/`), 否则退回 debug 包.

CI 见 `.github/workflows/android-app.yml`: 提供 `ANDROID_KEYSTORE_BASE64` / `ANDROID_KEYSTORE_PASSWORD` / `ANDROID_KEY_ALIAS` / `ANDROID_KEY_PASSWORD` 四个 secret 才会产出正式签名包, 否则产物是 debug 包. 分发方式是 GitHub Release 上的 APK 侧载; 应用商店对本项目的媒体内容域不可行, 因此不引入 Play 相关的签名托管与更新机制.

PR 门禁由 `.github/workflows/ci.yaml` 的 `android` job 执行 `just android-check` (编译 debug 包 + 单元测试).

最低支持 Android 10 (`minSdk 29`): 该版本起 `DownloadManager` 写公共目录不需要存储权限, 边缘到边缘与 WebView 行为也是当前设计所依据的基线.

## 不支持

- **手机端运行服务端**: 打包形式 (PyInstaller onedir) 与运行期加载的 Python 插件都不成立.
- **外部播放器与后台播放**: 播放流地址需要 cookie 才能取, 交给外部播放器必须在壳内起一个回环代理转发 `Range` 并附加凭据; 当前播放与画中画都在 WebView 内完成.
- **切换服务器保留页面状态**: 不同 origin 各自持有 cookie 与 localStorage, 换地址等于重新登录.
