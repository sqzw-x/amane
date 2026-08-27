import {
  Accordion,
  Badge,
  Checkbox,
  Group,
  SimpleGrid,
  Stack,
  Switch,
  Text,
  Textarea,
  TextInput,
  Tooltip,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { getPathTemplateSchemaOptions } from "@/client/@tanstack/react-query.gen";
import { LibraryCreateRequestSchema, LibraryResponseSchema } from "@/client/schemas.gen";
import type {
  DownloadableResource,
  LibraryAutomation,
  LibraryCreateRequest,
  LibraryResponse,
  LibraryUpdateRequest,
  PathTemplateSchemaResponse,
} from "@/client/types.gen";
import { PathPicker } from "@/components/path-picker";
import { encodeFormBody } from "@/components/schema-form/encode";
import { EnumToggle } from "@/components/common/enum-toggle";
import { FieldChrome } from "@/components/schema-form/fields/field-chrome";
import { DOWNLOADABLE_RESOURCES, LIBRARY_AUTOMATIONS } from "@/lib/exhaustive-maps";

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
] as const;

type OptionalTemplateKey = (typeof OPTIONAL_TEMPLATE_KEYS)[number];

const MOVE_MODES: readonly LibraryResponse["move_mode"][] = ["move", "copy", "hardlink", "symlink"];

export const LIBRARY_AUTOMATION_BADGE_COLOR = {
  none: "gray",
  watch: "blue",
  scrape: "teal",
} as const satisfies Record<LibraryAutomation, string>;

/** 媒体库创建/编辑表单的本地字段状态 (与 API body 的 null/undefined 语义解耦). */
export interface LibraryFormState {
  name: string;
  path: string;
  recursive: boolean;
  patterns: string;
  move_mode: LibraryResponse["move_mode"];
  write_nfo: boolean;
  copy_resources: DownloadableResource[];
  trailer_pattern: string;
  blacklist_patterns: string;
  subtitle_extensions: string;
  video_template: string;
  cd_suffix_template: string;
  thumb_template: string;
  poster_template: string;
  fanart_template: string;
  extrafanart_template: string;
  nfo_template: string;
  trailer_template: string;
  subtitle_template: string;
  automation: LibraryAutomation;
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
    write_nfo: true,
    copy_resources: DOWNLOADABLE_RESOURCES.filter((r) => r !== "trailer"),
    trailer_pattern: "(?i)trailer",
    blacklist_patterns: "",
    subtitle_extensions: (
      schema?.subtitle_extensions_default ?? [".srt", ".ass", ".ssa", ".vtt", ".sub"]
    ).join(", "),
    video_template: schema?.video_default ?? "{studio}/{number}/{number}.{ext}",
    cd_suffix_template: schema?.cd_suffix_default ?? "-CD{cd}",
    thumb_template: defaults?.thumb_template ?? "",
    poster_template: defaults?.poster_template ?? "",
    fanart_template: defaults?.fanart_template ?? "",
    extrafanart_template: defaults?.extrafanart_template ?? "",
    nfo_template: defaults?.nfo_template ?? "",
    trailer_template: defaults?.trailer_template ?? "",
    subtitle_template: defaults?.subtitle_template ?? "",
    automation: "scrape",
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
    write_nfo: lib.write_nfo,
    copy_resources: parseCopyResources(lib.copy_resources),
    trailer_pattern: lib.trailer_pattern,
    blacklist_patterns: lib.blacklist_patterns?.join("\n") ?? "",
    subtitle_extensions: lib.subtitle_extensions?.join(", ") ?? "",
    video_template: lib.video_template,
    cd_suffix_template: lib.cd_suffix_template,
    thumb_template: lib.thumb_template ?? "",
    poster_template: lib.poster_template ?? "",
    fanart_template: lib.fanart_template ?? "",
    extrafanart_template: lib.extrafanart_template ?? "",
    nfo_template: lib.nfo_template ?? "",
    trailer_template: lib.trailer_template ?? "",
    subtitle_template: lib.subtitle_template ?? "",
    automation: lib.automation,
    scan: false,
  };
}

export function parseLibraryPatterns(s: string): string[] {
  return s
    .split(",")
    .map((p) => p.trim())
    .filter(Boolean);
}

/** 黑名单正则按行分隔: 正则本身可含逗号 (如量词 {2,3}), 不能用逗号切分. */
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
    write_nfo: form.write_nfo,
    copy_resources: form.copy_resources,
    trailer_pattern: form.trailer_pattern,
    blacklist_patterns: parseBlacklistPatterns(form.blacklist_patterns),
    subtitle_extensions: parseLibraryPatterns(form.subtitle_extensions),
    video_template: form.video_template.trim(),
    cd_suffix_template: form.cd_suffix_template.trim(),
    thumb_template: form.thumb_template.trim(),
    poster_template: form.poster_template.trim(),
    fanart_template: form.fanart_template.trim(),
    extrafanart_template: form.extrafanart_template.trim(),
    nfo_template: form.nfo_template.trim(),
    trailer_template: form.trailer_template.trim(),
    subtitle_template: form.subtitle_template.trim(),
    automation: form.automation,
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
} as const satisfies Record<OptionalTemplateKey, string>;

interface LibraryFormFieldsProps {
  value: LibraryFormState;
  onChange: (v: LibraryFormState) => void;
  /** true: 创建表单 (显示 automation + 创建后扫描开关); false: 编辑表单 (仅 automation). */
  showCreateOnly: boolean;
}

/** 媒体库表单字段 - 创建/编辑模态框共用. 占位符/默认值以后端 path-template-schema 为准. */
export function LibraryFormFields({ value, onChange, showCreateOnly }: LibraryFormFieldsProps) {
  const { t } = useTranslation("library");
  const { data: schema } = useQuery(getPathTemplateSchemaOptions());

  const placeholders = schema?.placeholders ?? [];
  const optionalDefaults = schema?.optional_defaults ?? {};

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
        <TextInput
          label={t("fieldSubtitleExtensions")}
          description={t("fieldSubtitleExtensionsHint")}
          value={value.subtitle_extensions}
          onChange={(e) => onChange({ ...value, subtitle_extensions: e.currentTarget.value })}
        />
        <FieldChrome label={t("fieldMoveMode")} description={t("fieldMoveModeHint")}>
          <EnumToggle
            options={MOVE_MODES}
            value={value.move_mode}
            onChange={(move_mode) => onChange({ ...value, move_mode })}
            getLabel={(mode) => t(`moveMode.${mode}`)}
          />
        </FieldChrome>
        <FieldChrome label={t("fieldWriteNfo")} description={t("fieldWriteNfoHint")}>
          <Switch
            checked={value.write_nfo}
            onChange={(e) => onChange({ ...value, write_nfo: e.currentTarget.checked })}
            aria-label={t("fieldWriteNfo")}
          />
        </FieldChrome>
      </SimpleGrid>
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
      <TextInput
        label={t("fieldCdSuffixTemplate")}
        description={t("fieldCdSuffixTemplateHint")}
        placeholder={schema?.cd_suffix_default ?? "-CD{cd}"}
        value={value.cd_suffix_template}
        onChange={(e) => onChange({ ...value, cd_suffix_template: e.currentTarget.value })}
      />
      <TextInput
        label={t("fieldVideoTemplate")}
        description={t("fieldVideoTemplateHint")}
        value={value.video_template}
        onChange={(e) => onChange({ ...value, video_template: e.currentTarget.value })}
      />

      {placeholders.length > 0 && (
        <Stack gap={4}>
          <Text size="xs" c="dimmed">
            {t("placeholders.label")} · {t("placeholders.hint")}
          </Text>
          <Group gap={4} wrap="wrap">
            {placeholders.map((p) => (
              <Tooltip
                key={p.name}
                label={t(`placeholders.items.${p.name}`, {
                  defaultValue: t(`placeholders.phases.${p.phase}`, { defaultValue: p.phase }),
                })}
                multiline
                maw={280}
              >
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
            ))}
          </Group>
        </Stack>
      )}

      <Accordion variant="contained" radius="sm">
        <Accordion.Item value="templates">
          <Accordion.Control>{t("advancedTemplates")}</Accordion.Control>
          <Accordion.Panel>
            <Stack gap="sm">
              <Text size="xs" c="dimmed">
                {t("advancedTemplatesHint")}
              </Text>
              <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="sm">
                {OPTIONAL_TEMPLATE_KEYS.map((key) => (
                  <TextInput
                    key={key}
                    label={t(TPL_I18N[key])}
                    description={
                      optionalDefaults[key]
                        ? t("optionalTemplateDefault", { default: optionalDefaults[key] })
                        : undefined
                    }
                    placeholder={optionalDefaults[key] ?? undefined}
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
