package com.github.sqzwx.amane.android

import kotlin.math.abs

/** 一段拖动被归到哪个方向. */
enum class DragAxis { Horizontal, Vertical }

/**
 * 手势方向判定: 以起手点为原点, 两轴中先越过 touchSlop 的一轴决定整段手势的归属.
 *
 * 结论在一段手势内保持不变 — 横向拖动往往伴随纵向偏移, 中途改判会让下拉刷新被误触.
 */
class DragAxisTracker(private val slop: Float) {

    private var originX = 0f
    private var originY = 0f
    private var decided: DragAxis? = null

    /** 记下起手点, 开始新的一段判定. */
    fun start(x: Float, y: Float) {
        originX = x
        originY = y
        decided = null
    }

    /** 返回 [start] 以来确定的方向; 两轴都还没越过 touchSlop 时返回 null. */
    fun axis(x: Float, y: Float): DragAxis? {
        decided?.let { return it }
        val dx = abs(x - originX)
        val dy = abs(y - originY)
        if (dx < slop && dy < slop) return null
        // 两轴位移相等时归横向: 只有纵向明确占优的拖动才允许触发下拉刷新.
        decided = if (dy > dx) DragAxis.Vertical else DragAxis.Horizontal
        return decided
    }
}
