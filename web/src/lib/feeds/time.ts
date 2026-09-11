export type FeedDateBucket =
  | { id: "today" }
  | { id: "yesterday" }
  | { id: "day"; year: number; month: number; day: number };

export function feedDateBucketKey(bucket: FeedDateBucket): string {
  return bucket.id === "day" ? `day:${bucket.year}-${bucket.month}-${bucket.day}` : bucket.id;
}

function startOfLocalDay(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

/** 按本地日历分段: 今天、昨天, 更早按日. */
export function feedDateBucket(iso: string, now: Date = new Date()): FeedDateBucket {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return { id: "today" };
  }
  const itemDay = startOfLocalDay(date);
  const today = startOfLocalDay(now);
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);

  if (itemDay.getTime() >= today.getTime()) {
    return { id: "today" };
  }
  if (itemDay.getTime() >= yesterday.getTime()) {
    return { id: "yesterday" };
  }
  return {
    id: "day",
    year: itemDay.getFullYear(),
    month: itemDay.getMonth() + 1,
    day: itemDay.getDate(),
  };
}

export function feedDateBucketLabel(
  bucket: FeedDateBucket,
  labels: { today: string; yesterday: string },
  locale: string,
  now: Date = new Date(),
): string {
  if (bucket.id !== "day") {
    return labels[bucket.id];
  }
  return new Date(bucket.year, bucket.month - 1, bucket.day).toLocaleDateString(locale, {
    year: bucket.year === now.getFullYear() ? undefined : "numeric",
    month: "long",
    day: "numeric",
  });
}
