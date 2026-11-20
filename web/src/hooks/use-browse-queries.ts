import { useInfiniteQuery, useQuery, type QueryKey } from "@tanstack/react-query";
import { BROWSE_GC_TIME, nextOffsetPageParam } from "@/lib/infinite-list";

const GRID_LIMIT = 30;
type Page = { offset: number; limit: number };

export function useBrowseQueries<T extends { items: readonly unknown[]; total: number }>({
  isList,
  page,
  listLimit,
  enabled,
  queryKey,
  loadPage,
}: {
  isList: boolean;
  page: number;
  listLimit: number;
  enabled: boolean;
  queryKey: (page: { limit: number; offset?: number }) => QueryKey;
  loadPage: (page: Page, signal: AbortSignal) => Promise<T>;
}) {
  const listPage = { offset: (page - 1) * listLimit, limit: listLimit };
  const listQuery = useQuery({
    queryKey: queryKey(listPage),
    queryFn: ({ signal }) => loadPage(listPage, signal),
    enabled: isList && enabled,
  });
  const gridQuery = useInfiniteQuery({
    queryKey: queryKey({ limit: GRID_LIMIT }),
    queryFn: ({ pageParam, signal }) => loadPage({ offset: pageParam, limit: GRID_LIMIT }, signal),
    enabled: !isList && enabled,
    initialPageParam: 0,
    getNextPageParam: nextOffsetPageParam,
    gcTime: BROWSE_GC_TIME,
  });
  return { listQuery, gridQuery, activeQuery: isList ? listQuery : gridQuery };
}
