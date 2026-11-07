import { AspectRatio, Center, SimpleGrid, Skeleton, Stack, Text } from "@mantine/core";
import { useRatioRefresh } from "@/hooks/use-ratio-refresh";
import { DEFAULT_POSTER_RATIO, posterEntries } from "@/lib/media/poster-image";
import { AdaptivePosterGrid, type PosterGridBodyProps } from "./adaptive-poster-grid";
import { PosterCard } from "./poster-card";

// sm/md 收窄一列: 48em 起导航栏展开, 内容区反而变窄, 沿用桌面列数会让缩略图小一档.
const GRID_COLS = { base: 2, xs: 3, sm: 3, md: 4, lg: 6, xl: 7 };

interface PosterGridProps extends PosterGridBodyProps {
  loading?: boolean;
  emptyMessage?: string;
  /** 列宽相等 (默认, 高度随图片) 或短边锚定 (横版定高、竖版定宽, 视觉大小一致). */
  layout?: "uniform" | "adaptive";
}

export function PosterGrid({
  loading = false,
  emptyMessage,
  layout = "uniform",
  ...body
}: PosterGridProps) {
  if (loading) {
    return (
      <SimpleGrid cols={GRID_COLS} spacing="md">
        {Array.from({ length: 12 }, (_, i) => (
          <Stack key={i} gap={2}>
            <AspectRatio ratio={DEFAULT_POSTER_RATIO}>
              <Skeleton radius="md" />
            </AspectRatio>
            <Skeleton h={12} w="70%" />
          </Stack>
        ))}
      </SimpleGrid>
    );
  }

  if (body.items.length === 0) {
    return (
      <Center py="xl">
        <Text c="dimmed" size="sm">
          {emptyMessage}
        </Text>
      </Center>
    );
  }

  return layout === "adaptive" ? <AdaptivePosterGrid {...body} /> : <UniformPosterGrid {...body} />;
}

/** 列宽相等: 盒子按各自的图片比例定高, 因此横版变矮而不是被裁掉, 列位置不因图片到达而移动. */
function UniformPosterGrid({ actorBirthday, ...source }: PosterGridBodyProps) {
  const refresh = useRatioRefresh();
  return (
    <SimpleGrid cols={GRID_COLS} spacing="md">
      {posterEntries(source).map(({ item, url, ratio }) => (
        <PosterCard
          key={item.id}
          item={item}
          actorBirthday={actorBirthday}
          imageUrl={url}
          imageRatio={ratio}
          onImageRatioChange={refresh}
        />
      ))}
    </SimpleGrid>
  );
}
