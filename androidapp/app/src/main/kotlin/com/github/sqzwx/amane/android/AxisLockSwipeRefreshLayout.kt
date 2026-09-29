package com.github.sqzwx.amane.android

import android.content.Context
import android.util.AttributeSet
import android.view.MotionEvent
import android.view.ViewConfiguration
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout

/**
 * 只接管纵向手势的 [SwipeRefreshLayout].
 *
 * 基类判定是否开始拖动时只比较纵位移与 touchSlop (AOSP `startDragging`), 横向滑动在起手阶段伴随的纵向偏移
 * 因此会被当成下拉刷新. 这里先用 [DragAxisTracker] 定方向, 判定为横向时不接管, 事件交由子视图处理.
 */
class AxisLockSwipeRefreshLayout @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : SwipeRefreshLayout(context, attrs) {

    private val axisTracker =
        DragAxisTracker(ViewConfiguration.get(context).scaledTouchSlop.toFloat())

    override fun onInterceptTouchEvent(event: MotionEvent): Boolean {
        if (event.actionMasked == MotionEvent.ACTION_DOWN) {
            axisTracker.start(event.x, event.y)
        }
        if (axisTracker.axis(event.x, event.y) == DragAxis.Horizontal) return false
        return super.onInterceptTouchEvent(event)
    }
}
