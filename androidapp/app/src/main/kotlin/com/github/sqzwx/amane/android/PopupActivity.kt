package com.github.sqzwx.amane.android

/**
 * `window.open` 的目标窗口, 由 [BrowserActivity] 的 chrome client 启动.
 *
 * 与入口分开是为了 launchMode: 入口是 singleTask (点桌面图标回到现有实例),
 * 弹窗必须能在同一个 task 里叠加实例.
 */
class PopupActivity : BrowserActivity()
