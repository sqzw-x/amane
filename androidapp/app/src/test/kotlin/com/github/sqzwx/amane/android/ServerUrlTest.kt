package com.github.sqzwx.amane.android

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** 地址归一化是壳里唯一有多分支的纯逻辑, 因此用表测试覆盖. */
class ServerUrlTest {

    @Test
    fun normalizesInput() {
        val cases = listOf(
            "192.168.1.10:8000" to "http://192.168.1.10:8000",
            "amane.lan" to "http://amane.lan",
            "  amane.lan:8000  " to "http://amane.lan:8000",
            "http://amane.lan:8000" to "http://amane.lan:8000",
            "https://amane.example.com" to "https://amane.example.com",
            "HTTP://amane.lan:8000" to "http://amane.lan:8000",
            "http://amane.lan:8000/libraries" to "http://amane.lan:8000",
            "http://amane.lan:8000/" to "http://amane.lan:8000",
            "http://user:pass@amane.lan:8000" to "http://amane.lan:8000",
            "http://[::1]:8000" to "http://[::1]:8000",
        )
        for ((input, expected) in cases) {
            assertEquals(input, expected, normalizeServerUrl(input))
        }
    }

    @Test
    fun rejectsUnusableInput() {
        for (input in listOf("", "   ", "http://", "http:///libraries", "not a url")) {
            assertNull(input, normalizeServerUrl(input))
        }
    }
}
