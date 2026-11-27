import type { CSSProperties } from "react";

/**
 * 行尾换行: 多条记录复制到剪贴板时, 记录之间因此隔一个空行.
 *
 * 行是 flex 容器, 末尾追加子项会多占一个 gap, 正文随之收窄; 用等量负外边距抵消该间距,
 * 复制结果多一个换行而排版不变. 文本节点不能直接作为行的子项 —— 纯空白的匿名 flex 项不渲染.
 */
const ROW_BREAK_STYLE: CSSProperties = {
  whiteSpace: "pre",
  fontSize: 0,
  lineHeight: 0,
  marginInlineStart: "calc(-1 * var(--group-gap, 0px))",
};

export function LogRowBreak() {
  return (
    <span aria-hidden style={ROW_BREAK_STYLE}>
      {"\n"}
    </span>
  );
}
