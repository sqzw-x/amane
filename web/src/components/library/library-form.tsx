import {
  Accordion,
  Anchor,
  Badge,
  Checkbox,
  Group,
  Input,
  NumberInput,
  SimpleGrid,
  Stack,
  Switch,
  Text,
  Textarea,
  TextInput,
  Tooltip,
} from "@mantine/core";
import { IconChevronDown, IconChevronRight } from "@tabler/icons-react";
import { useMemo, useState } from "react";
import { notifications } from "@mantine/notifications";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { getPathTemplateSchemaOptions } from "@/client/@tanstack/react-query.gen";
import { LibraryCreateRequestSchema, LibraryResponseSchema } from "@/client/schemas.gen";
import type {
  DownloadableResource,
  LibraryAutomation,
  LibraryCreateRequest,
  LibraryIngest,
  LibraryResponse,
  LibraryUpdateRequest,
  OptionalPathTemplateDefaults,
  PathTemplateSchemaResponse,
} from "@/client/types.gen";
import { PathPicker } from "@/components/path-picker";
import { encodeFormBody } from "@/components/schema-form/encode";
import { EnumToggle } from "@/components/common/enum-toggle";
import { FieldChrome } from "@/components/schema-form/fields/field-chrome";
import {
  DOWNLOADABLE_RESOURCES,
  LIBRARY_AUTOMATIONS,
  LIBRARY_INGESTS,
  LINK_MODES,
} from "@/lib/exhaustive-maps";
import classes from "./library-form.module.css";
import { templateCatalogFromPlaceholders } from "./template-highlight";
import { TemplateInput } from "./template-input";

/** 创建/编辑共用: 宽屏两列, 窄屏仍单列. */
export const LIBRARY_FORM_MODAL_SIZE = "min(64rem, 94vw)";

function isCopyResource(v: string): v is DownloadableResource {
  return DOWNLOADABLE_RESOURCES.some((item) => item === v);
}

function parseCopyResources(values: string[]): DownloadableResource[] {
  return values.filter(isCopyResource);
}

const OPTIONAL_TEMPLATE_KEYS = [
  "thumb_template",
  "poster_template",
  "fanart_template",
  "extrafanart_template",
  "nfo_template",
  "trailer_template",
  "subtitle_template",
] as const satisfies readonly (keyof OptionalPathTemplateDefaults)[];

const MOVE_MODES: readonly LibraryResponse["move_mode"][] = ["move", "copy", "hardlink", "symlink"];

const BYTES_PER_MB = 1024 * 1024;

function bytesToMb(bytes: number): number {
  return bytes <= 0 ? 0 : bytes / BYTES_PER_MB;
}

function mbToBytes(mb: number): number {
  return mb <= 0 ? 0 : Math.round(mb * BYTES_PER_MB);
}

export const LIBRARY_AUTOMATION_BADGE_COLOR = {
  none: "gray",
  watch: "blue",
  scrape: "teal",
} as const satisfies Record<LibraryAutomation, string>;

export interface LibraryFormState {
  name: string;
  path: string;
  recursive: boolean;
  patterns: string;
  move_mode: LibraryResponse["move_mode"];
  link_template: string;
  link_mode: LibraryResponse["link_mode"];
  strm_content_template: string;
  write_nfo: boolean;
  copy_resources: DownloadableResource[];
  trailer_pattern: string;
  blacklist_patterns: string;
  min_file_size: number;
  subtitle_extensions: string;
  video_template: string;
  thumb_template: string;
  poster_template: string;
  fanart_template: string;
  extrafanart_template: string;
  nfo_template: string;
  trailer_template: string;
  subtitle_template: string;
  automation: LibraryAutomation;
  ingest: LibraryIngest;
  cloud_path: string;
  scan: boolean;
}

export function emptyLibraryForm(schema?: PathTemplateSchemaResponse | null): LibraryFormState {
  const defaults = schema?.optional_defaults;
  return {
    name: "",
    path: "",
    recursive: true,
    patterns: "",
    move_mode: "move",
    link_template: "",
    link_mode: "strm",
    strm_content_template: "",
    write_nfo: true,
    copy_resources: DOWNLOADABLE_RESOURCES.filter((r) => r !== "trailer"),
    trailer_pattern: "(?i)trailer",
    blacklist_patterns: "",
    min_file_size: 0,
    subtitle_extensions: (
      schema?.subtitle_extensions_default ?? [".srt", ".ass", ".ssa", ".vtt", ".sub"]
    ).join(", "),
    video_template: schema?.video_default ?? "{studio}/{number}/{number}[-CD{cd?}][-{sub?}].{ext}",
    thumb_template: defaults?.thumb_template ?? "",
    poster_template: defaults?.poster_template ?? "",
    fanart_template: defaults?.fanart_template ?? "",
    extrafanart_template: defaults?.extrafanart_template ?? "",
    nfo_template: defaults?.nfo_template ?? "",
    trailer_template: defaults?.trailer_template ?? "",
    subtitle_template: defaults?.subtitle_template ?? "",
    automation: "scrape",
    ingest: "native",
    cloud_path: "",
    scan: true,
  };
}

export function libraryFormFromResponse(lib: LibraryResponse): LibraryFormState {
  return {
    name: lib.name,
    path: lib.path,
    recursive: lib.recursive,
    patterns: lib.patterns?.join(", ") ?? "",
    move_mode: lib.move_mode,
    link_template: lib.link_template ?? "",
    link_mode: lib.link_mode,
    strm_content_template: lib.strm_content_template ?? "",
    write_nfo: lib.write_nfo,
    copy_resources: parseCopyResources(lib.copy_resources),
    trailer_pattern: lib.trailer_pattern,
    blacklist_patterns: lib.blacklist_patterns?.join("\n") ?? "",
    min_file_size: lib.min_file_size ?? 0,
    subtitle_extensions: lib.subtitle_extensions?.join(", ") ?? "",
    video_template: lib.video_template,
    thumb_template: lib.thumb_template ?? "",
    poster_template: lib.poster_template ?? "",
    fanart_template: lib.fanart_template ?? "",
    extrafanart_template: lib.extrafanart_template ?? "",
    nfo_template: lib.nfo_template ?? "",
    trailer_template: lib.trailer_template ?? "",
    subtitle_template: lib.subtitle_template ?? "",
    automation: lib.automation,
    ingest: lib.ingest,
    cloud_path: lib.cloud_path ?? "",
    scan: false,
  };
}

export function parseLibraryPatterns(s: string): string[] {
  return s
    .split(",")
    .map((p) => p.trim())
    .filter(Boolean);
}

/** 黑名单正则按行分隔: 正则本身可含逗号 (如量词 {2,3}), 不能按逗号分隔. */
export function parseBlacklistPatterns(s: string): string[] {
  return s
    .split(/\r?\n/)
    .map((p) => p.trim())
    .filter(Boolean);
}

function libraryFormValues(form: LibraryFormState): Record<string, unknown> {
  return {
    name: form.name.trim(),
    path: form.path.trim(),
    recursive: form.recursive,
    patterns: parseLibraryPatterns(form.patterns),
    move_mode: form.move_mode,
    link_template: form.link_template.trim(),
    link_mode: form.link_mode,
    strm_content_template: form.strm_content_template.trim(),
    write_nfo: form.write_nfo,
    copy_resources: form.copy_resources,
    trailer_pattern: form.trailer_pattern,
    blacklist_patterns: parseBlacklistPatterns(form.blacklist_patterns),
    min_file_size: form.min_file_size,
    subtitle_extensions: parseLibraryPatterns(form.subtitle_extensions),
    video_template: form.video_template.trim(),
    thumb_template: form.thumb_template.trim(),
    poster_template: form.poster_template.trim(),
    fanart_template: form.fanart_template.trim(),
    extrafanart_template: form.extrafanart_template.trim(),
    nfo_template: form.nfo_template.trim(),
    trailer_template: form.trailer_template.trim(),
    subtitle_template: form.subtitle_template.trim(),
    automation: form.automation,
    ingest: form.ingest,
    cloud_path: form.ingest === "clouddrive" ? form.cloud_path.trim() : "",
  };
}

/** 创建体: 空值按 LibraryCreateRequest schema 编码 (name 可空 → null; patterns → []). */
export function libraryFormToCreateBody(form: LibraryFormState): LibraryCreateRequest {
  // encodeFormBody 按 OpenAPI 列契约编码, 与生成的 CreateRequest 字段集一致.
  return encodeFormBody(LibraryCreateRequestSchema, {
    ...libraryFormValues(form),
    scan: form.scan,
  }) as LibraryCreateRequest;
}

/** 更新体: 空值按 LibraryResponse / 列契约编码 (name/path 非空; patterns → []). 不用 Update schema. */
export function libraryFormToUpdateBody(form: LibraryFormState): LibraryUpdateRequest {
  // 故意不用 LibraryUpdateRequestSchema: partial 把非空列标成 T|null, 空 glob 会编成 JSON null.
  return encodeFormBody(LibraryResponseSchema, libraryFormValues(form)) as LibraryUpdateRequest;
}

const TPL_I18N = {
  thumb_template: "tpl.thumb",
  poster_template: "tpl.poster",
  fanart_template: "tpl.fanart",
  extrafanart_template: "tpl.extrafanart",
  nfo_template: "tpl.nfo",
  trailer_template: "tpl.trailer",
  subtitle_template: "tpl.subtitle",
} as const satisfies Record<keyof OptionalPathTemplateDefaults, string>;

interface LibraryFormFieldsProps {
  value: LibraryFormState;
  onChange: (v: LibraryFormState) => void;
  /** true: 创建表单 (显示 automation + 创建后扫描开关); false: 编辑表单 (仅 automation). */
  showCreateOnly: boolean;
}

export function LibraryFormFields({ value, onChange, showCreateOnly }: LibraryFormFieldsProps) {
  const { t } = useTranslation("library");
  const { data: schema } = useQuery(getPathTemplateSchemaOptions());
  const [sidecarOpen, setSidecarOpen] = useState<string | null>(null);

  const placeholders = schema?.placeholders ?? [];
  const catalog = useMemo(
    () => templateCatalogFromPlaceholders(schema?.placeholders ?? []),
    [schema?.placeholders],
  );
  const optionalDefaults = schema?.optional_defaults;

  const copyPlaceholder = async (name: string) => {
    const text = `{${name}}`;
    try {
      await navigator.clipboard.writeText(text);
      notifications.show({
        message: t("placeholders.copied", { placeholder: text }),
        color: "blue",
      });
    } catch {
      notifications.show({ message: t("placeholders.copyFailed"), color: "red" });
    }
  };

  return (
    <Stack gap="md">
      <Group align="flex-end" grow preventGrowOverflow={false} wrap="wrap">
        <TextInput
          label={t("fieldName")}
          value={value.name}
          onChange={(e) => onChange({ ...value, name: e.currentTarget.value })}
          style={{ flex: "1 1 16rem" }}
        />
        <Switch
          label={t("fieldRecursive")}
          checked={value.recursive}
          onChange={(e) => onChange({ ...value, recursive: e.currentTarget.checked })}
          style={{ flex: "1 1 12rem" }}
        />
      </Group>
      <PathPicker
        label={t("fieldPath")}
        placeholder={t("placeholder")}
        value={value.path}
        onChange={(path) => onChange({ ...value, path })}
        pathType="directory"
      />
      <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="sm">
        <Input.Wrapper label={t("ingest.label")} description={t("ingest.hint")}>
          <div className={classes.ingestControl}>
            <EnumToggle
              options={LIBRARY_INGESTS}
              value={value.ingest}
              onChange={(ingest) =>
                onChange({
                  ...value,
                  ingest,
                  cloud_path: ingest === "native" ? "" : value.cloud_path,
                })
              }
              getLabel={(kind) => t(`ingest.${kind}`)}
            />
          </div>
        </Input.Wrapper>
        {value.ingest === "clouddrive" && (
          <TextInput
            label={t("ingest.cloudPath")}
            description={t("ingest.cloudPathHint")}
            placeholder="/115open/云下载"
            value={value.cloud_path}
            onChange={(e) => onChange({ ...value, cloud_path: e.currentTarget.value })}
          />
        )}
      </SimpleGrid>
      <Stack gap="sm">
        <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="sm">
          <TextInput
            label={t("fieldPatterns")}
            description={t("fieldPatternsHint")}
            value={value.patterns}
            onChange={(e) => onChange({ ...value, patterns: e.currentTarget.value })}
          />
          <TextInput
            label={t("fieldTrailerPattern")}
            description={t("fieldTrailerPatternHint")}
            value={value.trailer_pattern}
            onChange={(e) => onChange({ ...value, trailer_pattern: e.currentTarget.value })}
          />
          <Textarea
            label={t("fieldBlacklistPatterns")}
            description={t("fieldBlacklistPatternsHint")}
            autosize
            minRows={1}
            maxRows={5}
            value={value.blacklist_patterns}
            onChange={(e) => onChange({ ...value, blacklist_patterns: e.currentTarget.value })}
          />
          <NumberInput
            label={t("fieldMinFileSize")}
            description={t("fieldMinFileSizeHint")}
            min={0}
            allowDecimal={false}
            suffix=" MB"
            value={bytesToMb(value.min_file_size)}
            onChange={(raw) => {
              const mb = typeof raw === "number" ? raw : 0;
              onChange({ ...value, min_file_size: mbToBytes(mb) });
            }}
          />
        </SimpleGrid>
        <div className={classes.alignedRow}>
          <Input.Label className={classes.l1} htmlFor="library-subtitle-extensions">
            {t("fieldSubtitleExtensions")}
          </Input.Label>
          <Input.Label className={classes.l2}>{t("fieldMoveMode")}</Input.Label>
          <Input.Label className={classes.l3} htmlFor="library-write-nfo">
            {t("fieldWriteNfo")}
          </Input.Label>
          <Input.Description className={classes.d1}>
            {t("fieldSubtitleExtensionsHint")}
          </Input.Description>
          <Input.Description className={classes.d2} aria-hidden>
            {"\u00a0"}
          </Input.Description>
          <Input.Description className={classes.d3} aria-hidden>
            {"\u00a0"}
          </Input.Description>
          <TextInput
            id="library-subtitle-extensions"
            className={classes.c1}
            value={value.subtitle_extensions}
            onChange={(e) => onChange({ ...value, subtitle_extensions: e.currentTarget.value })}
          />
          <div className={classes.c2}>
            <EnumToggle
              options={MOVE_MODES}
              value={value.move_mode}
              onChange={(move_mode) => onChange({ ...value, move_mode })}
              getLabel={(mode) => t(`moveMode.${mode}`)}
            />
          </div>
          <div className={classes.c3}>
            <Switch
              id="library-write-nfo"
              checked={value.write_nfo}
              onChange={(e) => onChange({ ...value, write_nfo: e.currentTarget.checked })}
              aria-label={t("fieldWriteNfo")}
            />
          </div>
        </div>
      </Stack>
      <Checkbox.Group
        label={t("fieldCopyResources")}
        description={t("fieldCopyResourcesHint")}
        value={value.copy_resources}
        onChange={(selected) =>
          onChange({ ...value, copy_resources: parseCopyResources(selected) })
        }
      >
        <Group mt="xs">
          {DOWNLOADABLE_RESOURCES.map((kind) => (
            <Checkbox key={kind} value={kind} label={t(`copyResource.${kind}`)} />
          ))}
        </Group>
      </Checkbox.Group>
      <TemplateInput
        label={t("fieldVideoTemplate")}
        description={t("fieldVideoTemplateHint")}
        catalog={catalog}
        value={value.video_template}
        onChange={(e) => onChange({ ...value, video_template: e.currentTarget.value })}
      />
      <TemplateInput
        label={t("fieldLinkTemplate")}
        description={t("fieldLinkTemplateHint")}
        placeholder={t("fieldLinkTemplatePlaceholder")}
        catalog={catalog}
        value={value.link_template}
        onChange={(e) => onChange({ ...value, link_template: e.currentTarget.value })}
      />
      {value.link_template.trim() !== "" && (
        <FieldChrome label={t("fieldLinkMode")}>
          <EnumToggle
            options={LINK_MODES}
            value={value.link_mode}
            onChange={(link_mode) => onChange({ ...value, link_mode })}
            getLabel={(mode) => t(`linkMode.${mode}`)}
          />
        </FieldChrome>
      )}
      {value.link_template.trim() !== "" && value.link_mode === "strm" && (
        <TemplateInput
          label={t("fieldStrmContentTemplate")}
          description={t("fieldStrmContentTemplateHint")}
          catalog={catalog}
          value={value.strm_content_template}
          onChange={(e) => onChange({ ...value, strm_content_template: e.currentTarget.value })}
        />
      )}

      {placeholders.length > 0 && (
        <Stack gap={4}>
          <Text size="xs" c="dimmed">
            {t("placeholders.label")} · {t("placeholders.hint")}{" "}
            <Anchor
              href="https://sqzw-x.github.io/amane/user/libraries/#placeholders"
              target="_blank"
              rel="noreferrer"
              size="xs"
            >
              {t("placeholders.docs")}
            </Anchor>
          </Text>
          <Group gap={4} wrap="wrap">
            {placeholders.map((p) => {
              const item = t(`placeholders.items.${p.name}`, { defaultValue: p.name });
              const keys = p.map_keys ?? [];
              const label =
                keys.length > 0
                  ? `${item} · ${t("placeholders.mapKeys", { keys: keys.join(", ") })}`
                  : item;
              return (
                <Tooltip key={p.name} label={label} multiline maw={280}>
                  <Badge
                    component="button"
                    type="button"
                    size="sm"
                    variant="light"
                    style={{ cursor: "pointer" }}
                    onClick={() => void copyPlaceholder(p.name)}
                  >
                    {`{${p.name}}`}
                  </Badge>
                </Tooltip>
              );
            })}
          </Group>
        </Stack>
      )}

      <Accordion
        variant="contained"
        radius="sm"
        chevronPosition="left"
        disableChevronRotation
        chevron={
          sidecarOpen === "templates" ? (
            <IconChevronDown size={16} />
          ) : (
            <IconChevronRight size={16} />
          )
        }
        value={sidecarOpen}
        onChange={setSidecarOpen}
      >
        <Accordion.Item value="templates">
          <Accordion.Control>{t("advancedTemplates")}</Accordion.Control>
          <Accordion.Panel>
            <Stack gap="sm">
              <Text size="xs" c="dimmed">
                {t("advancedTemplatesHint")}
              </Text>
              <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="sm">
                {OPTIONAL_TEMPLATE_KEYS.map((key) => (
                  <TemplateInput
                    key={key}
                    label={t(TPL_I18N[key])}
                    description={
                      optionalDefaults?.[key]
                        ? t("optionalTemplateDefault", { default: optionalDefaults[key] })
                        : undefined
                    }
                    placeholder={optionalDefaults?.[key]}
                    catalog={catalog}
                    value={value[key]}
                    onChange={(e) => onChange({ ...value, [key]: e.currentTarget.value })}
                  />
                ))}
              </SimpleGrid>
            </Stack>
          </Accordion.Panel>
        </Accordion.Item>
      </Accordion>

      <SimpleGrid cols={{ base: 1, sm: showCreateOnly ? 2 : 1 }} spacing="sm">
        <FieldChrome label={t("automation.label")} description={t("automation.hint")}>
          <EnumToggle
            options={LIBRARY_AUTOMATIONS}
            value={value.automation}
            onChange={(automation) => onChange({ ...value, automation })}
            getLabel={(level) => t(`automation.${level}`)}
          />
        </FieldChrome>
        {showCreateOnly && (
          <FieldChrome label={t("fieldInitialRefresh")}>
            <Switch
              checked={value.scan}
              onChange={(e) => onChange({ ...value, scan: e.currentTarget.checked })}
              aria-label={t("fieldInitialRefresh")}
            />
          </FieldChrome>
        )}
      </SimpleGrid>
    </Stack>
  );
}
