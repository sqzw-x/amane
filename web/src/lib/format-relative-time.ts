const MINUTE = 60;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;
const MONTH = 30 * DAY;
const YEAR = 365 * DAY;

/**
 * 时间戳 → 相对时间文案 (如「3 分钟前」).
 *
 * 精度按量级递减: 一个月以上按天累计, 一年以上按年累计, 因此同一个绝对时间在刷新后文案可能变化.
 * 一分钟内交给调用方给文案 —— `Intl` 在 0 秒给的是「现在」, 不适合刚发表的评论.
 * `locale` 直接交给 `Intl`, 无法解析的输入原样返回.
 */
export function formatRelativeTime(value: string, locale: string, justNow: string): string {
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) return value;
  const seconds = Math.round((parsed - Date.now()) / 1000);
  const magnitude = Math.abs(seconds);
  if (magnitude < MINUTE) return justNow;
  const format = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  if (magnitude < HOUR) return format.format(Math.round(seconds / MINUTE), "minute");
  if (magnitude < DAY) return format.format(Math.round(seconds / HOUR), "hour");
  if (magnitude < MONTH) return format.format(Math.round(seconds / DAY), "day");
  if (magnitude < YEAR) return format.format(Math.round(seconds / MONTH), "month");
  return format.format(Math.round(seconds / YEAR), "year");
}
