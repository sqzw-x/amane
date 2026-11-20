import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Group,
  SegmentedControl,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import {
  IconArrowLeft,
  IconArrowRight,
  IconSearch,
  IconSparkles,
  IconTable,
  IconX,
} from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { createFileRoute, stripSearchParams } from "@tanstack/react-router";
import { useEffect, useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";
import {
  getFacetOptions,
  listLibrariesOptions,
  listMetadataInfiniteQueryKey,
  listMetadataQueryKey,
  semanticStatusOptions,
} from "@/client/@tanstack/react-query.gen";
import { listMetadata, searchMetadata } from "@/client/sdk.gen";
import { metadataAdvancedFilterQueryKey } from "@/lib/advanced-filter-query-keys";
import type {
  EmbeddingKind,
  FacetKind,
  MetadataSearchRequest,
  MetadataSortField,
  SemanticHitInfo,
} from "@/client/types.gen";
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
import { FacetBadge } from "@/components/media/facet-badge";
import { MetaTable } from "@/components/media/meta-table";
import { PosterGrid } from "@/components/media/poster-grid";
import { activeFacetFilters, type FacetFilters, removeFacetId } from "@/lib/facets";
import { useBrowseQueries } from "@/hooks/use-browse-queries";
import { readBrowseFilter } from "@/lib/browse-filter";
import { useResettingState } from "@/hooks/use-resetting-state";
import { EMBEDDING_KINDS } from "@/lib/exhaustive-maps";
import { GRID_PREFETCH_MARGIN } from "@/lib/infinite-list";
import {
  metaSearchSchema,
  METADATA_SORT_OPTIONS,
  metadataQuickFilterNodes,
  withoutMetadataQuickFilters,
} from "@/lib/media/browse";
import { metaListDefaults } from "@/lib/nav-defaults";
import { useUIStore } from "@/stores/ui";
import classes from "./meta.module.css";

const SEMANTIC_KIND_OPTIONS = ["all", ...EMBEDDING_KINDS] as const;

/** 分段控件回传 string; "all" 与未知值均视为缺省 (封面与剧照都参与). */
function toEmbeddingKind(value: string): EmbeddingKind | undefined {
  return EMBEDDING_KINDS.find((kind) => kind === value);
}

export const Route = createFileRoute("/meta/")({
  validateSearch: metaSearchSchema,
  search: { middlewares: [stripSearchParams({ view: "grid", page: 1 })] },
  component: MetaIndexPage,
});

function parseBoolean(value: "true" | "false" | undefined) {
  if (value === "true") return true;
  if (value === "false") return false;
  return undefined;
}

function ActiveFacetChip({
  kind,
  id,
  onClear,
}: {
  kind: FacetKind;
  id: number;
  onClear: () => void;
}) {
  const { data } = useQuery(getFacetOptions({ path: { kind, facet_id: id } }));
  const { t } = useTranslation(["metadata", "common"]);
  return (
    <Group gap={4} wrap="nowrap">
      <FacetBadge
        kind={kind}
        id={id}
        name={`${t(`browse.kinds.${kind}`)}: ${data?.name ?? `#${id}`}`}
        variant="outline"
      />
      <ActionIcon
        size="sm"
        variant="subtle"
        color="gray"
        onClick={onClear}
        aria-label={`${t("common:actions.remove")} ${t(`browse.kinds.${kind}`)}: ${data?.name ?? `#${id}`}`}
      >
        <IconX size={14} />
      </ActionIcon>
    </Group>
  );
}

function MetaIndexPage() {
  const { t } = useTranslation(["metadata", "common", "savedQueries"]);
  const search = Route.useSearch();
  const semanticMode = search.semantic === 1;
  const semanticQuery = semanticMode && search.q ? search.q : undefined;
  const semanticActive = semanticQuery != null;
  const navigate = Route.useNavigate();
  const listLimit = useUIStore((s) => s.pageSizes.metaList);
  const filter = useMemo(
    () => readBrowseFilter("metadata", search, metadataQuickFilterNodes(search)),
    [search],
  );

  const { mosaic, definition, content_type: contentType } = search;
  const hasFiles = parseBoolean(search.has_files);
  const vr = parseBoolean(search.vr);
  const hasSubtitle = parseBoolean(search.has_subtitle);
  const uncensored = parseBoolean(search.uncensored);
  const libraryIds = search.library_id ?? [];
  const { data: librariesData } = useQuery(listLibrariesOptions());
  const libraries = librariesData?.items ?? [];
  const libraryNameById = new Map(libraries.map((library) => [library.id, library.name]));
  const legacyPanelActive =
    hasFiles !== undefined ||
    vr !== undefined ||
    hasSubtitle !== undefined ||
    uncensored !== undefined ||
    mosaic != null ||
    definition != null ||
    contentType != null ||
    libraryIds.length > 0;
  const [searchInput, setSearchInput] = useResettingState(() => search.q ?? "", search.q);
  const panel = useBrowseFilterPanel(legacyPanelActive || filter.active);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const composingRef = useRef(false);
  const committedSearchRef = useRef({ q: search.q, semantic: semanticMode });
  useEffect(() => {
    if (
      committedSearchRef.current.q !== search.q ||
      committedSearchRef.current.semantic !== semanticMode
    ) {
      committedSearchRef.current = { q: search.q, semantic: semanticMode };
      clearTimeout(debounceRef.current);
    }
    return () => clearTimeout(debounceRef.current);
  }, [search.q, semanticMode]);

  const filters: FacetFilters = {
    actor_id: search.actor_id,
    director_id: search.director_id,
    tag_id: search.tag_id,
    studio_id: search.studio_id,
    publisher_id: search.publisher_id,
    series_id: search.series_id,
    user_tag_id: search.user_tag_id,
  };

  const filterParams = {
    library_id: libraryIds.length > 0 ? libraryIds : undefined,
    has_files: hasFiles,
    vr,
    has_subtitle: hasSubtitle,
    uncensored,
    mosaic,
    definition,
    content_type: contentType,
    ...filters,
    saved_query_id: filter.presetId,
  };

  const commonQuery = {
    search: semanticActive ? undefined : search.q || undefined,
    sort_by: semanticActive ? undefined : search.sort_by,
    order: semanticActive ? undefined : search.order,
    semantic_query: semanticQuery,
    semantic_kind: semanticActive ? (search.kind ?? "all") : undefined,
  } satisfies Partial<MetadataSearchRequest>;
  const listQueryParams = { ...filterParams, ...commonQuery };
  const postBody = {
    version: 1,
    expression: filter.expression,
    ...commonQuery,
  } satisfies MetadataSearchRequest;

  const isList = search.view === "list";
  const { data: semanticStatus, refetch: refetchSemanticStatus } =
    useQuery(semanticStatusOptions());
  const semanticAvailable = semanticStatus?.available === true;

  // 进入语义模式时重新获取: 索引任务可能刚在别处完成, 缓存的状态会过期.
  useEffect(() => {
    if (semanticMode) void refetchSemanticStatus();
  }, [semanticMode, refetchSemanticStatus]);

  const semanticNotice =
    !semanticActive || semanticStatus == null
      ? null
      : !semanticStatus.enabled
        ? t("semantic.disabled")
        : !semanticStatus.search_ready
          ? t("semantic.modelMissing", {
              size: Math.round((semanticStatus.download_size ?? 0) / 1024 / 1024),
            })
          : !semanticStatus.has_embeddings
            ? t("semantic.noEmbeddings")
            : null;
  // 语义状态未返回或不可检索时不发送列表请求, 避免先按普通排序渲染一屏.
  const listEnabled = !semanticActive || (semanticStatus != null && semanticNotice == null);

  const { listQuery, gridQuery, activeQuery } = useBrowseQueries({
    isList,
    page: search.page,
    listLimit,
    enabled: !filter.blocked && listEnabled,
    queryKey: ({ offset, limit }) => {
      if (filter.active) {
        return metadataAdvancedFilterQueryKey(
          offset === undefined ? "grid" : "list",
          search.f,
          postBody,
          limit,
          offset,
        );
      }
      const key = offset === undefined ? listMetadataInfiniteQueryKey : listMetadataQueryKey;
      return key({ query: { ...listQueryParams, offset, limit } });
    },
    loadPage: async (page, signal) => {
      const { data } = filter.active
        ? await searchMetadata({ body: { ...postBody, ...page }, signal, throwOnError: true })
        : await listMetadata({
            query: { ...listQueryParams, ...page },
            signal,
            throwOnError: true,
          });
      return data;
    },
  });
  const showResults = !activeQuery.isError || activeQuery.data != null;

  const gridItems = useMemo(
    () => gridQuery.data?.pages.flatMap((p) => p.items) ?? [],
    [gridQuery.data],
  );
  const total = isList ? (listQuery.data?.total ?? 0) : (gridQuery.data?.pages[0]?.total ?? 0);
  const semanticHits = useMemo(() => {
    const map = new Map<number, SemanticHitInfo>();
    if (!semanticActive) return map;
    if (isList) {
      for (const hit of listQuery.data?.semantic_hits ?? []) map.set(hit.id, hit);
    } else {
      for (const page of gridQuery.data?.pages ?? []) {
        for (const hit of page.semantic_hits ?? []) map.set(hit.id, hit);
      }
    }
    return map;
  }, [semanticActive, isList, listQuery.data, gridQuery.data]);

  function handleSearchChange(v: string) {
    setSearchInput(v);
    if (semanticMode || composingRef.current) return;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      void navigate({ search: (prev) => ({ ...prev, q: v || undefined, page: 1 }) });
    }, 300);
  }

  function submitSearch() {
    clearTimeout(debounceRef.current);
    const value = searchInput.trim();
    void navigate({ search: (prev) => ({ ...prev, q: value || undefined, page: 1 }) });
  }

  function toggleSemantic() {
    clearTimeout(debounceRef.current);
    // 切换搜索方式时清空输入, 避免将关键词作为画面描述提交, 或反向提交.
    setSearchInput("");
    void navigate({
      search: (prev) => ({
        ...prev,
        q: undefined,
        semantic: semanticMode ? undefined : 1,
        kind: undefined,
        page: 1,
      }),
    });
  }

  function clearFacet(kind: FacetKind, id: number) {
    void navigate({ search: (prev) => ({ ...prev, ...removeFacetId(prev, kind, id), page: 1 }) });
  }

  function clearLegacyFilter(key: keyof typeof filterParams) {
    void navigate({ search: (prev) => ({ ...prev, [key]: undefined, page: 1 }) });
  }

  function handleSort(field: MetadataSortField) {
    void navigate({
      search: (prev) => {
        if ((prev.sort_by ?? "updated_at") === field) {
          const nextOrder = (prev.order ?? "desc") === "asc" ? "desc" : "asc";
          return { ...prev, sort_by: field, order: nextOrder, page: 1 };
        }
        return { ...prev, sort_by: field, order: "desc", page: 1 };
      },
    });
  }

  function applyExpression(f: string | undefined) {
    void navigate({ search: (prev) => ({ ...withoutMetadataQuickFilters(prev), f }) });
  }

  function clearExpression() {
    void navigate({ search: (prev) => ({ ...prev, f: undefined, page: 1 }) });
  }

  const active = activeFacetFilters(search);
  const hasActiveFilters =
    active.length > 0 || legacyPanelActive || filter.active || filter.presetId != null;

  return (
    <BrowsePageShell
      fill={isList}
      filterDrawerFullHeight
      // 搜索槽内另有语义切换按钮, 放宽到 560, 使输入框宽度接近其他页面的 480
      searchMaxWidth={560}
      title={<Title order={2}>{t("common:nav.meta")}</Title>}
      actions={<ListDefaultActions update={{ key: "meta", value: metaListDefaults(search) }} />}
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
        <Group gap="xs" wrap="nowrap" w="100%">
          <div
            className={
              semanticMode
                ? `${classes.searchBox} ${classes.flowRing} ${classes.semanticInput}`
                : classes.searchBox
            }
          >
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
                if (e.key === "Enter" && !e.nativeEvent.isComposing) submitSearch();
              }}
              placeholder={semanticMode ? t("semantic.placeholder") : t("search.placeholder")}
              leftSection={
                semanticMode ? (
                  <span className={classes.glowIcon}>
                    <IconSparkles size={16} />
                  </span>
                ) : (
                  <IconSearch size={16} />
                )
              }
              rightSection={
                semanticMode ? (
                  <ActionIcon
                    variant="subtle"
                    color="gray"
                    aria-label={t("semantic.submit")}
                    disabled={searchInput.trim().length === 0}
                    onClick={submitSearch}
                  >
                    <IconArrowRight size={16} />
                  </ActionIcon>
                ) : undefined
              }
              rightSectionPointerEvents="all"
            />
          </div>
          {semanticMode ? (
            <Button
              variant="default"
              leftSection={<IconArrowLeft size={16} />}
              onClick={toggleSemantic}
            >
              {t("semantic.back")}
            </Button>
          ) : semanticAvailable ? (
            <Button
              variant="default"
              className={classes.semanticToggle}
              leftSection={
                <span className={classes.glowIcon}>
                  <IconSparkles size={16} />
                </span>
              }
              onClick={toggleSemantic}
            >
              <span className={classes.gradientText}>{t("semantic.toggle")}</span>
            </Button>
          ) : null}
        </Group>
      }
      extras={
        <>
          <BrowseFilterToggle panel={panel} active={hasActiveFilters} />
          {semanticMode && (
            <SegmentedControl
              value={search.kind ?? "all"}
              onChange={(v) =>
                void navigate({
                  search: (prev) => ({ ...prev, kind: toEmbeddingKind(v), page: 1 }),
                })
              }
              data={SEMANTIC_KIND_OPTIONS.map((value) => ({
                value,
                label: t(`semantic.kinds.${value}`),
              }))}
            />
          )}
          {!isList && !semanticMode && (
            <SortMenu
              options={METADATA_SORT_OPTIONS.map((option) => ({
                value: option.value,
                label: t(option.labelKey),
              }))}
              sortBy={search.sort_by}
              order={search.order}
              defaultSortBy="updated_at"
              defaultOrder="desc"
              onChange={(sort_by, order) =>
                void navigate({ search: (prev) => ({ ...prev, sort_by, order, page: 1 }) })
              }
            />
          )}
        </>
      }
      pageSize={
        isList ? (
          <PageSizeSelect
            sizeKey="metaList"
            onChanged={() => void navigate({ search: (prev) => ({ ...prev, page: 1 }) })}
          />
        ) : undefined
      }
      filterPanel={
        <BrowseFilterPanel
          panel={panel}
          filter={filter}
          libraries={libraries}
          onApply={applyExpression}
        />
      }
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
              <Badge variant="outline">
                {t("common:nav.savedQueries")}: #{search.saved_query_id}
              </Badge>
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
              <ActionIcon
                size="sm"
                variant="subtle"
                color="gray"
                onClick={() => clearLegacyFilter("saved_query_id")}
                aria-label={`${t("common:actions.remove")} ${t("common:nav.savedQueries")}`}
              >
                <IconX size={14} />
              </ActionIcon>
            </Group>
          )}
          <ActiveExpressionChip filter={filter} onClear={clearExpression} />
          {libraryIds.length > 0 && (
            <ActiveFilterChip
              label={`${t("search.library")}: ${libraryIds.map((id) => libraryNameById.get(id) ?? `#${id}`).join(", ")}`}
              onClear={() => clearLegacyFilter("library_id")}
            />
          )}
          {hasFiles !== undefined && (
            <ActiveFilterChip
              label={`${t("search.hasFiles")}: ${hasFiles ? t("search.hasFilesYes") : t("search.hasFilesNo")}`}
              onClear={() => clearLegacyFilter("has_files")}
            />
          )}
          {vr !== undefined && (
            <ActiveFilterChip
              label={`${t("search.vr")}: ${vr ? t("search.yes") : t("search.no")}`}
              onClear={() => clearLegacyFilter("vr")}
            />
          )}
          {hasSubtitle !== undefined && (
            <ActiveFilterChip
              label={`${t("search.hasSubtitle")}: ${hasSubtitle ? t("search.yes") : t("search.no")}`}
              onClear={() => clearLegacyFilter("has_subtitle")}
            />
          )}
          {uncensored !== undefined && (
            <ActiveFilterChip
              label={`${t("search.uncensored")}: ${uncensored ? t("search.yes") : t("search.no")}`}
              onClear={() => clearLegacyFilter("uncensored")}
            />
          )}
          {mosaic != null && (
            <ActiveFilterChip
              label={`${t("search.mosaic")}: ${t(`search.mosaics.${mosaic}`)}`}
              onClear={() => clearLegacyFilter("mosaic")}
            />
          )}
          {definition != null && (
            <ActiveFilterChip
              label={`${t("search.definition")}: ${definition}`}
              onClear={() => clearLegacyFilter("definition")}
            />
          )}
          {contentType != null && (
            <ActiveFilterChip
              label={`${t("search.contentType")}: ${t(`search.contentTypes.${contentType}`)}`}
              onClear={() => clearLegacyFilter("content_type")}
            />
          )}
          {active.map(({ kind, id }) => (
            <ActiveFacetChip
              key={`${kind}-${id}`}
              kind={kind}
              id={id}
              onClear={() => clearFacet(kind, id)}
            />
          ))}
        </Group>
      )}

      {filter.blocked || (!showResults && semanticNotice == null) ? null : semanticNotice !=
        null ? (
        <Alert color="blue" title={t("semantic.unavailableTitle")} maw={720} mx="auto" w="100%">
          {semanticNotice}
        </Alert>
      ) : isList ? (
        <MetaTable
          key={listLimit}
          items={listQuery.data?.items ?? []}
          isLoading={listQuery.isLoading}
          total={total}
          page={search.page}
          sortBy={search.sort_by}
          order={search.order}
          onPageChange={(page) => void navigate({ search: (prev) => ({ ...prev, page }) })}
          onSort={handleSort}
        />
      ) : (
        <>
          <PosterGrid
            layout="adaptive"
            items={gridItems}
            loading={gridQuery.isLoading && gridItems.length === 0}
            emptyMessage={t("empty")}
            imageUrlOf={(item) => {
              const hit = semanticHits.get(item.id);
              return hit?.kind === "still" ? hit.resource_url : undefined;
            }}
            imageRatioOf={(item) =>
              semanticHits.get(item.id)?.kind === "still" ? 16 / 9 : undefined
            }
          />
          {gridItems.length > 0 && (
            <InfiniteScrollSentinel
              hasNextPage={Boolean(gridQuery.hasNextPage)}
              isFetchingNextPage={gridQuery.isFetchingNextPage}
              fetchNextPage={() => void gridQuery.fetchNextPage()}
              prefetchMargin={GRID_PREFETCH_MARGIN}
              loadedCount={gridItems.length}
              loadedLabel={t("common:pagination.loadedOfTotal", {
                loaded: gridItems.length,
                total,
              })}
            />
          )}
        </>
      )}
    </BrowsePageShell>
  );
}
