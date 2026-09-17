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
import android.webkit.WebViewClient
import android.widget.Toast
import androidx.activity.addCallback
import androidx.activity.enableEdgeToEdge
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import androidx.webkit.WebSettingsCompat
import androidx.webkit.WebViewFeature
import com.github.sqzwx.amane.android.databinding.ActivityBrowserBinding
import java.util.Locale

/**
 * 浏览器窗口: 壳的主体就是 WebView, 页面是服务端同源提供的 SPA.
 *
 * 顶层 origin 必须是服务端本身 — 鉴权是 HttpOnly cookie, 图片代理 (`<img>`)、WebSocket 握手、
 * SSE 与播放的 Range 请求都无法自定义 header, 换成自带资源的本地 origin 后这些请求会全部返回 401.
 * 见 docs/dev/android.md.
 */
open class BrowserActivity : AppCompatActivity() {

    private lateinit var binding: ActivityBrowserBinding

    /** 服务端 origin (`scheme://host[:port]`), 用来判断站内/站外链接. */
    private var origin: String = ""

    private var customView: View? = null
    private var customViewCallback: WebChromeClient.CustomViewCallback? = null

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
        // 下拉刷新 = 浏览器里的重新加载; 指示器由页面加载结束时收起.
        binding.swipeRefresh.setOnRefreshListener { binding.webView.reload() }
        // 内部滚动优先: SwipeRefreshLayout 只看 WebView 自身的滚动位置, 而 SPA 的滚动都在内部容器里 (那里恒为 0),
        // 于是内层列表与弹窗里的下滑会被当成下拉刷新. 这里再问一句页面 — 触点处还能向上滚时不接管手势.
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
        binding.webContainer.removeView(binding.webView)
        binding.webView.destroy()
        super.onDestroy()
    }

    // region 全屏视频与画中画

    /**
     * 页面请求全屏时, WebView 把画面交给 chrome client 的自定义视图: 由外壳铺满窗口,
     * WebView 自身没有可用的 Fullscreen API.
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
            // 全屏播放转横屏: 竖屏全屏会让画面挤在中间一条, 主流播放器也是这个行为.
            requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE
            binding.swipeRefresh.isEnabled = false
            window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        }

        override fun onHideCustomView() {
            hideCustomView()
        }

        /**
         * `window.open`: 只有 SPA 的任务记录导出会用到它 (`task-detail-panel.tsx` 里的 `_blank`),
         * 那条请求带 `Content-Disposition: attachment`, 由 DownloadListener 处理而不是渲染.
         * 因此这里交回一个同配置的 WebView 让请求继续, 真正落到页面时才另开窗口.
         */
        override fun onCreateWindow(
            view: WebView,
            isDialog: Boolean,
            isUserGesture: Boolean,
            resultMsg: Message,
        ): Boolean {
            val popup = WebView(this@BrowserActivity)
            configureWebView(popup)
            popup.webViewClient = object : WebViewClient() {
                override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) {
                    if (url == null) return
                    startActivity(
                        Intent(this@BrowserActivity, PopupActivity::class.java).putExtra(EXTRA_URL, url),
                    )
                }
            }
            (resultMsg.obj as WebView.WebViewTransport).webView = popup
            resultMsg.sendToTarget()
            return true
        }

        override fun onCloseWindow(window: WebView) {
            window.destroy()
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
        customView = null
        customViewCallback?.onCustomViewHidden()
        customViewCallback = null
    }

    /** 全屏视频时按 Home 转画中画: 后者让 WebView 的合成器继续出帧, 直接退到后台会停掉画面. */
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

    @SuppressLint("SetJavaScriptEnabled")
    private fun configureWebView(web: WebView) {
        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            javaScriptCanOpenWindowsAutomatically = true
            // 与浏览器一致: 切到某个来源后由页面自己起播, 不要求再点一次
            mediaPlaybackRequiresUserGesture = false
            setSupportZoom(false)
            userAgentString = "$userAgentString AmaneShell/${BuildConfig.VERSION_NAME}"
        }
        if (WebViewFeature.isFeatureSupported(WebViewFeature.ALGORITHMIC_DARKENING)) {
            // 让 prefers-color-scheme 跟随系统深色, SPA 的 Mantine 主题据此切换
            WebSettingsCompat.setAlgorithmicDarkeningAllowed(web.settings, true)
        }
        if (BuildConfig.DEBUG) WebView.setWebContentsDebuggingEnabled(true)
        // 页面经 window.amaneshell 发起服务器切换与登录清除; 其余原生入口已全部移入 SPA 界面.
        web.addJavascriptInterface(ShellBridge(this), BRIDGE_NAME)
        web.webChromeClient = ShellChromeClient()
        web.webViewClient = ShellWebViewClient()
        web.setDownloadListener { url, userAgent, contentDisposition, mimeType, _ ->
            enqueueDownload(url, userAgent, contentDisposition, mimeType)
        }
    }

    private inner class ShellWebViewClient : WebViewClient() {
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            val url = request.url
            if (url.scheme in HTTP_SCHEMES && url.authority == Uri.parse(origin).authority) return false
            openExternally(url)
            return true
        }

        override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) {
            bootCheckGeneration += 1
            // 新文档还没报过自己的滚动状态, 先按"没有可向上滚的内容"算.
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
            // 站内子资源失败由页面自己呈现; 只有主文档 5xx 才覆盖页面
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
        // 兜底界面是原生的, 页面留下的滚动状态在这里没有意义, 否则下拉刷新会一直被拦掉.
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

    /** 由 [ShellBridge] 从 JavaBridge 线程调用; 字段是 `@Volatile`, UI 线程随后读到的就是新值. */
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
     * 主文档加载成功不等于页面能用: 壳不携带浏览器内核, WebView 版本由设备决定, 脚本在挂载前抛异常时
     * 页面会停在空白上, 而页面自己的错误界面不会出现 — 只有原生层能给出重试与换服务器的出口.
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
     * 窗口 inset 用原生 padding 落在根容器上, 而不是让页面自己用 `env(safe-area-inset-*)`.
     * 这样 WebView 的视口等于安全区, SPA 现有的 `100dvh` 高度计算 (`app-shell-metrics.ts`) 自动成立,
     * 前端不必为壳再维护一套断点; 状态栏区域因此由系统背景填充, 页面自己的头部不会与状态栏叠在一起.
     */
    private fun applyWindowInsets() {
        ViewCompat.setOnApplyWindowInsetsListener(binding.root) { _, insets ->
            val bars = insets.getInsets(
                WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.ime(),
            )
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
        private const val TOKEN_COOKIE = "amane_token"
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
