package com.github.sqzwx.amane.android

import android.content.Context
import android.util.AttributeSet
import android.view.MotionEvent
import android.view.ViewConfiguration
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout

/**
 * 把下拉刷新的接管判定交给 [PullGestureTracker] 的 [SwipeRefreshLayout].
 *
 * 基类的两处做法在这里都不成立, 因此它既不自行决定能否接管, 也不独自记起点:
 * - **按下时问能否接管**: 基类先问 canChildScrollUp(), 答"还能向上滚"时连起点都不记, 一直沿用上一次被接受
 *   时的位置. 页面推来的滚动状态是异步的, 按下那一刻读到的是上一段手势的结论, 于是先前滑动的行程被算进这次
 *   下拉. 这里让基类恒定报告"不可向上滚", 判定改由本类在移动中做.
 * - **换手指不换起点**: 抬起一根手指时基类只换指针不重设起点 (AOSP onSecondaryPointerUp), 纵坐标跳到另一根
 *   手指的位置, 位移瞬间越过阈值并把指示器拉满. 这里一段手势只认最先按下的那根手指, 它离场即结算.
 *
 * 基类的下拉距离是"当前坐标 - 它自己记的起点", 因此接管之前喂给它的事件里, 坐标一律钉在被跟踪手指的按下
 * 位置: 它的起点随之落在按下处, 而位移恒为 0. 早于接管时刻的行程属于页面滚动, 只有这样才不会折成下拉.
 */
class AxisLockSwipeRefreshLayout @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : SwipeRefreshLayout(context, attrs) {

    private val tracker = PullGestureTracker(
        ViewConfiguration.get(context).scaledTouchSlop.toFloat(),
    )

    /** 已经交给基类; 本段手势不再回头判定. */
    private var accepted = false

    /** 基类不再自行决定: 判据异步到达, 什么时候算下拉由这里决定. */
    override fun canChildScrollUp(): Boolean = false

    /** 页面此刻还能不能向上滚 — 壳之前挂上的那条回调 (页面推来的值加 WebView 自身的滚动位置). */
    private fun pageCanScrollUp(): Boolean = super.canChildScrollUp()

    override fun onInterceptTouchEvent(event: MotionEvent): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                accepted = false
                if (!isEnabled) return false
                tracker.down(event.getPointerId(0), event.x, event.y)
                // 坐标即按下处: 基类在这儿记起点, 位移还是 0.
                forwardIntercept(event, event.x, event.y)
                return false
            }

            MotionEvent.ACTION_MOVE -> {
                if (accepted) return super.onInterceptTouchEvent(event)
                val index = event.findPointerIndex(tracker.trackedPointer())
                if (index < 0) return false
                if (tracker.accepts(event.getX(index), event.getY(index), pageCanScrollUp())) {
                    accepted = true
                    return super.onInterceptTouchEvent(event)
                }
                // 还没定下来: 对基类显示成"没有位移", 它就不会自行开始拖动.
                forwardIntercept(event, tracker.originX(), tracker.originY())
                return false
            }

            MotionEvent.ACTION_POINTER_UP -> {
                // 被跟踪的手指先走了: 不换手给别的指针, 这段手势到此为止.
                if (tracker.isTracked(event.getPointerId(event.actionIndex))) {
                    tracker.cancel()
                    accepted = false
                }
                return false
            }

            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                tracker.cancel()
                val wasAccepted = accepted
                accepted = false
                return wasAccepted && super.onInterceptTouchEvent(event)
            }

            else -> return accepted && super.onInterceptTouchEvent(event)
        }
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        if (!accepted) return false
        when (event.actionMasked) {
            // 接管之后不再让基类看见第二根手指: 它会换指针, 而起点不会跟着换.
            MotionEvent.ACTION_POINTER_DOWN -> return true

            MotionEvent.ACTION_POINTER_UP -> {
                val index = event.actionIndex
                // 被跟踪的那根离场: 其余手指还按着, 等不到抬起事件, 就地按当前位置结算.
                if (tracker.isTracked(event.getPointerId(index))) {
                    val up = single(event, MotionEvent.ACTION_UP, event.getX(index), event.getY(index))
                    super.onTouchEvent(up)
                    up.recycle()
                    tracker.cancel()
                    accepted = false
                }
                return true
            }

            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                tracker.cancel()
                accepted = false
            }
        }
        return super.onTouchEvent(event)
    }

    /** 把"位移为零"的副本交给基类拦截处理; 副本用完即回收. */
    private fun forwardIntercept(source: MotionEvent, x: Float, y: Float) {
        val copy = single(source, source.actionMasked, x, y)
        super.onInterceptTouchEvent(copy)
        copy.recycle()
    }

    /** 只带被跟踪手指的事件, 用于给基类补按下 / 抬起, 或钉住它的起点. */
    private fun single(source: MotionEvent, action: Int, x: Float, y: Float): MotionEvent {
        val properties = arrayOf(
            MotionEvent.PointerProperties().apply {
                id = 0
                toolType = MotionEvent.TOOL_TYPE_FINGER
            },
        )
        val coords = arrayOf(
            MotionEvent.PointerCoords().apply {
                this.x = x
                this.y = y
                pressure = 1f
                size = 1f
            },
        )
        return MotionEvent.obtain(
            source.downTime,
            source.eventTime,
            action,
            properties.size,
            properties,
            coords,
            source.metaState,
            source.buttonState,
            source.xPrecision,
            source.yPrecision,
            source.deviceId,
            source.edgeFlags,
            source.source,
            source.flags,
        )
    }
}
