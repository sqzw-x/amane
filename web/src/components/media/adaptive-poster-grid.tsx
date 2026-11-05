import { Box, Group } from "@mantine/core";
import { useElementSize } from "@mantine/hooks";
import { useRatioRefresh } from "@/hooks/use-ratio-refresh";
import { adaptiveShortEdge, adaptiveTileWidth, layoutAdaptiveRows } from "@/lib/media/adaptive";
import { posterEntries, type PosterEntriesSource } from "@/lib/media/poster-image";
import { PosterCard } from "./poster-card";

/** 与列宽相等的栅格 (`SimpleGrid` 的 md 间距) 对齐. */
const GAP_PX = 16;

/** 两种栅格共用的入参. */
export interface PosterGridBodyProps extends PosterEntriesSource {
  /** 传入时海报显示演员出演时年龄. */
  actorBirthday?: string | null;
}

/**
 * 短边锚定的自适应栅格: 每张图的短边一样长, 横版更宽、竖版更高, 行内高度参差而文字随图.
 *
 * 行以「行首条目 id」作 key, 追加一页时既有行不重挂载; 卡片自身 memo, 入参未变的卡片跳过重渲染.
 * 图片比例要等加载完才知道, 首屏先按竖版比例排, 图片陆续到达后重排 (按帧合并, 见 `useRatioRefresh`).
 */
export function AdaptivePosterGrid({ actorBirthday, ...source }: PosterGridBodyProps) {
  const { ref, width } = useElementSize<HTMLDivElement>();
  const requestLayout = useRatioRefresh();

  const entries = posterEntries(source);
  const shortEdge = adaptiveShortEdge(width);
  const rows = layoutAdaptiveRows(
    entries.map((entry) => entry.ratio),
    width,
    GAP_PX,
    shortEdge,
  );

  return (
    <Box ref={ref}>
      {rows.map((row) => {
        const rowEntries = entries.slice(row.start, row.start + row.count);
        return (
          <Group
            key={rowEntries[0].item.id}
            gap={GAP_PX}
            wrap="nowrap"
            align="flex-start"
            mb={GAP_PX}
          >
            {rowEntries.map(({ item, url, ratio }) => (
              <Box
                key={item.id}
                style={{ width: adaptiveTileWidth(ratio, shortEdge, row.scale), flexShrink: 0 }}
              >
                <PosterCard
                  item={item}
                  actorBirthday={actorBirthday}
                  imageUrl={url}
                  imageRatio={ratio}
                  onImageRatioChange={requestLayout}
                />
              </Box>
            ))}
          </Group>
        );
      })}
    </Box>
  );
}
