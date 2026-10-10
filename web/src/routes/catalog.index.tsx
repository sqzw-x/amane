import { Badge, Group, Stack, Text, Title } from "@mantine/core";
import { IconStar } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { createFileRoute, Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { listFacetsOptions } from "@/client/@tanstack/react-query.gen";
import type { FacetResponse } from "@/client/types.gen";
import { FacetFavoriteMark } from "@/components/media/facet-favorite-star";
import { CATALOG_FACET_KINDS, FAVORITE_FACET_KINDS } from "@/lib/exhaustive-maps";
import { FACET_KIND_ICON } from "@/lib/facets";

export const Route = createFileRoute("/catalog/")({ component: CatalogIndexPage });

const PREVIEW = 36;
/** 收藏区块每 kind 的预览条数; 少于种类区块, 收藏是精选而非清单. */
const FAVORITE_PREVIEW = 24;

type CatalogKind = (typeof CATALOG_FACET_KINDS)[number];
type FavoriteKind = (typeof FAVORITE_FACET_KINDS)[number];

function FacetChip({ kind, facet }: { kind: CatalogKind; facet: FacetResponse }) {
  return (
    <Link
      to="/catalog/$kind/$facetId"
      params={{ kind, facetId: String(facet.id) }}
      style={{ textDecoration: "none" }}
    >
      <Badge
        variant="light"
        radius="xl"
        size="md"
        style={{ textTransform: "none", cursor: "pointer" }}
      >
        {facet.is_favorite && (
          <Text span c="yellow" mr={4} style={{ display: "inline-flex", verticalAlign: "middle" }}>
            <FacetFavoriteMark />
          </Text>
        )}
        {facet.name}
        <Text span c="dimmed" size="xs" ml={4}>
          ({facet.count})
        </Text>
      </Badge>
    </Link>
  );
}

/**
 * 收藏汇总: 四个 kind 各取一次收藏列表.
 *
 * 四个查询全部就绪且至少一项非空才渲染整块 — 区块位于种类区块之上, 加载期先渲染标题或骨架都会
 * 造成跳动; 板块为空时它整块不存在, 不占位.
 */
function FavoriteSection() {
  const { t } = useTranslation("metadata");
  const query = { favorite: true, limit: FAVORITE_PREVIEW, offset: 0 } as const;
  const tag = useQuery(listFacetsOptions({ path: { kind: "tag" }, query }));
  const studio = useQuery(listFacetsOptions({ path: { kind: "studio" }, query }));
  const publisher = useQuery(listFacetsOptions({ path: { kind: "publisher" }, query }));
  const series = useQuery(listFacetsOptions({ path: { kind: "series" }, query }));
  const groups: Array<{
    kind: FavoriteKind;
    items: FacetResponse[];
    total: number;
    pending: boolean;
  }> = [
    {
      kind: "tag",
      items: tag.data?.items ?? [],
      total: tag.data?.total ?? 0,
      pending: tag.isPending,
    },
    {
      kind: "studio",
      items: studio.data?.items ?? [],
      total: studio.data?.total ?? 0,
      pending: studio.isPending,
    },
    {
      kind: "publisher",
      items: publisher.data?.items ?? [],
      total: publisher.data?.total ?? 0,
      pending: publisher.isPending,
    },
    {
      kind: "series",
      items: series.data?.items ?? [],
      total: series.data?.total ?? 0,
      pending: series.isPending,
    },
  ];

  const visible = groups.filter((group) => group.items.length > 0);
  if (groups.some((group) => group.pending) || visible.length === 0) return null;

  return (
    <Stack gap="sm">
      <Group gap="xs">
        <IconStar size={18} stroke={1.5} color="var(--mantine-color-yellow-6)" />
        <Text fw={600}>{t("favorite.sectionTitle")}</Text>
      </Group>
      {visible.map((group) => (
        <Group key={group.kind} gap="xs" align="center">
          <Text size="sm" c="dimmed" w={72}>
            {t(`browse.kinds.${group.kind}`)}
          </Text>
          <Group gap={8}>
            {group.items.map((facet) => (
              <FacetChip key={facet.id} kind={group.kind} facet={facet} />
            ))}
            {group.total > group.items.length && (
              <Link
                to="/catalog/$kind"
                params={{ kind: group.kind }}
                search={{ favorite: "true" }}
                style={{ textDecoration: "none" }}
              >
                <Badge variant="outline" radius="xl" color="gray" style={{ cursor: "pointer" }}>
                  +{group.total - group.items.length}
                </Badge>
              </Link>
            )}
          </Group>
        </Group>
      ))}
    </Stack>
  );
}

function KindSection({ kind }: { kind: CatalogKind }) {
  const { t } = useTranslation("metadata");
  const Icon = FACET_KIND_ICON[kind];
  const { data, isLoading } = useQuery(
    listFacetsOptions({ path: { kind }, query: { limit: PREVIEW, offset: 0 } }),
  );

  const items = data?.items ?? [];
  const total = data?.total ?? 0;

  return (
    <Stack gap="sm">
      <Link
        to="/catalog/$kind"
        params={{ kind }}
        style={{ textDecoration: "none", color: "inherit", width: "fit-content" }}
      >
        <Group gap="xs" style={{ cursor: "pointer" }}>
          <Icon size={18} stroke={1.5} />
          <Text fw={600}>
            {t(`browse.kinds.${kind}`)}
            <Text span c="dimmed" fw={400} ml={6}>
              ({total})
            </Text>
          </Text>
        </Group>
      </Link>

      {isLoading && (
        <Text size="sm" c="dimmed">
          …
        </Text>
      )}

      <Group gap={8}>
        {items.map((facet) => (
          <FacetChip key={facet.id} kind={kind} facet={facet} />
        ))}
        {total > items.length && (
          <Link to="/catalog/$kind" params={{ kind }} style={{ textDecoration: "none" }}>
            <Badge variant="outline" radius="xl" color="gray" style={{ cursor: "pointer" }}>
              +{total - items.length}
            </Badge>
          </Link>
        )}
      </Group>
    </Stack>
  );
}

function CatalogIndexPage() {
  const { t } = useTranslation("metadata");

  return (
    <Stack gap="xl">
      <div>
        <Title order={2}>{t("browse.title")}</Title>
        <Text c="dimmed" size="sm">
          {t("browse.subtitle")}
        </Text>
      </div>
      <FavoriteSection />
      {CATALOG_FACET_KINDS.map((kind) => (
        <KindSection key={kind} kind={kind} />
      ))}
    </Stack>
  );
}
