/**
 * 短边锚定的自适应排布.
 *
 * 等宽锚「宽」、等高锚「高」, 卡片面积都随比例线性变化, 极端比例于是必然一边倒: 锚宽则 3:1 被压成细条,
 * 锚高则 3:1 的面积是竖版的四倍. 这里锚**短边**: 横版让高 = S、竖版让宽 = S, 每张图的短边一样长,
 * 视觉大小一致, 宽图只是更宽.
 *
 * 行的宽度由整行缩放 k 撑满 (k = (容器宽 - 间距) / Σ宽); k 允许小幅浮动 (图多略缩、图少略放),
 * 放大以 `MAX_SCALE` 为限, 超出时右端留白. 行内高度不齐, 文字随各自的图.
 */

export interface AdaptiveRow {
  /** 行首在入参数组里的下标. */
  start: number;
  count: number;
  /** 行缩放系数; 实际短边 = 基准短边 × scale. */
  scale: number;
}

/**
 * 行缩放的允许区间.
 *
 * 下限即「再加一列的最小卡片宽度」: 一张竖版海报缩到基准宽度的 95% 才多排一列, 下限再低时窗口稍一变宽
 * 就会跳出一列小卡片. 上限决定单张图独占一行时的放大程度.
 */
const MIN_SCALE = 0.95;
const MAX_SCALE = 1.45;

/**
 * 基准短边 (竖版海报的宽度): 约 7.2 张一行, 夹在 [150, 240].
 *
 * 上限过低时宽屏一行塞进十几张小卡片, 过高时一张占掉半屏; 下限保证窄屏仍能放下两张.
 */
const SHORT_EDGE_MIN = 150;
const SHORT_EDGE_MAX = 240;
const POSTER_COLUMNS = 7.2;

export function adaptiveShortEdge(containerWidth: number): number {
  return Math.min(Math.max(containerWidth / POSTER_COLUMNS, SHORT_EDGE_MIN), SHORT_EDGE_MAX);
}

/** 以短边为单位的卡片宽度: 横版 = 比例, 竖版 = 1. */
function widthInShortEdges(ratio: number): number {
  return Math.max(ratio, 1);
}

/** 卡片宽度, 取两位小数; 高度由卡片按图片比例撑开. */
export function adaptiveTileWidth(ratio: number, shortEdge: number, scale: number): number {
  return Math.round(shortEdge * scale * widthInShortEdges(ratio) * 100) / 100;
}

export function layoutAdaptiveRows(
  ratios: readonly number[],
  containerWidth: number,
  gap: number,
  shortEdge: number,
): AdaptiveRow[] {
  const rows: AdaptiveRow[] = [];
  if (containerWidth <= 0) return rows;

  const scaleOf = (units: number, count: number) =>
    (containerWidth - gap * (count - 1)) / (units * shortEdge);

  let start = 0;
  while (start < ratios.length) {
    // 允许多装一张再整体缩一点: 只在缩放跌破下限时才收尾, 否则行会普遍撑不满
    let count = 1;
    let units = widthInShortEdges(ratios[start]);
    while (start + count < ratios.length) {
      const next = units + widthInShortEdges(ratios[start + count]);
      if (scaleOf(next, count + 1) < MIN_SCALE) break;
      units = next;
      count += 1;
    }
    rows.push({ start, count, scale: Math.min(scaleOf(units, count), MAX_SCALE) });
    start += count;
  }

  return rows;
}
