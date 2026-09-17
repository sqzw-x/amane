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

    @JavascriptInterface
    fun signOut() {
        activity.runOnUiThread { activity.signOut() }
    }

    /** 壳的版本号, 与 APK 的 `versionName` 一致. */
    @JavascriptInterface
    fun shellVersion(): String = BuildConfig.VERSION_NAME

    /**
     * 系统 WebView 的版本号 (`126.0.6478.122` 形式); 取不到时返回空串.
     *
     * 页面据此判断是否低于前端下限并提示更新 — 壳不携带内核, 这个版本由设备决定.
     */
    @JavascriptInterface
    fun webViewVersion(): String =
        WebViewCompat.getCurrentWebViewPackage(activity)?.versionName.orEmpty()
}
