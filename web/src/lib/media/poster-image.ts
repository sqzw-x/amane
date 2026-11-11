/**
 * 海报卡片的图片地址与实测比例.
 *
 * 外链图经 `/api/resources/proxy` 反代、本地资源直接渲染, 比例缓存的键必须是卡片实际请求的地址,
 * 否则缓存与 `<img>` 对不上. 比例只有图片加载完才有, 未加载与缺图一律按竖版海报比例参与布局.
 */

import type { MetadataListItem } from "@/client/types.gen";
import { proxyImageUrl } from "@/lib/utils";

/** 竖版海报的缺省宽高比: 缺图条目与尚未加载完的图片按它占位. */
export const DEFAULT_POSTER_RATIO = 0.7;

const ratios = new Map<string, number>();

/** 卡片实际渲染的图片地址: 覆盖值 (语义检索的剧照) 优先, 其次条目的海报. */
export function posterImageUrl(
  item: MetadataListItem,
  override?: string | null,
): string | undefined {
  return proxyImageUrl(override ?? item.poster_url ?? item.poster_urls?.[0]);
}

/**
 * 记下 `<img>` 的自然尺寸; 未解码或已损坏的图没有自然尺寸, 忽略.
 *
 * 返回是否真的更新了缓存: 栅格据此重排, 重挂载的图重新触发 load 时不能再次通知, 否则成环.
 */
export function rememberImageRatio(
  url: string | undefined,
  width: number,
  height: number,
): boolean {
  if (!url || width <= 0 || height <= 0) return false;
  const ratio = width / height;
  if (ratios.get(url) === ratio) return false;
  ratios.set(url, ratio);
  return true;
}

export function cachedImageRatio(url: string | undefined): number | undefined {
  return url ? ratios.get(url) : undefined;
}

/** 栅格逐条算好的图片地址与比例. */
export interface PosterEntry {
  item: MetadataListItem;
  /** 卡片实际请求的地址; 缺图条目为空. */
  url: string | undefined;
  /** 实测优先, 其次调用方给的 (语义检索的剧照), 最后回落到竖版缺省. */
  ratio: number;
}

export interface PosterEntriesSource {
  items: readonly MetadataListItem[];
  /** 逐条覆盖卡片图片与宽高比 (语义检索的剧照结果). */
  imageUrlOf?: (item: MetadataListItem) => string | null | undefined;
  imageRatioOf?: (item: MetadataListItem) => number | undefined;
}

export function posterEntries({
  items,
  imageUrlOf,
  imageRatioOf,
}: PosterEntriesSource): PosterEntry[] {
  return items.map((item) => {
    const url = posterImageUrl(item, imageUrlOf?.(item));
    return {
      item,
      url,
      ratio: cachedImageRatio(url) ?? imageRatioOf?.(item) ?? DEFAULT_POSTER_RATIO,
    };
  });
}
