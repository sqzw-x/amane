package com.github.sqzwx.amane.android

import java.net.URI
import java.net.URISyntaxException

/**
 * 把用户输入整理成 origin (`scheme://host[:port]`), 丢弃路径与结尾斜杠.
 *
 * 缺 scheme 时补 `http://`: 自建服务默认监听 `0.0.0.0:8000`. 解析失败或没有主机名返回 null,
 * 由调用方给出「地址无效」; 用户信息 (user:pass@) 丢弃 — 鉴权只有 token, 不带凭证的地址更好读.
 */
fun normalizeServerUrl(input: String): String? {
    val raw = input.trim()
    if (raw.isEmpty()) return null
    val withScheme =
        if (raw.startsWith("http://", ignoreCase = true) || raw.startsWith("https://", ignoreCase = true)) raw
        else "http://$raw"
    val uri = try {
        URI(withScheme)
    } catch (_: URISyntaxException) {
        return null
    }
    val host = uri.host ?: return null
    val port = if (uri.port == -1) "" else ":${uri.port}"
    return "${uri.scheme.lowercase()}://$host$port"
}
