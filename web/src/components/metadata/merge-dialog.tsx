import {
  Anchor,
  Badge,
  Button,
  Group,
  Modal,
  Paper,
  ScrollArea,
  SimpleGrid,
  Stack,
  Text,
  Tooltip,
  UnstyledButton,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconArrowBackUp, IconExternalLink } from "@tabler/icons-react";
import { useMutation } from "@tanstack/react-query";
import { useMemo, type CSSProperties, type ReactNode } from "react";
import type { ParseKeys } from "i18next";
import { useTranslation } from "react-i18next";
import { mergeMetadataMutation } from "@/client/@tanstack/react-query.gen";
import type { ActorGender, MetadataField, MetadataResponse } from "@/client/types.gen";
import { FanartStrip } from "@/components/media/fanart-lightbox";
import { GenderMark } from "@/components/media/gender-mark";
import { useResettingState } from "@/hooks/use-resetting-state";
import { ACTOR_GENDERS } from "@/lib/actors/browse";
import { extractErrorMessage } from "@/lib/api-error";
import { assertExhaustive, exhaustiveRecord, isOneOf } from "@/lib/exhaustive";
import { isRecord } from "@/lib/utils";

/** Scalar fields - point-pick by equal value groups. */
const TEXT_FIELDS = [
  "title",
  "actors",
  "studio",
  "publisher",
  "release",
  "runtime",
  "tags",
  "series",
  "plot",
  "directors",
] as const satisfies readonly MetadataField[];

/**
 * Media / URL fields as stored in `raw[site]` (MediaMetadata dump).
 * `extrafanart` → DB `extrafanart_urls`; `score` → DB `scores`.
 */
const MEDIA_FIELDS = [
  "poster_urls",
  "thumb_urls",
  "trailer_urls",
  "extrafanart",
] as const satisfies readonly MetadataField[];
const SCORE_FIELD = "score" as const satisfies MetadataField;

/** 分区并集必须覆盖 `MetadataField` 全集, 新增字段时此处编译失败. */
assertExhaustive<MetadataField>()([...TEXT_FIELDS, ...MEDIA_FIELDS, SCORE_FIELD] as const);

/** MetadataField → metadata 命名空间下的字段标签 key. */
const FIELD_LABEL_KEY = exhaustiveRecord<MetadataField>()({
  title: "detail.fields.title",
  plot: "detail.fields.plot",
  actors: "detail.fields.actors",
  directors: "detail.fields.directors",
  tags: "detail.fields.tags",
  series: "detail.fields.series",
  release: "detail.fields.release",
  runtime: "detail.fields.runtime",
  publisher: "detail.fields.publisher",
  studio: "detail.fields.studio",
  poster_urls: "detail.fields.poster",
  thumb_urls: "detail.fields.thumb",
  trailer_urls: "detail.fields.trailer",
  extrafanart: "detail.fields.extrafanart",
  score: "detail.fields.score",
} as const satisfies Record<MetadataField, ParseKeys<"metadata">>);

type TextField = (typeof TEXT_FIELDS)[number];
type MediaField = (typeof MEDIA_FIELDS)[number];
type FilmActorValue = { name: string; gender: ActorGender };

function isTextField(field: string): field is TextField {
  return isOneOf(TEXT_FIELDS, field);
}

function isMediaField(field: string): field is MediaField {
  return isOneOf(MEDIA_FIELDS, field);
}

/** raw.actors 为 FilmActor (`name` + `gender`) 或展示名字符串; 缺省性别视为 unknown. */
function asFilmActor(value: unknown): FilmActorValue | undefined {
  if (typeof value === "string" && value.length > 0) {
    return { name: value, gender: "unknown" };
  }
  if (typeof value !== "object" || value === null || Array.isArray(value)) return undefined;
  if (!("name" in value) || typeof value.name !== "string" || value.name.length === 0) {
    return undefined;
  }
  const gender =
    "gender" in value && isOneOf(ACTOR_GENDERS, value.gender) ? value.gender : "unknown";
  return { name: value.name, gender };
}

function formatItem(value: unknown): string {
  const actor = asFilmActor(value);
  if (actor) return actor.name;
  if (value == null) return "";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function formatValue(value: unknown): string {
  if (value == null) return "—";
  if (Array.isArray(value)) {
    const parts = value.map(formatItem).filter((s) => s.length > 0);
    return parts.length > 0 ? parts.join(", ") : "—";
  }
  return formatItem(value) || "—";
}

function filmActorsFromRaw(value: unknown): FilmActorValue[] {
  if (!Array.isArray(value)) {
    const one = asFilmActor(value);
    return one ? [one] : [];
  }
  const out: FilmActorValue[] = [];
  for (const item of value) {
    const actor = asFilmActor(item);
    if (actor) out.push(actor);
  }
  return out;
}

function sameValue(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true;
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((v, i) => sameValue(v, b[i]));
  }
  const fa = asFilmActor(a);
  const fb = asFilmActor(b);
  if (fa && fb) return fa.name === fb.name && fa.gender === fb.gender;
  return false;
}

function urlsFromRaw(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.filter((u): u is string => typeof u === "string" && u.length > 0);
  }
  if (typeof value === "string" && value.length > 0) return [value];
  return [];
}

function firstDictKey(value: unknown): string | undefined {
  if (value == null || typeof value !== "object" || Array.isArray(value)) return undefined;
  const keys = Object.keys(value);
  return keys[0];
}

/** Current DB-side media URLs for result preview (poster/thumb may be materialized). */
function currentDbMediaUrls(metadata: MetadataResponse, field: MediaField): string[] {
  if (field === "extrafanart") {
    if (metadata.extrafanart?.length) return metadata.extrafanart;
    const grouped = metadata.extrafanart_urls ?? {};
    const first = Object.values(grouped)[0];
    return Array.isArray(first) ? first.filter((u): u is string => typeof u === "string") : [];
  }
  const list = metadata[field];
  return Array.isArray(list) ? list.filter((u): u is string => typeof u === "string") : [];
}

/**
 * Infer which raw source currently backs a media field.
 * - extrafanart: first key of DB `extrafanart_urls`
 * - list URL fields: exact match against raw URL lists (fails after materialize → undefined)
 */
function inferMediaSource(
  metadata: MetadataResponse,
  field: MediaField,
  entries: FieldEntry[],
): string | undefined {
  if (entries.length === 0) return undefined;

  if (field === "extrafanart") {
    const key = firstDictKey(metadata.extrafanart_urls);
    if (key != null && entries.some((e) => e.source === key)) return key;
    return undefined;
  }

  const dbUrls = currentDbMediaUrls(metadata, field);
  if (dbUrls.length === 0) return undefined;
  const match = entries.find((e) => sameValue(urlsFromRaw(e.value), dbUrls));
  return match?.source;
}

/** Infer score source from DB `scores` dict (first key = display priority). */
function inferScoreSource(metadata: MetadataResponse, entries: FieldEntry[]): string | undefined {
  if (entries.length === 0) return undefined;
  const key = firstDictKey(metadata.scores);
  if (key != null && entries.some((e) => e.source === key)) return key;
  const current = metadata.score;
  if (current == null) return undefined;
  const match = entries.find((e) => e.value === current);
  return match?.source;
}

interface FieldEntry {
  source: string;
  value: unknown;
}

interface MergeDialogProps {
  metadata: MetadataResponse;
  opened: boolean;
  onClose: () => void;
  onMerged: () => void;
}

/**
 * Pick per-field values from multi-source `raw`.
 * Text: equal-value groups. Media: whole-source pick (no cross-site splice).
 */
export function MergeDialog({ metadata, opened, onClose, onMerged }: MergeDialogProps) {
  const { t } = useTranslation(["metadata", "common"]);
  const [selections, setSelections] = useResettingState(
    (): Record<string, string> => ({}),
    opened ? metadata.id : "closed",
  );

  const { textFields, mediaFields, scoreEntries, fieldData, originalSources, mediaSources } =
    useMemo(() => {
      const raw: Record<string, Record<string, unknown>> = {};
      for (const [site, data] of Object.entries(metadata.raw ?? {})) {
        if (isRecord(data)) raw[site] = data;
      }
      const byField: Record<string, FieldEntry[]> = {};

      for (const [src, srcData] of Object.entries(raw)) {
        for (const [field, val] of Object.entries(srcData)) {
          if (isTextField(field)) {
            if (val == null || val === "") continue;
            if (!byField[field]) byField[field] = [];
            byField[field].push({ source: src, value: val });
          } else if (isMediaField(field)) {
            if (urlsFromRaw(val).length === 0) continue;
            if (!byField[field]) byField[field] = [];
            byField[field].push({ source: src, value: val });
          } else if (field === SCORE_FIELD) {
            if (typeof val !== "number") continue;
            if (!byField[field]) byField[field] = [];
            byField[field].push({ source: src, value: val });
          }
        }
      }

      const media = MEDIA_FIELDS.filter((f) => (byField[f]?.length ?? 0) > 0);
      const inferred: Partial<Record<MediaField, string>> = {};
      for (const f of media) {
        const src = inferMediaSource(metadata, f, byField[f] ?? []);
        if (src != null) inferred[f] = src;
      }

      const sources: Record<string, string> = {};
      for (const [k, v] of Object.entries(metadata.field_sources ?? {})) {
        if (typeof v === "string") sources[k] = v;
      }

      return {
        textFields: TEXT_FIELDS.filter((f) => (byField[f]?.length ?? 0) > 0),
        mediaFields: media,
        scoreEntries: byField[SCORE_FIELD] ?? [],
        fieldData: byField,
        originalSources: sources,
        mediaSources: inferred,
      };
    }, [metadata]);

  const scoreSource = useMemo(
    () => inferScoreSource(metadata, scoreEntries),
    [metadata, scoreEntries],
  );

  const mergeMutation = useMutation({
    ...mergeMetadataMutation(),
    onSuccess: () => {
      notifications.show({ message: t("common:toast.metadataUpdated"), color: "teal" });
      setSelections({});
      onMerged();
      onClose();
    },
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      }),
  });

  const changedCount = Object.keys(selections).length;
  const hasScore = scoreEntries.length > 0;
  const hasAny = textFields.length > 0 || mediaFields.length > 0 || hasScore;

  function effectiveTextSource(field: string): string | undefined {
    return selections[field] ?? originalSources[field];
  }

  function effectiveMediaSource(field: MediaField): string | undefined {
    return selections[field] ?? mediaSources[field];
  }

  function effectiveScoreSource(): string | undefined {
    return selections[SCORE_FIELD] ?? scoreSource;
  }

  function revert(field: string) {
    setSelections((prev) => {
      const next = { ...prev };
      delete next[field];
      return next;
    });
  }

  /** Toggle / revert when picking the currently-applied source. */
  function selectWithBaseline(field: string, source: string, baseline: string | undefined) {
    if ((selections[field] ?? baseline) === source) {
      if (field in selections) revert(field);
      return;
    }
    if (baseline === source) {
      revert(field);
    } else {
      setSelections((prev) => ({ ...prev, [field]: source }));
    }
  }

  function fieldLabel(field: MetadataField): string {
    return t(FIELD_LABEL_KEY[field]);
  }

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={`${t("merge.title")} — ${metadata.number}`}
      size="90%"
      styles={{ body: { maxHeight: "75vh", display: "flex", flexDirection: "column" } }}
    >
      {!hasAny ? (
        <Text c="dimmed" size="sm" py="xl" ta="center">
          {t("merge.noRawData")}
        </Text>
      ) : (
        <Stack gap="md" style={{ flex: 1, minHeight: 0 }}>
          <SimpleGrid cols={{ base: 1, md: 2 }} spacing="md" style={{ flex: 1, minHeight: 0 }}>
            <Panel
              title="Result"
              trailing={
                changedCount > 0 ? (
                  <Button variant="subtle" size="compact-xs" onClick={() => setSelections({})}>
                    {t("merge.revertAll")}
                  </Button>
                ) : undefined
              }
            >
              <Stack gap={6}>
                {textFields.map((field) => {
                  const src = effectiveTextSource(field);
                  const value = src
                    ? fieldData[field]?.find((e) => e.source === src)?.value
                    : undefined;
                  const changed = field in selections;
                  return (
                    <ResultTextRow
                      key={field}
                      label={fieldLabel(field)}
                      value={
                        field === "actors" ? (
                          <ActorNamesValue actors={filmActorsFromRaw(value)} />
                        ) : (
                          formatValue(value)
                        )
                      }
                      tooltip={formatValue(value)}
                      source={src}
                      changed={changed}
                      onRevert={() => revert(field)}
                      revertLabel={t("merge.revert")}
                    />
                  );
                })}
                {hasScore && (
                  <ResultTextRow
                    label={fieldLabel(SCORE_FIELD)}
                    value={formatValue(
                      effectiveScoreSource() != null
                        ? scoreEntries.find((e) => e.source === effectiveScoreSource())?.value
                        : metadata.score,
                    )}
                    source={effectiveScoreSource()}
                    changed={SCORE_FIELD in selections}
                    onRevert={() => revert(SCORE_FIELD)}
                    revertLabel={t("merge.revert")}
                  />
                )}
                {mediaFields.map((field) => {
                  const selectedSrc = selections[field];
                  const changed = selectedSrc != null;
                  const effective = effectiveMediaSource(field);
                  const urls = changed
                    ? urlsFromRaw(fieldData[field]?.find((e) => e.source === selectedSrc)?.value)
                    : currentDbMediaUrls(metadata, field);
                  return (
                    <ResultMediaRow
                      key={field}
                      label={fieldLabel(field)}
                      field={field}
                      urls={urls}
                      sourceBadge={effective ?? t("merge.current", { defaultValue: "当前" })}
                      changed={changed}
                      onRevert={() => revert(field)}
                      revertLabel={t("merge.revert")}
                    />
                  );
                })}
              </Stack>
            </Panel>

            <Panel title="Compare">
              <Stack gap="md">
                {textFields.map((field) => {
                  const entries = fieldData[field];
                  if (!entries || entries.length <= 1) return null;
                  const current = effectiveTextSource(field);
                  const groups = groupByValue(entries, current);
                  return (
                    <div key={field}>
                      <Text size="xs" c="dimmed" fw={600} tt="uppercase" mb={4}>
                        {fieldLabel(field)}
                      </Text>
                      <Stack gap={4}>
                        {groups.map((g) => {
                          const active = g.sources.includes(current ?? "");
                          return (
                            <UnstyledButton
                              key={g.sources[0]}
                              onClick={() =>
                                selectWithBaseline(field, g.sources[0], originalSources[field])
                              }
                              p="xs"
                              style={optionStyle(active)}
                            >
                              <Group gap="sm" wrap="nowrap">
                                <Text size="xs" c="dimmed" w={100} style={{ flexShrink: 0 }}>
                                  {g.sources.join(", ")}
                                </Text>
                                {field === "actors" ? (
                                  <div style={{ flex: 1, minWidth: 0 }}>
                                    <ActorNamesValue actors={filmActorsFromRaw(g.value)} />
                                  </div>
                                ) : (
                                  <Text size="sm" lineClamp={2} style={{ flex: 1, minWidth: 0 }}>
                                    {formatValue(g.value)}
                                  </Text>
                                )}
                                {active && (
                                  <Badge size="xs" color="brand">
                                    ✓
                                  </Badge>
                                )}
                              </Group>
                            </UnstyledButton>
                          );
                        })}
                      </Stack>
                    </div>
                  );
                })}

                {scoreEntries.length > 1 && (
                  <div>
                    <Text size="xs" c="dimmed" fw={600} tt="uppercase" mb={4}>
                      {fieldLabel(SCORE_FIELD)}
                    </Text>
                    <Stack gap={4}>
                      {groupByValue(scoreEntries, effectiveScoreSource()).map((g) => {
                        const active = g.sources.includes(effectiveScoreSource() ?? "");
                        return (
                          <UnstyledButton
                            key={g.sources[0]}
                            onClick={() =>
                              selectWithBaseline(SCORE_FIELD, g.sources[0], scoreSource)
                            }
                            p="xs"
                            style={optionStyle(active)}
                          >
                            <Group gap="sm" wrap="nowrap">
                              <Text size="xs" c="dimmed" w={100} style={{ flexShrink: 0 }}>
                                {g.sources.join(", ")}
                              </Text>
                              <Text size="sm" style={{ flex: 1, minWidth: 0 }}>
                                {formatValue(g.value)}
                              </Text>
                              {active && (
                                <Badge size="xs" color="brand">
                                  ✓
                                </Badge>
                              )}
                            </Group>
                          </UnstyledButton>
                        );
                      })}
                    </Stack>
                  </div>
                )}

                {mediaFields.map((field) => {
                  const entries = fieldData[field] ?? [];
                  if (entries.length === 0) return null;
                  const current = effectiveMediaSource(field);
                  const baseline = mediaSources[field];
                  return (
                    <div key={field}>
                      <Text size="xs" c="dimmed" fw={600} tt="uppercase" mb={4}>
                        {fieldLabel(field)}
                      </Text>
                      <Stack gap={6}>
                        {entries.map((entry) => {
                          const urls = urlsFromRaw(entry.value);
                          const active = current === entry.source;
                          return (
                            <Paper
                              key={entry.source}
                              component="div"
                              p="xs"
                              role="button"
                              tabIndex={0}
                              onClick={() => selectWithBaseline(field, entry.source, baseline)}
                              onKeyDown={(e) => {
                                if (e.key === "Enter" || e.key === " ") {
                                  e.preventDefault();
                                  selectWithBaseline(field, entry.source, baseline);
                                }
                              }}
                              style={{
                                ...optionStyle(active),
                                cursor: "pointer",
                              }}
                            >
                              <Stack gap={6}>
                                <Group justify="space-between">
                                  <Text size="xs" c="dimmed">
                                    {entry.source}
                                    <Text span ml={6}>
                                      ({urls.length})
                                    </Text>
                                  </Text>
                                  {active && (
                                    <Badge size="xs" color="brand">
                                      ✓
                                    </Badge>
                                  )}
                                </Group>
                                <MediaPreview field={field} urls={urls} />
                              </Stack>
                            </Paper>
                          );
                        })}
                      </Stack>
                    </div>
                  );
                })}
              </Stack>
            </Panel>
          </SimpleGrid>

          <Group justify="space-between">
            <Text size="sm" c="dimmed">
              {changedCount > 0
                ? t("merge.changedCount", { count: changedCount })
                : t("merge.noChanges")}
            </Text>
            <Group>
              <Button variant="default" onClick={onClose}>
                {t("common:actions.cancel")}
              </Button>
              <Button
                disabled={changedCount === 0}
                loading={mergeMutation.isPending}
                onClick={() =>
                  mergeMutation.mutate({
                    path: { metadata_id: metadata.id },
                    body: { selections },
                  })
                }
              >
                {t("merge.apply")}
              </Button>
            </Group>
          </Group>
        </Stack>
      )}
    </Modal>
  );
}

function optionStyle(active: boolean): CSSProperties {
  return {
    borderRadius: "var(--mantine-radius-sm)",
    border: active ? "1px solid var(--mantine-color-brand-5)" : "1px solid transparent",
    background: active ? "var(--mantine-color-brand-light)" : "var(--mantine-color-default-hover)",
    width: "100%",
  };
}

function Panel({
  title,
  trailing,
  children,
}: {
  title: string;
  trailing?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Paper
      withBorder
      radius="md"
      style={{ display: "flex", flexDirection: "column", minHeight: 0 }}
    >
      <Group
        justify="space-between"
        px="md"
        py="sm"
        style={{ borderBottom: "1px solid var(--mantine-color-default-border)" }}
      >
        <Text fw={600} size="sm">
          {title}
        </Text>
        {trailing}
      </Group>
      <ScrollArea style={{ flex: 1 }} p="sm">
        {children}
      </ScrollArea>
    </Paper>
  );
}

function ResultTextRow({
  label,
  value,
  tooltip,
  source,
  changed,
  onRevert,
  revertLabel,
}: {
  label: string;
  value: ReactNode;
  tooltip?: string;
  source: string | undefined;
  changed: boolean;
  onRevert: () => void;
  revertLabel: string;
}) {
  const tip = tooltip ?? (typeof value === "string" ? value : undefined);
  return (
    <Group
      gap="sm"
      wrap="nowrap"
      p="xs"
      style={{
        borderRadius: "var(--mantine-radius-sm)",
        background: changed ? "var(--mantine-color-brand-light)" : undefined,
      }}
    >
      <Text size="sm" fw={500} w={88} style={{ flexShrink: 0 }}>
        {label}
      </Text>
      <Tooltip label={tip} multiline maw={400} disabled={tip == null || tip.length < 40}>
        {typeof value === "string" ? (
          <Text size="sm" lineClamp={2} style={{ flex: 1, minWidth: 0 }}>
            {value}
          </Text>
        ) : (
          <div style={{ flex: 1, minWidth: 0 }}>{value}</div>
        )}
      </Tooltip>
      <Badge size="xs" variant={changed ? "filled" : "light"} style={{ flexShrink: 0 }}>
        {source ?? "—"}
      </Badge>
      {changed && <ActionRevert onClick={onRevert} label={revertLabel} />}
    </Group>
  );
}

function ActorNamesValue({ actors }: { actors: FilmActorValue[] }) {
  if (actors.length === 0) {
    return (
      <Text size="sm" c="dimmed">
        —
      </Text>
    );
  }
  return (
    <Group gap={8} wrap="wrap">
      {actors.map((actor, index) => (
        <Group key={`${actor.name}:${actor.gender}:${index}`} gap={4} wrap="nowrap">
          <Text size="sm">{actor.name}</Text>
          <GenderMark gender={actor.gender} />
        </Group>
      ))}
    </Group>
  );
}

function ResultMediaRow({
  label,
  field,
  urls,
  sourceBadge,
  changed,
  onRevert,
  revertLabel,
}: {
  label: string;
  field: MediaField;
  urls: string[];
  sourceBadge: string;
  changed: boolean;
  onRevert: () => void;
  revertLabel: string;
}) {
  return (
    <Stack
      gap={6}
      p="xs"
      style={{
        borderRadius: "var(--mantine-radius-sm)",
        background: changed ? "var(--mantine-color-brand-light)" : undefined,
      }}
    >
      <Group justify="space-between" wrap="nowrap">
        <Text size="sm" fw={500}>
          {label}
        </Text>
        <Group gap={6}>
          <Badge size="xs" variant={changed ? "filled" : "light"}>
            {sourceBadge}
          </Badge>
          {changed && <ActionRevert onClick={onRevert} label={revertLabel} />}
        </Group>
      </Group>
      <MediaPreview field={field} urls={urls} />
    </Stack>
  );
}

/** Trailer URLs are video links - show as anchors; image fields use thumb strip. */
function MediaPreview({ field, urls }: { field: MediaField; urls: string[] }) {
  if (urls.length === 0) {
    return (
      <Text size="xs" c="dimmed">
        —
      </Text>
    );
  }
  if (field === "trailer_urls") {
    return (
      <Stack gap={4}>
        {urls.map((url) => (
          <Anchor
            key={url}
            href={url}
            target="_blank"
            rel="noreferrer"
            size="xs"
            lineClamp={1}
            onClick={(e) => e.stopPropagation()}
          >
            {url} <IconExternalLink size={11} style={{ verticalAlign: "middle" }} />
          </Anchor>
        ))}
      </Stack>
    );
  }
  return (
    <FanartStrip
      images={urls}
      maxVisible={8}
      thumbStyle={{ width: 56, height: 40 }}
      empty={
        <Text size="xs" c="dimmed">
          —
        </Text>
      }
    />
  );
}

function ActionRevert({ onClick, label }: { onClick: () => void; label: string }) {
  return (
    <Tooltip label={label}>
      <Button variant="subtle" size="compact-xs" px={4} onClick={onClick}>
        <IconArrowBackUp size={14} />
      </Button>
    </Tooltip>
  );
}

function groupByValue(
  entries: FieldEntry[],
  currentSource: string | undefined,
): Array<{ sources: string[]; value: unknown; isCurrent: boolean }> {
  const groups: Array<{ sources: string[]; value: unknown; isCurrent: boolean }> = [];
  for (const entry of entries) {
    const existing = groups.find((g) => sameValue(g.value, entry.value));
    if (existing) {
      existing.sources.push(entry.source);
      if (entry.source === currentSource) existing.isCurrent = true;
    } else {
      groups.push({
        sources: [entry.source],
        value: entry.value,
        isCurrent: entry.source === currentSource,
      });
    }
  }
  return groups;
}
