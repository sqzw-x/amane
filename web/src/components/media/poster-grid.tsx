import { AspectRatio, Center, SimpleGrid, Skeleton, Stack, Text } from "@mantine/core";
import type { MetadataResponse } from "@/client/types.gen";
import { PosterCard } from "./poster-card";

// sm/md 收窄一列: 48em 起导航栏展开, 内容区反而变窄, 沿用桌面列数会让缩略图小一档.
const GRID_COLS = { base: 2, xs: 3, sm: 3, md: 4, lg: 6, xl: 7 };

interface PosterGridProps {
  items: MetadataResponse[];
  loading?: boolean;
  emptyMessage?: string;
  /** 传入时海报显示演员出演时年龄. */
  actorBirthday?: string | null;
}

export function PosterGrid({
  items,
  loading = false,
  emptyMessage,
  actorBirthday,
}: PosterGridProps) {
  if (loading) {
    return (
      <SimpleGrid cols={GRID_COLS} spacing="md">
        {Array.from({ length: 12 }, (_, i) => (
          <Stack key={i} gap={2}>
            <AspectRatio ratio={0.7}>
              <Skeleton radius="md" />
            </AspectRatio>
            <Skeleton h={12} w="70%" />
          </Stack>
        ))}
      </SimpleGrid>
    );
  }

  if (items.length === 0) {
    return (
      <Center py="xl">
        <Text c="dimmed" size="sm">
          {emptyMessage}
        </Text>
      </Center>
    );
  }

  return (
    <SimpleGrid cols={GRID_COLS} spacing="md">
      {items.map((item) => (
        <PosterCard key={item.id} item={item} actorBirthday={actorBirthday} />
      ))}
    </SimpleGrid>
  );
}
