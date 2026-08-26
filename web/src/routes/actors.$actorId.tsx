import {
  Anchor,
  Badge,
  Box,
  Breadcrumbs,
  Button,
  Divider,
  Group,
  Loader,
  Menu,
  rem,
  Stack,
  Text,
  Title,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import {
  IconEraser,
  IconExternalLink,
  IconFilter,
  IconPencil,
  IconRefresh,
  IconStar,
  IconTrash,
} from "@tabler/icons-react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { type ReactNode, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  deleteFacetMutation,
  getActorOptions,
  getActorQueryKey,
  listActorsQueryKey,
  listMetadataInfiniteOptions,
  listMetadataQueryKey,
  renameFacetMutation,
  scrapeActorMutation,
  updateActorMutation,
} from "@/client/@tanstack/react-query.gen";
import type { ActorResponse, CacheKind } from "@/client/types.gen";
import { InfiniteScrollSentinel } from "@/components/common/infinite-scroll-sentinel";
import { ActorEditDialog } from "@/components/media/actor-edit-dialog";
import { FanartLightbox } from "@/components/media/fanart-lightbox";
import { PosterGrid } from "@/components/media/poster-grid";
import { extractErrorMessage } from "@/lib/api-error";
import { CLEARED_ACTOR_PERSON_PATCH } from "@/lib/actors/person";
import { confirm } from "@/lib/confirm";
import { metaSearchForFacet } from "@/lib/facets";
import { ageFromBirthday } from "@/lib/format-birthday";
import { nextOffsetPageParam } from "@/lib/infinite-list";
import { proxyImageUrl } from "@/lib/utils";
import { ProxyImage } from "@/components/media/proxy-image";

const CHUNK = 30;
const AVATAR_WIDTH = 240;

/** 无图占位高度: 主图排队占位与无照片占位共用. */
const NO_PHOTO_HEIGHT = 320;

/** 主图加载失败时的占位 (对齐原 Mantine Image fallbackSrc 行为). */
const FALLBACK_IMAGE_SRC = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'/%3E";

export const Route = createFileRoute("/actors/$actorId")({
  component: ActorDetailPage,
});

function FieldBlock({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <Text size="xs" c="dimmed" mb={4}>
        {label}
      </Text>
      <div>{children}</div>
    </div>
  );
}

function ActorDetailPage() {
  const { actorId } = Route.useParams();
  const { t } = useTranslation(["metadata", "common"]);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const id = Number(actorId);
  const validId = Number.isInteger(id) && id > 0;

  const [editOpen, setEditOpen] = useState(false);

  const { data: actor, isLoading: actorLoading } = useQuery({
    ...getActorOptions({ path: { actor_id: id } }),
    enabled: validId,
  });

  const { data, isLoading, hasNextPage, isFetchingNextPage, fetchNextPage } = useInfiniteQuery({
    ...listMetadataInfiniteOptions({
      query: { limit: CHUNK, actor_id: [id] },
    }),
    enabled: validId,
    initialPageParam: 0,
    getNextPageParam: nextOffsetPageParam,
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({
      queryKey: getActorQueryKey({ path: { actor_id: id } }),
    });
    void queryClient.invalidateQueries({ queryKey: listActorsQueryKey() });
  };

  const scrapeMutation = useMutation({
    ...scrapeActorMutation(),
    onSuccess: () => {
      notifications.show({ message: t("browse.scrapeQueued"), color: "blue" });
      invalidate();
    },
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      }),
  });

  const updateMutation = useMutation({
    ...updateActorMutation(),
    onSuccess: () => {
      notifications.show({ message: t("common:toast.metadataUpdated"), color: "blue" });
      setEditOpen(false);
      invalidate();
    },
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      }),
  });

  const renameMutation = useMutation({
    ...renameFacetMutation(),
    onSuccess: () => {
      notifications.show({ message: t("actors.setDisplayNameDone"), color: "blue" });
      invalidate();
      // 展示名改写会同步影片真值, 刷新片库列表中的演员名
      void queryClient.invalidateQueries({ queryKey: listMetadataQueryKey() });
    },
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      }),
  });

  const clearMutation = useMutation({
    ...updateActorMutation(),
    onSuccess: () => {
      notifications.show({ message: t("actors.clearPersonDone"), color: "blue" });
      invalidate();
    },
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      }),
  });

  const deleteMutation = useMutation({
    ...deleteFacetMutation(),
    onSuccess: () => {
      notifications.show({ message: t("common:toast.facetDeleted"), color: "blue" });
      void navigate({ to: "/actors" });
    },
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      }),
  });

  async function handleClear() {
    const ok = await confirm({
      title: t("actors.clearPerson"),
      message: t("actors.clearPersonBody", { name: actor?.name ?? "" }),
      confirmLabel: t("actors.clearPerson"),
    });
    if (!ok) return;
    clearMutation.mutate({ path: { actor_id: id }, body: CLEARED_ACTOR_PERSON_PATCH });
  }

  async function handleSetDisplay(alias: string) {
    const ok = await confirm({
      title: t("actors.setDisplayName"),
      message: t("actors.setDisplayNameBody", { name: actor?.name ?? "", alias }),
      confirmLabel: t("actors.setDisplayName"),
    });
    if (!ok) return;
    renameMutation.mutate({ path: { kind: "actor", facet_id: id }, body: { name: alias } });
  }

  async function handleDelete() {
    const ok = await confirm({
      title: t("common:actions.delete"),
      message: t("manage.deleteFacetBody", { name: actor?.name ?? "" }),
      confirmLabel: t("common:actions.delete"),
    });
    if (!ok) return;
    deleteMutation.mutate({ path: { kind: "actor", facet_id: id } });
  }

  const items = useMemo(() => data?.pages.flatMap((p) => p.items) ?? [], [data]);
  const total = data?.pages[0]?.total ?? 0;

  if (!validId) {
    return (
      <Text c="red" size="sm">
        {t("common:status.error")}
      </Text>
    );
  }

  return (
    <Stack gap="lg">
      <Breadcrumbs>
        <Anchor component={Link} to="/actors" size="sm">
          {t("actors.title")}
        </Anchor>
        {actor && (
          <Text size="sm" c="dimmed">
            {actor.name}
          </Text>
        )}
      </Breadcrumbs>

      {actorLoading && !actor ? (
        <Loader size="sm" />
      ) : actor ? (
        <ActorHero
          actor={actor}
          scrapePending={scrapeMutation.isPending}
          clearPending={clearMutation.isPending}
          onScrape={(useCache) =>
            scrapeMutation.mutate({
              path: { actor_id: id },
              body: { use_cache: useCache },
            })
          }
          onEdit={() => setEditOpen(true)}
          onClear={() => void handleClear()}
          onDelete={() => void handleDelete()}
          onSetDisplay={(alias) => void handleSetDisplay(alias)}
        />
      ) : null}

      <Divider label={t("actors.filmography", { count: total })} labelPosition="left" />

      <PosterGrid
        items={items}
        loading={isLoading && items.length === 0}
        emptyMessage={t("empty")}
        actorBirthday={actor?.birthday}
      />

      {items.length > 0 && (
        <InfiniteScrollSentinel
          hasNextPage={Boolean(hasNextPage)}
          isFetchingNextPage={isFetchingNextPage}
          fetchNextPage={() => void fetchNextPage()}
          loadedLabel={t("common:pagination.loadedOfTotal", { loaded: items.length, total })}
        />
      )}

      {actor && (
        <ActorEditDialog
          actor={actor}
          opened={editOpen}
          onClose={() => setEditOpen(false)}
          saving={updateMutation.isPending}
          onSave={(body) => updateMutation.mutate({ path: { actor_id: id }, body })}
        />
      )}
    </Stack>
  );
}

function ActorHero({
  actor,
  scrapePending,
  clearPending,
  onScrape,
  onEdit,
  onClear,
  onDelete,
  onSetDisplay,
}: {
  actor: ActorResponse;
  scrapePending: boolean;
  clearPending: boolean;
  onScrape: (useCache: CacheKind[]) => void;
  onEdit: () => void;
  onClear: () => void;
  onDelete: () => void;
  onSetDisplay: (alias: string) => void;
}) {
  const { t } = useTranslation(["metadata", "common"]);
  const [lightboxOpen, lightbox] = useDisclosure(false);
  const imageUrls = actor.image_urls ?? [];
  const primaryImage = imageUrls[0];
  const aliases = actor.aliases ?? [];
  const sourceUrls = Object.entries(actor.source_urls ?? {}).filter(
    ([, url]) => typeof url === "string" && /^https?:\/\//i.test(url),
  );
  const measurements = [actor.bust, actor.waist, actor.hip].every((v) => v == null)
    ? null
    : `${actor.bust ?? "-"} / ${actor.waist ?? "-"} / ${actor.hip ?? "-"}`;
  const age = ageFromBirthday(actor.birthday);
  const birthdayLabel = actor.birthday
    ? age != null
      ? t("actors.birthdayWithAge", { date: actor.birthday, age })
      : actor.birthday
    : null;

  return (
    <Group align="flex-start" gap="xl" wrap="wrap" style={{ minWidth: 0 }}>
      <Box w={AVATAR_WIDTH} style={{ flexShrink: 0 }}>
        {primaryImage ? (
          <Box
            component="button"
            type="button"
            onClick={lightbox.open}
            style={{
              padding: 0,
              border: "none",
              background: "none",
              cursor: "zoom-in",
              lineHeight: 0,
              borderRadius: "var(--mantine-radius-md)",
              overflow: "hidden",
              display: "block",
            }}
          >
            <ProxyImage
              src={proxyImageUrl(primaryImage) ?? primaryImage}
              alt={actor.name}
              referrerPolicy="no-referrer"
              loading="lazy"
              style={{
                display: "block",
                width: rem(AVATAR_WIDTH),
                maxHeight: rem(360),
                objectFit: "cover",
                borderRadius: "var(--mantine-radius-md)",
              }}
              placeholder={
                <div
                  aria-hidden
                  style={{
                    width: rem(AVATAR_WIDTH),
                    height: rem(NO_PHOTO_HEIGHT),
                    borderRadius: "var(--mantine-radius-md)",
                    background: "var(--mantine-color-default-hover)",
                  }}
                />
              }
              onError={(e) => {
                const img = e.currentTarget;
                // Mantine Image fallbackSrc 等价行为: 失败时替换为空白 SVG
                if (img.src !== FALLBACK_IMAGE_SRC) img.src = FALLBACK_IMAGE_SRC;
              }}
            />
          </Box>
        ) : (
          <Box
            w={AVATAR_WIDTH}
            h={NO_PHOTO_HEIGHT}
            bg="var(--mantine-color-default-hover)"
            style={{
              borderRadius: "var(--mantine-radius-md)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <Text c="dimmed" size="sm">
              {t("actors.noPhoto")}
            </Text>
          </Box>
        )}
      </Box>

      <Stack gap="sm" style={{ flex: 1, minWidth: 0 }}>
        <Group gap="sm" align="center">
          <Title order={2}>{actor.name}</Title>
          <Badge size="lg" variant="light">
            {t("browse.count", { count: actor.count })}
          </Badge>
          <Badge size="lg" variant="outline" style={{ textTransform: "none" }}>
            {t(`browse.person.gender_${actor.gender ?? "unknown"}`)}
          </Badge>
        </Group>

        {(actor.gender ?? "unknown") === "unknown" && (
          <Text size="xs" c="dimmed">
            {t("actors.genderUnknownHint")}
          </Text>
        )}

        {aliases.length > 0 && (
          <FieldBlock label={t("browse.person.aliases")}>
            <AliasTags values={aliases} onSetDisplay={onSetDisplay} />
          </FieldBlock>
        )}
        {birthdayLabel && (
          <FieldBlock label={t("browse.person.birthday")}>
            <Text size="sm">{birthdayLabel}</Text>
          </FieldBlock>
        )}
        {actor.birthplace && (
          <FieldBlock label={t("browse.person.birthplace")}>
            <Text size="sm">{actor.birthplace}</Text>
          </FieldBlock>
        )}
        {actor.height != null && (
          <FieldBlock label={t("browse.person.height")}>
            <Text size="sm">{t("browse.person.cm", { value: actor.height })}</Text>
          </FieldBlock>
        )}
        {measurements && (
          <FieldBlock label={t("browse.person.measurements")}>
            <Text size="sm">{measurements}</Text>
          </FieldBlock>
        )}
        {actor.cup && (
          <FieldBlock label={t("browse.person.cup")}>
            <Text size="sm">{actor.cup}</Text>
          </FieldBlock>
        )}
        {actor.tagline && (
          <FieldBlock label={t("browse.person.tagline")}>
            <Text size="sm">{actor.tagline}</Text>
          </FieldBlock>
        )}
        {sourceUrls.length > 0 && (
          <FieldBlock label={t("detail.fields.sourceUrls")}>
            <Group gap={6}>
              {sourceUrls.map(([site, url]) => (
                <Badge
                  key={site}
                  component="a"
                  href={url}
                  target="_blank"
                  rel="noreferrer"
                  variant="outline"
                  color="gray"
                  size="sm"
                  rightSection={<IconExternalLink size={12} />}
                  style={{
                    cursor: "pointer",
                    textTransform: "none",
                    textDecoration: "none",
                  }}
                >
                  {site}
                </Badge>
              ))}
            </Group>
          </FieldBlock>
        )}
        {actor.overview && (
          <FieldBlock label={t("browse.person.overview")}>
            <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
              {actor.overview}
            </Text>
          </FieldBlock>
        )}

        <Group gap="xs" pt="xs">
          <Menu shadow="md" position="bottom-start">
            <Menu.Target>
              <Button
                size="xs"
                variant="light"
                leftSection={<IconRefresh size={14} />}
                loading={scrapePending}
              >
                {t("browse.scrapeActor")}
              </Button>
            </Menu.Target>
            <Menu.Dropdown>
              <Menu.Item onClick={() => onScrape(["metadata", "trans"])}>
                <Text size="sm">{t("common:actions.scrapeNormal")}</Text>
                <Text size="xs" c="dimmed">
                  {t("common:actions.scrapeNormalDesc")}
                </Text>
              </Menu.Item>
              <Menu.Item onClick={() => onScrape([])}>
                <Text size="sm">{t("common:actions.scrapeForce")}</Text>
                <Text size="xs" c="dimmed">
                  {t("common:actions.scrapeForceDesc")}
                </Text>
              </Menu.Item>
            </Menu.Dropdown>
          </Menu>
          <Link
            to="/meta"
            search={metaSearchForFacet("actor", actor.id)}
            style={{ textDecoration: "none" }}
          >
            <Button
              size="xs"
              variant="light"
              leftSection={<IconFilter size={14} />}
              component="span"
            >
              {t("actors.goToLibrary")}
            </Button>
          </Link>
          <Button size="xs" variant="light" leftSection={<IconPencil size={14} />} onClick={onEdit}>
            {t("common:actions.edit")}
          </Button>
          <Button
            size="xs"
            variant="light"
            leftSection={<IconEraser size={14} />}
            loading={clearPending}
            onClick={onClear}
          >
            {t("actors.clearPerson")}
          </Button>
          <Button
            size="xs"
            variant="light"
            color="red"
            leftSection={<IconTrash size={14} />}
            onClick={onDelete}
          >
            {t("common:actions.delete")}
          </Button>
        </Group>
      </Stack>

      {lightboxOpen && imageUrls.length > 0 && (
        <FanartLightbox images={imageUrls} initialIndex={0} onClose={lightbox.close} />
      )}
    </Group>
  );
}

function AliasTags({
  values,
  onSetDisplay,
}: {
  values: string[];
  onSetDisplay: (alias: string) => void;
}) {
  const { t } = useTranslation(["metadata", "common"]);
  return (
    <Group gap={6}>
      {values.map((value) => (
        <Button
          key={value}
          variant="light"
          size="compact-sm"
          style={{ textTransform: "none", fontWeight: 500 }}
          title={t("actors.setDisplayName")}
          rightSection={<IconStar size={12} />}
          onClick={() => onSetDisplay(value)}
        >
          {value}
        </Button>
      ))}
    </Group>
  );
}
