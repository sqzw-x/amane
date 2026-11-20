/**
 * 灯箱缩放的档位与手势仲裁. 放大后的拖动交给舞台自身的原生滚动, 不做手势变换.
 */

export type ZoomTier = "fit" | "actual";

/** 双指同时落下又同时抬起的时间上界与位移容差. */
const TWO_FINGER_TAP_MS = 300;
const TWO_FINGER_TAP_SLOP_PX = 24;

/** 超过该位移的触摸是拖动 (滚动画面), 不是点按. */
const DRAG_SLOP_PX = 12;

/** 手势收尾后这段时间内的 click 由浏览器补发, 不按点击处理. */
const GESTURE_CLICK_SUPPRESS_MS = 500;

/** 两档互为对方. */
export function nextZoomTier(tier: ZoomTier): ZoomTier {
  return tier === "fit" ? "actual" : "fit";
}

/** 1:1 档下横向拖动用于查看画面, 不判为切图手势. */
export function swipeEnabled(tier: ZoomTier): boolean {
  return tier === "fit";
}

/** 双指同时落下又同时抬起算一次点按; 带拖动或超时都不算. */
export function isTwoFingerTap(maxMovedPx: number, elapsedMs: number): boolean {
  return maxMovedPx <= TWO_FINGER_TAP_SLOP_PX && elapsedMs >= 0 && elapsedMs <= TWO_FINGER_TAP_MS;
}

/** 拖动超过阈值: 该次手势用于滚动画面, 收尾后的 click 不得关闭预览. */
export function isDragGesture(movedPx: number): boolean {
  return movedPx > DRAG_SLOP_PX;
}

/** 距离上次手势是否已超出抑制窗口; 未超出的 click 不关闭预览. */
export function maskClickCloses(msSinceGesture: number): boolean {
  return msSinceGesture >= GESTURE_CLICK_SUPPRESS_MS;
}

/** 手势点落在图片上的比例; 尺寸未知或超出图片时收敛到 [0, 1]. */
export function focalFraction(point: number, start: number, size: number): number {
  if (!(size > 0)) return 0.5;
  return Math.min(1, Math.max(0, (point - start) / size));
}

/** 放大后让手势处的画面停在视口中央: 舞台应滚到的位置 (浏览器自行收敛到可滚范围). */
export function focalScrollOffset(args: {
  contentOffset: number;
  contentSize: number;
  viewportSize: number;
  fraction: number;
}): number {
  return Math.round(args.contentOffset + args.fraction * args.contentSize - args.viewportSize / 2);
}
