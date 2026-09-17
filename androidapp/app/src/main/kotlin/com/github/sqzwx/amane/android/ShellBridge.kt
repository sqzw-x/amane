package com.github.sqzwx.amane.android

import android.webkit.JavascriptInterface
import androidx.webkit.WebViewCompat

/**
 * 暴露给页面的壳接口 (`window.amaneshell`).
 *
 * 壳不提供原生工具栏: 服务器切换与登录清除由 SPA 的界面发起, 走这里回调到原生实现.
 * 传入的每个调用都切回主线程 — WebView 的 JS 桥线程不是 UI 线程.
 *
 * 安全边界: `addJavascriptInterface` 对 WebView 加载的文档全部可见, 因此 [BrowserActivity]
 * 只让服务端自身 origin 留在 WebView 内, 站外链接交给系统浏览器.
 */
class ShellBridge(private val activity: BrowserActivity) {

    @JavascriptInterface
    fun switchServer() {
        activity.runOnUiThread { activity.openSetup() }
    }

    /**
     * 页面报告"触点处是否还有可以向上滚的内容", 决定下拉刷新能否接管这次手势.
     *
     * 手势是在 MOVE 越过阈值时才判定的, 因此页面在 `touchstart` 里推来的值来得及生效. 这是状态推送而不是
     * 动作, 直接写在 `@Volatile` 字段上即可, 不必切回 UI 线程.
     */
    @JavascriptInterface
    fun setPageScrollableUp(scrollableUp: Boolean) {
        activity.setPageScrollableUp(scrollableUp)
    }

    /**
     * 系统 WebView 的提供方与包版本 ; 取不到时为空串.
     *
     * 包版本是厂商自己的编号 (), 与 Chromium 版本无关 — 页面判断内核下限时只用
     * UA 里的 `Chrome/<版本>`. 这里的值只用于展示"谁在渲染"以及给出更新入口.
     */
    @JavascriptInterface
    fun webViewPackage(): String {
        val info = WebViewCompat.getCurrentWebViewPackage(activity) ?: return ""
        return "${info.packageName} ${info.versionName.orEmpty()}".trim()
    }
}
