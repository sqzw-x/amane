/**
 * 浏览页 infinite query 的数据保留时长.
 *
 * 返回浏览页时的滚动恢复发生在首帧提交内, 高度必须由已加载的页提供; 缓存过期后只剩第一页,
 * 恢复位置被限制在列表顶部. 库默认的 5 分钟小于用户在其他页面停留的常见时长.
 */
export const BROWSE_GC_TIME = 30 * 60_000;

/** Offset/limit 列表的 infinite query 下一页参数. */
export function nextOffsetPageParam<T extends { items: readonly unknown[]; total: number }>(
  lastPage: T,
  allPages: T[],
): number | undefined {
  const loaded = allPages.reduce((sum, p) => sum + p.items.length, 0);
  return loaded < lastPage.total ? loaded : undefined;
}
