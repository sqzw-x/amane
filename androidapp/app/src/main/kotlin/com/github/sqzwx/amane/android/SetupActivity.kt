package com.github.sqzwx.amane.android

import android.content.Intent
import android.graphics.Typeface
import android.os.Bundle
import android.view.View
import android.webkit.CookieManager
import android.widget.Button
import android.widget.TextView
import androidx.activity.enableEdgeToEdge
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import com.github.sqzwx.amane.android.databinding.ActivitySetupBinding
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import kotlin.concurrent.thread

/**
 * 服务器地址与首次登录.
 *
 * 这里不保存 token: 填进来的 token 只用于向 `/api/system/desktop` 换取服务端下发的
 * HttpOnly cookie, 之后登录态由 WebView 自己的 cookie 罐维持 (与浏览器一致, 30 天).
 * 留空直接连接也成立 — 服务端若开了鉴权, SPA 会显示自己的登录门.
 */
class SetupActivity : AppCompatActivity() {

    private lateinit var binding: ActivitySetupBinding
    private val store by lazy { ServerStore(this) }

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        binding = ActivitySetupBinding.inflate(layoutInflater)
        setContentView(binding.root)
        applyWindowInsets()

        binding.version.text = getString(R.string.setup_version, BuildConfig.VERSION_NAME)
        binding.connect.setOnClickListener { connect() }
        binding.serverInput.setText(store.active().orEmpty())
        renderServers()
    }

    private fun connect() {
        val url = normalizeServerUrl(binding.serverInput.text.toString())
        if (url == null) {
            showError(getString(R.string.error_invalid_url))
            return
        }
        val token = binding.tokenInput.text.toString().trim()
        binding.error.visibility = View.GONE
        setBusy(true)
        // 探活是阻塞 IO; 为了一个请求引入协程依赖不值得, 用线程加 runOnUiThread.
        thread {
            val failure = probe(url, token)
            runOnUiThread {
                setBusy(false)
                if (failure == null) open(url) else showError(failure)
            }
        }
    }

    /** 返回 null 表示可以打开: 服务器可达 (且 token 可用, 或本来就不需要 token). */
    private fun probe(url: String, token: String): String? {
        var connection: HttpURLConnection? = null
        return try {
            connection = (URL("$url/api/system/desktop").openConnection() as HttpURLConnection).apply {
                connectTimeout = TIMEOUT_MS
                readTimeout = TIMEOUT_MS
                if (token.isNotEmpty()) setRequestProperty("Authorization", "Bearer $token")
            }
            when (val code = connection.responseCode) {
                HttpURLConnection.HTTP_OK -> {
                    adoptCookies(connection, url)
                    null
                }
                // 服务端开了鉴权而这里没填 token: 交给 SPA 的登录门, 不在这里拦住用户
                HttpURLConnection.HTTP_UNAUTHORIZED ->
                    if (token.isEmpty()) null else getString(R.string.error_invalid_token)
                else -> getString(R.string.error_status, code)
            }
        } catch (e: IOException) {
            getString(R.string.error_unreachable, e.message.orEmpty())
        } finally {
            connection?.disconnect()
        }
    }

    /**
     * 中间件在 Bearer 认证成功时会下发 `amane_token` cookie; 原生请求收到的响应头对 WebView
     * 不可见, 因此手工写入它的 cookie 罐, 否则打开后仍会停在登录门.
     */
    private fun adoptCookies(connection: HttpURLConnection, url: String) {
        val manager = CookieManager.getInstance()
        val headers = connection.headerFields["Set-Cookie"].orEmpty()
        for (header in headers) {
            if (header.startsWith("$TOKEN_COOKIE=")) manager.setCookie(url, header)
        }
        manager.flush()
    }

    private fun open(url: String) {
        store.activate(url)
        startActivity(
            Intent(this, MainActivity::class.java).putExtra(BrowserActivity.EXTRA_URL, "$url/"),
        )
        finish()
    }

    private fun renderServers() {
        val servers = store.list()
        binding.savedGroup.visibility = if (servers.isEmpty()) View.GONE else View.VISIBLE
        binding.savedList.removeAllViews()
        for (url in servers) {
            val row = layoutInflater.inflate(R.layout.row_server, binding.savedList, false)
            val label = row.findViewById<TextView>(R.id.server_label)
            label.text = url
            if (url == store.active()) label.setTypeface(null, Typeface.BOLD)
            row.findViewById<Button>(R.id.server_open).setOnClickListener { open(url) }
            row.findViewById<Button>(R.id.server_remove).setOnClickListener {
                store.remove(url)
                renderServers()
            }
            binding.savedList.addView(row)
        }
    }

    private fun setBusy(busy: Boolean) {
        binding.connect.isEnabled = !busy
        binding.progress.visibility = if (busy) View.VISIBLE else View.GONE
    }

    private fun showError(message: String) {
        binding.error.text = message
        binding.error.visibility = View.VISIBLE
    }

    /** 与 [BrowserActivity] 同一套 inset 处理: padding 施加在滚动容器上, 内容因此不会进入状态栏与导航条区域. */
    private fun applyWindowInsets() {
        ViewCompat.setOnApplyWindowInsetsListener(binding.root) { _, insets ->
            val bars = insets.getInsets(
                WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.ime(),
            )
            binding.root.updatePadding(
                top = bars.top,
                bottom = bars.bottom,
                left = bars.left,
                right = bars.right,
            )
            insets
        }
    }

    private companion object {
        const val TIMEOUT_MS = 5_000
        const val TOKEN_COOKIE = "amane_token"
    }
}
