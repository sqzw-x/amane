import {
  ActionIcon,
  Alert,
  Badge,
  Box,
  Button,
  Card,
  Group,
  Loader,
  Menu,
  Modal,
  SimpleGrid,
  Stack,
  Text,
  Title,
  Tooltip,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import {
  IconAlertCircle,
  IconCheck,
  IconCrop,
  IconExternalLink,
  IconGitMerge,
  IconLock,
  IconLockOpen,
  IconPencil,
  IconPhotoOff,
  IconPlayerPlay,
  IconRefresh,
  IconStar,
  IconTrash,
  IconX,
} from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { z } from "zod";
import {
  batchMetadataUserTagsMutation,
  createUserTagsMutation,
  deleteMetadataMutation,
  getMetadataOptions,
  getMetadataQueryKey,
  listFacetsOptions,
  listFacetsQueryKey,
  listMetadataQueryKey,
  setMetadataLocksMutation,
  submitTaskMutation,
  updateMetadataMutation,
} from "@/client/@tanstack/react-query.gen";
import { getMetadataSchema } from "@/client/sdk.gen";
import type { MetadataField, MetadataResponse } from "@/client/types.gen";
import { FacetBadge } from "@/components/media/facet-badge";
import { UserTagActions } from "@/components/media/user-tag-add";
import { FanartLightbox, FanartStrip } from "@/components/media/fanart-lightbox";
import { FilePhaseBadges, FilePhaseOverlay } from "@/components/media/file-phase-badges";
import { PosterCropDialog } from "@/components/media/poster-crop-dialog";
import { MergeDialog } from "@/components/metadata/merge-dialog";
import { type JSONSchemaObject, resolveSchema } from "@/components/schema-form/schema";
import { SchemaForm } from "@/components/schema-form/schema-form";
import { extractErrorMessage } from "@/lib/api-error";
import { confirm } from "@/lib/confirm";
import { USER_TAG_FACET_LIST } from "@/lib/facets";
import { FIELD_LABEL_KEY, LOCKABLE_FIELDS } from "@/lib/media/metadata-fields";
import { proxyImageUrl } from "@/lib/utils";
import { ProxyImage } from "@/components/media/proxy-image";
import { CommentSection } from "@/components/media/comment-section";
import { PlaybackPanel } from "@/components/media/playback-panel";
import type { SeekRequest } from "@/components/media/playback-player";

/**
 * `t` 是评论时间戳跳转的目标秒数: 写进地址栏以便分享与刷新后定位, 因此可以非整数以外的任何值
 * 都按未提供处理.
 */
const metaDetailSearchSchema = z.object({
  t: z.coerce.number().int().min(0).optional().catch(undefined),
});

export const Route = createFileRoute("/meta/$metadataId")({
  validateSearch: metaDetailSearchSchema,
  component: TitleDetailPage,
});

function formatRuntime(minutes?: number | null): string | null {
  if (!minutes) return null;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

type LockProps = {
  field: MetadataField;
  locked: boolean;
  busy: boolean;
  onToggleLock: (field: MetadataField) => void;
};

/** 字段旁的小锁: 点击切换该字段的锁定状态; 锁定后刮削不再覆盖该字段. */
function LockToggle({ field, locked, busy, onToggleLock }: LockProps) {
  const { t } = useTranslation("metadata");
  return (
    <Tooltip label={locked ? t("lock.toggleLocked") : t("lock.toggleUnlocked")} withArrow>
      <ActionIcon
        size="xs"
        variant="subtle"
        color={locked ? "yellow" : "gray"}
        aria-label={locked ? t("lock.locked") : t("lock.unlocked")}
        disabled={busy}
        onClick={() => onToggleLock(field)}
        // 未锁定时弱化, 避免比字段名更醒目.
        style={{ opacity: locked ? 1 : 0.45 }}
      >
        {locked ? <IconLock size={12} /> : <IconLockOpen size={12} />}
      </ActionIcon>
    </Tooltip>
  );
}

/** 无字段块的资源 (封面 / 海报 / 预告片) 使用的带文字锁开关. */
function LockChip({ label, ...lock }: LockProps & { label: string }) {
  const { t } = useTranslation("metadata");
  return (
    <Tooltip label={lock.locked ? t("lock.toggleLocked") : t("lock.toggleUnlocked")} withArrow>
      <Button
        size="compact-xs"
        variant={lock.locked ? "light" : "subtle"}
        color={lock.locked ? "yellow" : "gray"}
        leftSection={lock.locked ? <IconLock size={12} /> : <IconLockOpen size={12} />}
        disabled={lock.busy}
        onClick={() => lock.onToggleLock(lock.field)}
        style={lock.locked ? undefined : { opacity: 0.65 }}
      >
        {label}
      </Button>
    </Tooltip>
  );
}

function FieldBlock({
  label,
  children,
  lock,
}: {
  label: string;
  children: ReactNode;
  lock?: LockProps;
}) {
  return (
    <div>
      <Group gap={4} wrap="nowrap" align="center">
        <Text size="xs" c="dimmed">
          {label}
        </Text>
        {lock && <LockToggle {...lock} />}
      </Group>
      <div>{children}</div>
    </div>
  );
}

function TitleDetailPage() {
  const { metadataId } = Route.useParams();
  const { t } = useTranslation(["metadata", "common"]);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [editOpen, setEditOpen] = useState(false);
  const [mergeOpen, setMergeOpen] = useState(false);
  const [cropOpen, setCropOpen] = useState(false);
  const [coverLightboxOpen, coverLightbox] = useDisclosure(false);
  const [playingTrailer, setPlayingTrailer] = useState(false);
  const [thumbBroken, setThumbBroken] = useState(false);
  const [posterBroken, setPosterBroken] = useState(false);
  // 评论时间戳的跳转: 本地请求负责即时响应 (同一秒连点也要重新触发), 地址栏的 `t` 负责分享与刷新定位.
  const [seekRequest, setSeekRequest] = useState<SeekRequest | null>(null);
  const [canSeek, setCanSeek] = useState(false);
  const seekFromUrlRef = useRef<number | null>(null);
  // 跳转请求在处理之后会被清空, 因此 nonce 不能取自请求本身: 每次都从 1 重新计数时, 播放器会把
  // 新请求当成已处理的那一条而忽略. 计数只增不减, 见 `SeekRequest`.
  const seekNonceRef = useRef(0);
  const { t: urlSeekSeconds } = Route.useSearch();

  useEffect(() => {
    if (urlSeekSeconds == null || seekFromUrlRef.current === urlSeekSeconds) {
      return;
    }
    seekFromUrlRef.current = urlSeekSeconds;
    seekNonceRef.current += 1;
    setSeekRequest({ seconds: urlSeekSeconds, nonce: seekNonceRef.current });
  }, [urlSeekSeconds]);

  const requestSeek = useCallback(
    (seconds: number) => {
      seekFromUrlRef.current = seconds;
      seekNonceRef.current += 1;
      setSeekRequest({ seconds, nonce: seekNonceRef.current });
      // replace: 时间戳是定位而不是导航, 不该在历史里堆一串记录.
      // resetScroll: 路由默认在位置提交后把页面滚动到顶部, 与定位的语义冲突; 滚动由播放器按需调整.
      void navigate({
        to: "/meta/$metadataId",
        params: { metadataId },
        search: (prev) => ({ ...prev, t: seconds }),
        replace: true,
        resetScroll: false,
      });
    },
    [metadataId, navigate],
  );

  const id = Number(metadataId);
  const validId = Number.isInteger(id) && id > 0;
  const [prevId, setPrevId] = useState(id);
  if (id !== prevId) {
    setPrevId(id);
    setPlayingTrailer(false);
    setThumbBroken(false);
    setPosterBroken(false);
  }

  const { data, isLoading, isError } = useQuery({
    ...getMetadataOptions({ path: { metadata_id: id } }),
    enabled: validId,
  });

  const invalidateDetail = () => {
    void queryClient.invalidateQueries({
      queryKey: getMetadataQueryKey({ path: { metadata_id: id } }),
    });
  };

  const scrapeMutation = useMutation({
    ...submitTaskMutation(),
    onSuccess: () => {
      notifications.show({ message: t("common:toast.scrapeStarted"), color: "blue" });
      void queryClient.invalidateQueries({ queryKey: listMetadataQueryKey() });
    },
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      }),
  });

  const deleteMutation = useMutation({
    ...deleteMetadataMutation(),
    onSuccess: () => {
      notifications.show({ message: t("common:toast.metadataDeleted"), color: "blue" });
      void queryClient.invalidateQueries({ queryKey: listMetadataQueryKey() });
      void navigate({ to: "/meta" });
    },
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      }),
  });

  async function handleDelete() {
    const ok = await confirm({
      title: t("common:actions.delete"),
      message: t("confirmDelete", { id }),
      confirmLabel: t("common:actions.delete"),
    });
    if (!ok) return;
    deleteMutation.mutate({ path: { metadata_id: id } });
  }

  const { data: userTagOptions } = useQuery(listFacetsOptions(USER_TAG_FACET_LIST));
  const { data: rawSchema } = useQuery({
    queryKey: ["metadata-schema"],
    queryFn: async () => {
      const { data: schemaData } = await getMetadataSchema();
      // OpenAPI schema 运行时对象 → Schema 表单内部类型.
      return schemaData as JSONSchemaObject;
    },
  });
  const editSchema = useMemo(
    () => (rawSchema ? resolveSchema(rawSchema, rawSchema) : null),
    [rawSchema],
  );

  const updateMutation = useMutation({
    ...updateMetadataMutation(),
    onSuccess: () => {
      notifications.show({ message: t("common:toast.metadataUpdated"), color: "blue" });
      setEditOpen(false);
      invalidateDetail();
      void queryClient.invalidateQueries({ queryKey: listMetadataQueryKey() });
    },
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      }),
  });

  const ensureTagsMutation = useMutation(createUserTagsMutation());
  const applyTagsMutation = useMutation(batchMetadataUserTagsMutation());

  const [pendingLock, setPendingLock] = useState<MetadataField | null>(null);
  const [lockTarget, setLockTarget] = useState<{ id: number; fields: MetadataField[] } | null>(
    null,
  );
  // 锁写入单飞: 在途期间的点击合并为最后目标, 结算后补发, 避免并发 PUT 乱序丢切换.
  const queuedLocksRef = useRef<{ id: number; fields: MetadataField[] } | null>(null);
  const lockMutation = useMutation({
    ...setMetadataLocksMutation(),
    onSuccess: async (_data, variables) => {
      // 按本次请求的条目失效, 补发旧条目时不会误刷当前页; 等详情重取完成再撤销乐观显示.
      await queryClient.invalidateQueries({
        queryKey: getMetadataQueryKey({ path: { metadata_id: variables.path.metadata_id } }),
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
        // 条目已切换时也补发其最后目标; 乐观显示按条目 id 生效, 不受影响.
        lockMutation.mutate({ path: { metadata_id: queued.id }, body: { fields: queued.fields } });
        return;
      }
      setPendingLock(null);
      setLockTarget(null);
    },
  });
  const serverLockedFields = useMemo(
    () => new Set<MetadataField>(data?.metadata.locked_fields ?? []),
    [data?.metadata.locked_fields],
  );
  // 目标集合先于服务端确认显示; 在途请求期间其他锁控件保持原样式, 只把点击并入单飞队列.
  const lockedFields = useMemo(
    () =>
      lockTarget && lockTarget.id === id
        ? new Set<MetadataField>(lockTarget.fields)
        : serverLockedFields,
    [lockTarget, id, serverLockedFields],
  );

  async function handleAddTags(selection: { tagIds: number[]; createNames: string[] }) {
    const names = [
      ...new Set(
        selection.createNames.map((name) => name.trim()).filter((name) => name.length > 0),
      ),
    ];
    if (selection.tagIds.length === 0 && names.length === 0) return;
    try {
      // 新建与挂载各一次请求: 名称 → id 由批量创建端点取回, 已存在的名称直接复用
      let createdIds: number[] = [];
      let created = 0;
      if (names.length > 0) {
        const ensured = await ensureTagsMutation.mutateAsync({ body: { names } });
        createdIds = ensured.items.map((tag) => tag.id);
        created = ensured.created;
        // 标签此刻已落库; created 为 0 也可能是别人刚建的同名行落在候选之外, 故一律重取
        void queryClient.invalidateQueries({ queryKey: listFacetsQueryKey(USER_TAG_FACET_LIST) });
      }
      await applyTagsMutation.mutateAsync({
        body: { ids: [id], user_tag_ids: [...selection.tagIds, ...createdIds], action: "attach" },
      });
      notifications.show({
        message: created > 0 ? t("common:toast.userTagCreated") : t("common:toast.userTagAttached"),
        color: "blue",
      });
      invalidateDetail();
    } catch (err) {
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      });
    }
  }

  async function handleDetachTags(tagIds: number[]) {
    if (tagIds.length === 0) return;
    try {
      await applyTagsMutation.mutateAsync({
        body: { ids: [id], user_tag_ids: tagIds, action: "detach" },
      });
      notifications.show({ message: t("common:toast.userTagDetached"), color: "blue" });
      invalidateDetail();
    } catch (err) {
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      });
    }
  }

  if (!validId) {
    return (
      <Alert color="red" icon={<IconAlertCircle size={18} />}>
        {t("invalidId")}
      </Alert>
    );
  }

  if (isLoading) {
    return <Loader />;
  }

  if (isError || !data) {
    return (
      <Alert color="red" icon={<IconAlertCircle size={18} />}>
        {t("notFound")}
      </Alert>
    );
  }

  const item: MetadataResponse = data.metadata;
  // 详情只展示一张主图: 优先横版封面 (thumb), 失败或不存在时回退竖版海报.
  const thumbSrc = item.thumb_url ?? item.thumb_urls?.[0] ?? null;
  const posterSrc = item.poster_url ?? item.poster_urls?.[0] ?? null;
  const coverSrc =
    thumbSrc && !thumbBroken ? thumbSrc : posterSrc && !posterBroken ? posterSrc : null;
  const coverUrl = proxyImageUrl(coverSrc);
  const coverFailed = Boolean((thumbSrc || posterSrc) && !coverUrl);
  const hasExtrafanart = item.extrafanart && item.extrafanart.length > 0;
  const runtime = formatRuntime(item.runtime);
  const allLocked = LOCKABLE_FIELDS.every((field) => lockedFields.has(field));

  function applyLocks(fields: readonly MetadataField[], pending: MetadataField | null = null) {
    const target = [...fields];
    setPendingLock(pending);
    setLockTarget({ id, fields: target });
    if (lockMutation.isPending) {
      // 单飞: 在途请求结束后补发最后目标, 中间的多次点击合并.
      queuedLocksRef.current = { id, fields: target };
      return;
    }
    lockMutation.mutate({ path: { metadata_id: id }, body: { fields: target } });
  }

  function toggleLock(field: MetadataField) {
    const next = new Set(lockedFields);
    if (next.has(field)) {
      next.delete(field);
    } else {
      next.add(field);
    }
    applyLocks(
      LOCKABLE_FIELDS.filter((candidate) => next.has(candidate)),
      field,
    );
  }

  const lockProps = (field: MetadataField): LockProps => ({
    field,
    locked: lockedFields.has(field),
    busy: pendingLock === field,
    onToggleLock: toggleLock,
  });

  function handleCoverError() {
    if (coverSrc && coverSrc === thumbSrc) {
      setThumbBroken(true);
      return;
    }
    if (coverSrc && coverSrc === posterSrc) {
      setPosterBroken(true);
    }
  }

  const trailerPlayButton = item.trailer_url ? (
    <ActionIcon
      variant="filled"
      color="dark"
      radius="xl"
      size={56}
      aria-label={t("detail.playTrailer")}
      onClick={() => setPlayingTrailer(true)}
      style={{
        position: "absolute",
        top: "50%",
        left: "50%",
        transform: "translate(-50%, -50%)",
        opacity: 0.88,
        zIndex: 1,
      }}
    >
      <IconPlayerPlay size={28} />
    </ActionIcon>
  ) : null;

  return (
    // 下沿留出的空白比其余三边大得多: 滚到底时末尾的卡片不贴视口底边, 还能再滚一截.
    <Stack gap="md" pb="calc(var(--amane-vh) * 0.1)">
      <Group align="flex-start" wrap="wrap" gap="lg" style={{ flexDirection: "row-reverse" }}>
        <Stack gap="xs" style={{ flex: "3 1 360px", minWidth: 280 }}>
          <div
            style={{
              position: "relative",
              width: "100%",
              borderRadius: "var(--mantine-radius-md)",
              overflow: "hidden",
              background: playingTrailer ? "#000" : "var(--mantine-color-default-hover)",
              // 无可展示封面时用 16:9 占位; 有封面则由 img 撑住尺寸, 播放不跳变.
              ...(!coverUrl ? { aspectRatio: "16 / 9" as const } : {}),
            }}
          >
            {coverUrl && (
              <ProxyImage
                key={coverUrl}
                src={coverUrl}
                alt={playingTrailer ? "" : item.number}
                referrerPolicy="no-referrer"
                aria-hidden={playingTrailer}
                onError={handleCoverError}
                style={{
                  width: "100%",
                  height: "auto",
                  display: "block",
                  // 播放时保留原图占位, 避免切换到 video 后比例跳变.
                  visibility: playingTrailer ? "hidden" : "visible",
                }}
                placeholder={<div style={{ aspectRatio: "16 / 9" }} aria-hidden />}
              />
            )}
            {!playingTrailer && <FilePhaseOverlay phase={item.file_phase} />}

            {playingTrailer && item.trailer_url ? (
              <>
                <video
                  key={item.trailer_url}
                  src={item.trailer_url}
                  controls
                  autoPlay
                  playsInline
                  style={{
                    position: "absolute",
                    inset: 0,
                    width: "100%",
                    height: "100%",
                    objectFit: "contain",
                    background: "#000",
                  }}
                />
                <ActionIcon
                  variant="filled"
                  color="dark"
                  radius="xl"
                  size="sm"
                  aria-label={t("detail.closeTrailer")}
                  onClick={() => setPlayingTrailer(false)}
                  style={{ position: "absolute", top: 8, right: 8, zIndex: 1 }}
                >
                  <IconX size={14} />
                </ActionIcon>
              </>
            ) : coverUrl ? (
              <>
                <button
                  type="button"
                  onClick={coverLightbox.open}
                  aria-label={item.number}
                  style={{
                    position: "absolute",
                    inset: 0,
                    padding: 0,
                    border: "none",
                    background: "none",
                    cursor: "zoom-in",
                  }}
                />
                {trailerPlayButton}
              </>
            ) : (
              <div
                style={{
                  position: "absolute",
                  inset: 0,
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: 10,
                  padding: 16,
                }}
              >
                <IconPhotoOff
                  size={36}
                  stroke={1.25}
                  color="var(--mantine-color-dimmed)"
                  aria-hidden
                />
                <Text size="sm" ff="monospace" c="dimmed" ta="center">
                  {item.number}
                </Text>
                {coverFailed && (
                  <Text size="xs" c="dimmed" ta="center">
                    {t("detail.coverUnavailable")}
                  </Text>
                )}
                {item.trailer_url && (
                  <ActionIcon
                    variant="filled"
                    color="dark"
                    radius="xl"
                    size={48}
                    aria-label={t("detail.playTrailer")}
                    onClick={() => setPlayingTrailer(true)}
                    style={{ opacity: 0.88 }}
                  >
                    <IconPlayerPlay size={24} />
                  </ActionIcon>
                )}
              </div>
            )}
          </div>
          <Group gap="xs" wrap="wrap">
            <Tooltip label={t("detail.cropPoster.noThumb")} disabled={thumbSrc != null}>
              <Box display="inline-block">
                <Button
                  size="xs"
                  variant="light"
                  leftSection={<IconCrop size={14} />}
                  disabled={!thumbSrc}
                  onClick={() => setCropOpen(true)}
                >
                  {t("detail.cropPoster.action")}
                </Button>
              </Box>
            </Tooltip>
            <LockChip label={t("detail.fields.thumb")} {...lockProps("thumb_urls")} />
            <LockChip label={t("detail.fields.poster")} {...lockProps("poster_urls")} />
            <LockChip label={t("detail.fields.trailer")} {...lockProps("trailer_urls")} />
          </Group>
        </Stack>

        <Stack gap="sm" style={{ flex: "2 1 280px", minWidth: 260 }}>
          <div>
            <Title order={2} ff="monospace">
              {item.number}
            </Title>
            {item.title && (
              <Group gap={4} align="center" wrap="nowrap">
                <Text c="dimmed">{item.title}</Text>
                <LockToggle {...lockProps("title")} />
              </Group>
            )}
          </div>

          <Group gap="lg" wrap="wrap">
            {item.studio && (
              <FieldBlock label={t("detail.fields.studio")} lock={lockProps("studio")}>
                <FacetBadge kind="studio" id={data.studio_id} name={item.studio} />
              </FieldBlock>
            )}
            {item.publisher && (
              <FieldBlock label={t("detail.fields.publisher")} lock={lockProps("publisher")}>
                <FacetBadge kind="publisher" id={data.publisher_id} name={item.publisher} />
              </FieldBlock>
            )}
            {item.series && (
              <FieldBlock label={t("detail.fields.series")} lock={lockProps("series")}>
                <FacetBadge kind="series" id={data.series_id} name={item.series} />
              </FieldBlock>
            )}
            {item.release && (
              <FieldBlock label={t("detail.fields.release")} lock={lockProps("release")}>
                <Text size="sm">{item.release}</Text>
              </FieldBlock>
            )}
            {runtime && (
              <FieldBlock label={t("detail.fields.runtime")} lock={lockProps("runtime")}>
                <Text size="sm">{runtime}</Text>
              </FieldBlock>
            )}
            {item.score != null && (
              <FieldBlock label={t("detail.fields.score")} lock={lockProps("score")}>
                <Badge color="yellow" variant="light" leftSection={<IconStar size={12} />}>
                  {item.score.toFixed(1)}
                </Badge>
              </FieldBlock>
            )}
          </Group>

          {item.directors && item.directors.length > 0 && (
            <FieldBlock label={t("detail.fields.directors")} lock={lockProps("directors")}>
              <Group gap={6}>
                {item.directors.map((d) => (
                  <FacetBadge key={d} kind="director" id={data.director_ids?.[d]} name={d} />
                ))}
              </Group>
            </FieldBlock>
          )}

          {item.actors && item.actors.length > 0 && (
            <FieldBlock label={t("detail.fields.actors")} lock={lockProps("actors")}>
              <Group gap={6}>
                {item.actors.map((a) => (
                  <FacetBadge
                    key={a}
                    kind="actor"
                    id={data.actor_ids?.[a]}
                    name={a}
                    gender={data.actor_genders?.[a]}
                  />
                ))}
              </Group>
            </FieldBlock>
          )}

          {item.tags && item.tags.length > 0 && (
            <FieldBlock label={t("detail.fields.tags")} lock={lockProps("tags")}>
              <Group gap={6}>
                {item.tags.map((tg) => (
                  <FacetBadge key={tg} kind="tag" id={data.tag_ids?.[tg]} name={tg} />
                ))}
              </Group>
            </FieldBlock>
          )}

          <FieldBlock label={t("detail.userTags")}>
            <Group gap={6} align="center" wrap="wrap">
              {(data.user_tags ?? []).map((tag) => (
                <FacetBadge key={tag.id} kind="user_tag" id={tag.id} name={tag.name} />
              ))}
              <UserTagActions
                attached={data.user_tags ?? []}
                candidates={(userTagOptions?.items ?? []).filter(
                  (tag) => !(data.user_tags ?? []).some((attached) => attached.id === tag.id),
                )}
                onChoose={(selection) => void handleAddTags(selection)}
                onDetach={(ids) => void handleDetachTags(ids)}
                disabled={ensureTagsMutation.isPending || applyTagsMutation.isPending}
              />
            </Group>
          </FieldBlock>

          {item.source_urls && Object.keys(item.source_urls).length > 0 && (
            <FieldBlock label={t("detail.fields.sourceUrls")}>
              <Group gap={6}>
                {Object.entries(item.source_urls).flatMap(([site, raw]) => {
                  if (typeof raw !== "string" || !/^https?:\/\//i.test(raw)) return [];
                  return [
                    <Badge
                      key={site}
                      component="a"
                      href={raw}
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
                    </Badge>,
                  ];
                })}
              </Group>
            </FieldBlock>
          )}

          {item.plot && (
            <FieldBlock label={t("detail.fields.plot")} lock={lockProps("plot")}>
              <Text size="sm" style={{ whiteSpace: "pre-line" }}>
                {item.plot}
              </Text>
            </FieldBlock>
          )}

          {hasExtrafanart && (
            <FieldBlock
              label={`${t("detail.fields.extrafanart")} (${item.extrafanart?.length})`}
              lock={lockProps("extrafanart")}
            >
              <FanartStrip images={item.extrafanart ?? []} />
            </FieldBlock>
          )}

          <Group gap="xs" pt="xs">
            <Menu shadow="md" position="bottom-start">
              <Menu.Target>
                <Button
                  size="xs"
                  variant="light"
                  leftSection={<IconRefresh size={14} />}
                  loading={scrapeMutation.isPending}
                >
                  {t("actions.scrape")}
                </Button>
              </Menu.Target>
              <Menu.Dropdown>
                <Menu.Item
                  onClick={() =>
                    scrapeMutation.mutate({ body: { type: "scrape", number: item.number } })
                  }
                >
                  <Text size="sm">{t("common:actions.scrapeNormal")}</Text>
                  <Text size="xs" c="dimmed">
                    {t("common:actions.scrapeNormalDesc")}
                  </Text>
                </Menu.Item>
                <Menu.Item
                  onClick={() =>
                    scrapeMutation.mutate({
                      body: { type: "scrape", number: item.number, use_cache: [] },
                    })
                  }
                >
                  <Text size="sm">{t("common:actions.scrapeForce")}</Text>
                  <Text size="xs" c="dimmed">
                    {t("common:actions.scrapeForceDesc")}
                  </Text>
                </Menu.Item>
              </Menu.Dropdown>
            </Menu>
            <Button
              size="xs"
              variant="light"
              leftSection={<IconPencil size={14} />}
              onClick={() => setEditOpen(true)}
            >
              {t("common:actions.edit")}
            </Button>
            <Menu shadow="md" position="bottom-start">
              <Menu.Target>
                <Button
                  size="xs"
                  variant="light"
                  color={allLocked ? "yellow" : "gray"}
                  leftSection={<IconLock size={14} />}
                  loading={lockMutation.isPending}
                >
                  {t("lock.manage")}
                </Button>
              </Menu.Target>
              <Menu.Dropdown>
                <Menu.Item onClick={() => applyLocks(allLocked ? [] : LOCKABLE_FIELDS)}>
                  {allLocked ? t("lock.clearAll") : t("lock.all")}
                </Menu.Item>
                <Menu.Divider />
                {LOCKABLE_FIELDS.map((field) => (
                  <Menu.Item
                    key={field}
                    onClick={() => toggleLock(field)}
                    leftSection={
                      lockedFields.has(field) ? <IconLock size={14} /> : <IconLockOpen size={14} />
                    }
                    rightSection={lockedFields.has(field) ? <IconCheck size={14} /> : undefined}
                  >
                    {t(FIELD_LABEL_KEY[field])}
                  </Menu.Item>
                ))}
              </Menu.Dropdown>
            </Menu>
            <Button
              size="xs"
              variant="light"
              color="grape"
              leftSection={<IconGitMerge size={14} />}
              onClick={() => setMergeOpen(true)}
            >
              {t("merge.title")}
            </Button>
            <Button
              size="xs"
              variant="light"
              color="red"
              leftSection={<IconTrash size={14} />}
              loading={deleteMutation.isPending}
              onClick={() => void handleDelete()}
            >
              {t("common:actions.delete")}
            </Button>
          </Group>
        </Stack>
      </Group>

      <PlaybackPanel
        metadataId={id}
        seekRequest={seekRequest}
        onSeekHandled={() => setSeekRequest(null)}
        onCanSeekChange={setCanSeek}
      />

      {/* 宽屏下并排, 窄屏落回上下叠放; 各自保持自然高度, 拉平会把空白挪进较短的一栏. */}
      <SimpleGrid cols={{ base: 1, lg: 2 }} spacing="md" style={{ alignItems: "start" }}>
        <Card withBorder radius="md" p="md">
          <Title order={5} mb="sm">
            {t("detail.connections")}
          </Title>
          {data.files.length === 0 ? (
            <Text size="sm" c="dimmed">
              {t("detail.noFiles")}
            </Text>
          ) : (
            <Stack gap={6}>
              {data.files.map((f) => (
                <Group key={f.id} justify="space-between" gap="xs" wrap="nowrap">
                  <Stack gap={4} style={{ minWidth: 0, flex: 1 }}>
                    <Text size="sm" truncate="end" ff="monospace">
                      {f.path}
                    </Text>
                    <FilePhaseBadges phase={f} />
                  </Stack>
                  <Badge size="sm" variant="light">
                    {f.status}
                  </Badge>
                </Group>
              ))}
            </Stack>
          )}
        </Card>

        <CommentSection
          metadataId={id}
          comments={data.comments ?? []}
          canSeek={canSeek}
          onSeek={requestSeek}
        />
      </SimpleGrid>

      <Modal
        opened={editOpen}
        onClose={() => setEditOpen(false)}
        title={`${t("edit.title")} — ${item.number}`}
        size="72rem"
        styles={{ body: { paddingBottom: 88 } }}
      >
        {editSchema ? (
          <SchemaForm
            schema={editSchema}
            prefix="editor"
            i18nPrefix="metadata"
            fieldLayout="grid"
            actionsPlacement="affix"
            values={(() => {
              const bag: Record<string, unknown> = { ...item };
              const out: Record<string, unknown> = {};
              for (const key of Object.keys(editSchema.properties ?? {})) {
                out[key] = bag[key] ?? null;
              }
              return out;
            })()}
            onSave={(patch) => updateMutation.mutate({ path: { metadata_id: id }, body: patch })}
            saving={updateMutation.isPending}
          />
        ) : (
          <Text size="sm" c="dimmed">
            {t("common:status.loading")}
          </Text>
        )}
      </Modal>

      <MergeDialog
        metadata={item}
        opened={mergeOpen}
        onClose={() => setMergeOpen(false)}
        onMerged={invalidateDetail}
      />

      {thumbSrc && (
        <PosterCropDialog
          opened={cropOpen}
          onClose={() => setCropOpen(false)}
          metadataId={id}
          thumbUrl={thumbSrc}
          onSuccess={() => {
            invalidateDetail();
            void queryClient.invalidateQueries({ queryKey: listMetadataQueryKey() });
          }}
        />
      )}

      {coverLightboxOpen && coverSrc && (
        <FanartLightbox images={[coverSrc]} onClose={coverLightbox.close} />
      )}
    </Stack>
  );
}
