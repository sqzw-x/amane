/**
 * 浏览页 infinite query 的数据保留时长.
 *
 * 返回浏览页时的滚动恢复发生在首帧提交内, 高度必须由已加载的页提供; 缓存过期后只剩第一页,
 * 恢复位置被限制在列表顶部. 库默认的 5 分钟小于用户在其他页面停留的常见时长.
 */
export const BROWSE_GC_TIME = 30 * 60_000;

/**
 * 海报栅格的预取距离: 一屏.
 *
 * 一页在桌面约 1.5 屏、窄屏更多, 提前一屏取下一页后, 滚到底时数据已在路上或已到.
 * 目录树另算: 那里追加一页很短, 提前一屏会让整棵树一次展开过多, 沿用默认的 200px.
 */
export const GRID_PREFETCH_MARGIN = "0px 0px 100% 0px";

/** Offset/limit 列表的 infinite query 下一页参数. */
export function nextOffsetPageParam<T extends { items: readonly unknown[]; total: number }>(
  lastPage: T,
  allPages: T[],
): number | undefined {
  const loaded = allPages.reduce((sum, p) => sum + p.items.length, 0);
  return loaded < lastPage.total ? loaded : undefined;
}
