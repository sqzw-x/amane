import { ActionIcon, Badge, Group, SegmentedControl, Text, TextInput, Title } from "@mantine/core";
import { IconFilter, IconSearch, IconTable, IconX } from "@tabler/icons-react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { createFileRoute, stripSearchParams } from "@tanstack/react-router";
import type { ParseKeys } from "i18next";
import { useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { listActorsInfiniteOptions, listActorsOptions } from "@/client/@tanstack/react-query.gen";
import type { ActorGender, ActorSortField } from "@/client/types.gen";
import { BrowsePageShell } from "@/components/common/browse-page-shell";
import { HintedActionIcon } from "@/components/common/hinted-action-icon";
import { InfiniteScrollSentinel } from "@/components/common/infinite-scroll-sentinel";
import { PageSizeSelect } from "@/components/common/page-size-select";
import { SortMenu } from "@/components/common/sort-menu";
import { ActorFilterControls } from "@/components/media/actor-filter-controls";
import { ActorGrid } from "@/components/media/actor-grid";
import { ActorTable } from "@/components/media/actor-table";
import {
  ACTOR_RANGE_FILTERS,
  type ActorFilterKey,
  type ActorFilterPatch,
  type ActorFilterValues,
  actorFilterValuesFromSearch,
  actorListQueryFromSearch,
  actorsBrowseSearchSchema,
  clearActorFilterKeys,
  DEFAULT_ACTOR_GENDER_FILTER,
  formatActorRangeChip,
  hasActiveActorFilters,
  hasNonDefaultActorFilters,
  mergeActorFilterPatch,
  rangeValue,
  replaceActorFilters,
} from "@/lib/actors/browse";
import { exhaustiveRecord } from "@/lib/exhaustive";
import { ACTOR_SORT_FIELDS } from "@/lib/exhaustive-maps";
import { nextOffsetPageParam } from "@/lib/infinite-list";
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
        gender: [...DEFAULT_ACTOR_GENDER_FILTER],
      }),
    ],
  },
  component: ActorsIndexPage,
});

/** 头像墙每批条数 - 与片库 grid 一样固定 chunk, 不暴露页大小控件. */
const GRID_CHUNK = 30;

function ActiveFilterChip({ label, onClear }: { label: string; onClear: () => void }) {
  return (
    <Group gap={4} wrap="nowrap">
      <Badge variant="outline">{label}</Badge>
      <ActionIcon size="sm" variant="subtle" color="gray" onClick={onClear} aria-label="clear">
        <IconX size={14} />
      </ActionIcon>
    </Group>
  );
}

function ActorsIndexPage() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  const { t } = useTranslation(["metadata", "common", "agent"]);
  const listLimit = useUIStore((s) => s.pageSizes.actorsList);

  const filters = actorFilterValuesFromSearch(search);
  const hasActiveFilters = hasActiveActorFilters(filters) || search.saved_query_id != null;
  const hasNonDefaultFilters = hasNonDefaultActorFilters(filters) || search.saved_query_id != null;

  const [searchInput, setSearchInput] = useState(search.q ?? "");
  const [advancedOpen, setAdvancedOpen] = useState(hasNonDefaultFilters);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const isList = search.view === "list";
  const offset = (search.page - 1) * listLimit;
  const listQueryParams = actorListQueryFromSearch(search);

  // list 仅 list 视图订阅; grid 不再并行预热, 避免和无限滚动/头像抢连接.
  const listQuery = useQuery({
    ...listActorsOptions({
      query: {
        ...listQueryParams,
        offset,
        limit: listLimit,
      },
    }),
    enabled: isList,
  });

  const gridQuery = useInfiniteQuery({
    ...listActorsInfiniteOptions({
      query: {
        ...listQueryParams,
        limit: GRID_CHUNK,
      },
    }),
    enabled: !isList,
    initialPageParam: 0,
    getNextPageParam: nextOffsetPageParam,
  });

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
      search: (prev) => mergeActorFilterPatch(prev, patch),
    });
  }

  function applyFilters(next: ActorFilterValues) {
    void navigate({
      search: (prev) => replaceActorFilters(prev, next),
    });
  }

  function clearFilterKeys(keys: readonly ActorFilterKey[]) {
    void navigate({
      search: (prev) => clearActorFilterKeys(prev, keys),
    });
  }

  return (
    <BrowsePageShell
      fill={isList}
      title={<Title order={2}>{t("actors.title")}</Title>}
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
        <Text size="sm" c="dimmed">
          {t("common:pagination.totalItems", { count: total })}
        </Text>
      }
      search={
        <TextInput
          value={searchInput}
          onChange={(e) => handleSearchChange(e.currentTarget.value)}
          placeholder={t("actors.searchPlaceholder")}
          leftSection={<IconSearch size={16} />}
          w="100%"
        />
      }
      extras={
        <>
          <HintedActionIcon
            variant={advancedOpen || hasNonDefaultFilters ? "filled" : "default"}
            size={36}
            onClick={() => setAdvancedOpen((v) => !v)}
            label={t("search.advanced")}
          >
            <IconFilter size={16} />
          </HintedActionIcon>
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
    >
      <ActorFilterControls opened={advancedOpen} committed={filters} onApply={applyFilters} />

      {hasActiveFilters && (
        <Group gap="xs">
          {search.saved_query_id != null && (
            <Group gap={4} wrap="nowrap">
              <ActiveFilterChip
                label={`${t("common:nav.agent")}: #${search.saved_query_id}`}
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
                aria-label={t("agent:openData")}
              >
                <IconTable size={14} />
              </ActionIcon>
            </Group>
          )}
          {filters.gender.map((g) => (
            <ActiveFilterChip
              key={g}
              label={t(GENDER_I18N[g])}
              onClear={() => applyFilterPatch({ gender: filters.gender.filter((x) => x !== g) })}
            />
          ))}
          {ACTOR_RANGE_FILTERS.map((range) => {
            const min = rangeValue(filters, range.min);
            const max = rangeValue(filters, range.max);
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

      {isList ? (
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
