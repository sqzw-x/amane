package com.github.sqzwx.amane.android

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * 服务器列表的不变量与存储格式.
 *
 * 存储里可能残留只记地址的旧条目, 不允许读成空列表, 因此新旧两种格式与名字回退都要覆盖;
 * 界面部分由真机验证, 不在单元测试范围内.
 */
class ServerStoreTest {

    @Test
    fun `连接同一地址只保留一条并置顶`() {
        val existing = listOf(server("旧名字", "http://a:8000"), server("B", "http://b:8000"))
        val next = upsertServer(existing, server("新名字", "http://a:8000"))

        assertEquals(listOf("http://a:8000", "http://b:8000"), next.map { it.url })
        assertEquals("新名字", next.first().name)
    }

    @Test
    fun `编辑改地址后旧地址不再存在`() {
        val existing = listOf(server("A", "http://a:8000"), server("B", "http://b:8000"))
        val next = replaceServer(existing, "http://a:8000", server("A", "http://c:8000"))

        assertEquals(listOf("http://c:8000", "http://b:8000"), next.map { it.url })
    }

    @Test
    fun `编辑改成另一条已有的地址时只留一条`() {
        val existing = listOf(server("A", "http://a:8000"), server("B", "http://b:8000"))
        val next = replaceServer(existing, "http://a:8000", server("A", "http://b:8000"))

        assertEquals(listOf("http://b:8000"), next.map { it.url })
        assertEquals("A", next.first().name)
    }

    @Test
    fun `名字留空时回退到主机与端口`() {
        val cases = mapOf(
            "http://192.168.1.10:8000" to "192.168.1.10:8000",
            "https://amane.example.com/" to "amane.example.com",
            "http://host" to "host",
            "not-a-url" to "not-a-url",
        )
        for ((url, expected) in cases) {
            assertEquals(expected, defaultServerName(url))
        }
    }

    @Test
    fun `旧格式只有地址时补出名字与空 token`() {
        val parsed = parseSavedServers("""["http://a:8000","https://b.example.com/"]""")

        assertEquals(listOf("http://a:8000", "https://b.example.com/"), parsed.map { it.url })
        assertEquals(listOf("a:8000", "b.example.com"), parsed.map { it.name })
        assertEquals(listOf("", ""), parsed.map { it.token })
    }

    @Test
    fun `损坏的存储视为空列表`() {
        assertEquals(emptyList<SavedServer>(), parseSavedServers("{not json"))
        assertEquals(emptyList<SavedServer>(), parseSavedServers(null))
    }

    @Test
    fun `对象数组往返保留名字与 token`() {
        val servers = listOf(
            SavedServer("客厅的服务器", "http://a:8000", "tok=en/value"),
            server("B", "http://b:8000"),
        )

        assertEquals(servers, parseSavedServers(serializeSavedServers(servers)))
    }

    private fun server(name: String, url: String) = SavedServer(name, url, "")
}
