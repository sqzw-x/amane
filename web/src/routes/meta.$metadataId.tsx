import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  Menu,
  Modal,
  Stack,
  Text,
  Textarea,
  Title,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import {
  IconAlertCircle,
  IconCrop,
  IconExternalLink,
  IconGitMerge,
  IconPencil,
  IconPhotoOff,
  IconPlayerPlay,
  IconRefresh,
  IconSend,
  IconStar,
  IconTrash,
  IconX,
} from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  attachUserTagMutation,
  createCommentMutation,
  createUserTagMutation,
  deleteCommentMutation,
  deleteMetadataMutation,
  detachUserTagMutation,
  getMetadataOptions,
  getMetadataQueryKey,
  listFacetsOptions,
  listFacetsQueryKey,
  listMetadataQueryKey,
  submitTaskMutation,
  updateMetadataMutation,
} from "@/client/@tanstack/react-query.gen";
import { getMetadataSchema } from "@/client/sdk.gen";
import type { MetadataResponse } from "@/client/types.gen";
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
import { proxyImageUrl } from "@/lib/utils";
import { ProxyImage } from "@/components/media/proxy-image";

export const Route = createFileRoute("/meta/$metadataId")({ component: TitleDetailPage });

function formatRuntime(minutes?: number | null): string | null {
  if (!minutes) return null;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

function FieldBlock({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <Text size="xs" c="dimmed">
        {label}
      </Text>
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
  const [newComment, setNewComment] = useState("");

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

  const createTagMutation = useMutation(createUserTagMutation());
  const attachTagMutation = useMutation(attachUserTagMutation());
  const detachTagMutation = useMutation(detachUserTagMutation());
  const createCommentMut = useMutation({
    ...createCommentMutation(),
    onSuccess: () => {
      notifications.show({ message: t("common:toast.commentCreated"), color: "blue" });
      setNewComment("");
      invalidateDetail();
    },
  });
  const deleteCommentMut = useMutation({
    ...deleteCommentMutation(),
    onSuccess: () => {
      notifications.show({ message: t("common:toast.commentDeleted"), color: "blue" });
      invalidateDetail();
    },
  });

  async function handleAddTags(names: string[]) {
    const unique = [...new Set(names.map((name) => name.trim()).filter((name) => name.length > 0))];
    if (unique.length === 0) return;
    try {
      let created = 0;
      for (const name of unique) {
        let tagId = userTagOptions?.items.find((tag) => tag.name === name)?.id;
        if (tagId == null) {
          const createdTag = await createTagMutation.mutateAsync({ body: { name } });
          tagId = createdTag.id;
          created += 1;
        }
        await attachTagMutation.mutateAsync({ path: { metadata_id: id, user_tag_id: tagId } });
      }
      if (created > 0) {
        void queryClient.invalidateQueries({ queryKey: listFacetsQueryKey(USER_TAG_FACET_LIST) });
      }
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

  async function handleDetachTags(ids: number[]) {
    if (ids.length === 0) return;
    try {
      for (const tagId of ids) {
        await detachTagMutation.mutateAsync({ path: { metadata_id: id, user_tag_id: tagId } });
      }
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
    <Stack gap="md">
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
          <Button
            size="xs"
            variant="light"
            leftSection={<IconCrop size={14} />}
            disabled={!thumbSrc}
            title={!thumbSrc ? t("detail.cropPoster.noThumb") : undefined}
            onClick={() => setCropOpen(true)}
          >
            {t("detail.cropPoster.action")}
          </Button>
        </Stack>

        <Stack gap="sm" style={{ flex: "2 1 280px", minWidth: 260 }}>
          <div>
            <Title order={2} ff="monospace">
              {item.number}
            </Title>
            {item.title && <Text c="dimmed">{item.title}</Text>}
          </div>

          <Group gap="lg" wrap="wrap">
            {item.studio && (
              <FieldBlock label={t("detail.fields.studio")}>
                <FacetBadge kind="studio" id={data.studio_id} name={item.studio} />
              </FieldBlock>
            )}
            {item.publisher && (
              <FieldBlock label={t("detail.fields.publisher")}>
                <FacetBadge kind="publisher" id={data.publisher_id} name={item.publisher} />
              </FieldBlock>
            )}
            {item.series && (
              <FieldBlock label={t("detail.fields.series")}>
                <FacetBadge kind="series" id={data.series_id} name={item.series} />
              </FieldBlock>
            )}
            {item.release && (
              <FieldBlock label={t("detail.fields.release")}>
                <Text size="sm">{item.release}</Text>
              </FieldBlock>
            )}
            {runtime && (
              <FieldBlock label={t("detail.fields.runtime")}>
                <Text size="sm">{runtime}</Text>
              </FieldBlock>
            )}
            {item.score != null && (
              <FieldBlock label={t("detail.fields.score")}>
                <Badge color="yellow" variant="light" leftSection={<IconStar size={12} />}>
                  {item.score.toFixed(1)}
                </Badge>
              </FieldBlock>
            )}
          </Group>

          {item.directors && item.directors.length > 0 && (
            <FieldBlock label={t("detail.fields.directors")}>
              <Group gap={6}>
                {item.directors.map((d) => (
                  <FacetBadge key={d} kind="director" id={data.director_ids?.[d]} name={d} />
                ))}
              </Group>
            </FieldBlock>
          )}

          {item.actors && item.actors.length > 0 && (
            <FieldBlock label={t("detail.fields.actors")}>
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
            <FieldBlock label={t("detail.fields.tags")}>
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
                onChoose={(names) => void handleAddTags(names)}
                onDetach={(ids) => void handleDetachTags(ids)}
                disabled={
                  createTagMutation.isPending ||
                  attachTagMutation.isPending ||
                  detachTagMutation.isPending
                }
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
            <FieldBlock label={t("detail.fields.plot")}>
              <Text size="sm" style={{ whiteSpace: "pre-line" }}>
                {item.plot}
              </Text>
            </FieldBlock>
          )}

          {hasExtrafanart && (
            <FieldBlock label={`${t("detail.fields.extrafanart")} (${item.extrafanart?.length})`}>
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

      <Card withBorder radius="md" p="md">
        <Title order={5} mb="sm">
          {t("detail.comments")}
        </Title>
        {(data.comments ?? []).length === 0 ? (
          <Text size="sm" c="dimmed" mb="sm">
            {t("detail.noComments")}
          </Text>
        ) : (
          <Stack gap="xs" mb="sm">
            {(data.comments ?? []).map((c) => (
              <Group key={c.id} justify="space-between" align="flex-start" wrap="nowrap">
                <Text size="sm">{c.body}</Text>
                <ActionIcon
                  size="sm"
                  variant="subtle"
                  color="red"
                  onClick={() => deleteCommentMut.mutate({ path: { comment_id: c.id } })}
                >
                  <IconTrash size={14} />
                </ActionIcon>
              </Group>
            ))}
          </Stack>
        )}
        <Group gap="xs" align="flex-end">
          <Textarea
            value={newComment}
            onChange={(e) => setNewComment(e.currentTarget.value)}
            placeholder={t("detail.commentPlaceholder")}
            style={{ flex: 1 }}
            rows={2}
          />
          <Button
            size="xs"
            leftSection={<IconSend size={14} />}
            disabled={!newComment.trim()}
            onClick={() =>
              createCommentMut.mutate({
                path: { metadata_id: id },
                body: { body: newComment.trim() },
              })
            }
          >
            {t("detail.addComment")}
          </Button>
        </Group>
      </Card>

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
