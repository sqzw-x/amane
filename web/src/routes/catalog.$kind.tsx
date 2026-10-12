import {
  Alert,
  Badge,
  Collapse,
  Group,
  SegmentedControl,
  Stack,
  Tabs,
  Text,
  TextInput,
} from "@mantine/core";
import { IconAlertCircle, IconFilter, IconSearch } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { createFileRoute, Link, stripSearchParams } from "@tanstack/react-router";
import { useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { z } from "zod";
import { listFacetsOptions } from "@/client/@tanstack/react-query.gen";
import type { FacetKind, FacetResponse, FacetSortField } from "@/client/types.gen";
import { BrowsePageShell } from "@/components/common/browse-page-shell";
import { HintedActionIcon } from "@/components/common/hinted-action-icon";
import { ListPagination } from "@/components/common/list-pagination";
import { PageSizeSelect } from "@/components/common/page-size-select";
import { SortMenu } from "@/components/common/sort-menu";
import { TriStateSegment } from "@/components/common/tri-state-segment";
import { CatalogFacetTable } from "@/components/media/catalog-facet-table";
import { FacetFavoriteMark } from "@/components/media/facet-favorite-star";
import { isOneOf } from "@/lib/exhaustive";
import {
  CATALOG_FACET_KINDS,
  FACET_SORT_FIELDS,
  FAVORITE_FACET_KINDS,
  SORT_ORDERS,
} from "@/lib/exhaustive-maps";
import { FACET_KIND_ICON } from "@/lib/facets";
import { useNarrowViewport } from "@/hooks/use-narrow-viewport";
import { useUIStore } from "@/stores/ui";

const catalogKindSearchSchema = z.object({
  q: z.string().optional(),
  view: z.enum(["cloud", "list"]).catch("list").default("list"),
  sort_by: z.enum(FACET_SORT_FIELDS).optional(),
  order: z.enum(SORT_ORDERS).optional(),
  // 三态: 省略为不限; 默认解析器不解析 'true' / 'false', 只能是字符串枚举.
  favorite: z.enum(["true", "false"]).optional(),
  page: z.coerce.number().int().min(1).catch(1).default(1),
});

export const Route = createFileRoute("/catalog/$kind")({
  validateSearch: catalogKindSearchSchema,
  search: { middlewares: [stripSearchParams({ view: "list", page: 1 })] },
  component: CatalogKindPage,
});

function chipSize(count: number, max: number): number {
  if (max <= 0) return 13;
  const t = Math.log1p(count) / Math.log1p(max);
  return Math.round(12 + t * 10);
}

function FacetChip({
  facet,
  kind,
  maxCount,
}: {
  facet: FacetResponse;
  kind: FacetKind;
  maxCount: number;
}) {
  const size = chipSize(facet.count, maxCount);
  return (
    <Link
      to="/catalog/$kind/$facetId"
      params={{ kind, facetId: String(facet.id) }}
      style={{ textDecoration: "none" }}
    >
      <Badge
        variant="light"
        size="lg"
        radius="xl"
        style={{
          fontSize: size,
          fontWeight: 500,
          paddingInline: 10 + (size - 12),
          paddingBlock: 6 + (size - 12) / 2,
          cursor: "pointer",
          textTransform: "none",
        }}
      >
        {facet.is_favorite && (
          <Text span c="yellow" mr={4} style={{ display: "inline-flex", verticalAlign: "middle" }}>
            <FacetFavoriteMark />
          </Text>
        )}
        {facet.name}
        <Text span c="dimmed" size="xs" ml={6} style={{ fontSize: Math.max(10, size - 3) }}>
          ({facet.count})
        </Text>
      </Badge>
    </Link>
  );
}

function CatalogKindPage() {
  const { kind: rawKind } = Route.useParams();
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  const { t } = useTranslation(["metadata", "common"]);
  const cloudLimit = useUIStore((s) => s.pageSizes.catalogKind);
  const listLimit = useUIStore((s) => s.pageSizes.catalogList);
  const narrow = useNarrowViewport("md");

  const [searchInput, setSearchInput] = useState(search.q ?? "");
  // 带筛选进入时直接展开高级筛选区, 否则筛选项藏在折叠面板里而列表已被过滤.
  const [advancedOpen, setAdvancedOpen] = useState(search.favorite != null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const validKind = isOneOf(CATALOG_FACET_KINDS, rawKind);
  const kind: FacetKind = validKind ? rawKind : "tag";
  const isList = search.view === "list";
  const limit = isList ? listLimit : cloudLimit;
  const offset = (search.page - 1) * limit;
  // 不支持收藏的分类不发该参数: 陈旧 URL 上的 favorite 不会打到 400.
  const supportsFavorite = isOneOf(FAVORITE_FACET_KINDS, kind);
  const favoriteFilter =
    search.favorite === "true" ? true : search.favorite === "false" ? false : null;

  const { data, isLoading } = useQuery({
    ...listFacetsOptions({
      path: { kind },
      query: {
        search: search.q || undefined,
        favorite: supportsFavorite ? (favoriteFilter ?? undefined) : undefined,
        offset,
        limit,
        sort_by: search.sort_by ?? "name",
        order: search.order ?? "asc",
      },
    }),
    enabled: validKind,
  });

  const maxCount = useMemo(
    () => Math.max(0, ...(data?.items.map((i) => i.count) ?? [0])),
    [data?.items],
  );

  if (!validKind) {
    return (
      <Alert color="red" icon={<IconAlertCircle size={18} />}>
        {t("common:status.error")}
      </Alert>
    );
  }

  function handleSearchChange(v: string) {
    setSearchInput(v);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      void navigate({ search: (prev) => ({ ...prev, q: v || undefined, page: 1 }) });
    }, 300);
  }

  function handleSort(field: FacetSortField) {
    void navigate({
      search: (prev) => {
        if ((prev.sort_by ?? "name") === field) {
          const nextOrder = (prev.order ?? "asc") === "asc" ? "desc" : "asc";
          return { ...prev, sort_by: field, order: nextOrder, page: 1 };
        }
        return {
          ...prev,
          sort_by: field,
          order: field === "count" ? "desc" : "asc",
          page: 1,
        };
      },
    });
  }

  function handleKindChange(value: string | null) {
    if (value == null || value === kind) return;
    const next = isOneOf(CATALOG_FACET_KINDS, value) ? value : null;
    if (next == null) return;
    void navigate({
      to: "/catalog/$kind",
      params: { kind: next },
      search: (prev) => ({ view: prev.view, page: 1 }),
    });
  }

  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / limit));

  return (
    <BrowsePageShell
      fill={isList}
      title={
        <Stack gap={4}>
          <Text component={Link} to="/catalog" size="xs" c="dimmed">
            {t("browse.title")}
          </Text>
          {/* 窄屏不渲染种类切换: 六项放不进一行; 换种类改由上面的分类浏览入口完成. */}
          {narrow ? null : (
            <Tabs
              value={kind}
              onChange={handleKindChange}
              variant="pills"
              styles={{ list: { flexWrap: "wrap" } }}
            >
              <Tabs.List>
                {CATALOG_FACET_KINDS.map((k) => {
                  const Icon = FACET_KIND_ICON[k];
                  return (
                    <Tabs.Tab key={k} value={k} leftSection={<Icon size={16} stroke={1.5} />}>
                      {t(`browse.kinds.${k}`)}
                    </Tabs.Tab>
                  );
                })}
              </Tabs.List>
            </Tabs>
          )}
        </Stack>
      }
      viewSwitch={
        <SegmentedControl
          value={search.view}
          onChange={(v) =>
            void navigate({
              search: (prev) => ({
                ...prev,
                view: v === "list" ? "list" : "cloud",
                page: 1,
              }),
            })
          }
          data={[
            { value: "cloud", label: t("view.cloud") },
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
          placeholder={t("browse.searchPlaceholder")}
          leftSection={<IconSearch size={16} />}
          w="100%"
        />
      }
      extras={
        <Group gap="xs" wrap="nowrap">
          {/* 窄屏的筛选在底部面板里常驻展开, 该开关只在宽屏有意义. */}
          {narrow || !supportsFavorite ? null : (
            <HintedActionIcon
              variant={advancedOpen || favoriteFilter != null ? "filled" : "default"}
              size={36}
              onClick={() => setAdvancedOpen((v) => !v)}
              label={t("search.advanced")}
            >
              <IconFilter size={16} />
            </HintedActionIcon>
          )}
          {!isList && (
            <SortMenu
              options={[
                { value: "name", label: t("manage.name") },
                { value: "count", label: t("manage.count") },
              ]}
              sortBy={search.sort_by}
              order={search.order}
              defaultSortBy="name"
              defaultOrder="asc"
              onChange={(sort_by, order) =>
                void navigate({
                  search: (prev) => ({ ...prev, sort_by, order, page: 1 }),
                })
              }
            />
          )}
        </Group>
      }
      filterPanel={
        supportsFavorite ? (
          <Collapse expanded={narrow || advancedOpen}>
            <TriStateSegment
              label={t("favorite.filterLabel")}
              value={search.favorite}
              anyLabel={t("favorite.filterAny")}
              yesLabel={t("favorite.filterYes")}
              noLabel={t("favorite.filterNo")}
              onChange={(favorite) =>
                void navigate({ search: (prev) => ({ ...prev, favorite, page: 1 }) })
              }
            />
          </Collapse>
        ) : undefined
      }
      pageSize={
        <PageSizeSelect
          sizeKey={isList ? "catalogList" : "catalogKind"}
          onChanged={() => void navigate({ search: (prev) => ({ ...prev, page: 1 }) })}
        />
      }
    >
      {isList ? (
        <CatalogFacetTable
          key={listLimit}
          kind={kind}
          items={data?.items ?? []}
          isLoading={isLoading}
          total={total}
          page={search.page}
          sortBy={search.sort_by}
          order={search.order}
          onPageChange={(page) => void navigate({ search: (prev) => ({ ...prev, page }) })}
          onSort={handleSort}
        />
      ) : (
        <>
          <Group gap="sm" align="center">
            {(data?.items ?? []).map((facet) => (
              <FacetChip key={facet.id} facet={facet} kind={kind} maxCount={maxCount} />
            ))}
          </Group>

          {!isLoading && (data?.items.length ?? 0) === 0 && (
            <Text c="dimmed" size="sm" ta="center" py="xl">
              {t("common:status.empty")}
            </Text>
          )}

          {totalPages > 1 && (
            <Group justify="center" mt="md">
              <ListPagination
                totalPages={totalPages}
                page={search.page}
                onChange={(page) => void navigate({ search: (prev) => ({ ...prev, page }) })}
              />
            </Group>
          )}
        </>
      )}
    </BrowsePageShell>
  );
}
