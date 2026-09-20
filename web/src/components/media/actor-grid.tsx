import { AspectRatio, Center, SimpleGrid, Skeleton, Stack, Text, useMatches } from "@mantine/core";
import { useLocation } from "@tanstack/react-router";
import { forwardRef, memo, useCallback, useState } from "react";
import {
  VirtuosoGrid,
  type GridItemProps,
  type GridListProps,
  type GridStateSnapshot,
} from "react-virtuoso";
import type { ActorResponse } from "@/client/types.gen";
import { ActorCard } from "./actor-card";

/**
 * 演员墙的虚拟化状态按历史条目保存: 挂载时还原格子尺寸与滚动位置.
 *
 * 路由器在 onRendered 复位窗口滚动, 此刻虚拟列表尚未产生高度, 复位会被浏览器夹回文档顶部;
 * 快照由 Virtuoso 在挂载后自行应用, 不依赖复位时机. 上限只保留最近浏览的若干条目.
 */
const GRID_STATE_CACHE = new Map<string, GridStateSnapshot>();
const GRID_STATE_LIMIT = 16;

function rememberGridState(key: string, state: GridStateSnapshot): void {
  GRID_STATE_CACHE.delete(key);
  GRID_STATE_CACHE.set(key, state);
  if (GRID_STATE_CACHE.size > GRID_STATE_LIMIT) {
    const oldest = GRID_STATE_CACHE.keys().next().value;
    if (oldest !== undefined) GRID_STATE_CACHE.delete(oldest);
  }
}

// sm/md 收窄一列: 48em 起导航栏展开, 内容区反而变窄, 沿用桌面列数会让缩略图小一档.
const GRID_COLS = { base: 2, xs: 3, sm: 3, md: 4, lg: 6, xl: 7 } as const;

interface ActorGridProps {
  items: ActorResponse[];
  loading?: boolean;
  emptyMessage?: string;
}

const ActorGridList = forwardRef<HTMLDivElement, GridListProps>(function ActorGridList(
  { style, children, className, ...props },
  ref,
) {
  const cols = useMatches(GRID_COLS);
  return (
    <div
      ref={ref}
      className={className}
      style={{
        display: "grid",
        gap: "var(--mantine-spacing-md)",
        gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
        ...style,
      }}
      {...props}
    >
      {children}
    </div>
  );
});

const ActorGridItem = forwardRef<HTMLDivElement, GridItemProps>(function ActorGridItem(
  { children, style, ...props },
  ref,
) {
  return (
    <div ref={ref} {...props} style={{ ...style, minWidth: 0 }}>
      {children}
    </div>
  );
});

const GRID_COMPONENTS = { List: ActorGridList, Item: ActorGridItem };

function renderActorCard(_index: number, item: ActorResponse) {
  return <ActorCard item={item} />;
}

function actorCardKey(_index: number, item: ActorResponse) {
  return item.id;
}

/**
 * 窗口滚动虚拟化, 避免无限滚动把全部卡片留在 DOM.
 *
 * `restoreStateFrom` 只在挂载时生效, 变更时会重新应用快照 —
 * 因此快照在挂载时读取一次, 身份在整个挂载期内保持稳定.
 */
function ActorPosterWall({ items, restoreKey }: { items: ActorResponse[]; restoreKey: string }) {
  const [restoreState] = useState(() => GRID_STATE_CACHE.get(restoreKey));
  const rememberState = useCallback(
    (state: GridStateSnapshot) => rememberGridState(restoreKey, state),
    [restoreKey],
  );

  return (
    <VirtuosoGrid
      useWindowScroll
      data={items}
      components={GRID_COMPONENTS}
      itemContent={renderActorCard}
      computeItemKey={actorCardKey}
      restoreStateFrom={restoreState}
      stateChanged={rememberState}
      increaseViewportBy={600}
    />
  );
}

export const ActorGrid = memo(function ActorGrid({
  items,
  loading = false,
  emptyMessage,
}: ActorGridProps) {
  // 历史条目 key 与路由器自身的滚动缓存同源: 新进入的条目没有快照, 不会继承上一次的浏览位置.
  const restoreKey = useLocation({ select: (l) => l.state.key ?? l.href });

  if (loading) {
    return (
      <SimpleGrid cols={GRID_COLS} spacing="md">
        {Array.from({ length: 12 }, (_, i) => (
          <Stack key={i} gap={2}>
            <AspectRatio ratio={0.75}>
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

  return <ActorPosterWall items={items} restoreKey={restoreKey} />;
});
