import {
  ActionIcon,
  Badge,
  Group,
  SegmentedControl,
  Text,
  TextInput,
  Title,
  Tooltip,
} from "@mantine/core";
import { IconFilter, IconSearch, IconTable, IconX } from "@tabler/icons-react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { createFileRoute, stripSearchParams } from "@tanstack/react-router";
import { useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { z } from "zod";
import {
  getFacetOptions,
  listMetadataInfiniteOptions,
  listMetadataOptions,
} from "@/client/@tanstack/react-query.gen";
import type { FacetKind, MetadataSortField } from "@/client/types.gen";
import { BrowsePageShell } from "@/components/common/browse-page-shell";
import { InfiniteScrollSentinel } from "@/components/common/infinite-scroll-sentinel";
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
import {
  CONTENT_TYPES,
  FILE_DEFINITIONS,
  METADATA_SORT_FIELDS,
  MOSAICS,
  SORT_ORDERS,
} from "@/lib/exhaustive-maps";
import {
  activeFacetFilters,
  addFacetId,
  coerceIdList,
  type FacetFilters,
  removeFacetId,
} from "@/lib/facets";
import { nextOffsetPageParam } from "@/lib/infinite-list";
import { useUIStore } from "@/stores/ui";

const CHUNK = 30;

const SORT_FIELDS = [
  "updated_at",
  "created_at",
  "number",
  "title",
  "studio",
  "release",
  "file_count",
] as const satisfies readonly MetadataSortField[];

const idListSchema = z.preprocess(coerceIdList, z.array(z.number().int().positive()).optional());

const metaSearchSchema = z.object({
  q: z.string().optional(),
  view: z.enum(["grid", "list"]).catch("grid").default("grid"),
  sort_by: z.enum(METADATA_SORT_FIELDS).optional(),
  order: z.enum(SORT_ORDERS).optional(),
  page: z.coerce.number().int().min(1).catch(1).default(1),
  actor_id: idListSchema,
  director_id: idListSchema,
  tag_id: idListSchema,
  studio_id: idListSchema,
  publisher_id: idListSchema,
  series_id: idListSchema,
  user_tag_id: idListSchema,
  has_files: z.enum(["true", "false"]).optional(),
  has_subtitle: z.enum(["true", "false"]).optional(),
  uncensored: z.enum(["true", "false"]).optional(),
  mosaic: z.enum(MOSAICS).optional(),
  definition: z.enum(FILE_DEFINITIONS).optional(),
  content_type: z.enum(CONTENT_TYPES).optional(),
  saved_query_id: z.coerce.number().int().positive().optional(),
});

export const Route = createFileRoute("/meta/")({
  validateSearch: metaSearchSchema,
  search: { middlewares: [stripSearchParams({ view: "grid", page: 1 })] },
  component: MetaIndexPage,
});

const SORT_FIELD_COLUMN_KEY = {
  updated_at: "updated",
  created_at: "created",
  number: "number",
  title: "title",
  studio: "studio",
  release: "release",
  file_count: "fileCount",
} as const satisfies Record<MetadataSortField, string>;

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
  const { t } = useTranslation(["metadata", "common", "agent"]);
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  const listLimit = useUIStore((s) => s.pageSizes.metaList);

  const hasFiles = parseHasFiles(search.has_files);
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
  const [advancedOpen, setAdvancedOpen] = useState(hasFiles !== null || phaseActive);
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

  const listQueryParams = {
    search: search.q || undefined,
    sort_by: search.sort_by,
    order: search.order,
    has_files: hasFiles === null ? undefined : hasFiles,
    has_subtitle: filePhase.has_subtitle === null ? undefined : filePhase.has_subtitle,
    uncensored: filePhase.uncensored === null ? undefined : filePhase.uncensored,
    mosaic: filePhase.mosaic ?? undefined,
    definition: filePhase.definition ?? undefined,
    content_type: filePhase.content_type ?? undefined,
    ...filters,
    ...(search.saved_query_id != null ? { saved_query_id: search.saved_query_id } : {}),
  };

  const isList = search.view === "list";

  const gridQuery = useInfiniteQuery({
    ...listMetadataInfiniteOptions({
      query: {
        ...listQueryParams,
        limit: CHUNK,
      },
    }),
    initialPageParam: 0,
    getNextPageParam: nextOffsetPageParam,
    enabled: !isList,
  });

  const listOffset = (search.page - 1) * listLimit;
  const listQuery = useQuery({
    ...listMetadataOptions({
      query: {
        ...listQueryParams,
        offset: listOffset,
        limit: listLimit,
        sort_by: search.sort_by ?? "updated_at",
        order: search.order ?? "desc",
      },
    }),
    enabled: isList,
  });

  const gridItems = useMemo(
    () => gridQuery.data?.pages.flatMap((p) => p.items) ?? [],
    [gridQuery.data],
  );
  const total = isList ? (listQuery.data?.total ?? 0) : (gridQuery.data?.pages[0]?.total ?? 0);

  function handleSearchChange(v: string) {
    setSearchInput(v);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      void navigate({ search: (prev) => ({ ...prev, q: v || undefined, page: 1 }) });
    }, 300);
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
    active.length > 0 || hasFiles !== null || phaseActive || search.saved_query_id != null;

  return (
    <BrowsePageShell
      fill={isList}
      title={<Title order={2}>{t("common:nav.meta")}</Title>}
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
          placeholder={t("search.placeholder")}
          leftSection={<IconSearch size={16} />}
          w="100%"
        />
      }
      extras={
        <>
          <Tooltip label={t("search.advanced")}>
            <ActionIcon
              variant={advancedOpen || hasFiles !== null ? "filled" : "default"}
              size={36}
              onClick={() => setAdvancedOpen((v) => !v)}
              aria-label={t("search.advanced")}
            >
              <IconFilter size={16} />
            </ActionIcon>
          </Tooltip>
          {!isList && (
            <SortMenu
              options={SORT_FIELDS.map((f) => ({
                value: f,
                label: t(`columns.${SORT_FIELD_COLUMN_KEY[f]}`),
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
    >
      <FacetFilterControls
        opened={advancedOpen}
        filters={filters}
        onSelect={appendFacet}
        hasFiles={hasFiles}
        onHasFilesChange={setHasFilesFilter}
        filePhase={filePhase}
        onFilePhaseChange={setFilePhaseFilter}
      />

      {hasActiveFilters && (
        <Group gap="xs">
          {search.saved_query_id != null && (
            <Group gap={4} wrap="nowrap">
              <Badge variant="outline">
                {t("common:nav.agent")}: #{search.saved_query_id}
              </Badge>
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

      {isList ? (
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
