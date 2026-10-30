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
  IconFilter,
  IconSearch,
  IconSparkles,
  IconTable,
  IconX,
} from "@tabler/icons-react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { createFileRoute, stripSearchParams } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  getFacetOptions,
  listMetadataInfiniteOptions,
  listMetadataOptions,
  semanticStatusOptions,
} from "@/client/@tanstack/react-query.gen";
import type {
  EmbeddingKind,
  FacetKind,
  ListMetadataData,
  MetadataSortField,
  SemanticHitInfo,
} from "@/client/types.gen";
import { BrowsePageShell } from "@/components/common/browse-page-shell";
import { HintedActionIcon } from "@/components/common/hinted-action-icon";
import { InfiniteScrollSentinel } from "@/components/common/infinite-scroll-sentinel";
import { ListDefaultActions } from "@/components/common/list-default-actions";
import { PageSizeSelect } from "@/components/common/page-size-select";
import { SortMenu } from "@/components/common/sort-menu";
import { FacetBadge } from "@/components/media/facet-badge";
import {
  FacetFilterControls,
  type FilePhaseFilters,
  type HasFilesFilter,
} from "@/components/media/facet-filter-controls";
import { MetaTable } from "@/components/media/meta-table";
import { PosterGrid } from "@/components/media/poster-grid";
import { activeFacetFilters, addFacetId, type FacetFilters, removeFacetId } from "@/lib/facets";
import { useNarrowViewport } from "@/hooks/use-narrow-viewport";
import { EMBEDDING_KINDS } from "@/lib/exhaustive-maps";
import { nextOffsetPageParam } from "@/lib/infinite-list";
import { metaSearchSchema, METADATA_SORT_OPTIONS } from "@/lib/media/browse";
import { metaListDefaults } from "@/lib/nav-defaults";
import { useUIStore } from "@/stores/ui";
import classes from "./meta.module.css";

const CHUNK = 30;

const SEMANTIC_KIND_OPTIONS = ["all", ...EMBEDDING_KINDS] as const;

/** 分段控件回传 string; "all" 与未知值都还原为缺省 (封面与剧照都参与). */
function toEmbeddingKind(value: string): EmbeddingKind | undefined {
  return EMBEDDING_KINDS.find((kind) => kind === value);
}

export const Route = createFileRoute("/meta/")({
  validateSearch: metaSearchSchema,
  search: { middlewares: [stripSearchParams({ view: "grid", page: 1 })] },
  component: MetaIndexPage,
});

function parseHasFiles(value: "true" | "false" | undefined): HasFilesFilter {
  if (value === "true") return true;
  if (value === "false") return false;
  return null;
}

function triToSearch(value: HasFilesFilter): "true" | "false" | undefined {
  if (value === true) return "true";
  if (value === false) return "false";
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
  const { t } = useTranslation("metadata");
  return (
    <Group gap={4} wrap="nowrap">
      <FacetBadge
        kind={kind}
        id={id}
        name={`${t(`browse.kinds.${kind}`)}: ${data?.name ?? `#${id}`}`}
        variant="outline"
      />
      <ActionIcon size="sm" variant="subtle" color="gray" onClick={onClear} aria-label="clear">
        <IconX size={14} />
      </ActionIcon>
    </Group>
  );
}

function ActiveHasFilesChip({ hasFiles, onClear }: { hasFiles: boolean; onClear: () => void }) {
  const { t } = useTranslation("metadata");
  return (
    <ActiveTriChip
      label={`${t("search.hasFiles")}: ${hasFiles ? t("search.hasFilesYes") : t("search.hasFilesNo")}`}
      onClear={onClear}
    />
  );
}

function ActiveVrChip({ vr, onClear }: { vr: boolean; onClear: () => void }) {
  const { t } = useTranslation("metadata");
  return (
    <ActiveTriChip
      label={`${t("search.vr")}: ${vr ? t("search.yes") : t("search.no")}`}
      onClear={onClear}
    />
  );
}

function ActiveTriChip({ label, onClear }: { label: string; onClear: () => void }) {
  return (
    <Group gap={4} wrap="nowrap">
      <Badge variant="outline">{label}</Badge>
      <ActionIcon size="sm" variant="subtle" color="gray" onClick={onClear} aria-label="clear">
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
  const narrowViewport = useNarrowViewport("md");

  const hasFiles = parseHasFiles(search.has_files);
  const vr = parseHasFiles(search.vr);
  const filePhase: FilePhaseFilters = {
    has_subtitle: parseHasFiles(search.has_subtitle),
    uncensored: parseHasFiles(search.uncensored),
    mosaic: search.mosaic ?? null,
    definition: search.definition ?? null,
    content_type: search.content_type ?? null,
  };
  const phaseActive =
    filePhase.has_subtitle !== null ||
    filePhase.uncensored !== null ||
    filePhase.mosaic != null ||
    filePhase.definition != null ||
    filePhase.content_type != null;
  const [searchInput, setSearchInput] = useState(search.q ?? "");
  const [advancedOpen, setAdvancedOpen] = useState(hasFiles !== null || vr !== null || phaseActive);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

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
    has_files: hasFiles === null ? undefined : hasFiles,
    vr: vr === null ? undefined : vr,
    has_subtitle: filePhase.has_subtitle === null ? undefined : filePhase.has_subtitle,
    uncensored: filePhase.uncensored === null ? undefined : filePhase.uncensored,
    mosaic: filePhase.mosaic ?? undefined,
    definition: filePhase.definition ?? undefined,
    content_type: filePhase.content_type ?? undefined,
    ...filters,
    ...(search.saved_query_id != null ? { saved_query_id: search.saved_query_id } : {}),
  };

  const semanticParams: NonNullable<ListMetadataData["query"]> =
    semanticQuery != null
      ? { semantic_query: semanticQuery, semantic_kind: search.kind ?? "all" }
      : {};

  const listQueryParams = {
    search: semanticActive ? undefined : search.q || undefined,
    sort_by: semanticActive ? undefined : search.sort_by,
    order: semanticActive ? undefined : search.order,
    ...semanticParams,
    ...filterParams,
  };

  const isList = search.view === "list";
  const { data: semanticStatus, refetch: refetchSemanticStatus } =
    useQuery(semanticStatusOptions());
  const semanticAvailable = semanticStatus?.available === true;

  // 进入语义模式时再取一次: 索引任务可能刚在别处完成, 缓存的空索引状态会过期.
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
  // 语义状态未到或不可检索时不发列表请求, 避免先按普通排序闪一屏.
  const listEnabled = !semanticActive || (semanticStatus != null && semanticNotice == null);

  const gridQuery = useInfiniteQuery({
    ...listMetadataInfiniteOptions({
      query: {
        ...listQueryParams,
        limit: CHUNK,
      },
    }),
    initialPageParam: 0,
    getNextPageParam: nextOffsetPageParam,
    enabled: !isList && listEnabled,
  });

  const listOffset = (search.page - 1) * listLimit;
  const listQuery = useQuery({
    ...listMetadataOptions({
      query: {
        ...listQueryParams,
        offset: listOffset,
        limit: listLimit,
      },
    }),
    enabled: isList && listEnabled,
  });

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
    if (semanticMode) return;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      void navigate({ search: (prev) => ({ ...prev, q: v || undefined, page: 1 }) });
    }, 300);
  }

  function submitSearch() {
    const value = searchInput.trim();
    void navigate({ search: (prev) => ({ ...prev, q: value || undefined, page: 1 }) });
  }

  function toggleSemantic() {
    // 切换搜索方式时清空输入, 避免把关键词当成画面描述或反过来.
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

  function appendFacet(kind: FacetKind, id: number) {
    void navigate({
      search: (prev) => {
        const next = addFacetId(
          {
            actor_id: prev.actor_id,
            director_id: prev.director_id,
            tag_id: prev.tag_id,
            studio_id: prev.studio_id,
            publisher_id: prev.publisher_id,
            series_id: prev.series_id,
            user_tag_id: prev.user_tag_id,
          },
          kind,
          id,
        );
        return { ...prev, ...next, page: 1 };
      },
    });
  }

  function clearFacet(kind: FacetKind, id: number) {
    void navigate({
      search: (prev) => {
        const next = removeFacetId(
          {
            actor_id: prev.actor_id,
            director_id: prev.director_id,
            tag_id: prev.tag_id,
            studio_id: prev.studio_id,
            publisher_id: prev.publisher_id,
            series_id: prev.series_id,
            user_tag_id: prev.user_tag_id,
          },
          kind,
          id,
        );
        return { ...prev, ...next, page: 1 };
      },
    });
  }

  function setHasFilesFilter(value: HasFilesFilter) {
    void navigate({
      search: (prev) => ({
        ...prev,
        has_files: triToSearch(value),
        page: 1,
      }),
    });
  }

  function setVrFilter(value: HasFilesFilter) {
    void navigate({
      search: (prev) => ({
        ...prev,
        vr: triToSearch(value),
        page: 1,
      }),
    });
  }

  function setFilePhaseFilter(value: FilePhaseFilters) {
    void navigate({
      search: (prev) => ({
        ...prev,
        has_subtitle: triToSearch(value.has_subtitle),
        uncensored: triToSearch(value.uncensored),
        mosaic: value.mosaic ?? undefined,
        definition: value.definition ?? undefined,
        content_type: value.content_type ?? undefined,
        page: 1,
      }),
    });
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

  const active = activeFacetFilters(filters);
  const hasActiveFilters =
    active.length > 0 ||
    hasFiles !== null ||
    vr !== null ||
    phaseActive ||
    search.saved_query_id != null;

  return (
    <BrowsePageShell
      fill={isList}
      // 搜索槽内还有语义切换按钮, 放宽到 560 让输入框本身保持接近其他页的 480 宽
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
        <Text size="sm" c="dimmed">
          {t("common:pagination.totalItems", { count: total })}
        </Text>
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
              onKeyDown={(e) => {
                if (e.key === "Enter") submitSearch();
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
          {/* 窄屏的筛选在底部面板里常驻展开, 该开关只在宽屏有意义. */}
          {narrowViewport ? null : (
            <HintedActionIcon
              variant={advancedOpen || hasFiles !== null || vr !== null ? "filled" : "default"}
              size={36}
              onClick={() => setAdvancedOpen((v) => !v)}
              label={t("search.advanced")}
            >
              <IconFilter size={16} />
            </HintedActionIcon>
          )}
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
        <FacetFilterControls
          opened={narrowViewport || advancedOpen}
          filters={filters}
          onSelect={appendFacet}
          hasFiles={hasFiles}
          onHasFilesChange={setHasFilesFilter}
          vr={vr}
          onVrChange={setVrFilter}
          filePhase={filePhase}
          onFilePhaseChange={setFilePhaseFilter}
        />
      }
    >
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
                onClick={() =>
                  void navigate({
                    search: (prev) => ({ ...prev, saved_query_id: undefined, page: 1 }),
                  })
                }
                aria-label="clear"
              >
                <IconX size={14} />
              </ActionIcon>
            </Group>
          )}
          {hasFiles !== null && (
            <ActiveHasFilesChip hasFiles={hasFiles} onClear={() => setHasFilesFilter(null)} />
          )}
          {vr !== null && <ActiveVrChip vr={vr} onClear={() => setVrFilter(null)} />}
          {filePhase.has_subtitle !== null && (
            <ActiveTriChip
              label={`${t("search.hasSubtitle")}: ${filePhase.has_subtitle ? t("search.yes") : t("search.no")}`}
              onClear={() => setFilePhaseFilter({ ...filePhase, has_subtitle: null })}
            />
          )}
          {filePhase.uncensored !== null && (
            <ActiveTriChip
              label={`${t("search.uncensored")}: ${filePhase.uncensored ? t("search.yes") : t("search.no")}`}
              onClear={() => setFilePhaseFilter({ ...filePhase, uncensored: null })}
            />
          )}
          {filePhase.mosaic != null && (
            <ActiveTriChip
              label={`${t("search.mosaic")}: ${t(`search.mosaics.${filePhase.mosaic}`)}`}
              onClear={() => setFilePhaseFilter({ ...filePhase, mosaic: null })}
            />
          )}
          {filePhase.definition != null && (
            <ActiveTriChip
              label={`${t("search.definition")}: ${filePhase.definition}`}
              onClear={() => setFilePhaseFilter({ ...filePhase, definition: null })}
            />
          )}
          {filePhase.content_type != null && (
            <ActiveTriChip
              label={`${t("search.contentType")}: ${t(`search.contentTypes.${filePhase.content_type}`)}`}
              onClear={() => setFilePhaseFilter({ ...filePhase, content_type: null })}
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

      {semanticNotice != null ? (
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
