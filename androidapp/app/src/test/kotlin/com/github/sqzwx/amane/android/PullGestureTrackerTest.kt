package com.github.sqzwx.amane.android

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** 下拉的接管判据; 两种误触 (横滑、多指) 与"积蓄行程"都在这里钉住. */
class PullGestureTrackerTest {

    private val slop = 24f

    @Test
    fun rejectsHorizontalDrag() {
        val cases = listOf(
            Triple(24f, 10f, false),
            Triple(120f, 30f, false),
            // 两轴位移相等时归横向.
            Triple(24f, 24f, false),
            Triple(0f, 30f, true),
            Triple(30f, 120f, true),
        )
        for ((dx, dy, expected) in cases) {
            val tracker = PullGestureTracker(slop)
            tracker.down(0, 0f, 0f)
            assertEquals("dx=$dx dy=$dy", expected, tracker.accepts(dx, dy, pageCanScrollUp = false))
        }
    }

    /** 位移必须真的超过 touchSlop, 恰好等于时还不算. */
    @Test
    fun requiresSlopPast() {
        val tracker = PullGestureTracker(slop)
        tracker.down(0, 0f, 0f)
        assertFalse(tracker.accepts(0f, slop, pageCanScrollUp = false))
        assertTrue(tracker.accepts(0f, slop + 1f, pageCanScrollUp = false))
    }

    /** 页面还能向上滚时不接管; 滚到顶之后的同一段手势里才开始接管. */
    @Test
    fun waitsUntilPageReachesTop() {
        val tracker = PullGestureTracker(slop)
        tracker.down(0, 0f, 800f)
        assertFalse(tracker.accepts(0f, 760f, pageCanScrollUp = true))
        assertFalse(tracker.accepts(0f, 840f, pageCanScrollUp = true))
        assertTrue(tracker.accepts(0f, 860f, pageCanScrollUp = false))
    }

    /**
     * 判据来回变也不会把行程"积蓄"成一次刷新: 起算点始终是按下位置. 手指上滑到页面顶端时仍在起点上方,
     * 那里不是下拉; 必须越过起点再往下才成立.
     */
    @Test
    fun scrollingUpDoesNotAccumulatePull() {
        val tracker = PullGestureTracker(slop)
        tracker.down(0, 0f, 800f)
        assertFalse(tracker.accepts(0f, 300f, pageCanScrollUp = true))
        assertFalse(tracker.accepts(0f, 100f, pageCanScrollUp = true))
        // 页面到顶, 手指仍在起点上方 — 不是下拉.
        assertFalse(tracker.accepts(0f, 110f, pageCanScrollUp = false))
        assertFalse(tracker.accepts(0f, 820f, pageCanScrollUp = false))
        assertTrue(tracker.accepts(0f, 830f, pageCanScrollUp = false))
    }

    @Test
    fun tracksOnlyFirstPointer() {
        val tracker = PullGestureTracker(slop)
        tracker.down(7, 0f, 0f)
        assertTrue(tracker.isTracked(7))
        assertFalse(tracker.isTracked(3))
        assertEquals(7, tracker.trackedPointer())
        tracker.cancel()
        assertEquals(PullGestureTracker.NO_POINTER, tracker.trackedPointer())
    }
}
