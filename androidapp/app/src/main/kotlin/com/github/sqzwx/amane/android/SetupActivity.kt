package com.github.sqzwx.amane.android

import android.content.Intent
import android.os.Bundle
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import android.webkit.CookieManager
import android.widget.Toast
import androidx.activity.enableEdgeToEdge
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import com.github.sqzwx.amane.android.databinding.ActivitySetupBinding
import com.github.sqzwx.amane.android.databinding.DialogServerFormBinding
import com.github.sqzwx.amane.android.databinding.RowServerBinding
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import kotlin.concurrent.thread
import kotlin.math.abs

/**
 * 服务器地址与首次登录.
 *
 * 这里填的 token 只用于向 `/api/system/desktop` 换取服务端下发的 cookie, 之后登录态由 WebView 自己的
 * cookie 罐维持 (见 docs/dev/android.md 的登录一节); 留空直接连接也成立, 那时由 SPA 的登录门接管.
 *
 * 页面只有一个「添加服务器」按钮: 表单放在弹窗里, 与编辑共用同一个 ([showForm]), 列表本身因此占满整屏.
 */
class SetupActivity : AppCompatActivity() {

    private lateinit var binding: ActivitySetupBinding
    private val store by lazy { ServerStore(this) }
    private val touchSlop by lazy { ViewConfiguration.get(this).scaledTouchSlop }

    /** 当前露出操作按钮的那一行; 同一时刻只允许一行. */
    private var revealedCard: View? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        binding = ActivitySetupBinding.inflate(layoutInflater)
        setContentView(binding.root)
        applyWindowInsets()

        binding.version.text = getString(R.string.setup_version, BuildConfig.VERSION_NAME)
        binding.addServer.setOnClickListener { showForm(null) }
        renderServers()
    }

    /**
     * 打开服务器表单; `server` 为空时是添加.
     *
     * 添加要连上服务器才算完成, 因此先探活再保存 (它同时也是换取 cookie 的那一次请求); 编辑只落盘, 地址或
     * token 有改动时在后台补一次探活, 失败只提示 — 编辑结果已经保存, 不因一次探活失败而回退.
     */
    private fun showForm(server: SavedServer?) {
        val form = DialogServerFormBinding.inflate(layoutInflater)
        form.formName.setText(server?.name.orEmpty())
        form.formServer.setText(server?.url.orEmpty())
        form.formToken.setText(server?.token.orEmpty())
        val dialog = AlertDialog.Builder(this)
            .setTitle(if (server == null) R.string.setup_add_title else R.string.setup_edit_title)
            .setView(form.root)
            .setNegativeButton(R.string.setup_cancel, null)
            .setPositiveButton(
                if (server == null) R.string.setup_connect else R.string.setup_save,
                null,
            )
            .create()
        // 正面按钮自行接管: 默认实现点击后立即关闭弹窗, 地址写错或连不上时输入与报错一起消失, 只能重新填写.
        dialog.setOnShowListener {
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                if (server == null) submitNew(dialog, form) else submitEdit(dialog, form, server)
            }
        }
        dialog.show()
    }

    /** 添加: 探活通过才保存并打开; 失败把原因留在弹窗里. */
    private fun submitNew(dialog: AlertDialog, form: DialogServerFormBinding) {
        val url = normalizeServerUrl(form.formServer.text.toString())
        if (url == null) {
            showFormError(form, getString(R.string.error_invalid_url))
            return
        }
        val token = form.formToken.text.toString().trim()
        val name = form.formName.text.toString().trim().ifEmpty { defaultServerName(url) }
        hideFormError(form)
        setFormBusy(dialog, form, true)
        // 探活是阻塞 IO; 为一次请求引入协程依赖不值得.
        thread {
            val failure = probe(url, token)
            runOnUiThread {
                if (isDestroyed) return@runOnUiThread
                setFormBusy(dialog, form, false)
                if (failure == null) {
                    dialog.dismiss()
                    open(SavedServer(name, url, token))
                } else {
                    showFormError(form, failure)
                }
            }
        }
    }

    /** 编辑: 只校验地址格式, 保存后按需在后台补探活. */
    private fun submitEdit(dialog: AlertDialog, form: DialogServerFormBinding, server: SavedServer) {
        val url = normalizeServerUrl(form.formServer.text.toString())
        if (url == null) {
            showFormError(form, getString(R.string.error_invalid_url))
            return
        }
        val token = form.formToken.text.toString().trim()
        val name = form.formName.text.toString().trim().ifEmpty { defaultServerName(url) }
        store.update(server.url, SavedServer(name, url, token))
        dialog.dismiss()
        renderServers()
        if (url != server.url || token != server.token) refreshSession(url, token)
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
                // 服务端开了鉴权而这里没填 token: 交给 SPA 的登录门, 不在这里拦截用户.
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
     * 中间件在 Bearer 认证成功时下发 `amane_token` cookie; 原生请求收到的响应头对 WebView 不可见,
     * 因此必须手工写入 WebView 的 cookie 罐, 否则打开页面后仍停在登录门.
     */
    private fun adoptCookies(connection: HttpURLConnection, url: String) {
        val manager = CookieManager.getInstance()
        val headers = connection.headerFields["Set-Cookie"].orEmpty()
        for (header in headers) {
            if (header.startsWith("$TOKEN_COOKIE=")) manager.setCookie(url, header)
        }
        manager.flush()
    }

    private fun open(server: SavedServer) {
        store.activate(server)
        startActivity(
            Intent(this, MainActivity::class.java)
                .putExtra(BrowserActivity.EXTRA_URL, "${server.url}/"),
        )
        finish()
    }

    private fun renderServers() {
        val servers = store.list()
        binding.savedGroup.visibility = if (servers.isEmpty()) View.GONE else View.VISIBLE
        binding.savedEmpty.visibility = if (servers.isEmpty()) View.VISIBLE else View.GONE
        binding.savedList.removeAllViews()
        revealedCard = null
        val activeUrl = store.active()
        for (server in servers) {
            val row = RowServerBinding.inflate(layoutInflater, binding.savedList, false)
            row.serverName.text = server.name
            row.serverUrl.text = server.url
            row.serverActive.visibility = if (server.url == activeUrl) View.VISIBLE else View.GONE
            // 展开时点卡片是收起, 不打开服务器: 滑动之后的一次误触不该切换会话.
            row.serverCard.setOnClickListener {
                if (row.serverCard.translationX < 0f) closeReveal() else open(server)
            }
            row.serverEdit.setOnClickListener {
                closeReveal()
                showForm(server)
            }
            row.serverRemove.setOnClickListener {
                closeReveal()
                confirmRemove(server)
            }
            bindSwipe(row)
            binding.savedList.addView(row.root)
        }
    }

    /**
     * 卡片的横向手势: 左滑露出贴右边的编辑与删除.
     *
     * 手指落下必须返回 true 才能拿到后续事件, 但此时不能禁用父级拦截, 否则列表无法滚动; 方向确定为横向
     * 之后才 `requestDisallowInterceptTouchEvent(true)`.
     *
     * UP 与 CANCEL 必须分开: 父级把这次触摸拿去滚动列表时补发的是 CANCEL, 那一次不是点击 — 在这里
     * `performClick()` 会让用户滚动列表时打开服务器、切走会话并关闭本页.
     */
    private fun bindSwipe(row: RowServerBinding) {
        val card = row.serverCard
        val actions = row.serverActions
        var downX = 0f
        var downY = 0f
        var startX = 0f
        var dragging = false

        card.setOnTouchListener { _, event ->
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    downX = event.rawX
                    downY = event.rawY
                    startX = card.translationX
                    dragging = false
                    true
                }

                MotionEvent.ACTION_MOVE -> {
                    val dx = event.rawX - downX
                    if (!dragging) {
                        val dy = event.rawY - downY
                        if (abs(dx) <= touchSlop || abs(dx) <= abs(dy)) {
                            return@setOnTouchListener false
                        }
                        dragging = true
                        card.parent?.requestDisallowInterceptTouchEvent(true)
                        reveal(card)
                    }
                    card.translationX = (startX + dx).coerceIn(-actions.width.toFloat(), 0f)
                    true
                }

                MotionEvent.ACTION_UP -> {
                    if (dragging) {
                        dragging = false
                        card.parent?.requestDisallowInterceptTouchEvent(false)
                        settle(card, actions)
                        true
                    } else {
                        card.performClick()
                        false
                    }
                }

                MotionEvent.ACTION_CANCEL -> {
                    if (dragging) {
                        dragging = false
                        card.parent?.requestDisallowInterceptTouchEvent(false)
                        settle(card, actions)
                    }
                    false
                }

                else -> false
            }
        }
    }

    private fun reveal(card: View) {
        val revealed = revealedCard
        if (revealed !== card) closeReveal()
        revealedCard = card
        // 上一次的吸附动画未结束时会与这次拖动争用 translationX.
        card.animate().cancel()
    }

    private fun closeReveal() {
        val card = revealedCard ?: return
        revealedCard = null
        card.animate().translationX(0f).setDuration(ANIMATION_MS).start()
    }

    private fun settle(card: View, actions: View) {
        val width = actions.width.toFloat()
        val open = card.translationX < -width / 2f
        if (!open) revealedCard = null
        card.animate().translationX(if (open) -width else 0f).setDuration(ANIMATION_MS).start()
    }

    /**
     * 地址或 token 改过就重新换取 cookie: 新 origin 上还没有会话, 旧 cookie 也不再对应当前配置.
     * 在后台进行, 失败只提示 — 编辑结果已经保存, 不因一次探活失败而回退.
     */
    private fun refreshSession(url: String, token: String) {
        if (token.isEmpty()) return
        thread {
            val failure = probe(url, token) ?: return@thread
            runOnUiThread {
                if (!isDestroyed) Toast.makeText(this, failure, Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun confirmRemove(server: SavedServer) {
        AlertDialog.Builder(this)
            .setMessage(getString(R.string.setup_remove_confirm, server.name))
            .setNegativeButton(R.string.setup_cancel, null)
            .setPositiveButton(R.string.setup_remove) { _, _ ->
                store.remove(server.url)
                renderServers()
            }
            .show()
    }

    /**
     * 探活期间的忙碌态: 输入与两个按钮一起锁住.
     *
     * 正面按钮是自定义点击监听, 不会随弹窗默认的自动关闭而失效, 因此必须在这里显式禁用 — 否则连点会重复发请求,
     * 晚到的那次失败还会盖掉已经打开的新页面.
     */
    private fun setFormBusy(dialog: AlertDialog, form: DialogServerFormBinding, busy: Boolean) {
        form.formProgress.visibility = if (busy) View.VISIBLE else View.GONE
        dialog.getButton(AlertDialog.BUTTON_POSITIVE).isEnabled = !busy
        dialog.getButton(AlertDialog.BUTTON_NEGATIVE).isEnabled = !busy
        form.formServer.isEnabled = !busy
        form.formToken.isEnabled = !busy
        form.formName.isEnabled = !busy
    }

    private fun showFormError(form: DialogServerFormBinding, message: String) {
        form.formError.text = message
        form.formError.visibility = View.VISIBLE
    }

    private fun hideFormError(form: DialogServerFormBinding) {
        form.formError.visibility = View.GONE
    }

    /** 与 [BrowserActivity] 同一套 inset 处理: padding 施加在根容器上, 内容因此不会进入状态栏与导航条区域. */
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
        const val ANIMATION_MS = 160L
    }
}
