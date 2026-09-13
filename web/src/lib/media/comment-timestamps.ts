/**
 * 评论里的时间戳.
 *
 * 只认 `mm:ss` 与 `h:mm:ss`, 且秒必须是两位: 这一条把最典型的误判挡在外面 —— `16:9`(画面比例)与
 * `3:2`(比分)的秒位只有一位, 不会被当成时间戳. 前后不允许紧邻数字或冒号, 因此 `123:45` 与 `12:345`
 * 也不匹配. 与 YouTube 相同, 形如 `12:30` 的墙钟时间无法与时间戳区分, 一并按时间戳处理.
 */

const TIMESTAMP_PATTERN = /(?<![\d:])(\d{1,2}):([0-5]\d)(?::([0-5]\d))?(?![\d:])/g;

export type CommentSegment =
  | { kind: "text"; text: string }
  | { kind: "timestamp"; text: string; seconds: number };

export function splitCommentTimestamps(body: string): CommentSegment[] {
  const segments: CommentSegment[] = [];
  let cursor = 0;
  for (const match of body.matchAll(TIMESTAMP_PATTERN)) {
    const text = match[0];
    const start = match.index;
    if (start > cursor) {
      segments.push({ kind: "text", text: body.slice(cursor, start) });
    }
    // 三段都在时前一段是小时, 否则前一段是分钟.
    const third = match[3];
    const hours = third == null ? 0 : Number(match[1]);
    const minutes = Number(third == null ? match[1] : match[2]);
    const seconds = Number(third == null ? match[2] : third);
    segments.push({ kind: "timestamp", text, seconds: hours * 3600 + minutes * 60 + seconds });
    cursor = start + text.length;
  }
  if (cursor < body.length) {
    segments.push({ kind: "text", text: body.slice(cursor) });
  }
  return segments;
}
