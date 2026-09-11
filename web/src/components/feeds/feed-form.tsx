import {
  Autocomplete,
  Box,
  Checkbox,
  Group,
  Input,
  NumberInput,
  Stack,
  Switch,
  Textarea,
  TextInput,
} from "@mantine/core";
import { useTranslation } from "react-i18next";
import { FeedCreateRequestSchema } from "@/client/schemas.gen";
import type { CacheKind, ContentType, FeedCreateRequest, FeedResponse } from "@/client/types.gen";
import { EnumToggle } from "@/components/common/enum-toggle";
import { encodeFormBody } from "@/components/schema-form/encode";
import { CACHE_KINDS, CONTENT_TYPES } from "@/lib/exhaustive-maps";
import { feedGroup, tryNormalizeFeedGroup } from "@/lib/feeds/groups";
import { formatIgnoreKeywordsText, parseIgnoreKeywordsText } from "@/lib/feeds/keywords";

export { feedDisplayName } from "@/lib/feeds/groups";

export const INTERVAL_MIN_SECONDS = 60;
export const INTERVAL_MAX_SECONDS = 86400;
export const DEFAULT_INTERVAL_SECONDS = 3600;
export const INTERVAL_UNITS = ["hours", "minutes", "seconds"] as const;
export type IntervalUnit = (typeof INTERVAL_UNITS)[number];

export type FeedFormState = {
  name: string;
  url: string;
  group: string;
  intervalValue: number;
  intervalUnit: IntervalUnit;
  numberPattern: string;
  contentType: ContentType | "";
  useCache: CacheKind[];
  enabled: boolean;
  autoEnqueue: boolean;
  ignoreKeywords: string;
};

export function toSeconds(value: number, unit: IntervalUnit): number {
  if (unit === "hours") return Math.round(value * 3600);
  if (unit === "minutes") return Math.round(value * 60);
  return Math.round(value);
}

export function fromSeconds(seconds: number, unit: IntervalUnit): number {
  if (unit === "hours") return seconds / 3600;
  if (unit === "minutes") return seconds / 60;
  return seconds;
}

export function preferredUnit(seconds: number): IntervalUnit {
  if (seconds % 3600 === 0) return "hours";
  if (seconds % 60 === 0) return "minutes";
  return "seconds";
}

export function intervalInRange(seconds: number): boolean {
  return seconds >= INTERVAL_MIN_SECONDS && seconds <= INTERVAL_MAX_SECONDS;
}

export function feedFormCanSubmit(form: FeedFormState): boolean {
  return (
    form.url.trim() !== "" &&
    intervalInRange(toSeconds(form.intervalValue, form.intervalUnit)) &&
    tryNormalizeFeedGroup(form.group) != null
  );
}

export function intervalLabelKey(
  seconds: number,
): "labels.intervalHours" | "labels.intervalMinutes" | "labels.intervalSeconds" {
  const unit = preferredUnit(seconds);
  if (unit === "hours") return "labels.intervalHours";
  if (unit === "minutes") return "labels.intervalMinutes";
  return "labels.intervalSeconds";
}

export function intervalLabelCount(seconds: number): number {
  return fromSeconds(seconds, preferredUnit(seconds));
}

const CONTENT_TYPE_OPTIONS = ["", ...CONTENT_TYPES] as const;

function parseCacheKinds(values: string[]): CacheKind[] {
  const result: CacheKind[] = [];
  for (const value of values) {
    for (const kind of CACHE_KINDS) {
      if (kind === value) {
        result.push(kind);
      }
    }
  }
  return result;
}

const CACHE_KIND_LABEL: Record<CacheKind, "fields.cacheMetadata" | "fields.cacheTrans"> = {
  metadata: "fields.cacheMetadata",
  trans: "fields.cacheTrans",
};

export function emptyFeedForm(): FeedFormState {
  return {
    name: "",
    url: "",
    group: "",
    intervalValue: fromSeconds(DEFAULT_INTERVAL_SECONDS, "hours"),
    intervalUnit: "hours",
    numberPattern: "",
    contentType: "",
    useCache: [...CACHE_KINDS],
    enabled: true,
    autoEnqueue: true,
    ignoreKeywords: "",
  };
}

export function feedFormFromResponse(feed: FeedResponse): FeedFormState {
  const unit = preferredUnit(feed.interval_seconds);
  return {
    name: feed.name,
    url: feed.url,
    group: feedGroup(feed),
    intervalValue: fromSeconds(feed.interval_seconds, unit),
    intervalUnit: unit,
    numberPattern: feed.number_pattern ?? "",
    contentType: feed.content_type ?? "",
    useCache: feed.use_cache ?? [],
    enabled: feed.enabled,
    autoEnqueue: feed.auto_enqueue,
    ignoreKeywords: formatIgnoreKeywordsText(feed.ignore_keywords ?? []),
  };
}

export function feedFormToBody(form: FeedFormState): FeedCreateRequest {
  const group = tryNormalizeFeedGroup(form.group);
  // encodeFormBody 按 Create schema 编码; 与生成的 FeedCreateRequest 字段集一致.
  return encodeFormBody(FeedCreateRequestSchema, {
    name: form.name.trim(),
    url: form.url.trim(),
    group: group ?? form.group.trim(),
    interval_seconds: toSeconds(form.intervalValue, form.intervalUnit),
    number_pattern: form.numberPattern.trim(),
    content_type: form.contentType,
    use_cache: form.useCache,
    enabled: form.enabled,
    auto_enqueue: form.autoEnqueue,
    ignore_keywords: parseIgnoreKeywordsText(form.ignoreKeywords),
  }) as FeedCreateRequest;
}

export function FeedFormFields({
  form,
  onChange,
  groupOptions = [],
}: {
  form: FeedFormState;
  onChange: (next: FeedFormState) => void;
  groupOptions?: readonly string[];
}) {
  const { t } = useTranslation("feeds");
  const seconds = toSeconds(form.intervalValue, form.intervalUnit);
  const patch = (partial: Partial<FeedFormState>) => onChange({ ...form, ...partial });
  const groupValid = tryNormalizeFeedGroup(form.group) != null;
  return (
    <Stack gap="md">
      <TextInput
        label={t("fields.name")}
        placeholder={t("fields.namePlaceholder")}
        description={t("fields.nameHint")}
        value={form.name}
        onChange={(e) => patch({ name: e.currentTarget.value })}
      />
      <TextInput
        label={t("fields.url")}
        placeholder={t("fields.urlPlaceholder")}
        value={form.url}
        onChange={(e) => patch({ url: e.currentTarget.value })}
      />
      <Autocomplete
        label={t("fields.group")}
        placeholder={t("fields.groupPlaceholder")}
        description={t("fields.groupHint")}
        data={[...groupOptions]}
        value={form.group}
        error={groupValid ? undefined : t("fields.groupInvalid")}
        onChange={(value) => patch({ group: value })}
      />
      <Input.Wrapper
        label={t("fields.interval")}
        description={t("fields.intervalHint")}
        error={intervalInRange(seconds) ? undefined : t("fields.intervalRange")}
      >
        <Group align="center" gap="sm" wrap="nowrap" mt="xs">
          <NumberInput
            style={{ flex: 1 }}
            min={fromSeconds(INTERVAL_MIN_SECONDS, form.intervalUnit)}
            max={fromSeconds(INTERVAL_MAX_SECONDS, form.intervalUnit)}
            decimalScale={form.intervalUnit === "hours" ? 2 : 0}
            value={form.intervalValue}
            onChange={(value) => {
              if (typeof value === "number") patch({ intervalValue: value });
            }}
          />
          <EnumToggle
            options={INTERVAL_UNITS}
            value={form.intervalUnit}
            onChange={(intervalUnit) =>
              patch({
                intervalUnit,
                intervalValue: fromSeconds(seconds, intervalUnit),
              })
            }
            getLabel={(unit) => t(`units.${unit}`)}
          />
        </Group>
      </Input.Wrapper>
      <TextInput
        label={t("fields.numberPattern")}
        description={t("fields.numberPatternHint")}
        value={form.numberPattern}
        onChange={(e) => patch({ numberPattern: e.currentTarget.value })}
      />
      <Input.Wrapper label={t("fields.contentType")} description={t("fields.contentTypeHint")}>
        <Box mt="xs">
          <EnumToggle
            fullWidth
            options={CONTENT_TYPE_OPTIONS}
            value={form.contentType}
            onChange={(contentType) => patch({ contentType })}
            getLabel={(value) =>
              value === "" ? t("fields.contentTypeAuto") : t(`contentTypes.${value}`)
            }
          />
        </Box>
      </Input.Wrapper>
      <Checkbox.Group
        label={t("fields.useCache")}
        description={t("fields.useCacheHint")}
        value={form.useCache}
        onChange={(value) => patch({ useCache: parseCacheKinds(value) })}
      >
        <Group mt="xs">
          {CACHE_KINDS.map((kind) => (
            <Checkbox key={kind} value={kind} label={t(CACHE_KIND_LABEL[kind])} />
          ))}
        </Group>
      </Checkbox.Group>
      <Textarea
        label={t("fields.ignoreKeywords")}
        description={t("fields.ignoreKeywordsHint")}
        placeholder={t("fields.ignoreKeywordsPlaceholder")}
        value={form.ignoreKeywords}
        onChange={(event) => patch({ ignoreKeywords: event.currentTarget.value })}
        autosize
        minRows={3}
        maxRows={10}
      />
      <Switch
        label={t("fields.enabled")}
        description={t("fields.enabledHint")}
        checked={form.enabled}
        onChange={(e) => patch({ enabled: e.currentTarget.checked })}
      />
      <Switch
        label={t("fields.autoEnqueue")}
        description={t("fields.autoEnqueueHint")}
        checked={form.autoEnqueue}
        onChange={(e) => patch({ autoEnqueue: e.currentTarget.checked })}
      />
    </Stack>
  );
}
