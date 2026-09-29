package com.github.sqzwx.amane.android

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** 方向判定决定下拉刷新是否会接管横向滑动, 用表测试覆盖门槛与两轴相等的情况. */
class DragAxisTrackerTest {

    private val slop = 24f

    @Test
    fun decidesByFirstAxisPastSlop() {
        val cases = listOf(
            Triple(0f, 0f, null),
            Triple(10f, 23f, null),
            Triple(23f, 23f, null),
            Triple(24f, 0f, DragAxis.Horizontal),
            Triple(30f, -4f, DragAxis.Horizontal),
            Triple(-30f, 4f, DragAxis.Horizontal),
            Triple(0f, -24f, DragAxis.Vertical),
            Triple(-6f, 30f, DragAxis.Vertical),
            Triple(120f, 30f, DragAxis.Horizontal),
            Triple(30f, 120f, DragAxis.Vertical),
            // 两轴位移相等时归横向.
            Triple(24f, 24f, DragAxis.Horizontal),
            Triple(25f, 24f, DragAxis.Horizontal),
            Triple(24f, 25f, DragAxis.Vertical),
        )
        for ((x, y, expected) in cases) {
            val tracker = DragAxisTracker(slop)
            tracker.start(0f, 0f)
            assertEquals("dx=$x dy=$y", expected, tracker.axis(x, y))
        }
    }

    @Test
    fun keepsDirectionForWholeGesture() {
        val tracker = DragAxisTracker(slop)
        tracker.start(100f, 100f)
        assertEquals(DragAxis.Horizontal, tracker.axis(60f, 100f))
        // 判定之后即使折向纵向也不再改判, 否则横向滑动收尾时的纵向偏移会触发刷新.
        assertEquals(DragAxis.Horizontal, tracker.axis(60f, 300f))
        assertEquals(DragAxis.Horizontal, tracker.axis(100f, 100f))
    }

    @Test
    fun restartsFromNewOrigin() {
        val tracker = DragAxisTracker(slop)
        tracker.start(0f, 0f)
        assertEquals(DragAxis.Vertical, tracker.axis(2f, 40f))
        tracker.start(200f, 200f)
        assertNull(tracker.axis(210f, 210f))
        assertEquals(DragAxis.Horizontal, tracker.axis(140f, 200f))
    }
}
