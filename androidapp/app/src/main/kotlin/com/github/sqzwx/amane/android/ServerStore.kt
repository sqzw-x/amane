package com.github.sqzwx.amane.android

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

/**
 * 一台已保存的服务器.
 *
 * `token` 与地址一起保存: 它是用户自己填进来的凭据, 与地址同处应用私有存储, 掩码不构成保护 — 编辑页
 * 因此明文显示. 首次连接用它向 `/api/system/desktop` 换取 cookie 后, 登录态以 WebView 的 cookie 罐
 * 为准 (见 docs/dev/android.md).
 */
data class SavedServer(val name: String, val url: String, val token: String)

/** 名字留空时取主机名与端口: 列表首行必须有内容可读. */
fun defaultServerName(url: String): String =
    url.substringAfter("://", missingDelimiterValue = url).substringBefore('/').ifEmpty { url }

/** 同一台服务器只留一条并排到最前: 重复连接不该在列表里堆出多行. */
fun upsertServer(servers: List<SavedServer>, incoming: SavedServer): List<SavedServer> =
    listOf(incoming) + servers.filterNot { it.url == incoming.url }

/**
 * 编辑保存后的列表: 摘掉旧条目, 新条目按 [upsertServer] 的规则写回.
 *
 * 编辑可能把地址改成另一条已有的地址, 那时只留一条, 否则两行同地址、两行都显示「当前」.
 */
fun replaceServer(
    servers: List<SavedServer>,
    previousUrl: String,
    updated: SavedServer,
): List<SavedServer> = upsertServer(servers.filterNot { it.url == previousUrl }, updated)

/**
 * 读取存储.
 *
 * 兼容只存地址的字符串数组条目: 那种条目按主机名补出名字与空 token, 升级不丢弃用户已填过的地址.
 * 存储损坏时视为空列表, 由用户重新填写, 启动路径不因此崩溃.
 */
fun parseSavedServers(raw: String?): List<SavedServer> = runCatching {
    val array = JSONArray(raw ?: "[]")
    (0 until array.length()).mapNotNull { index ->
        when (val item = array.opt(index)) {
            is JSONObject -> {
                val url = item.optString(FIELD_URL)
                if (url.isEmpty()) {
                    null
                } else {
                    SavedServer(
                        name = item.optString(FIELD_NAME).ifEmpty { defaultServerName(url) },
                        url = url,
                        token = item.optString(FIELD_TOKEN),
                    )
                }
            }

            is String -> if (item.isEmpty()) null else SavedServer(defaultServerName(item), item, "")
            else -> null
        }
    }
}.getOrDefault(emptyList())

fun serializeSavedServers(servers: List<SavedServer>): String {
    val array = JSONArray()
    for (server in servers) {
        array.put(
            JSONObject()
                .put(FIELD_NAME, server.name)
                .put(FIELD_URL, server.url)
                .put(FIELD_TOKEN, server.token),
        )
    }
    return array.toString()
}

private const val FIELD_NAME = "name"
private const val FIELD_URL = "url"
private const val FIELD_TOKEN = "token"

/** 已保存的服务器与当前选中项. */
class ServerStore(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    fun list(): List<SavedServer> = parseSavedServers(prefs.getString(KEY_LIST, null))

    fun active(): String? = prefs.getString(KEY_ACTIVE, null)

    fun activeServer(): SavedServer? {
        val active = active() ?: return null
        return list().firstOrNull { it.url == active }
    }

    /** 加入列表 (去重置顶) 并设为当前. */
    fun activate(server: SavedServer) {
        write(upsertServer(list(), server), active = server.url)
    }

    /** 编辑保存 (见 [replaceServer] 的去重规则); 若改的正是当前服务器, 当前项跟着改到新地址. */
    fun update(previousUrl: String, server: SavedServer) {
        val active = if (active() == previousUrl) server.url else active()
        write(replaceServer(list(), previousUrl, server), active)
    }

    fun remove(url: String) {
        write(list().filterNot { it.url == url }, active()?.takeIf { it != url })
    }

    private fun write(servers: List<SavedServer>, active: String?) {
        val editor = prefs.edit().putString(KEY_LIST, serializeSavedServers(servers))
        if (active == null) editor.remove(KEY_ACTIVE) else editor.putString(KEY_ACTIVE, active)
        editor.apply()
    }

    private companion object {
        const val PREFS_NAME = "amane"
        const val KEY_LIST = "servers"
        const val KEY_ACTIVE = "active_server"
    }
}
