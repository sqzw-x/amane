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
}

export function InfiniteScrollSentinel({
  hasNextPage,
  isFetchingNextPage,
  fetchNextPage,
  loadedLabel,
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
      { rootMargin: "200px" },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [fetchRef, fetchingRef, hasNextPage]);

  return (
    <Center ref={ref} py="md">
      {isFetchingNextPage ? (
        <Loader size="sm" />
      ) : hasNextPage && loadedLabel ? (
        <Text size="sm" c="dimmed">
          {loadedLabel}
        </Text>
      ) : null}
    </Center>
  );
}
