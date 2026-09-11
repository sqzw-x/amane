export type FeedDateBucket =
  | { id: "today" }
  | { id: "yesterday" }
  | { id: "thisWeek" }
  | { id: "lastWeek" }
  | { id: "month"; year: number; month: number };

export function feedDateBucketKey(bucket: FeedDateBucket): string {
  return bucket.id === "month" ? `month:${bucket.year}-${bucket.month}` : bucket.id;
}

function startOfLocalDay(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function startOfMondayWeek(date: Date): Date {
  const day = startOfLocalDay(date);
  const weekday = day.getDay();
  const offset = weekday === 0 ? 6 : weekday - 1;
  day.setDate(day.getDate() - offset);
  return day;
}

/** 按本地日历分段. 周一为一周之始; 昨天优先于「本周/上周」. */
export function feedDateBucket(iso: string, now: Date = new Date()): FeedDateBucket {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return { id: "today" };
  }
  const itemDay = startOfLocalDay(date);
  const today = startOfLocalDay(now);
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);
  const thisWeek = startOfMondayWeek(now);
  const lastWeek = new Date(thisWeek);
  lastWeek.setDate(lastWeek.getDate() - 7);

  if (itemDay.getTime() >= today.getTime()) {
    return { id: "today" };
  }
  if (itemDay.getTime() >= yesterday.getTime()) {
    return { id: "yesterday" };
  }
  if (itemDay.getTime() >= thisWeek.getTime()) {
    return { id: "thisWeek" };
  }
  if (itemDay.getTime() >= lastWeek.getTime()) {
    return { id: "lastWeek" };
  }
  return { id: "month", year: itemDay.getFullYear(), month: itemDay.getMonth() + 1 };
}

export function feedDateBucketLabel(
  bucket: FeedDateBucket,
  labels: { today: string; yesterday: string; thisWeek: string; lastWeek: string },
  locale: string,
): string {
  if (bucket.id === "month") {
    return new Date(bucket.year, bucket.month - 1, 1).toLocaleDateString(locale, {
      year: "numeric",
      month: "long",
    });
  }
  return labels[bucket.id];
}
