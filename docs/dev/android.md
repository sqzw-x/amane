# Android 壳

> 本文记录 Android 端的进程边界、鉴权契约、平台功能取舍与打包方式.
> 桌面形态 (macOS 菜单栏 / Windows 托盘) 见 [desktop.md](desktop.md).

## 进程模型

Android 端是**远程客户端**, 不是桌面壳的同类: 服务端 (FastAPI + SQLite + 运行期加载的 Python 插件) 无法打包进 APK, 也没有必要 — 媒体库、Emby 集成与任务系统都在服务器上.

`androidapp/` 是一个 Kotlin WebView 壳, 只有一个 Activity 家族加一个设置页:

| 组件 | 职责 |
|------|------|
| `BrowserActivity` | WebView 宿主: 下载、自定义视图 (全屏视频)、画中画、窗口 inset、启动看门狗、JS 桥 |
| `ShellBridge` | 暴露给页面的 `window.amaneshell` |
| `MainActivity` | `BrowserActivity` 的 `singleTask` 入口 |
| `PopupActivity` | `window.open` 的目标窗口, 每个弹窗一个实例 |
| `SetupActivity` | 服务器列表 (卡片 + 左滑操作)、连接与编辑 |
| `ServerStore` / `ServerUrl.kt` | 条目持久化 (名字 / 地址 / token, 兼容只存地址的旧格式) 与地址归一化 (`ServerStoreTest` / `ServerUrlTest` 覆盖) |

## origin 契约

**WebView 顶层加载的必须是服务端自身 origin** (`scheme://host[:port]`), 不允许把 SPA 打包进 APK 再指向远端 API:

- 鉴权是 HttpOnly cookie (`middleware.py::TokenAuthMiddleware`), 服务端 CORS 显式关闭了凭据 (`api/app.py` 的 `allow_credentials=False`), 跨源带凭据的请求不可能通过.
- 子资源请求无法携带 `Authorization`: 图片代理 (`<img>`)、WebSocket 握手、SSE、播放的 Range 请求都只能依赖 cookie. 换成自带资源的本地 origin 后, 这几条链路全部返回 401.

由此推出: 前端不需要运行期 API 地址 (`VITE_API_URL` 仍是构建期变量), 也不需要为壳做条件分支.

## 登录

首次连接的 token 只在 `SetupActivity` 里用于向 `/api/system/desktop` 发一次 Bearer 请求; 中间件随之下发 `amane_token` cookie, 壳把响应头里的 `Set-Cookie` 手工写入 `CookieManager` (原生请求的响应头 WebView 不可见), 之后一切与浏览器一致. token 随服务器条目一起保存在应用私有存储里 (编辑页明文显示、可改: 同一份存储里本就是原文, 掩码不构成保护), 但登录态仍然只是 WebView 的 cookie 罐 — 打开页面不会自动重发 token. cookie 失效时由 SPA 自己的登录门接管.

服务端关闭鉴权 (`AMANE_TOKEN=off`) 或用户留空 token 时, 探活返回 401 也直接打开页面.

**壳内不显示页面的登录门**: 页面无从区分「服务端换了 token」与「地址填错」, 而 token 的正确入口是服务器页 (它校验 Bearer 并换取 cookie). 因此入口处发现未认证 (挂载探活失败, 或任何请求 401 触发失效事件) 时跳回 `SetupActivity` 一次 — 每次页面加载只跳一次, 用户从那里返回后落在登录门, 门内另给一条「服务器设置」入口.  

## 服务器列表

条目是卡片: 第一行名字 (留空时取主机名与端口), 第二行地址, 当前服务器另带「当前」标记. 点击卡片直接打开; 卡片左滑露出贴右边的「编辑」与「删除」, 同一时刻只展开一行, 展开时点卡片是收起而不是打开. 编辑弹窗改名字、地址与 token, 地址或 token 有改动时用新值在后台重新换取 cookie — 失败只提示, 编辑结果已经保存.

左滑在方向确定为横向之后才 `requestDisallowInterceptTouchEvent(true)`: 手指落下时就要返回 `true` 才能拿到后续事件, 那时禁用父级拦截会让列表再也滚不动. 纵向手势由外层 ScrollView 接管, 那时框架补发 `ACTION_CANCEL` — 那一次不是点击, 只做复位 (否则用户滚动列表就会打开服务器并切走会话). 左滑是物理方向, 不随 RTL 镜像: 壳只有中英两套文案, 而 `layout_gravity="end"` 在 RTL 下会贴到左边, 操作区再也滑不出来, 因此条目容器固定 `layoutDirection="ltr"`. 条目的容器是 `SwipeRowLayout`: 卡片与下层的操作区要完全重叠且等高, 框架的现成布局都做不到 — FrameLayout 只在多于一个 `match_parent` 子节点时才按自己测出的高度重测子节点, 而行高来自 `wrap_content` 的卡片; 在布局回调里改 `layoutParams` 又会在布局过程中再发起一次布局. 因此由它自己测量: 先量卡片, 再按卡片高度量操作区. 两个按钮紧邻、无间隔, 只有外侧两角是圆的.

## 界面归属与桥

壳不渲染工具栏: 页面自带头部, 壳只保留加载进度条与两个原生兜底界面. 服务器切换与运行期信息都在**「客户端设置」页** (`web/src/routes/client.tsx` + `components/shell/client-settings.tsx`), 入口是顶栏语言切换旁的手机图标 (只在 APP 内出现). 不提供单独的「退出登录」: 切换服务器保留既有会话, 登录态失效由页面的 401 拦截送回服务器页, 单独的退出登录没有额外作用.

是否在壳内由 **UA 标记**判定 (`WebSettings.userAgentString` 追加的 `AmaneShell/<version>`, 每个请求都带), 不是 JS 桥: 桥只承载动作, 缺了它页面仍列出入口并提示重装. 以桥作为判据会让入口在部分加载下整块消失 — 这正是"有时显示有时不显示"的来源. 桌面浏览器与 Docker 部署没有该标记, 入口不出现.

| 桥方法 | 实现 |
|---------|------|
| `switchServer()` | 打开 `SetupActivity`, 与错误界面的「切换服务器」同一入口 |
| `webViewPackage()` | `WebViewCompat.getCurrentWebViewPackage` 的包名与厂商版本, 取不到时为空串 |

**内核版本只认 UA 里的 `Chrome/<版本>`**: 厂商包版本与 Chromium 版本没有对应关系, 拿它比较前端下限会误报. 商店链接也只在提供方是 Google 发行的包 (`com.google.android.webview` / `com.android.chrome`) 时给出 — 厂商自带的 WebView 在 Play 上没有条目.

`addJavascriptInterface` 对 WebView 加载的文档全部可见, 因此站外链接必须交给系统浏览器, 桥也只做上表这几件事、不接受参数. 弹窗与 `window.open` 的过渡 WebView 都可能落到站外文档 (SPA 里多处 `target="_blank"` 的外链), 因此它们不装桥, 并与主窗口共用同一条站内判据.

**启动看门狗**: 主文档加载成功不等于页面能用. 页面在挂载前抛异常时 (例如 WebView 低于前端下限), 页面自己的错误界面不会出现, 用户看到的只是一张空白页. 壳在 `onPageFinished` 后检查 `#root` 是否有子节点, 两次检查仍为空则显示原生错误界面 — 这是这种情况下唯一的重试与换服务器出口.

## 平台功能

| 场景 | 处理方式 |
|------|---------|
| 主文档加载失败 | 原生错误页 (重试 / 换服务器), 不显示 WebView 自带的错误页 — 局域网服务器关机会经常遇到 |
| 页面脚本未挂载 | 启动看门狗给出同一个原生错误页, 见下 |
| 下拉刷新 | `SwipeRefreshLayout` 包住 WebView (WebView 自身没有该手势), 松开即 `reload()`; 加载结束或失败时收起指示器, 全屏播放期间禁用. 手势优先级低于页面内部滚动: `SwipeRefreshLayout` 只看得到 WebView 自身的滚动位置, 而 SPA 的滚动多在内部容器里 (弹窗正文、侧栏、列表, 播放器还自己消费纵向拖动), 因此页面在 `touchstart` 实测"触点处还有没有可向上滚的内容"并经桥的 `setPageScrollableUp` 推给壳, 由它在手势起点决定是否接管 (`web/src/lib/pull-refresh.ts`); 页面加载开始与原生错误页显示时该状态复位 |
| 文件选择 (`<input type="file">`) | WebView 自身不实现文件选择器, 必须由 `onShowFileChooser` 交给系统选择器 (`FileChooserParams.createIntent()`, 带页面的类型过滤与多选开关), 结果经 `ActivityResultContracts` 回给同一份回调; 取消与异常回 `null`, 否则页面上的输入一直停在等待状态 |
| 下载 (`Content-Disposition: attachment`) | `DownloadManager`; 它在独立进程, 不共享 cookie 罐, 因此显式写入 `Cookie` 请求头 |
| `window.open` | 附件交给下载监听器 (任务记录导出即此类), 真页面才另起 `PopupActivity` |
| 全屏视频 | `onShowCustomView` 的自定义视图 (`<video>` 与页面自己的 Fullscreen API 都走这条路), 同时收起状态栏与导航栏 (`WindowInsetsControllerCompat`, 划出时临时显示) 并把方向锁到传感器横屏 (竖屏全屏会把画面挤在中间), 页面侧另有同一用途的方向锁用于浏览器 (见 docs/dev/frontend.md); 期间根容器的 inset 内边距归零, 否则画面被让出的状态栏高度顶下去; 返回键先请求页面退出全屏, 超时未退出则按原生方式收起, 退出时恢复系统栏并把方向交还系统 |
| 按 Home 键 | 全屏视频转画中画 (`PictureInPictureParams` 的宽高取自自定义视图) |
| 站外链接 | 交给系统浏览器, 不留在 WebView 内 |
| 浅色/深色 | 算法深色 (强深色) 必须关掉: 页面自己按用户设置在深浅两套之间切换, 内核在系统深色时再叠一层会把浅色主题反转成另一种深色. `prefers-color-scheme` 由应用主题 (DayNight) 决定, 与这个开关无关, 因此「跟随系统」这一档不受影响 |

窗口 inset 以原生 padding 施加在根容器上, 页面不使用 `env(safe-area-inset-*)`: WebView 的视口因此等于安全区, SPA 既有的 `100dvh` 高度计算 (`web/src/components/layout/app-shell-metrics.ts`) 无需改动. 壳没有自己的栏, 状态栏区域显示系统背景 (跟随 DayNight), 页面头部不会被状态栏压住.

`usesCleartextTraffic="true"` 是刻意的: 网络策略不能按用户在运行时填写的地址放开明文, 而自建服务默认是 `http://<host>:8000`. 非局域网部署应自备 HTTPS 反代.

## WebView 运行期

壳不携带浏览器内核, 页面运行在设备自带的 WebView 上, 版本由用户设备决定. 前端因此声明一个运行期下限 (Chromium 99 / Safari 16.4, 见 `web/vite.config.ts` 的 `MODERN_TARGETS`; 99 是实测最低内核), 兼容由 `@vitejs/plugin-legacy` 承担: `modernTargets` 同时充当语法目标与 `@babel/preset-env` 的收集目标, polyfill 从 core-js 按 bundle 的实际使用自动挑选, 不需要维护方法清单. `renderLegacyChunks` 关闭 — 下限内核都支持 ESM, 不需要 SystemJS 包.

下限只保证 JS 不崩, 样式仍按样式表自身的要求退化: `dvh` (108), `:has()` 与 `@container` (105), `color-mix()` (111) 在更低内核上整体失效, 而 CSS 不能由 core-js 补. 视口高度这一项已用 `--amane-vh` 兜住 (见 [frontend.md](frontend.md)); 其余特性需要时逐个给出回退, 否则低于下限的设备须更新系统 WebView.

`build.target` 只降语法: 内建方法 (`Array.prototype.toSorted` 等) 不会被降级, 缺失时只能由 polyfill 提供 — 因此「降低构建目标」不能替代这里的配置.

算法深色要在两侧都关掉. 页面侧: 壳内的根元素带 `data-amane-shell` (`web/src/main.tsx` 按 UA 标记盖上), `global.css` 在 `prefers-color-scheme: dark` 下把它的 `color-scheme` 钉成 `dark` — 内核只对"用色方案为浅色"的页面叠加算法深色, 换掉这一项它就不再动手, 而页面自身仍按用户设置渲染. 壳侧: `setAlgorithmicDarkeningAllowed(false)`, 旧内核退回 `setForceDark(FORCE_DARK_OFF)`. 两处都要有 — Android 13 以上且 targetSdk ≥ 33 时旧的 `setForceDark` 是空操作, 而低于 Chromium 105 的 WebView 不支持前者, 只靠任何一侧都会漏.

## 打包与分发

```
just android-app                     # → dist/Amane-app-<version>.apk
```

**APP 版本独立于服务端与桌面端**: 唯一来源是 `androidapp/version.txt`, 构建脚本与 Gradle 都读它, `versionName` 与 `versionCode` 由它推导 (三段各占两位十进制, 必须单调递增 — Android 拒绝降级覆盖安装). 起始版本取 `1.0.0`, 它的 `versionCode` 10000 高于共用版本号时期最后发布的 `0.15.0` (1500), 因此可以直接覆盖安装. `androidapp/keystore.properties` 存在时构建 `assembleRelease` (签名密钥相对 `androidapp/`), 否则退回 debug 包.

CI 见 `.github/workflows/android-app.yml`: 提供 `ANDROID_KEYSTORE_BASE64` / `ANDROID_KEYSTORE_PASSWORD` / `ANDROID_KEY_ALIAS` / `ANDROID_KEY_PASSWORD` 四个 secret 才会产出正式签名包, 否则产物是 debug 包. 发版**完全独立**: 只有 `app-` 前缀的 tag 触发这里的构建与 Release (发布说明按提交生成), `v*` 不再产出 APK — 本体连续发几个版本时 APP 往往没变过, 每次都附一份 APK 会让下载的人以为 APP 也更新了. 发版步骤: 改 `androidapp/version.txt` → 提交 → `git tag app-<version>` → push. 这些 tag 解析不出版本, 本体的更新检查会跳过它们 (检查读发布列表而不是 `/releases/latest`, 见 `src/amane/release.py`). 分发方式是 GitHub Release 上的 APK 侧载; 应用商店对本项目的媒体内容域不可行, 因此不引入 Play 相关的签名托管与更新机制.

PR 门禁由 `.github/workflows/ci.yaml` 的 `android` job 执行 `just android-check` (编译 debug 包 + 单元测试); 它前面有一个轻量 job 先判断这次改动有没有碰到 APP (`androidapp/**`、构建脚本、Justfile 与两个工作流), 没碰到就整块跳过 — 该 job 要装 JDK 与 Android SDK 再跑 Gradle, 一次一两分钟, 而 APP 的改动很少. 这里用 job 级条件而不是工作流级 `paths`: 后者会让整个工作流不触发, 被设为必需的门禁检查会一直停在 pending.

最低支持 Android 10 (`minSdk 29`): 该版本起 `DownloadManager` 写公共目录不需要存储权限, 边缘到边缘与 WebView 行为也是当前设计所依据的基线.

## 不支持

- **手机端运行服务端**: 打包形式 (PyInstaller onedir) 与运行期加载的 Python 插件都不成立.
- **外部播放器与后台播放**: 播放流地址需要 cookie 才能取, 交给外部播放器必须在壳内起一个回环代理转发 `Range` 并附加凭据; 当前播放与画中画都在 WebView 内完成.
- **切换服务器保留页面状态**: 不同 origin 各自持有 cookie 与 localStorage, 换地址等于重新登录.
