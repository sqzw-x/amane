package com.github.sqzwx.amane.android

import android.webkit.JavascriptInterface
import androidx.webkit.WebViewCompat

/**
 * 暴露给页面的壳接口 (`window.amaneshell`).
 *
 * 壳不提供原生工具栏: 服务器切换等界面动作由 SPA 发起, 经这里回到原生实现; 需要改动界面的调用切回主线程,
 * WebView 的 JS 桥线程不是 UI 线程.
 *
 * 安全边界: `addJavascriptInterface` 对 WebView 加载的文档全部可见, 因此站外文档所在的 WebView 不装桥,
 * 站外链接交给系统浏览器 (见 [BrowserActivity.configureWebView]).
 */
class ShellBridge(private val activity: BrowserActivity) {

    @JavascriptInterface
    fun switchServer() {
        activity.runOnUiThread { activity.openSetup() }
    }

    /**
     * 页面报告"触点处是否还有可以向上滚的内容", 决定下拉刷新能否接管这次手势.
     *
     * 手势在 MOVE 越过阈值时才判定, 因此页面在 `touchstart` 里推来的值来得及生效. 这是状态推送而不是
     * 动作, 直接写在 `@Volatile` 字段上即可, 不必切回 UI 线程.
     */
    @JavascriptInterface
    fun setPageScrollableUp(scrollableUp: Boolean) {
        activity.setPageScrollableUp(scrollableUp)
    }

    /**
     * 系统 WebView 的提供方与包版本, 取不到时为空串.
     *
     * 只用于展示当前渲染方与给出更新入口. 包版本是提供方自己的编号, 与内核版本没有对应关系 —
     * 页面判断内核下限只看 UA 里的 `Chrome/<版本>` (见 docs/dev/android.md).
     */
    @JavascriptInterface
    fun webViewPackage(): String {
        val info = WebViewCompat.getCurrentWebViewPackage(activity) ?: return ""
        return "${info.packageName} ${info.versionName.orEmpty()}".trim()
    }
}
