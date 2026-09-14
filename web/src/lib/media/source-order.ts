/**
 * 按用户保存的来源顺序重排后端返回的来源列表.
 *
 * 顺序表只存来源 ID, 后端仍按来源 ID 字典序返回: 表里没有的来源保持后端顺序追加在后, 因此新装
 * 与重装的来源落末位, 卸载不清理顺序表 (装回来仍在原位). 依赖 `Array.prototype.sort` 的稳定性,
 * 未列入的来源之间不改相对位置.
 */
export function orderPlaybackSources<T>(
  items: readonly T[],
  order: readonly string[],
  getSourceId: (item: T) => string,
): T[] {
  if (order.length === 0) {
    return [...items];
  }
  const rank = new Map(order.map((sourceId, index) => [sourceId, index]));
  const missing = order.length;
  return items.toSorted(
    (a, b) => (rank.get(getSourceId(a)) ?? missing) - (rank.get(getSourceId(b)) ?? missing),
  );
}
