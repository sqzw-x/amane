package com.github.sqzwx.amane.android

import android.annotation.SuppressLint
import android.app.DownloadManager
import android.app.PictureInPictureParams
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.pm.ActivityInfo
import android.graphics.Bitmap
import android.net.Uri
import android.os.Bundle
import android.os.Environment
import android.os.Message
import android.util.Rational
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.webkit.CookieManager
import android.webkit.URLUtil
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.ValueCallback
import android.webkit.WebViewClient
import android.widget.Toast
import androidx.activity.addCallback
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.graphics.Insets
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.core.view.updatePadding
import androidx.webkit.WebSettingsCompat
import androidx.webkit.WebViewFeature
import com.github.sqzwx.amane.android.databinding.ActivityBrowserBinding
import java.util.Locale

/**
 * 浏览器窗口: 壳的主体是 WebView, 页面是服务端同源提供的 SPA.
 *
 * 顶层 origin 必须保持在服务端本身, 理由与后果见 docs/dev/android.md 的 origin 契约.
 */
open class BrowserActivity : AppCompatActivity() {

    private lateinit var binding: ActivityBrowserBinding

    /** 服务端 origin, 用于判断站内与站外链接. */
    private var origin: String = ""

    private var customView: View? = null
    private var customViewCallback: WebChromeClient.CustomViewCallback? = null

    /**
     * `window.open` 的过渡 WebView: 它只承接一次导航, 内容由 [PopupActivity] 加载.
     * 页面不主动关闭窗口时 `onCloseWindow` 不会触发, 因此这里负责销毁它.
     */
    private var pendingPopup: WebView? = null

    /** 正在等待系统选择器的 `<input type="file">` 回调; 页面并发发起多次时只保留最后一次. */
    private var pendingFileChooser: ValueCallback<Array<Uri>>? = null

    /**
     * `<input type="file">` 的结果回调: 结果按 `parseResult` 转回 URI 数组, 取消或失败回 null —
     * 不回的话页面上的文件输入会一直停在等待状态.
     */
    private val fileChooserLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) { result ->
        val callback = pendingFileChooser ?: return@registerForActivityResult
        pendingFileChooser = null
        callback.onReceiveValue(
            WebChromeClient.FileChooserParams.parseResult(result.resultCode, result.data),
        )
    }

    /** 页面每次开始加载都自增, 用来作废上一次的启动检查. */
    private var bootCheckGeneration = 0

    /**
     * 页面报告的"触点处还有可以向上滚的内容", 由 SPA 在触摸开始时推送
     * (见 [ShellBridge.setPageScrollableUp]). JavaBridge 线程写、UI 线程读, 因此是 `@Volatile`.
     */
    @Volatile
    private var pageScrollableUp = false

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        binding = ActivityBrowserBinding.inflate(layoutInflater)
        setContentView(binding.root)
        applyWindowInsets()

        // 弹窗由 EXTRA_URL 指定具体页面; 入口窗口没有 EXTRA_URL 时用当前选中的服务器.
        val target = intent.getStringExtra(EXTRA_URL) ?: ServerStore(this).active()?.plus("/")
        val normalized = target?.let(::normalizeServerUrl)
        if (target == null || normalized == null) {
            startActivity(Intent(this, SetupActivity::class.java))
            finish()
            return
        }
        origin = normalized

        configureWebView(binding.webView)
        // 下拉刷新等同于重新加载页面; 指示器在页面加载结束时收起.
        binding.swipeRefresh.setOnRefreshListener { binding.webView.reload() }
        // 内部滚动优先: SwipeRefreshLayout 只读 WebView 自身的滚动位置, 而 SPA 的滚动都在内部容器里
        // (那里恒为 0), 因此这里再向页面查询一次 — 触点处还能向上滚时不接管手势.
        binding.swipeRefresh.setOnChildScrollUpCallback { _, _ ->
            pageScrollableUp || binding.webView.canScrollVertically(-1)
        }
        binding.errorRetry.setOnClickListener { binding.webView.reload() }
        binding.errorSwitch.setOnClickListener { openSetup() }
        onBackPressedDispatcher.addCallback(this) { handleBack() }

        showProgress()
        // 重建 (深色切换 / 字体缩放) 时回到原来的页面, 而不是回到根路由.
        binding.webView.loadUrl(savedInstanceState?.getString(STATE_URL) ?: target)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        // singleTask: 从设置页切换服务器后回到本实例, 必须重新加载, 不允许沿用旧 origin 的会话.
        val url = intent.getStringExtra(EXTRA_URL) ?: return
        val next = normalizeServerUrl(url) ?: return
        origin = next
        showProgress()
        binding.webView.loadUrl(url)
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        binding.webView.url?.let { outState.putString(STATE_URL, it) }
    }

    override fun onDestroy() {
        pendingFileChooser?.onReceiveValue(null)
        pendingFileChooser = null
        pendingPopup?.destroy()
        pendingPopup = null
        binding.webContainer.removeView(binding.webView)
        binding.webView.destroy()
        super.onDestroy()
    }

    // region 全屏视频与画中画

    /**
     * 页面请求的原生处理: 全屏画面经自定义视图铺满窗口 (`<video>` 与页面的 Fullscreen API 都是这条
     * 路径), 系统栏同时收起.
     */
    private inner class ShellChromeClient : WebChromeClient() {
        override fun onProgressChanged(view: WebView, newProgress: Int) {
            if (binding.progress.visibility != View.VISIBLE) return
            binding.progress.progress = newProgress
        }

        override fun onShowCustomView(view: View, callback: CustomViewCallback) {
            if (customView != null) {
                callback.onCustomViewHidden()
                return
            }
            customView = view
            customViewCallback = callback
            binding.customViewContainer.addView(
                view,
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT,
            )
            binding.customViewContainer.visibility = View.VISIBLE
            binding.progress.visibility = View.GONE
            binding.webView.visibility = View.INVISIBLE
            // 全屏播放锁定传感器横屏: 竖屏全屏会把画面挤在中间.
            requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE
            binding.swipeRefresh.isEnabled = false
            window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
            setSystemBarsVisible(false)
        }

        override fun onHideCustomView() {
            hideCustomView()
        }

        /**
         * `window.open`: 附件导出这类请求由下载监听器处理而不是渲染, 因此这里交回一个同配置的 WebView
         * 让导航继续, 真正落到页面时才另开窗口.
         */
        override fun onCreateWindow(
            view: WebView,
            isDialog: Boolean,
            isUserGesture: Boolean,
            resultMsg: Message,
        ): Boolean {
            val popup = WebView(this@BrowserActivity)
            // 过渡 WebView 与弹窗都可能落到站外文档 (SPA 里多处 `target="_blank"` 的外链), 因此都不装桥.
            configureWebView(popup, withBridge = false)
            popup.webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                    if (isServerUrl(request.url)) return false
                    openExternally(request.url)
                    dropPopup(view)
                    return true
                }

                /**
                 * 站内页面开弹窗, 站外交给系统浏览器 — 两条回调哪条先到都给出结果.
                 * 只认 `shouldOverrideUrlLoading` 时, 若新窗口的首次导航不经过它, 外链会落在这只不可见的
                 * 过渡 WebView 上, 界面没有任何反应.
                 */
                override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) {
                    val target = url?.let(Uri::parse) ?: return
                    if (isServerUrl(target)) {
                        startActivity(
                            Intent(this@BrowserActivity, PopupActivity::class.java).putExtra(EXTRA_URL, url),
                        )
                    } else {
                        openExternally(target)
                    }
                    dropPopup(view)
                }
            }
            pendingPopup?.destroy()
            pendingPopup = popup
            (resultMsg.obj as WebView.WebViewTransport).webView = popup
            resultMsg.sendToTarget()
            return true
        }

        override fun onCloseWindow(window: WebView) {
            dropPopup(window)
        }

        /**
         * `<input type="file">`: WebView 自身不实现文件选择器, 必须由壳交给系统.
         * 用 `FileChooserParams.createIntent()` 而不是自行拼 Intent — 它带上了页面声明的类型过滤与多选开关.
         */
        override fun onShowFileChooser(
            view: WebView,
            filePathCallback: ValueCallback<Array<Uri>>,
            fileChooserParams: FileChooserParams,
        ): Boolean {
            pendingFileChooser?.onReceiveValue(null)
            pendingFileChooser = filePathCallback
            val intent = fileChooserParams.createIntent()
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            return try {
                fileChooserLauncher.launch(intent)
                true
            } catch (_: ActivityNotFoundException) {
                pendingFileChooser = null
                filePathCallback.onReceiveValue(null)
                Toast.makeText(this@BrowserActivity, R.string.error_no_picker, Toast.LENGTH_SHORT).show()
                false
            }
        }
    }

    private fun hideCustomView() {
        val view = customView ?: return
        binding.customViewContainer.removeView(view)
        binding.customViewContainer.visibility = View.GONE
        binding.webView.visibility = View.VISIBLE
        // 交还方向控制权给系统 (跟随自动旋转与用户设置).
        requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED
        binding.swipeRefresh.isEnabled = true
        window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        setSystemBarsVisible(true)
        customView = null
        customViewCallback?.onCustomViewHidden()
        customViewCallback = null
    }

    /**
     * 全屏播放期间收起状态栏与导航栏, 退出时交还.
     *
     * WebView 只负责交出画面, 系统栏归壳管理: 不收起时状态栏一直压在画面上. 隐藏后窗口 inset 归零,
     * 根容器的内边距必须经 `requestApplyInsets` 重算一次, 否则内容仍按让出的高度偏移.
     */
    private fun setSystemBarsVisible(visible: Boolean) {
        val controller = WindowInsetsControllerCompat(window, binding.root)
        if (visible) {
            controller.show(WindowInsetsCompat.Type.systemBars())
        } else {
            // 划出时临时显示, 不永久顶掉用户的手势导航.
            controller.systemBarsBehavior =
                WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            controller.hide(WindowInsetsCompat.Type.systemBars())
        }
        ViewCompat.requestApplyInsets(binding.root)
    }

    /** 全屏视频时按 Home 转画中画: 画中画让 WebView 的合成器继续出帧, 直接退到后台会停掉画面. */
    override fun onUserLeaveHint() {
        super.onUserLeaveHint()
        val view = customView ?: return
        val width = view.width.takeIf { it > 0 } ?: DEFAULT_ASPECT.first
        val height = view.height.takeIf { it > 0 } ?: DEFAULT_ASPECT.second
        val params = PictureInPictureParams.Builder()
            .setAspectRatio(Rational(width, height))
            .build()
        runCatching { enterPictureInPictureMode(params) }
    }

    // endregion

    // region WebView

    /**
     * 壳内 WebView 的统一配置.
     *
     * `withBridge` 只在承载服务端自身页面的窗口上为真, 理由见 [ShellBridge].
     */
    @SuppressLint("SetJavaScriptEnabled")
    private fun configureWebView(web: WebView, withBridge: Boolean = true) {
        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            javaScriptCanOpenWindowsAutomatically = true
            // 与浏览器一致: 媒体播放不要求用户手势.
            mediaPlaybackRequiresUserGesture = false
            setSupportZoom(false)
            userAgentString = "$userAgentString AmaneShell/${BuildConfig.VERSION_NAME}"
        }
        // 算法深色必须关掉: 页面自己按用户设置在深浅两套之间切换, 内核在系统深色时再叠一层会把浅色主题
        // 反转成另一种深色. `prefers-color-scheme` 由应用主题 (DayNight) 决定, 与该开关无关, 因此关闭它
        // 不影响"跟随系统"这一选项.
        when {
            WebViewFeature.isFeatureSupported(WebViewFeature.ALGORITHMIC_DARKENING) ->
                WebSettingsCompat.setAlgorithmicDarkeningAllowed(web.settings, false)

            WebViewFeature.isFeatureSupported(WebViewFeature.FORCE_DARK) -> {
                @Suppress("DEPRECATION")
                WebSettingsCompat.setForceDark(web.settings, WebSettingsCompat.FORCE_DARK_OFF)
            }
        }
        if (BuildConfig.DEBUG) WebView.setWebContentsDebuggingEnabled(true)
        // 页面经 window.amaneshell 发起服务器切换; 原生不提供其它界面入口.
        if (withBridge) web.addJavascriptInterface(ShellBridge(this), BRIDGE_NAME)
        web.webChromeClient = ShellChromeClient()
        web.webViewClient = ShellWebViewClient()
        web.setDownloadListener { url, userAgent, contentDisposition, mimeType, _ ->
            enqueueDownload(url, userAgent, contentDisposition, mimeType)
        }
    }

    /**
     * 站内判据: scheme 与 authority 都与当前服务器一致.
     * 主窗口与弹窗共用这一条边界 — 弹窗没有地址栏, 站外文档不允许在弹窗内打开.
     */
    private fun isServerUrl(url: Uri): Boolean =
        url.scheme in HTTP_SCHEMES && url.authority == Uri.parse(origin).authority

    /** 过渡 WebView 只服务于一次 `window.open`: 内容交给 [PopupActivity] 之后即可销毁. */
    private fun dropPopup(web: WebView) {
        if (pendingPopup === web) pendingPopup = null
        web.stopLoading()
        web.post { web.destroy() }
    }

    private inner class ShellWebViewClient : WebViewClient() {
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            val url = request.url
            if (isServerUrl(url)) return false
            openExternally(url)
            return true
        }

        override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) {
            bootCheckGeneration += 1
            // 新文档还没报过自己的滚动状态, 先按"没有可向上滚的内容"计算.
            pageScrollableUp = false
            hideError()
            showProgress()
        }

        override fun onPageFinished(view: WebView, url: String?) {
            binding.progress.visibility = View.GONE
            binding.swipeRefresh.isRefreshing = false
            scheduleBootCheck()
        }

        override fun onReceivedError(
            view: WebView,
            request: WebResourceRequest,
            error: WebResourceError,
        ) {
            if (!request.isForMainFrame) return
            binding.swipeRefresh.isRefreshing = false
            showError(getString(R.string.error_unreachable, error.description?.toString().orEmpty()))
        }

        override fun onReceivedHttpError(
            view: WebView,
            request: WebResourceRequest,
            errorResponse: WebResourceResponse,
        ) {
            // 站内子资源失败由页面自己呈现; 只有主文档 5xx 才覆盖页面.
            if (!request.isForMainFrame || errorResponse.statusCode < 500) return
            binding.swipeRefresh.isRefreshing = false
            showError(getString(R.string.error_status, errorResponse.statusCode))
        }
    }

    // endregion

    // region 界面状态

    private fun showProgress() {
        binding.progress.progress = 0
        binding.progress.visibility = View.VISIBLE
    }

    private fun showError(message: String) {
        binding.progress.visibility = View.GONE
        binding.errorMessage.text = message
        binding.errorView.visibility = View.VISIBLE
        binding.webView.visibility = View.INVISIBLE
        // 兜底界面是原生的, 页面留下的滚动状态在这里没有意义, 否则下拉刷新会被一直拦截.
        pageScrollableUp = false
    }

    private fun hideError() {
        if (binding.errorView.visibility != View.VISIBLE) return
        binding.errorView.visibility = View.GONE
        binding.webView.visibility = View.VISIBLE
    }

    /** 打开壳的服务器设置页; 同时是错误界面的「切换服务器」与桥 `switchServer()` 的实现. */
    internal fun openSetup() {
        startActivity(Intent(this, SetupActivity::class.java))
    }

    /** 由 [ShellBridge] 从 JavaBridge 线程调用; 字段是 `@Volatile`, 不必切回 UI 线程. */
    internal fun setPageScrollableUp(scrollableUp: Boolean) {
        pageScrollableUp = scrollableUp
    }

    private fun handleBack() {
        when {
            customView != null -> exitFullscreen()
            binding.webView.canGoBack() -> {
                binding.webView.goBack()
            }
            else -> finish()
        }
    }

    /**
     * 退出全屏先请求页面自行退出 (只有页面知道当前在播放的元素), 超过约定时间仍未退出则按原生方式
     * 收起自定义视图, 否则画面会停在全屏且没有退出方式.
     */
    private fun exitFullscreen() {
        binding.webView.evaluateJavascript(EXIT_FULLSCREEN_JS, null)
        binding.customViewContainer.postDelayed(
            { if (customView != null) hideCustomView() },
            FULLSCREEN_EXIT_GRACE_MS,
        )
    }

    // endregion

    // region 启动看门狗

    /**
     * 主文档加载成功不等于页面能用: 脚本在挂载前抛异常时页面停在空白上, 页面自己的错误界面也不会出现,
     * 只有原生层能给出重试与切换服务器的出口.
     */
    private fun scheduleBootCheck() {
        if (binding.errorView.visibility == View.VISIBLE) return
        bootCheckGeneration += 1
        val generation = bootCheckGeneration
        binding.root.postDelayed(
            { if (generation == bootCheckGeneration) checkBoot(generation, BOOT_CHECK_ATTEMPTS) },
            BOOT_CHECK_DELAY_MS,
        )
    }

    private fun checkBoot(generation: Int, attemptsLeft: Int) {
        binding.webView.evaluateJavascript(BOOT_PROBE_JS) { result ->
            if (generation != bootCheckGeneration) return@evaluateJavascript
            if (result?.trim('"') == "true") return@evaluateJavascript
            if (attemptsLeft > 1) {
                binding.root.postDelayed(
                    {
                        if (generation == bootCheckGeneration) {
                            checkBoot(generation, attemptsLeft - 1)
                        }
                    },
                    BOOT_CHECK_RETRY_MS,
                )
                return@evaluateJavascript
            }
            showError(getString(R.string.error_boot_failed))
        }
    }

    // endregion

    private fun openExternally(url: Uri) {
        try {
            startActivity(Intent(Intent.ACTION_VIEW, url))
        } catch (_: ActivityNotFoundException) {
            Toast.makeText(this, getString(R.string.error_no_app, url.toString()), Toast.LENGTH_SHORT)
                .show()
        }
    }

    /**
     * 下载交由系统的 DownloadManager 执行: 它在独立进程中运行, 不共享 WebView 的 cookie 罐,
     * 因此必须把当前 cookie 显式写入请求头, 否则 `/api` 下的导出会返回 401.
     */
    private fun enqueueDownload(
        url: String,
        userAgent: String?,
        contentDisposition: String?,
        mimeType: String?,
    ) {
        val fileName = URLUtil.guessFileName(url, contentDisposition, mimeType)
        val request = DownloadManager.Request(Uri.parse(url))
            .setTitle(fileName)
            .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
            .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, fileName)
        CookieManager.getInstance().getCookie(url)?.let { request.addRequestHeader("Cookie", it) }
        userAgent?.let { request.addRequestHeader("User-Agent", it) }
        mimeType?.let { request.setMimeType(it) }
        try {
            val manager = getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
            manager.enqueue(request)
            Toast.makeText(this, R.string.download_started, Toast.LENGTH_SHORT).show()
        } catch (e: IllegalArgumentException) {
            Toast.makeText(this, getString(R.string.download_failed, e.message.orEmpty()), Toast.LENGTH_LONG)
                .show()
        }
    }

    /**
     * 窗口 inset 以原生 padding 施加在根容器上, 页面不使用 `env(safe-area-inset-*)`: WebView 的视口
     * 因此等于安全区, SPA 既有的高度计算 (`app-shell-metrics.ts`) 无需改动, 前端不必为壳维护断点;
     * 状态栏区域显示系统背景, 页面头部不会与状态栏重叠.
     */
    private fun applyWindowInsets() {
        ViewCompat.setOnApplyWindowInsetsListener(binding.root) { _, insets ->
            // 全屏播放时系统栏已收起, 内边距必须归零.
            val bars = if (customView == null) {
                insets.getInsets(
                    WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.ime(),
                )
            } else {
                Insets.NONE
            }
            binding.root.updatePadding(
                top = bars.top,
                bottom = bars.bottom,
                left = bars.left,
                right = bars.right,
            )
            insets
        }
    }

    companion object {
        /** 弹窗与入口共用: 带上具体页面地址启动本类. */
        const val EXTRA_URL = "com.github.sqzwx.amane.android.extra.URL"

        private const val STATE_URL = "amane:url"
        private const val BRIDGE_NAME = "amaneshell"
        private const val FULLSCREEN_EXIT_GRACE_MS = 400L
        private const val BOOT_CHECK_DELAY_MS = 1_500L
        private const val BOOT_CHECK_RETRY_MS = 4_000L
        private const val BOOT_CHECK_ATTEMPTS = 2
        private val HTTP_SCHEMES = listOf("http", "https")
        private val DEFAULT_ASPECT = 16 to 9

        /** 页面挂载完成的判据: `#root` 有子节点. */
        private const val BOOT_PROBE_JS =
            "(function(){var r=document.getElementById('root');return !!(r&&r.childElementCount>0);})()"

        private val EXIT_FULLSCREEN_JS = String.format(
            Locale.ROOT,
            """
            (function () {
              var video = document.querySelector('video');
              if (document.fullscreenElement && document.exitFullscreen) document.exitFullscreen();
              else if (document.webkitFullscreenElement && document.webkitExitFullscreen) document.webkitExitFullscreen();
              else if (video && video.webkitDisplayingFullscreen && video.webkitExitFullscreen) video.webkitExitFullscreen();
            })();
            """.trimIndent(),
        )
    }
}
