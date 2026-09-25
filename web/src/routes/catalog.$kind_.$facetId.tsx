import {
  Alert,
  Anchor,
  Badge,
  Breadcrumbs,
  Button,
  Group,
  Loader,
  Stack,
  Text,
  Title,
} from "@mantine/core";
import { IconAlertCircle, IconFilter } from "@tabler/icons-react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { createFileRoute, Link } from "@tanstack/react-router";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import {
  getFacetOptions,
  listActorsOptions,
  listMetadataInfiniteOptions,
} from "@/client/@tanstack/react-query.gen";
import type { FacetKind } from "@/client/types.gen";
import { InfiniteScrollSentinel } from "@/components/common/infinite-scroll-sentinel";
import { ActorCardGrid } from "@/components/media/actor-grid";
import { PosterGrid } from "@/components/media/poster-grid";
import { isOneOf } from "@/lib/exhaustive";
import { CATALOG_FACET_KINDS } from "@/lib/exhaustive-maps";
import { FACET_FILTER_PARAM, metaSearchForFacet } from "@/lib/facets";
import { nextOffsetPageParam } from "@/lib/infinite-list";

const CHUNK = 30;

/** 标签详情页的演员预览条数; 超过后在标题行给出进入演员筛选的入口. */
const ACTOR_PREVIEW = 24;

export const Route = createFileRoute("/catalog/$kind_/$facetId")({
  component: FacetDetailPage,
});

function FacetDetailPage() {
  const { kind: rawKind, facetId } = Route.useParams();
  const { t } = useTranslation(["metadata", "common"]);

  const id = Number(facetId);
  const validId = Number.isInteger(id) && id > 0;
  const validKind = isOneOf(CATALOG_FACET_KINDS, rawKind);
  const kind: FacetKind = validKind ? rawKind : "tag";
  const param = FACET_FILTER_PARAM[kind];

  const { data: facet, isLoading: facetLoading } = useQuery({
    ...getFacetOptions({ path: { kind, facet_id: id } }),
    enabled: validKind && validId,
  });
  const { data, isLoading, hasNextPage, isFetchingNextPage, fetchNextPage } = useInfiniteQuery({
    ...listMetadataInfiniteOptions({
      query: { limit: CHUNK, [param]: id },
    }),
    enabled: validKind && validId,
    initialPageParam: 0,
    getNextPageParam: nextOffsetPageParam,
  });

  const items = useMemo(() => data?.pages.flatMap((p) => p.items) ?? [], [data]);
  const total = data?.pages[0]?.total ?? 0;

  // 用户标签可挂在影片与演员两侧, 标签详情页同时列出两类; 某一类为空时整段不渲染.
  // 两个条数在各自查询 resolve 前都是 0, 因此加载期不能据此判定为空: 影片分区照常渲染
  // (PosterGrid 自带骨架), 空状态须等两侧都加载完, 否则冷加载会先闪一行「空」再被内容替换.
  const isUserTag = kind === "user_tag";
  const { data: actorData, isLoading: actorLoading } = useQuery({
    ...listActorsOptions({ query: { limit: ACTOR_PREVIEW, user_tag_ids: [id] } }),
    enabled: isUserTag && validId,
  });
  const actorItems = actorData?.items ?? [];
  const actorTotal = actorData?.total ?? 0;
  const showFilms = !isUserTag || isLoading || total > 0;
  const showActors = isUserTag && actorTotal > 0;
  const showEmpty = isUserTag && !isLoading && !actorLoading && total === 0 && actorTotal === 0;

  if (!validKind || !validId) {
    return (
      <Alert color="red" icon={<IconAlertCircle size={18} />}>
        {t("common:status.error")}
      </Alert>
    );
  }

  return (
    <Stack gap="md">
      <Breadcrumbs>
        <Anchor component={Link} to="/catalog" size="sm">
          {t("browse.title")}
        </Anchor>
        <Link to="/catalog/$kind" params={{ kind }} style={{ textDecoration: "none" }}>
          <Anchor component="span" size="sm">
            {t(`browse.kinds.${kind}`)}
          </Anchor>
        </Link>
      </Breadcrumbs>

      <Group gap="sm" align="center" justify="space-between" wrap="wrap">
        <Group gap="sm" align="center">
          {facetLoading ? <Loader size="sm" /> : <Title order={2}>{facet?.name}</Title>}
          {facet && !isUserTag && (
            <Badge size="lg" variant="light">
              {t("browse.count", { count: facet.count })}
            </Badge>
          )}
        </Group>
        <Link to="/meta" search={metaSearchForFacet(kind, id)} style={{ textDecoration: "none" }}>
          <Button variant="light" size="sm" leftSection={<IconFilter size={14} />} component="span">
            {t("browse.filterInMeta")}
          </Button>
        </Link>
      </Group>

      {showFilms && (
        <Stack gap="xs">
          {isUserTag && (
            <Group gap="xs" align="center">
              <Title order={3}>{t("browse.tagFilms")}</Title>
              {!isLoading && <Badge variant="light">{total}</Badge>}
            </Group>
          )}
          <PosterGrid
            items={items}
            loading={isLoading && items.length === 0}
            emptyMessage={t("empty")}
          />
          {items.length > 0 && (
            <InfiniteScrollSentinel
              hasNextPage={Boolean(hasNextPage)}
              isFetchingNextPage={isFetchingNextPage}
              fetchNextPage={() => void fetchNextPage()}
              loadedLabel={t("common:pagination.loadedOfTotal", { loaded: items.length, total })}
            />
          )}
        </Stack>
      )}

      {showActors && (
        <Stack gap="xs">
          <Group gap="xs" align="center">
            <Title order={3}>{t("browse.tagActors")}</Title>
            <Badge variant="light">{actorTotal}</Badge>
            {actorTotal > actorItems.length && (
              <Link
                to="/actors"
                search={{ user_tag_id: id, gender: [] }}
                style={{ textDecoration: "none" }}
              >
                <Anchor component="span" size="sm">
                  {t("actors.viewAll", { count: actorTotal })}
                </Anchor>
              </Link>
            )}
          </Group>
          <ActorCardGrid items={actorItems} />
        </Stack>
      )}

      {showEmpty && (
        <Text c="dimmed" size="sm">
          {t("common:status.empty")}
        </Text>
      )}
    </Stack>
  );
}
