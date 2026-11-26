package com.github.sqzwx.amane.android

/**
 * 一段下拉手势归谁处理.
 *
 * 三件事都在这里定, 因为它们互为前提:
 * - **哪根手指**: 一段手势只认最先按下的那根. 后按的手指只是陪衬 — 换指针而不换起点, 纵坐标的跳变会被当成
 *   位移, 指示器瞬间拉满并直接进入待刷新.
 * - **何时接管**: 先定方向为纵向, 再要求触点处没有可向上滚的内容. 后者由页面异步推来, 起手时可能是上一段
 *   手势的旧值, 因此每次移动都重新取, 而不是按下时问一次就定死.
 * - **从哪算起**: 恒为被跟踪手指的按下位置. 每个触摸事件都带多根手指的坐标, 取错了那根就是把别的行程算成下拉.
 *
 * 与 View 无关, 便于表测试; 触点的取用与事件补齐由 `AxisLockSwipeRefreshLayout` 负责.
 */
class PullGestureTracker(private val touchSlop: Float) {

    private val axis = DragAxisTracker(touchSlop)

    private var pointerId = NO_POINTER
    private var downX = 0f
    private var downY = 0f

    fun down(pointerId: Int, x: Float, y: Float) {
        this.pointerId = pointerId
        downX = x
        downY = y
        axis.start(x, y)
    }

    /**
     * 此刻能否交给下拉刷新. `x` / `y` 必须是被跟踪手指的当前坐标.
     *
     * `pageCanScrollUp` 为真表示页面还能向上滚, 这段手势归页面 — 返回 false 的那段行程不会被计入.
     */
    fun accepts(x: Float, y: Float, pageCanScrollUp: Boolean): Boolean {
        if (axis.axis(x, y) != DragAxis.Vertical) return false
        if (pageCanScrollUp) return false
        return y - downY > touchSlop
    }

    /** 这段手势被跟踪的手指; 没有按下时为 [NO_POINTER]. */
    fun trackedPointer(): Int = pointerId

    /** 被跟踪手指的按下位置 — 下拉距离从这里算起. */
    fun originX(): Float = downX

    fun originY(): Float = downY

    /** 抬起的是不是被跟踪的那根手指 — 只有它离场才结算, 其余的抬落都不影响. */
    fun isTracked(pointerId: Int): Boolean = pointerId == this.pointerId

    fun cancel() {
        pointerId = NO_POINTER
    }

    companion object {
        const val NO_POINTER = -1
    }
}
