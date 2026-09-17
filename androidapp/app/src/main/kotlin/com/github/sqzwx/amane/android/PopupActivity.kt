package com.github.sqzwx.amane.android

/**
 * `window.open` 的目标窗口, 由 [BrowserActivity] 的 chrome client 启动.
 *
 * 与入口分开是因为 launchMode: 入口是 singleTask, 弹窗必须能在同一个 task 里叠加实例.
 */
class PopupActivity : BrowserActivity()
