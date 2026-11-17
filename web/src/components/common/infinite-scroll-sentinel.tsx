import { Center, Loader, Text } from "@mantine/core";
import { useEffect, useRef } from "react";
import { useLatestRef } from "@/hooks/use-latest-ref";

interface InfiniteScrollSentinelProps {
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  fetchNextPage: () => unknown;
  /**
   * 还有更多页时展示的进度文案; 全部加载完不渲染任何文案.
   *
   * 总条数在列表页顶部已有展示, 列表末尾再报一次总数没有信息量.
   */
  loadedLabel?: string;
  /**
   * 提前取下一页的距离, 即观察器的下边距.
   *
   * 默认 200px 只够快速滑动 0.1s: 请求还没回来就已滚到底. 海报栅格改成一屏, 让请求在仍有
   * 内容可看时发出.
   */
  prefetchMargin?: string;
  /**
   * 已加载条数, 变化后重建观察器.
   *
   * 交叉状态不变时观察器不再回调: 快速滑动会让哨兵一次跳进预取区深处, 追加一页后仍在区内,
   * 于是停在列表底部不再取下一页. 重建后按当前状态回调一次, 由它补齐缓冲.
   */
  loadedCount?: number;
}

export function InfiniteScrollSentinel({
  hasNextPage,
  isFetchingNextPage,
  fetchNextPage,
  loadedLabel,
  prefetchMargin = "200px",
  loadedCount,
}: InfiniteScrollSentinelProps) {
  const ref = useRef<HTMLDivElement>(null);
  const fetchingRef = useLatestRef(isFetchingNextPage);
  const fetchRef = useLatestRef(fetchNextPage);

  useEffect(() => {
    const el = ref.current;
    if (!el || !hasNextPage) return;

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting) && !fetchingRef.current) {
          fetchRef.current();
        }
      },
      { rootMargin: prefetchMargin },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [fetchRef, fetchingRef, hasNextPage, loadedCount, prefetchMargin]);

  // 没有下一页时不占高度: 它挂在每个展开的目录里, 留白会随嵌套层数累加.
  // 元素本身要保留, IntersectionObserver 得挂在它上面才会在滚到底时触发取下一页.
  if (!hasNextPage && !isFetchingNextPage) {
    return <Center ref={ref} />;
  }
  return (
    <Center ref={ref} py="md">
      {isFetchingNextPage ? (
        <Loader size="sm" />
      ) : loadedLabel ? (
        <Text size="sm" c="dimmed">
          {loadedLabel}
        </Text>
      ) : null}
    </Center>
  );
}
