import { ActionIcon, Group, SegmentedControl, Text, TextInput, Title } from "@mantine/core";
import { IconSearch, IconTable } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { createFileRoute, stripSearchParams } from "@tanstack/react-router";
import type { ParseKeys } from "i18next";
import { useEffect, useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";
import {
  getFacetOptions,
  listActorsInfiniteQueryKey,
  listActorsQueryKey,
} from "@/client/@tanstack/react-query.gen";
import { listActors, searchActors } from "@/client/sdk.gen";
import { actorAdvancedFilterQueryKey } from "@/lib/advanced-filter-query-keys";
import type { ActorGender, ActorSearchRequest, ActorSortField } from "@/client/types.gen";
import { BrowsePageShell } from "@/components/common/browse-page-shell";
import {
  ActiveExpressionChip,
  ActiveFilterChip,
  BrowseFilterAlerts,
  BrowseFilterPanel,
  BrowseFilterToggle,
  useBrowseFilterPanel,
} from "@/components/media/browse-filter-panel";
import { InfiniteScrollSentinel } from "@/components/common/infinite-scroll-sentinel";
import { ListDefaultActions } from "@/components/common/list-default-actions";
import { PageSizeSelect } from "@/components/common/page-size-select";
import { SortMenu } from "@/components/common/sort-menu";
import { ActorGrid } from "@/components/media/actor-grid";
import { ActorTable } from "@/components/media/actor-table";
import {
  ACTOR_RANGE_FILTERS,
  type ActorFilterKey,
  type ActorFilterPatch,
  actorFilterValuesFromSearch,
  actorListQueryFromSearch,
  actorQuickFilterNodes,
  actorsBrowseSearchSchema,
  clearActorFilterKeys,
  stripDefaultActorGender,
  formatActorRangeChip,
  hasActiveActorFilters,
  hasNonDefaultActorFilters,
  applyActorExpression,
} from "@/lib/actors/browse";
import { useBrowseQueries } from "@/hooks/use-browse-queries";
import { readBrowseFilter } from "@/lib/browse-filter";
import { useResettingState } from "@/hooks/use-resetting-state";
import { exhaustiveRecord } from "@/lib/exhaustive";
import { ACTOR_SORT_FIELDS } from "@/lib/exhaustive-maps";
import { GRID_PREFETCH_MARGIN } from "@/lib/infinite-list";
import { actorListDefaults } from "@/lib/nav-defaults";
import { useUIStore } from "@/stores/ui";

const ACTOR_SORT_I18N_KEY = exhaustiveRecord<ActorSortField>()({
  name: "manage.name",
  count: "manage.count",
  updated_at: "columns.updated",
  has_image: "actors.hasImage",
  birthday: "browse.person.birthday",
  height: "browse.person.height",
  bust: "browse.person.bust",
  waist: "browse.person.waist",
  hip: "browse.person.hip",
  cup: "browse.person.cup",
} as const satisfies Record<ActorSortField, ParseKeys<"metadata">>);

const GENDER_I18N = {
  female: "browse.person.genderFemale",
  male: "browse.person.genderMale",
  unknown: "browse.person.genderUnknown",
} as const satisfies Record<ActorGender, ParseKeys<"metadata">>;

export const Route = createFileRoute("/actors/")({
  validateSearch: actorsBrowseSearchSchema,
  search: {
    middlewares: [
      stripSearchParams({
        view: "grid",
        page: 1,
        sort_by: "count",
        order: "desc",
      }),
      ({ search, next }) => stripDefaultActorGender(next(search)),
    ],
  },
  component: ActorsIndexPage,
});

/** 标签名经单条分类查询, 与片库的芯片同形. */
function ActiveUserTagChip({ tagId, onClear }: { tagId: number; onClear: () => void }) {
  const { t } = useTranslation("metadata");
  const { data } = useQuery(getFacetOptions({ path: { kind: "user_tag", facet_id: tagId } }));
  return (
    <ActiveFilterChip label={`${t("detail.userTags")}: ${data?.name ?? tagId}`} onClear={onClear} />
  );
}

function ActorsIndexPage() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  const { t } = useTranslation(["metadata", "common", "savedQueries"]);
  const listLimit = useUIStore((s) => s.pageSizes.actorsList);
  const filter = useMemo(
    () => readBrowseFilter("actor", search, actorQuickFilterNodes(search)),
    [search],
  );

  const filters = actorFilterValuesFromSearch(search);
  const hasActiveFilters =
    hasActiveActorFilters(filters) || filter.active || filter.presetId != null;
  const hasNonDefaultFilters = hasNonDefaultActorFilters(filters) || filter.presetId != null;

  const [searchInput, setSearchInput] = useResettingState(() => search.q ?? "", search.q);
  const panel = useBrowseFilterPanel(hasNonDefaultFilters || filter.active);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const composingRef = useRef(false);
  const committedSearchRef = useRef(search.q);
  useEffect(() => {
    if (committedSearchRef.current !== search.q) {
      committedSearchRef.current = search.q;
      clearTimeout(debounceRef.current);
    }
    return () => clearTimeout(debounceRef.current);
  }, [search.q]);

  const isList = search.view === "list";
  const listQueryParams = actorListQueryFromSearch(search);
  const postBody: Omit<ActorSearchRequest, "offset" | "limit"> = {
    version: 1,
    expression: filter.expression,
    search: listQueryParams.search,
    sort_by: search.sort_by,
    order: search.order,
  };
  const { listQuery, gridQuery, activeQuery } = useBrowseQueries({
    isList,
    page: search.page,
    listLimit,
    enabled: !filter.blocked,
    queryKey: ({ offset, limit }) => {
      if (filter.active) {
        return actorAdvancedFilterQueryKey(
          offset === undefined ? "grid" : "list",
          search.f,
          postBody,
          limit,
          offset,
        );
      }
      const key = offset === undefined ? listActorsInfiniteQueryKey : listActorsQueryKey;
      return key({ query: { ...listQueryParams, offset, limit } });
    },
    loadPage: async (page, signal) => {
      const { data } = filter.active
        ? await searchActors({ body: { ...postBody, ...page }, signal, throwOnError: true })
        : await listActors({ query: { ...listQueryParams, ...page }, signal, throwOnError: true });
      return data;
    },
  });
  const showResults = !activeQuery.isError || activeQuery.data != null;

  const listItems = listQuery.data?.items ?? [];
  const listTotal = listQuery.data?.total ?? 0;
  const gridItems = useMemo(
    () => gridQuery.data?.pages.flatMap((p) => p.items) ?? [],
    [gridQuery.data],
  );
  const gridTotal = gridQuery.data?.pages[0]?.total ?? 0;
  const total = isList ? listTotal : gridTotal;

  function handleSearchChange(v: string) {
    setSearchInput(v);
    if (composingRef.current) return;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      void navigate({
        search: (prev) => ({ ...prev, q: v || undefined, page: 1 }),
      });
    }, 300);
  }

  function onSort(field: ActorSortField) {
    void navigate({
      search: (prev) => {
        if (prev.sort_by === field) {
          const nextOrder = prev.order === "asc" ? "desc" : "asc";
          return { ...prev, sort_by: field, order: nextOrder, page: 1 };
        }
        return {
          ...prev,
          sort_by: field,
          order:
            field === "count" || field === "has_image" || field === "updated_at" ? "desc" : "asc",
          page: 1,
        };
      },
    });
  }

  function applyFilterPatch(patch: ActorFilterPatch) {
    void navigate({
      search: (prev) => ({ ...prev, ...patch, page: 1 }),
    });
  }

  function clearFilterKeys(keys: readonly ActorFilterKey[]) {
    void navigate({
      search: (prev) => clearActorFilterKeys(prev, keys),
    });
  }

  function applyExpression(f: string | undefined) {
    void navigate({
      search: (prev) => applyActorExpression(prev, f),
    });
  }

  function clearExpression() {
    void navigate({ search: (prev) => ({ ...prev, f: undefined, page: 1 }) });
  }

  return (
    <BrowsePageShell
      fill={isList}
      filterDrawerFullHeight
      title={<Title order={2}>{t("actors.title")}</Title>}
      actions={<ListDefaultActions update={{ key: "actors", value: actorListDefaults(search) }} />}
      viewSwitch={
        <SegmentedControl
          value={search.view}
          onChange={(v) =>
            void navigate({
              search: (prev) => ({
                ...prev,
                view: v === "list" ? "list" : "grid",
                page: 1,
              }),
            })
          }
          data={[
            { value: "grid", label: t("view.grid") },
            { value: "list", label: t("view.list") },
          ]}
        />
      }
      summary={
        !filter.blocked && !activeQuery.isError ? (
          <Text size="sm" c="dimmed">
            {t("common:pagination.totalItems", { count: total })}
          </Text>
        ) : undefined
      }
      search={
        <TextInput
          value={searchInput}
          onChange={(e) => handleSearchChange(e.currentTarget.value)}
          onCompositionStart={() => {
            composingRef.current = true;
            clearTimeout(debounceRef.current);
          }}
          onCompositionEnd={(e) => {
            composingRef.current = false;
            handleSearchChange(e.currentTarget.value);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.nativeEvent.isComposing) {
              clearTimeout(debounceRef.current);
              void navigate({
                search: (prev) => ({ ...prev, q: searchInput.trim() || undefined, page: 1 }),
              });
            }
          }}
          placeholder={t("actors.searchPlaceholder")}
          leftSection={<IconSearch size={16} />}
          w="100%"
        />
      }
      extras={
        <>
          <BrowseFilterToggle panel={panel} active={hasNonDefaultFilters || filter.active} />
          {!isList && (
            <SortMenu
              options={ACTOR_SORT_FIELDS.map((f) => ({
                value: f,
                label: t(ACTOR_SORT_I18N_KEY[f]),
              }))}
              sortBy={search.sort_by}
              order={search.order}
              defaultSortBy="count"
              defaultOrder="desc"
              onChange={(nextSortBy, nextOrder) =>
                void navigate({
                  search: (prev) => ({
                    ...prev,
                    sort_by: nextSortBy,
                    order: nextOrder,
                    page: 1,
                  }),
                })
              }
            />
          )}
        </>
      }
      pageSize={
        isList ? (
          <PageSizeSelect
            sizeKey="actorsList"
            onChanged={() => void navigate({ search: (prev) => ({ ...prev, page: 1 }) })}
          />
        ) : undefined
      }
      filterPanel={<BrowseFilterPanel panel={panel} filter={filter} onApply={applyExpression} />}
    >
      <BrowseFilterAlerts
        filter={filter}
        failed={activeQuery.isError}
        onRetry={() => void activeQuery.refetch()}
        onClear={clearExpression}
      />
      {hasActiveFilters && (
        <Group gap="xs">
          {search.saved_query_id != null && (
            <Group gap={4} wrap="nowrap">
              <ActiveFilterChip
                label={`${t("common:nav.savedQueries")}: #${search.saved_query_id}`}
                onClear={() =>
                  void navigate({
                    search: (prev) => ({ ...prev, saved_query_id: undefined, page: 1 }),
                  })
                }
              />
              <ActionIcon
                size="sm"
                variant="subtle"
                component="a"
                href={`/saved-queries/${search.saved_query_id}`}
                target="_blank"
                rel="noreferrer"
                aria-label={t("savedQueries:openData")}
              >
                <IconTable size={14} />
              </ActionIcon>
            </Group>
          )}
          <ActiveExpressionChip filter={filter} onClear={clearExpression} />
          {filters.gender.map((g) => (
            <ActiveFilterChip
              key={g}
              label={t(GENDER_I18N[g])}
              onClear={() => applyFilterPatch({ gender: filters.gender.filter((x) => x !== g) })}
            />
          ))}
          {ACTOR_RANGE_FILTERS.map((range) => {
            const min = filters[range.min];
            const max = filters[range.max];
            if (min == null && max == null) return null;
            return (
              <ActiveFilterChip
                key={range.min}
                label={formatActorRangeChip(t(range.labelKey), min, max)}
                onClear={() => clearFilterKeys([range.min, range.max])}
              />
            );
          })}
          {filters.birthplace != null && (
            <ActiveFilterChip
              label={`${t("browse.person.birthplace")}: ${filters.birthplace}`}
              onClear={() => clearFilterKeys(["birthplace"])}
            />
          )}
          {filters.user_tag_id != null && (
            <ActiveUserTagChip
              tagId={filters.user_tag_id}
              onClear={() => applyFilterPatch({ user_tag_id: undefined })}
            />
          )}
          {filters.has_person != null && (
            <ActiveFilterChip
              label={
                filters.has_person === "true"
                  ? t("actors.filterHasPerson")
                  : t("actors.filterNoPerson")
              }
              onClear={() => applyFilterPatch({ has_person: undefined })}
            />
          )}
          {filters.has_image != null && (
            <ActiveFilterChip
              label={
                filters.has_image === "true"
                  ? t("actors.filterHasImage")
                  : t("actors.filterNoImage")
              }
              onClear={() => applyFilterPatch({ has_image: undefined })}
            />
          )}
        </Group>
      )}

      {filter.blocked || !showResults ? null : isList ? (
        <ActorTable
          key={listLimit}
          items={listItems}
          isLoading={listQuery.isLoading}
          total={listTotal}
          page={search.page}
          sortBy={search.sort_by}
          order={search.order}
          onPageChange={(page) => void navigate({ search: (prev) => ({ ...prev, page }) })}
          onSort={onSort}
        />
      ) : (
        <>
          <ActorGrid
            items={gridItems}
            loading={gridQuery.isLoading && gridItems.length === 0}
            emptyMessage={t("actors.empty")}
          />
          {gridItems.length > 0 && (
            <InfiniteScrollSentinel
              hasNextPage={Boolean(gridQuery.hasNextPage)}
              isFetchingNextPage={gridQuery.isFetchingNextPage}
              fetchNextPage={gridQuery.fetchNextPage}
              prefetchMargin={GRID_PREFETCH_MARGIN}
              loadedCount={gridItems.length}
              loadedLabel={t("common:pagination.loadedOfTotal", {
                loaded: gridItems.length,
                total: gridTotal,
              })}
            />
          )}
        </>
      )}
    </BrowsePageShell>
  );
}
