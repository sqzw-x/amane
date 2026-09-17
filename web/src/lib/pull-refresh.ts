/**
 * 下拉刷新只在页面内部已经没有可向上滚的内容时才响应.
 *
 * 壳用 SwipeRefreshLayout 包住 WebView, 它只看得到 WebView 自身的滚动位置; 而 SPA 的滚动都在内部容器里,
 * 那个位置恒为 0 — 于是内层列表与弹窗里的下滑会被当成下拉刷新. 这里在触摸开始时实测触点处还有没有可向上滚的
 * 内容, 把结论推给壳, 由它在手势起点判断.
 *
 * 判据是沿触点元素向上找第一个 `scrollTop > 0` 的祖先: 不必判断谁是真滚动容器 — 不可滚动的元素恒为 0,
 * 而浏览器本身就是按"离触点最近且还能动的那个"链式滚动的.
 */

/** 最近一次触摸的落点; 惯性滑动会在手指抬起后继续, 需要靠它继续更新结论. */
let touched: Element | null = null;

/** 上一次推给壳的值, 避免每帧都跨线程调用. */
let reported: boolean | null = null;

/**
 * 自己消费纵向拖动的地方 — 播放器的竖直滑动调音量 / 亮度. 触点落在它们上面时不能算"在顶端", 否则从
 * 播放器上往下拖会被壳的下拉刷新抢走.
 */
const OWNS_VERTICAL_DRAG = "media-controller";

function canScrollUp(node: Element | null): boolean {
  if (node?.closest(OWNS_VERTICAL_DRAG) != null) return true;
  for (let el = node; el !== null; el = el.parentElement) {
    if (el.scrollTop > 0) return true;
  }
  return (document.scrollingElement?.scrollTop ?? 0) > 0;
}

function report(): void {
  const bridge = window.amaneshell;
  if (bridge?.setPageScrollableUp === undefined) return;
  const value = canScrollUp(touched);
  if (value === reported) return;
  reported = value;
  bridge.setPageScrollableUp(value);
}

/** 装上监听; 桥不可用时为空操作. */
export function installPullRefreshGate(): () => void {
  const onTouchStart = (event: TouchEvent) => {
    touched = event.target instanceof Element ? event.target : null;
    report();
  };
  const onScroll = () => report();
  // 两个监听都是 passive: 页面滚动仍由合成器驱动, 不受它们影响.
  document.addEventListener("touchstart", onTouchStart, { capture: true, passive: true });
  document.addEventListener("scroll", onScroll, { capture: true, passive: true });
  return () => {
    document.removeEventListener("touchstart", onTouchStart, { capture: true });
    document.removeEventListener("scroll", onScroll, { capture: true });
    touched = null;
    reported = null;
  };
}
