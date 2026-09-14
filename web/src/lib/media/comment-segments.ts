/**
 * 评论正文的分段: 时间戳与链接渲染为可交互的元素, 其余按纯文本输出.
 *
 * 时间戳只认 `mm:ss` 与 `h:mm:ss`, 且秒必须是两位: 这一条把最典型的误判挡在外面 —— `16:9`(画面比例)与
 * `3:2`(比分)的秒位只有一位, 不会被当成时间戳. 前后不允许紧邻数字或冒号, 因此 `123:45` 与 `12:345`
 * 也不匹配. 与 YouTube 相同, 形如 `12:30` 的墙钟时间无法与时间戳区分, 一并按时间戳处理.
 *
 * 链接从 `http://` 或 `https://` 开始, 到空白或分界符为止. 分界符取地址中不可能出现的全角标点与
 * 引号、尖括号 —— 只按空白切分时, 紧跟其后的中文会被并入地址. 同一轮扫描里链接优先于时间戳, 因此
 * 分享地址内部的 `12:30` 属于链接.
 */

const SEGMENT_PATTERN =
  /(?<![\d:])(\d{1,2}):([0-5]\d)(?::([0-5]\d))?(?![\d:])|https?:\/\/[^\s，。、；：？！（）【】《》「」『』“”‘’…—～·"'<>`]+/gi;

/** 地址末尾的句读. */
const TRAILING_PUNCTUATION = new Set([".", ",", ";", ":", "!", "?"]);

/** 右括号与它需要的左括号. */
const CLOSING_BRACKETS = new Map([
  [")", "("],
  ["]", "["],
  ["}", "{"],
]);

/**
 * 去掉地址末尾的句读与不成对的右括号: 它们是正文的标点, 属于链接之外.
 * 成对的右括号保留, 地址可以以 `)` 结尾 (维基百科等).
 */
function trimTrailingPunctuation(text: string): string {
  let end = text.length;
  while (end > 0) {
    const char = text.charAt(end - 1);
    const opening = CLOSING_BRACKETS.get(char);
    const trailing =
      opening == null ? TRAILING_PUNCTUATION.has(char) : !text.slice(0, end - 1).includes(opening);
    if (!trailing) {
      break;
    }
    end -= 1;
  }
  return text.slice(0, end);
}

export type CommentSegment =
  | { kind: "text"; text: string }
  | { kind: "timestamp"; text: string; seconds: number }
  | { kind: "link"; href: string };

export function splitCommentSegments(body: string): CommentSegment[] {
  const segments: CommentSegment[] = [];
  let cursor = 0;
  for (const match of body.matchAll(SEGMENT_PATTERN)) {
    const start = match.index;
    if (start > cursor) {
      segments.push({ kind: "text", text: body.slice(cursor, start) });
    }
    // 两条分支只有时间戳那一条有捕获组.
    if (match[1] == null) {
      const href = trimTrailingPunctuation(match[0]);
      segments.push({ kind: "link", href });
      // 去掉的句读留给后续文本.
      cursor = start + href.length;
      continue;
    }
    const text = match[0];
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
