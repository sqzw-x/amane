/** 当前页条目索引; current < 0 表示尚未展开任何条目. 不循环. */
export function nextFeedItemIndex(current: number, delta: number, length: number): number | null {
  if (length <= 0 || delta === 0) {
    return null;
  }
  if (current < 0 || current >= length) {
    return delta > 0 ? 0 : length - 1;
  }
  const next = current + delta;
  if (next < 0 || next >= length) {
    return null;
  }
  return next;
}

export function feedNavDelta(key: string): -1 | 1 | null {
  if (key === "ArrowLeft") {
    return -1;
  }
  if (key === "ArrowRight") {
    return 1;
  }
  return null;
}

/** 输入、对话框、分段控件占用焦点时不拦截方向键. */
export function shouldIgnoreFeedNavHotkey(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  if (target.closest("[role='dialog']") != null) {
    return true;
  }
  const tag = target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") {
    return true;
  }
  if (target.isContentEditable) {
    return true;
  }
  return (
    target.closest("[role='radiogroup'], [role='tablist'], [role='listbox'], [role='menu']") != null
  );
}
