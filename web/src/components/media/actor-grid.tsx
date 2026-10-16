import { AspectRatio, Center, SimpleGrid, Skeleton, Stack, Text, useMatches } from "@mantine/core";
import { useLocation } from "@tanstack/react-router";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Virtuoso, type StateSnapshot, type VirtuosoHandle } from "react-virtuoso";
import type { ActorResponse } from "@/client/types.gen";
import { ActorCard } from "./actor-card";

/**
 * 演员墙的虚拟化状态按历史条目保存: 挂载时还原行尺寸与滚动位置.
 *
 * 路由器在 onRendered 复位窗口滚动, 此刻虚拟列表尚未产生高度, 复位会被浏览器夹回文档顶部;
 * 快照由 Virtuoso 在挂载后自行应用, 不依赖复位时机. 上限只保留最近浏览的若干条目.
 */
const GRID_STATE_CACHE = new Map<string, StateSnapshot>();
const GRID_STATE_LIMIT = 16;

function rememberGridState(key: string, state: StateSnapshot): void {
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

/**
 * 卡片按列数切行: 行是虚拟化的单元, 行高由该行最高的卡片决定.
 *
 * 不使用 `VirtuosoGrid` — 它只测首个渲染元素的尺寸, 并据这一个高度推算全表的位置与文档高度,
 * 卡片高度不等时整表都会算错 (上游 #703 / #1220). 行距写在进行内边距上: margin 不计入
 * Virtuoso 测量的元素尺寸.
 */
function toRows(items: readonly ActorResponse[], cols: number): ActorResponse[][] {
  const rows: ActorResponse[][] = [];
  for (let i = 0; i < items.length; i += cols) {
    rows.push(items.slice(i, i + cols));
  }
  return rows;
}

/** 行键取该行首个演员: 追加分页只补齐末行, 已渲染行的身份不变. */
function actorRowKey(_index: number, row: ActorResponse[]) {
  return row[0].id;
}

function ActorGridRow({ items, cols }: { items: ActorResponse[]; cols: number }) {
  return (
    <div
      style={{
        display: "grid",
        columnGap: "var(--mantine-spacing-md)",
        paddingBottom: "var(--mantine-spacing-md)",
        gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
      }}
    >
      {items.map((item) => (
        <ActorCard key={item.id} item={item} />
      ))}
    </div>
  );
}

/**
 * 非虚拟化的演员网格, 给数量有界且与其它内容同页的场景使用 (虚拟化会接管窗口滚动).
 * 列数与演员墙同源, 不要另写一份断点.
 */
export function ActorCardGrid({ items }: { items: ActorResponse[] }) {
  const cols = useMatches(GRID_COLS);
  return (
    <SimpleGrid cols={cols} spacing="md">
      {items.map((item) => (
        <ActorCard key={item.id} item={item} />
      ))}
    </SimpleGrid>
  );
}

/**
 * 窗口滚动虚拟化, 避免无限滚动把全部卡片留在 DOM.
 *
 * `restoreStateFrom` 只在挂载时生效, 变更时会重新应用快照 —
 * 因此快照在挂载时读取一次, 身份在整个挂载期内保持稳定.
 *
 * 列表没有 Grid 的 `stateChanged`, 快照只能经句柄取; 卸载是唯一需要的时机 (快照只服务于
 * 下一次挂载), 而父组件的清理函数先于子树的 ref 解绑执行, 句柄此时仍可用.
 */
function ActorPosterWall({ items, restoreKey }: { items: ActorResponse[]; restoreKey: string }) {
  const cols = useMatches(GRID_COLS);
  const rows = useMemo(() => toRows(items, cols), [items, cols]);
  const virtuosoRef = useRef<VirtuosoHandle>(null);
  const [restoreState] = useState(() => GRID_STATE_CACHE.get(restoreKey));
  const renderRow = useCallback(
    (_index: number, row: ActorResponse[]) => <ActorGridRow items={row} cols={cols} />,
    [cols],
  );

  useEffect(() => {
    const handle = virtuosoRef;
    return () => {
      handle.current?.getState((state) => rememberGridState(restoreKey, state));
    };
  }, [restoreKey]);

  return (
    <Virtuoso
      ref={virtuosoRef}
      useWindowScroll
      data={rows}
      itemContent={renderRow}
      computeItemKey={actorRowKey}
      restoreStateFrom={restoreState}
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
