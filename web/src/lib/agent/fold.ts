/** 折叠块的展开状态与"钉住标题行": 展开会让块变高, 视口在跟随底部时会把标题顶走. */

import { useCallback, useRef, useState, type RefObject } from "react";

export type Fold = {
  open: boolean;
  toggle: () => void;
  headerRef: RefObject<HTMLButtonElement | null>;
};

/** 展开/收起前后把标题行钉回原来的屏幕位置, 同一处再点即可收起.

    视口在跟随底部 (assistant-ui 的 autoScroll): 内容一变高就自动滚到底, 标题随之被顶走,
    点击位置落在展开块的中段. 这里在状态变化前记下标题位置, 等布局与那次自动滚动落地后补回滚动量.
*/
export function useFold(): Fold {
  const [open, setOpen] = useState(false);
  const headerRef = useRef<HTMLButtonElement | null>(null);

  const toggle = useCallback(() => {
    const header = headerRef.current;
    const top = header?.getBoundingClientRect().top;
    setOpen((value) => !value);
    if (header === null || top === undefined) return;

    const restore = () => {
      const delta = header.getBoundingClientRect().top - top;
      if (delta !== 0) scrollableAncestor(header)?.scrollBy({ top: Math.round(delta) });
    };
    requestAnimationFrame(restore);
    // 自动滚动可能落在下一帧之后, 再补一次; 位置已经对上是空操作
    window.setTimeout(restore, 0);
  }, []);

  return { open, toggle, headerRef };
}

/** 最近的可滚动祖先: 窄屏抽屉里, 消息视口之上还有一层. */
function scrollableAncestor(element: HTMLElement): HTMLElement | null {
  for (let node = element.parentElement; node !== null; node = node.parentElement) {
    const overflowY = getComputedStyle(node).overflowY;
    if (
      (overflowY === "auto" || overflowY === "scroll" || overflowY === "overlay") &&
      node.scrollHeight > node.clientHeight
    ) {
      return node;
    }
  }
  return null;
}
