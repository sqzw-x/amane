import type { Element, ElementContent, Root } from "hast";

/**
 * 评论中的可跳转时间戳: 只认 `mm:ss` 与 `h:mm:ss`, 且秒必须是两位.
 *
 * 这一条把最典型的误判挡在外面 —— `16:9` (画面比例) 与 `3:2` (比分) 的秒位只有一位, 不会被当成时间戳.
 * 前后不允许紧邻数字或冒号, 因此 `123:45` 与 `12:345` 也不匹配. 与 YouTube 相同, 形如 `12:30` 的
 * 墙钟时间无法与时间戳区分, 一并按时间戳处理.
 */
const TIMESTAMP_PATTERN = /(?<![\d:])(\d{1,2}):([0-5]\d)(?::([0-5]\d))?(?![\d:])/g;

/** 链接与代码内的文本不参与改写: 地址与代码应按原文显示. */
const SKIPPED_TAGS = new Set(["a", "code", "pre"]);

function timestampElement(match: RegExpExecArray): Element {
  const third = match[3];
  const hours = third == null ? 0 : Number(match[1]);
  const minutes = Number(third == null ? match[1] : match[2]);
  const seconds = Number(third == null ? match[2] : third);
  return {
    type: "element",
    tagName: "span",
    properties: {
      "data-seek": hours * 3600 + minutes * 60 + seconds,
      "data-time": match[0],
    },
    children: [{ type: "text", value: match[0] }],
  };
}

/** 没有时间戳时返回 `null`, 调用方保留原文本节点. */
function splitTimestamps(value: string): ElementContent[] | null {
  const children: ElementContent[] = [];
  let cursor = 0;
  let matched = false;
  for (const match of value.matchAll(TIMESTAMP_PATTERN)) {
    matched = true;
    if (match.index > cursor) {
      children.push({ type: "text", value: value.slice(cursor, match.index) });
    }
    children.push(timestampElement(match));
    cursor = match.index + match[0].length;
  }
  if (!matched) return null;
  if (cursor < value.length) {
    children.push({ type: "text", value: value.slice(cursor) });
  }
  return children;
}

function rewrite(node: Root | Element, blocked: boolean): void {
  const children: ElementContent[] = [];
  for (const child of node.children) {
    if (child.type === "element") {
      rewrite(child, blocked || SKIPPED_TAGS.has(child.tagName));
      children.push(child);
      continue;
    }
    if (child.type === "text" && !blocked) {
      const parts = splitTimestamps(child.value);
      if (parts != null) {
        children.push(...parts);
        continue;
      }
    }
    // doctype 只可能出现在根节点, 评论的 Markdown 语法树不含它.
    if (child.type === "doctype") continue;
    children.push(child);
  }
  node.children = children;
}

/**
 * 把评论文本中的时间戳改写为 `span[data-seek]`, 由 `CommentBody` 渲染为跳转入口.
 *
 * 改写发生在 Markdown 语法树上而不是原文: 对原文做正则替换会命中代码块与链接地址.
 */
export function rehypeCommentTimestamps() {
  return (tree: Root): void => {
    rewrite(tree, false);
  };
}
