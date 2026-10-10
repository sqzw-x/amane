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
  IconCheck,
  IconCrop,
  IconEraser,
  IconExternalLink,
  IconFilter,
  IconLock,
  IconLockOpen,
  IconPencil,
  IconRefresh,
  IconStar,
  IconTrash,
} from "@tabler/icons-react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { type ReactNode, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  batchActorUserTagsMutation,
  clearActorPersonMutation,
  deleteFacetMutation,
  getActorOptions,
  getActorQueryKey,
  listActorsQueryKey,
  listMetadataInfiniteOptions,
  listMetadataQueryKey,
  renameFacetMutation,
  scrapeActorMutation,
  setActorLocksMutation,
  updateActorMutation,
} from "@/client/@tanstack/react-query.gen";
import type { ActorField, ActorResponse, CacheKind } from "@/client/types.gen";
import { InfiniteScrollSentinel } from "@/components/common/infinite-scroll-sentinel";
import { SortMenu } from "@/components/common/sort-menu";
import { ActorAvatarCropDialog } from "@/components/media/actor-avatar-crop-dialog";
import { ActorEditDialog } from "@/components/media/actor-edit-dialog";
import { ActorUserTags } from "@/components/media/actor-user-tags";
import { FavoriteStarToggle } from "@/components/media/user-tag-star";
import { ClearPersonNotice } from "@/components/media/clear-person-notice";
import { FanartLightbox } from "@/components/media/fanart-lightbox";
import { LockChip, LockToggle, type LockProps } from "@/components/media/field-lock";
import { PosterGrid } from "@/components/media/poster-grid";
import { ACTOR_FIELD_LABEL_KEY, LOCKABLE_ACTOR_FIELDS } from "@/lib/actors/fields";
import { extractErrorMessage } from "@/lib/api-error";
import { confirm } from "@/lib/confirm";
import { metaSearchForFacet } from "@/lib/facets";
import { ageFromBirthday } from "@/lib/format-birthday";
import { nextOffsetPageParam } from "@/lib/infinite-list";
import { DEFAULT_METADATA_SORT_PREFERENCE, METADATA_SORT_OPTIONS } from "@/lib/media/browse";
import { proxyImageUrl } from "@/lib/utils";
import { ProxyImage } from "@/components/media/proxy-image";
import { useUIStore } from "@/stores/ui";

const CHUNK = 30;
const AVATAR_WIDTH = 240;

/** 无图占位高度: 主图排队占位与无照片占位共用. */
const NO_PHOTO_HEIGHT = 320;

/** 主图加载失败时的占位 (对齐原 Mantine Image fallbackSrc 行为). */
const FALLBACK_IMAGE_SRC = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'/%3E";

export const Route = createFileRoute("/actors/$actorId")({
  component: ActorDetailPage,
});

function FieldBlock({
  label,
  children,
  lock,
}: {
  label: string;
  children: ReactNode;
  lock?: LockProps<ActorField>;
}) {
  return (
    <div>
      <Group gap={4} wrap="nowrap" align="center" mb={4}>
        <Text size="xs" c="dimmed">
          {label}
        </Text>
        {lock && <LockToggle {...lock} />}
      </Group>
      <div>{children}</div>
    </div>
  );
}

function EmptyValue() {
  return (
    <Text size="sm" c="dimmed">
      —
    </Text>
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
  const [cropOpen, setCropOpen] = useState(false);
  const actorWorksSort = useUIStore((state) => state.actorWorksSort);
  const setActorWorksSort = useUIStore((state) => state.setActorWorksSort);

  const { data: actor, isLoading: actorLoading } = useQuery({
    ...getActorOptions({ path: { actor_id: id } }),
    enabled: validId,
  });

  const { data, isLoading, hasNextPage, isFetchingNextPage, fetchNextPage } = useInfiniteQuery({
    ...listMetadataInfiniteOptions({
      query: {
        limit: CHUNK,
        actor_id: [id],
        sort_by: actorWorksSort.sort_by,
        order: actorWorksSort.order,
      },
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
    ...clearActorPersonMutation(),
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

  const [pendingLock, setPendingLock] = useState<ActorField | null>(null);
  const [lockTarget, setLockTarget] = useState<{ id: number; fields: ActorField[] } | null>(null);
  // 锁写入串行化: 在途点击合并为最后目标, 结算后补发.
  const queuedLocksRef = useRef<{ id: number; fields: ActorField[] } | null>(null);
  const lockMutation = useMutation({
    ...setActorLocksMutation(),
    onSuccess: async (_data, variables) => {
      // 失效按本次请求的条目; 等重取完成再撤销乐观显示, 避免图标回退翻转.
      await queryClient.invalidateQueries({
        queryKey: getActorQueryKey({ path: { actor_id: variables.path.actor_id } }),
      });
    },
    onError: (err) => {
      queuedLocksRef.current = null;
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      });
    },
    onSettled: () => {
      const queued = queuedLocksRef.current;
      queuedLocksRef.current = null;
      if (queued) {
        // 条目已切换时按原条目补发.
        lockMutation.mutate({ path: { actor_id: queued.id }, body: { fields: queued.fields } });
        return;
      }
      setPendingLock(null);
      setLockTarget(null);
    },
  });
  const serverLockedFields = useMemo(
    () => new Set<ActorField>(actor?.locked_fields ?? []),
    [actor?.locked_fields],
  );
  // 乐观显示目标集合; 在途状态不扩散到其它锁控件.
  const lockedFields = useMemo(
    () =>
      lockTarget && lockTarget.id === id
        ? new Set<ActorField>(lockTarget.fields)
        : serverLockedFields,
    [lockTarget, id, serverLockedFields],
  );
  const allLocked = LOCKABLE_ACTOR_FIELDS.every((field) => lockedFields.has(field));

  function applyLocks(fields: readonly ActorField[], pending: ActorField | null = null) {
    const target = [...fields];
    setPendingLock(pending);
    setLockTarget({ id, fields: target });
    if (lockMutation.isPending) {
      queuedLocksRef.current = { id, fields: target };
      return;
    }
    lockMutation.mutate({ path: { actor_id: id }, body: { fields: target } });
  }

  function toggleLock(field: ActorField) {
    const next = new Set(lockedFields);
    if (next.has(field)) {
      next.delete(field);
    } else {
      next.add(field);
    }
    applyLocks(
      LOCKABLE_ACTOR_FIELDS.filter((candidate) => next.has(candidate)),
      field,
    );
  }

  const lockProps = (field: ActorField): LockProps<ActorField> => ({
    field,
    locked: lockedFields.has(field),
    busy: pendingLock === field,
    onToggleLock: toggleLock,
  });

  async function handleClear() {
    const ok = await confirm({
      title: t("actors.clearPerson"),
      message: (
        <ClearPersonNotice first={t("actors.clearPersonFields", { name: actor?.name ?? "" })} />
      ),
      confirmLabel: t("actors.clearPerson"),
    });
    if (!ok) return;
    clearMutation.mutate({ path: { actor_id: id } });
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
          lockProps={lockProps}
          lockedFields={lockedFields}
          allLocked={allLocked}
          locksPending={lockMutation.isPending}
          onToggleLock={toggleLock}
          onApplyLocks={applyLocks}
          onScrape={(useCache) =>
            scrapeMutation.mutate({
              path: { actor_id: id },
              body: { use_cache: useCache },
            })
          }
          onEdit={() => setEditOpen(true)}
          onCrop={() => setCropOpen(true)}
          onClear={() => void handleClear()}
          onDelete={() => void handleDelete()}
          onSetDisplay={(alias) => void handleSetDisplay(alias)}
        />
      ) : null}

      <Group gap="sm" align="center" wrap="wrap" style={{ minWidth: 0 }}>
        <Divider
          label={t("actors.filmography", { count: total })}
          labelPosition="left"
          style={{ flex: "1 1 12rem" }}
        />
        <SortMenu
          options={METADATA_SORT_OPTIONS.map((option) => ({
            value: option.value,
            label: t(option.labelKey),
          }))}
          sortBy={actorWorksSort.sort_by}
          order={actorWorksSort.order}
          defaultSortBy={DEFAULT_METADATA_SORT_PREFERENCE.sort_by}
          defaultOrder={DEFAULT_METADATA_SORT_PREFERENCE.order}
          onChange={(sortBy, order) => setActorWorksSort({ sort_by: sortBy, order })}
        />
      </Group>

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

      {actor && (actor.image_urls?.[0] ?? "") !== "" && (
        <ActorAvatarCropDialog
          opened={cropOpen}
          onClose={() => setCropOpen(false)}
          actorId={actor.id}
          imageUrl={actor.image_urls?.[0] ?? ""}
          onSuccess={invalidate}
        />
      )}
    </Stack>
  );
}

function ActorHero({
  actor,
  scrapePending,
  clearPending,
  lockProps,
  lockedFields,
  allLocked,
  locksPending,
  onToggleLock,
  onApplyLocks,
  onScrape,
  onEdit,
  onCrop,
  onClear,
  onDelete,
  onSetDisplay,
}: {
  actor: ActorResponse;
  scrapePending: boolean;
  clearPending: boolean;
  lockProps: (field: ActorField) => LockProps<ActorField>;
  lockedFields: ReadonlySet<ActorField>;
  allLocked: boolean;
  locksPending: boolean;
  onToggleLock: (field: ActorField) => void;
  onApplyLocks: (fields: readonly ActorField[]) => void;
  onScrape: (useCache: CacheKind[]) => void;
  onEdit: () => void;
  onCrop: () => void;
  onClear: () => void;
  onDelete: () => void;
  onSetDisplay: (alias: string) => void;
}) {
  const { t } = useTranslation(["metadata", "common"]);
  const [lightboxOpen, lightbox] = useDisclosure(false);
  const queryClient = useQueryClient();
  const favoriteMutation = useMutation(batchActorUserTagsMutation());
  const imageUrls = actor.image_urls ?? [];
  const primaryImage = imageUrls[0];
  const aliases = actor.aliases ?? [];
  const sourceUrls = Object.entries(actor.source_urls ?? {}).filter(
    ([, url]) => typeof url === "string" && /^https?:\/\//i.test(url),
  );
  const measurements = [actor.bust, actor.waist, actor.hip].every((v) => v == null)
    ? null
    : `${actor.bust ?? "-"} / ${actor.waist ?? "-"} / ${actor.hip ?? "-"}`;
  const measurementLocked = (["bust", "waist", "hip"] as const).some((field) =>
    lockedFields.has(field),
  );
  const age = ageFromBirthday(actor.birthday);
  const birthdayLabel = actor.birthday
    ? age != null
      ? t("actors.birthdayWithAge", { date: actor.birthday, age })
      : actor.birthday
    : null;

  /** 收藏星标的提交入口; 失败时抛出, 由星标回滚乐观状态并提示. 详情重取由星标统一发起. */
  async function applyFavorite(favorited: boolean, tagId: number) {
    await favoriteMutation.mutateAsync({
      body: {
        ids: [actor.id],
        user_tag_ids: [tagId],
        action: favorited ? "attach" : "detach",
      },
    });
    void queryClient.invalidateQueries({ queryKey: listActorsQueryKey() });
  }

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
        {primaryImage && (
          <Button
            size="xs"
            variant="light"
            leftSection={<IconCrop size={14} />}
            mt="xs"
            fullWidth
            onClick={onCrop}
          >
            {t("actors.cropAvatar.action")}
          </Button>
        )}
        <Box mt="xs">
          <LockChip label={t("actors.imagesEdit")} {...lockProps("image_urls")} />
        </Box>
      </Box>

      {/* basis 不能为 0: 换行按 flex-basis 判定, 取 0 时信息列永不换行, 窄屏只会被压成几十像素宽. */}
      <Stack gap="sm" style={{ flex: "1 1 20rem", minWidth: 0 }}>
        <Group gap="sm" align="center">
          <Title order={2}>{actor.name}</Title>
          <FavoriteStarToggle
            tags={actor.user_tags}
            apply={applyFavorite}
            queryKey={getActorQueryKey({ path: { actor_id: actor.id } })}
          />
          <Badge size="lg" variant="light">
            {t("browse.count", { count: actor.count })}
          </Badge>
          <Badge size="lg" variant="outline" style={{ textTransform: "none" }}>
            {t(`browse.person.gender_${actor.gender ?? "unknown"}`)}
          </Badge>
          <LockToggle {...lockProps("gender")} />
        </Group>

        {(actor.gender ?? "unknown") === "unknown" && (
          <Text size="xs" c="dimmed">
            {t("actors.genderUnknownHint")}
          </Text>
        )}

        {(aliases.length > 0 || lockedFields.has("aliases")) && (
          <FieldBlock label={t("browse.person.aliases")} lock={lockProps("aliases")}>
            {aliases.length > 0 ? (
              <AliasTags values={aliases} onSetDisplay={onSetDisplay} />
            ) : (
              <EmptyValue />
            )}
          </FieldBlock>
        )}
        {(birthdayLabel || lockedFields.has("birthday")) && (
          <FieldBlock label={t("browse.person.birthday")} lock={lockProps("birthday")}>
            {birthdayLabel ? <Text size="sm">{birthdayLabel}</Text> : <EmptyValue />}
          </FieldBlock>
        )}
        {(actor.birthplace || lockedFields.has("birthplace")) && (
          <FieldBlock label={t("browse.person.birthplace")} lock={lockProps("birthplace")}>
            {actor.birthplace ? <Text size="sm">{actor.birthplace}</Text> : <EmptyValue />}
          </FieldBlock>
        )}
        {(actor.height != null || lockedFields.has("height")) && (
          <FieldBlock label={t("browse.person.height")} lock={lockProps("height")}>
            {actor.height != null ? (
              <Text size="sm">{t("browse.person.cm", { value: actor.height })}</Text>
            ) : (
              <EmptyValue />
            )}
          </FieldBlock>
        )}
        {(measurements || measurementLocked) && (
          <div>
            <Group gap={4} wrap="nowrap" align="center" mb={4}>
              <Text size="xs" c="dimmed">
                {t("browse.person.measurements")}
              </Text>
              <LockToggle {...lockProps("bust")} />
              <LockToggle {...lockProps("waist")} />
              <LockToggle {...lockProps("hip")} />
            </Group>
            <Text size="sm">{measurements ?? "- / - / -"}</Text>
          </div>
        )}
        {(actor.cup || lockedFields.has("cup")) && (
          <FieldBlock label={t("browse.person.cup")} lock={lockProps("cup")}>
            {actor.cup ? <Text size="sm">{actor.cup}</Text> : <EmptyValue />}
          </FieldBlock>
        )}
        {(actor.tagline || lockedFields.has("tagline")) && (
          <FieldBlock label={t("browse.person.tagline")} lock={lockProps("tagline")}>
            {actor.tagline ? <Text size="sm">{actor.tagline}</Text> : <EmptyValue />}
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
        {(actor.overview || lockedFields.has("overview")) && (
          <FieldBlock label={t("browse.person.overview")} lock={lockProps("overview")}>
            {actor.overview ? (
              <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
                {actor.overview}
              </Text>
            ) : (
              <EmptyValue />
            )}
          </FieldBlock>
        )}

        <FieldBlock label={t("detail.userTags")}>
          <ActorUserTags actorId={actor.id} attached={actor.user_tags ?? []} />
        </FieldBlock>

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
          <Menu shadow="md" position="bottom-start">
            <Menu.Target>
              <Button
                size="xs"
                variant="light"
                color={allLocked ? "yellow" : "gray"}
                leftSection={<IconLock size={14} />}
                loading={locksPending}
              >
                {t("actors.lockManage")}
              </Button>
            </Menu.Target>
            <Menu.Dropdown>
              <Menu.Item onClick={() => onApplyLocks(allLocked ? [] : LOCKABLE_ACTOR_FIELDS)}>
                {allLocked ? t("lock.clearAll") : t("lock.all")}
              </Menu.Item>
              <Menu.Divider />
              {LOCKABLE_ACTOR_FIELDS.map((field) => (
                <Menu.Item
                  key={field}
                  onClick={() => onToggleLock(field)}
                  leftSection={
                    lockedFields.has(field) ? <IconLock size={14} /> : <IconLockOpen size={14} />
                  }
                  rightSection={lockedFields.has(field) ? <IconCheck size={14} /> : undefined}
                >
                  {t(ACTOR_FIELD_LABEL_KEY[field])}
                </Menu.Item>
              ))}
            </Menu.Dropdown>
          </Menu>
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
  return (
    <Group gap={6}>
      {values.map((value) => (
        <Button
          key={value}
          variant="light"
          size="compact-sm"
          style={{ textTransform: "none", fontWeight: 500 }}
          rightSection={<IconStar size={12} />}
          onClick={() => onSetDisplay(value)}
        >
          {value}
        </Button>
      ))}
    </Group>
  );
}
