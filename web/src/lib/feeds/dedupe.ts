import type { FeedItemResponse } from "@/client/types.gen";

export type DedupedFeedItem = {
  item: FeedItemResponse;
  duplicates: FeedItemResponse[];
};

export function itemNumberKey(item: FeedItemResponse): string | null {
  const number = item.number?.trim();
  if (number == null || number === "") {
    return null;
  }
  return number.toLowerCase();
}

/** 折叠行覆盖的全部条目 id (代表行在前). 已读状态按组写入, 否则重复条目留在反状态. */
export function dedupedItemIds(row: DedupedFeedItem): number[] {
  return [row.item.id, ...row.duplicates.map((item) => item.id)];
}

/** 列表已按时间新→旧; 同番号保留首条, 其余归入 duplicates. */
export function dedupeFeedItems(items: readonly FeedItemResponse[]): DedupedFeedItem[] {
  const seen = new Map<string, DedupedFeedItem>();
  const result: DedupedFeedItem[] = [];
  for (const item of items) {
    const key = itemNumberKey(item);
    if (key == null) {
      result.push({ item, duplicates: [] });
      continue;
    }
    const existing = seen.get(key);
    if (existing == null) {
      const row: DedupedFeedItem = { item, duplicates: [] };
      seen.set(key, row);
      result.push(row);
    } else {
      existing.duplicates.push(item);
    }
  }
  return result;
}
