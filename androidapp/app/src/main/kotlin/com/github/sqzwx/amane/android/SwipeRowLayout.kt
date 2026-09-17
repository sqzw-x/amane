package com.github.sqzwx.amane.android

import android.content.Context
import android.util.AttributeSet
import android.widget.FrameLayout

/**
 * 服务器条目的容器: 卡片与它下层的操作区完全重叠, 且高度一致.
 *
 * 两个子节点的顺序固定为 [操作区, 卡片]: 卡片后绘制因此盖住操作区, 左滑把卡片移开才露出按钮.
 *
 * 高度必须自行测量, 框架的现成布局都做不到: FrameLayout 只在**多于一个** `match_parent` 子节点时才按
 * 自己测出的高度重测它们 (见 AOSP `FrameLayout.onMeasure` 的 `count > 1`), 而这里行高来自 `wrap_content`
 * 的卡片; 在布局回调里修改 `layoutParams` 又会在布局过程中再发起一次布局.
 */
class SwipeRowLayout @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : FrameLayout(context, attrs) {

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val actions = getChildAt(ACTIONS_INDEX)
        val card = getChildAt(CARD_INDEX)
        if (actions == null || card == null) {
            super.onMeasure(widthMeasureSpec, heightMeasureSpec)
            return
        }

        val horizontalPadding = paddingLeft + paddingRight
        val verticalPadding = paddingTop + paddingBottom
        card.measure(
            getChildMeasureSpec(widthMeasureSpec, horizontalPadding, LayoutParams.MATCH_PARENT),
            getChildMeasureSpec(heightMeasureSpec, verticalPadding, LayoutParams.WRAP_CONTENT),
        )
        actions.measure(
            getChildMeasureSpec(widthMeasureSpec, horizontalPadding, LayoutParams.WRAP_CONTENT),
            MeasureSpec.makeMeasureSpec(card.measuredHeight, MeasureSpec.EXACTLY),
        )

        setMeasuredDimension(
            resolveSize(card.measuredWidth, widthMeasureSpec),
            resolveSize(card.measuredHeight, heightMeasureSpec),
        )
    }

    private companion object {
        const val ACTIONS_INDEX = 0
        const val CARD_INDEX = 1
    }
}
