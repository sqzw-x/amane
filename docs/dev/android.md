# Android 壳

> 本文记录 Android 端的进程边界、鉴权契约、跨模块约定与打包方式.
> 桌面形态 (macOS 菜单栏 / Windows 托盘) 见 [desktop.md](desktop.md).

## 进程模型

Android 端是**远程客户端**: 服务端 (FastAPI + SQLite + 运行期加载的 Python 插件) 无法打包进 APK, 也没有必要 — 媒体库、Emby 集成与任务系统都在服务器上.

实现见 `androidapp/app/src/main/kotlin/com/github/sqzwx/amane/android/`: `BrowserActivity` 是 WebView 宿主与桥的挂载点, `MainActivity` 与 `PopupActivity` 是它的两个子类, `SetupActivity` 是服务器列表, `ShellBridge` 是页面可见的桥, `SwipeRowLayout` 承担服务器的左滑操作区.

## origin 契约

**WebView 顶层加载的必须是服务端自身 origin** (`scheme://host[:port]`), 不允许把 SPA 打包进 APK 再指向远端 API:

- 鉴权是 HttpOnly cookie (`middleware.py::TokenAuthMiddleware`), 服务端 CORS 显式关闭了凭据 (`api/app.py` 的 `allow_credentials=False`), 跨源带凭据的请求不可能通过.
- 子资源请求无法携带 `Authorization`: 图片代理 (`<img>`)、WebSocket 握手、SSE 与播放的 Range 请求都只能依赖 cookie, 换成自带资源的本地 origin 后这几条链路全部返回 401.

由此推出: 前端不需要运行期 API 地址 (`VITE_API_URL` 仍是构建期变量), 也不需要为壳做条件分支.

## 登录

首次连接的 token 只在 `SetupActivity` 里向 `/api/system/desktop` 发一次 Bearer 请求, 中间件随之下发 `amane_token` cookie; 壳把响应头里的 `Set-Cookie` 手工写入 `CookieManager` (原生请求的响应头 WebView 不可见), 之后一切与浏览器一致. token 随服务器条目一起存放在应用私有存储里, 明文可改 — 同一份存储里本就是原文, 掩码不构成保护; 登录态仍然只是 WebView 的 cookie 罐, 打开页面不会自动重发 token, cookie 失效由 SPA 自己的登录门接管.

**壳内不显示页面的登录门**: 页面无从区分「服务端换了 token」与「地址填错」, 而 token 的正确入口是服务器页 (它校验 Bearer 并换取 cookie). 入口处发现未认证 (挂载探活失败, 或任何请求 401 触发失效事件) 时跳回 `SetupActivity` 一次 — 每次页面加载只跳一次, 用户从那里返回后落在登录门, 门内另给一条「服务器设置」入口. 服务端关闭鉴权 (`AMANE_TOKEN=off`) 或用户留空 token 时, 探活返回 401 也直接打开页面.

## 服务器列表

条目是卡片: 第一行名字 (留空时取主机名与端口), 第二行地址, 当前服务器带「当前」标记; 点击卡片直接打开, 卡片左滑露出「编辑」与「删除」, 同一时刻只展开一行. 编辑弹窗改名字、地址与 token, 地址或 token 有改动时用新值在后台重新换取 cookie — 失败只提示, 编辑结果已经保存.

- 左滑是物理方向, 不随 RTL 镜像: 壳只有中英两套文案, 而操作区的 `layout_gravity="end"` 在 RTL 下会贴到左边, 操作区再也滑不出来, 因此条目容器固定 `layoutDirection="ltr"`.
- 纵向手势由外层 ScrollView 接管, 框架随之补发 `ACTION_CANCEL`; 那一次不是点击, 只做复位 — 否则用户滚动列表就会打开服务器, 会话随之切换.
- 卡片与操作区要求完全重叠且等高, 由 `SwipeRowLayout` 自行测量: 框架的现成布局都做不到.

## 界面归属与桥

壳不渲染工具栏: 页面自带头部, 壳只保留加载进度条与原生错误界面. 服务器切换与运行期信息都在壳内的「客户端设置」页 (入口见 [frontend.md](frontend.md)). 不提供单独的「退出登录」: 切换服务器保留既有会话, 失效由页面的 401 拦截送回服务器页, 单独的退出登录没有额外作用.

是否在壳内由 **UA 标记**判定 (`WebSettings.userAgentString` 追加的 `AmaneShell/<version>`, 每个请求都带), 不是 JS 桥: 桥只承载动作, 缺了它页面仍须列出入口, 以桥为判据会让入口在部分加载下整块消失. 桌面浏览器与 Docker 部署没有该标记, 入口不出现.

桥只承载 `ShellBridge.kt` 里声明的那几件事, 不接受参数; 页面侧的对应接口与消费方见 `web/src/lib/shell.ts`. **内核版本只取自 UA 里的 `Chrome/<版本>`**: 厂商包版本与 Chromium 版本没有对应关系, 用它比较前端下限会误报. 商店链接也只在提供方是 Google 发行的包 (`com.google.android.webview` / `com.android.chrome`) 时给出 — 厂商自带的 WebView 在 Play 上没有条目.

`addJavascriptInterface` 对 WebView 加载的文档全部可见, 因此站外链接必须交给系统浏览器. 弹窗与 `window.open` 的过渡 WebView 都可能落到站外文档 (SPA 里多处 `target="_blank"` 的外链), 因此它们不装桥, 并与主窗口共用同一条站内判据.

**启动看门狗**: 主文档加载成功不等于页面能用 — 页面在挂载前抛异常时 (WebView 低于前端下限即是这种情况), 页面自己的错误界面不会出现, 用户看到的只是一张空白页. 壳在 `onPageFinished` 后检查 `#root` 是否有子节点, 两次检查仍为空则显示原生错误界面, 这是该情况下唯一的重试与换服务器出口.

## 平台功能

- **主文档加载失败**: 显示原生错误页 (重试 / 换服务器), 不使用 WebView 自带的错误页 — 局域网服务器关机会经常遇到.
- **下拉刷新**: `SwipeRefreshLayout` 包住 WebView (WebView 自身没有该手势), 松开即 `reload()`, 加载结束或失败时收起指示器, 全屏播放期间禁用. 手势优先级低于页面内部滚动: `SwipeRefreshLayout` 只依据 WebView 自身的滚动位置, 而 SPA 的滚动多在内部容器里, 因此页面在 `touchstart` 实测触点处还有没有可向上滚的内容并经桥推送至壳, 由壳在手势起点决定是否接管 (`web/src/lib/pull-refresh.ts`).
- **文件选择**: WebView 自身不实现文件选择器, `<input type="file">` 必须由系统选择器接管; 取消与异常要回 `null`, 否则页面上的输入一直停在等待状态.
- **下载**: `Content-Disposition: attachment` 交给 `DownloadManager`; 它在独立进程, 不共享 cookie 罐, 因此显式写入 `Cookie` 请求头. 附件型 `window.open` 同样交给它, 真页面才另起 `PopupActivity`.
- **全屏视频**: `onShowCustomView` 的自定义视图 (`<video>` 与页面自己的 Fullscreen API 都走这条路), 同时收起系统栏并把方向锁到传感器横屏 (竖屏全屏会把画面挤在中间), 期间根容器的 inset 内边距归零; 返回键先请求页面退出全屏, 超时未退出则按原生方式收起; 全屏期间按 Home 键切换为画中画. 页面侧另有同一用途的方向锁, 供浏览器使用 (见 [frontend.md](frontend.md)).
- **浅色 / 深色**: 算法深色 (强深色) 必须在页面侧与壳侧都关掉. 页面自己按用户设置在深浅两套之间切换, 内核在系统深色时再叠一层会把浅色主题反转成另一种深色; 页面侧由 `web/src/global.css` 把壳内根元素 (`data-amane-shell`) 的 `color-scheme` 钉成 `dark`, 内核只对"用色方案为浅色"的页面叠加算法深色, 换掉这一项它就不再动手; 壳侧调 `setAlgorithmicDarkeningAllowed(false)`, 旧内核回退到 `setForceDark(FORCE_DARK_OFF)` — Android 13 以上且 targetSdk ≥ 33 时旧接口是空操作, 低于 Chromium 105 的 WebView 又不支持前者, 只靠任何一侧都会漏. `prefers-color-scheme` 由应用主题 (DayNight) 决定, 与这个开关无关, 「跟随系统」这一档不受影响.

窗口 inset 以原生 padding 施加在根容器上, 页面不使用 `env(safe-area-inset-*)`: WebView 的视口因此等于安全区, SPA 既有视口高度计算无需改动 (见 [frontend.md](frontend.md)). 壳没有自己的栏, 状态栏区域显示系统背景 (跟随 DayNight), 页面头部不会被状态栏压住.

`usesCleartextTraffic="true"` 是刻意的: 网络策略不能按用户在运行期填写的地址放开明文, 而自建服务默认走 `http://`. 非局域网部署应自备 HTTPS 反代.

## WebView 运行期

壳不携带浏览器内核, 页面运行在设备自带的 WebView 上, 版本由用户设备决定. 前端因此声明一个运行期下限 (按 Chromium 计, 数值见 `web/vite.config.ts` 的 `MODERN_TARGETS`, 该数值为实测最低内核), 兼容由 `@vitejs/plugin-legacy` 承担: 目标同时充当语法目标与 `@babel/preset-env` 的收集目标, polyfill 从 core-js 按 bundle 的实际使用自动挑选, 不需要维护方法清单; `renderLegacyChunks` 关闭 — 下限内核都支持 ESM.

下限只保证 JS 不崩, 样式仍按样式表自身的要求退化: `:has()` 与 `@container` (105)、`color-mix()` (111) 在更低内核上整条声明失效, 而 CSS 不能由 core-js 补; 视口单位另见 [frontend.md](frontend.md) 的 `--amane-vh`. 其余特性需要时逐个给出回退, 否则低于下限的设备须更新系统 WebView.

`build.target` 只降语法: 内建方法 (`Array.prototype.toSorted` 等) 不会被降级, 缺失时只能由 polyfill 提供 — 因此「降低构建目标」不能替代这里的配置.

## 打包与分发

**APP 版本独立于服务端与桌面端**: 唯一来源是 `androidapp/version.txt`, 构建脚本与 Gradle 都读取它, `versionName` 与 `versionCode` 由 semver 推导; `versionCode` 必须随版本单调递增 — Android 拒绝降级覆盖安装. 签名配置读取 `androidapp/keystore.properties` (不入库), 缺席时回退到 debug 包; CI 经仓库 secret 提供同一份密钥.

发版**完全独立**: 只有 `app-` 前缀的 tag 触发 APK 构建与 Release, 本体的 `v*` 不产出 APK — 本体发版通常不含 APP 变更, 每次都附一份 APK 会让下载的人以为 APP 也更新了. 这些 tag 解析不出版本, 本体的更新检查会跳过它们 (检查读取发布列表而不是 `/releases/latest`, 见 `src/amane/release.py`). 分发方式是 GitHub Release 上的 APK 侧载; 应用商店对本项目的媒体内容域不可行, 因此不引入 Play 相关的签名托管与更新机制.

PR 门禁由 `.github/workflows/ci.yaml` 的 `android` job 执行 `just android-check`; 它前面有一个轻量 job 先判断改动有没有碰到 APP, 没碰到就整块跳过 — 该 job 要装 JDK 与 Android SDK 再跑 Gradle, 而 APP 的改动很少. 这里用 job 级条件而不是工作流级 `paths`: 后者会让整个工作流不触发, 被设为必需的门禁检查会一直停在 pending.

最低支持 Android 10 (`minSdk 29`): 该版本起 `DownloadManager` 写公共目录不需要存储权限.

## 不支持

- **外部播放器与后台播放**: 播放流地址需要 cookie 才能获取, 交给外部播放器必须在壳内创建一个回环代理转发 `Range` 并附加凭据; 当前播放与画中画都在 WebView 内完成.
- **切换服务器保留页面状态**: 不同 origin 各自持有 cookie 与 localStorage, 换地址等于重新登录.
