package com.github.sqzwx.amane.android

import android.content.Context
import org.json.JSONArray

/**
 * 已保存的服务器地址与当前选中项.
 *
 * 只存地址, 不存 token: 登录态是 WebView 自己的 cookie (HttpOnly, 30 天, 见
 * `src/amane/api/middleware.py`), 首次连接的 token 只用于换取该 cookie.
 */
class ServerStore(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    /** 最近使用的排在前面. 存储损坏时视为空列表, 由用户重新填写, 而不是让启动路径崩溃. */
    fun list(): List<String> = runCatching {
        val array = JSONArray(prefs.getString(KEY_LIST, "[]"))
        (0 until array.length()).map { array.getString(it) }
    }.getOrDefault(emptyList())

    fun active(): String? = prefs.getString(KEY_ACTIVE, null)

    /** 加入列表 (去重置顶) 并设为当前. */
    fun activate(url: String) {
        val next = listOf(url) + list().filterNot { it == url }
        prefs.edit()
            .putString(KEY_LIST, JSONArray(next).toString())
            .putString(KEY_ACTIVE, url)
            .apply()
    }

    fun remove(url: String) {
        val next = list().filterNot { it == url }
        val editor = prefs.edit().putString(KEY_LIST, JSONArray(next).toString())
        if (active() == url) editor.remove(KEY_ACTIVE)
        editor.apply()
    }

    private companion object {
        const val PREFS_NAME = "amane"
        const val KEY_LIST = "servers"
        const val KEY_ACTIVE = "active_server"
    }
}
