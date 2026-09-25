/**
 * 演员浏览高级筛选 - 草稿编辑, "应用"后才写入 URL (见 ``lib/actors/browse``).
 */

import {
  Button,
  Chip,
  Collapse,
  Group,
  Input,
  NumberInput,
  SegmentedControl,
  Select,
  Stack,
  Text,
  TextInput,
} from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { listFacetsOptions } from "@/client/@tanstack/react-query.gen";
import type { ActorGender } from "@/client/types.gen";
import { useResettingState } from "@/hooks/use-resetting-state";
import {
  ACTOR_GENDERS,
  ACTOR_RANGE_FILTERS,
  type ActorFilterPatch,
  type ActorFilterValues,
  type ActorRangeFilter,
  type ActorTriBool,
  actorFiltersEqual,
  actorFilterFingerprint,
  cloneActorFilterValues,
  normalizeActorFilterValues,
  rangeValue,
} from "@/lib/actors/browse";
import { USER_TAG_FACET_LIST } from "@/lib/facets";

export type { ActorFilterPatch, ActorFilterValues };

export interface ActorFilterControlsProps {
  opened: boolean;
  /** 已生效的 URL 筛选 (芯片清除等会更新). */
  committed: ActorFilterValues;
  onApply: (filters: ActorFilterValues) => void;
}

/** 筛选项按内容定宽, 外层 wrap 排布, 一行尽量多塞. */
function RangeNumberInputs({
  label,
  min,
  max,
  onMinChange,
  onMaxChange,
}: {
  label: string;
  min: number | undefined;
  max: number | undefined;
  onMinChange: (v: number | undefined) => void;
  onMaxChange: (v: number | undefined) => void;
}) {
  const { t } = useTranslation("metadata");
  return (
    <Input.Wrapper label={label} size="sm" styles={{ root: { width: "fit-content" } }}>
      <Group gap={4} wrap="nowrap">
        <NumberInput
          size="sm"
          placeholder={t("actors.rangeMin")}
          value={min ?? ""}
          min={0}
          allowDecimal={false}
          hideControls
          w={72}
          onChange={(v) => onMinChange(typeof v === "number" ? v : undefined)}
        />
        <Text size="sm" c="dimmed">
          –
        </Text>
        <NumberInput
          size="sm"
          placeholder={t("actors.rangeMax")}
          value={max ?? ""}
          min={0}
          allowDecimal={false}
          hideControls
          w={72}
          onChange={(v) => onMaxChange(typeof v === "number" ? v : undefined)}
        />
      </Group>
    </Input.Wrapper>
  );
}

function RangeTextInputs({
  label,
  min,
  max,
  minPlaceholder,
  maxPlaceholder,
  fieldWidth,
  onMinChange,
  onMaxChange,
}: {
  label: string;
  min: string | undefined;
  max: string | undefined;
  minPlaceholder: string;
  maxPlaceholder: string;
  fieldWidth: number;
  onMinChange: (v: string | undefined) => void;
  onMaxChange: (v: string | undefined) => void;
}) {
  return (
    <Input.Wrapper label={label} size="sm" styles={{ root: { width: "fit-content" } }}>
      <Group gap={4} wrap="nowrap">
        <TextInput
          size="sm"
          placeholder={minPlaceholder}
          value={min ?? ""}
          w={fieldWidth}
          onChange={(e) => onMinChange(e.currentTarget.value || undefined)}
        />
        <Text size="sm" c="dimmed">
          –
        </Text>
        <TextInput
          size="sm"
          placeholder={maxPlaceholder}
          value={max ?? ""}
          w={fieldWidth}
          onChange={(e) => onMaxChange(e.currentTarget.value || undefined)}
        />
      </Group>
    </Input.Wrapper>
  );
}

const GENDER_I18N = {
  female: "browse.person.genderFemale",
  male: "browse.person.genderMale",
  unknown: "browse.person.genderUnknown",
} as const;

function RangeFilterControl({
  range,
  filters,
  onChange,
}: {
  range: ActorRangeFilter;
  filters: ActorFilterValues;
  onChange: (patch: ActorFilterPatch) => void;
}) {
  const { t } = useTranslation("metadata");
  const label = t(range.labelKey);
  const minRaw = rangeValue(filters, range.min);
  const maxRaw = rangeValue(filters, range.max);

  if (range.kind === "int") {
    return (
      <RangeNumberInputs
        label={label}
        min={typeof minRaw === "number" ? minRaw : undefined}
        max={typeof maxRaw === "number" ? maxRaw : undefined}
        onMinChange={(v) => onChange({ [range.min]: v })}
        onMaxChange={(v) => onChange({ [range.max]: v })}
      />
    );
  }

  if (range.kind === "cup") {
    return (
      <RangeTextInputs
        label={label}
        min={typeof minRaw === "string" ? minRaw : undefined}
        max={typeof maxRaw === "string" ? maxRaw : undefined}
        minPlaceholder={t("actors.cupMinPlaceholder")}
        maxPlaceholder={t("actors.cupMaxPlaceholder")}
        fieldWidth={56}
        onMinChange={(v) => onChange({ [range.min]: v })}
        onMaxChange={(v) => onChange({ [range.max]: v })}
      />
    );
  }

  return (
    <RangeTextInputs
      label={label}
      min={typeof minRaw === "string" ? minRaw : undefined}
      max={typeof maxRaw === "string" ? maxRaw : undefined}
      minPlaceholder={t("actors.dateMinPlaceholder")}
      maxPlaceholder={t("actors.dateMaxPlaceholder")}
      fieldWidth={118}
      onMinChange={(v) => onChange({ [range.min]: v })}
      onMaxChange={(v) => onChange({ [range.max]: v })}
    />
  );
}

function TriStateSegment({
  label,
  value,
  yesLabel,
  noLabel,
  onChange,
}: {
  label: string;
  value: ActorTriBool | undefined;
  yesLabel: string;
  noLabel: string;
  onChange: (next: ActorTriBool | undefined) => void;
}) {
  const { t } = useTranslation("metadata");
  return (
    <Input.Wrapper label={label} size="sm">
      <SegmentedControl
        size="sm"
        value={value ?? "any"}
        onChange={(v) => {
          if (v === "true" || v === "false") onChange(v);
          else onChange(undefined);
        }}
        data={[
          { value: "any", label: t("search.hasFilesAny") },
          { value: "true", label: yesLabel },
          { value: "false", label: noLabel },
        ]}
      />
    </Input.Wrapper>
  );
}

function applyPatch(prev: ActorFilterValues, patch: ActorFilterPatch): ActorFilterValues {
  const next: ActorFilterValues = {
    ...prev,
    ...patch,
    gender: patch.gender !== undefined ? patch.gender : prev.gender,
  };
  // 年龄与生日互斥: 改一侧清空另一侧, 避免应用时语义打架.
  if ("age_min" in patch || "age_max" in patch) {
    next.birthday_min = undefined;
    next.birthday_max = undefined;
  }
  if ("birthday_min" in patch || "birthday_max" in patch) {
    next.age_min = undefined;
    next.age_max = undefined;
  }
  return next;
}

export function ActorFilterControls({ opened, committed, onApply }: ActorFilterControlsProps) {
  const { t } = useTranslation(["metadata", "common"]);
  const { data: userTags } = useQuery(listFacetsOptions(USER_TAG_FACET_LIST));
  const committedKey = actorFilterFingerprint(committed);
  const [draft, setDraft] = useResettingState(
    () => cloneActorFilterValues(committed),
    committedKey,
  );

  const dirty = !actorFiltersEqual(
    normalizeActorFilterValues(draft),
    normalizeActorFilterValues(committed),
  );

  function patchDraft(patch: ActorFilterPatch) {
    setDraft((prev) => applyPatch(prev, patch));
  }

  return (
    <Collapse expanded={opened}>
      <Stack gap="sm">
        <Group gap="lg" align="flex-end" wrap="wrap">
          <Input.Wrapper label={t("browse.person.gender")} size="sm">
            <Chip.Group
              multiple
              value={draft.gender}
              onChange={(values) =>
                patchDraft({
                  gender: values.filter((v): v is ActorGender =>
                    (ACTOR_GENDERS as readonly string[]).includes(v),
                  ),
                })
              }
            >
              <Group gap={6} mt={4}>
                {ACTOR_GENDERS.map((g) => (
                  <Chip key={g} value={g} size="sm" variant="outline">
                    {t(GENDER_I18N[g])}
                  </Chip>
                ))}
              </Group>
            </Chip.Group>
          </Input.Wrapper>

          <TriStateSegment
            label={t("actors.filterPerson")}
            value={draft.has_person}
            yesLabel={t("actors.filterHasPerson")}
            noLabel={t("actors.filterNoPerson")}
            onChange={(has_person) => patchDraft({ has_person })}
          />
          <TriStateSegment
            label={t("actors.filterImage")}
            value={draft.has_image}
            yesLabel={t("actors.filterHasImage")}
            noLabel={t("actors.filterNoImage")}
            onChange={(has_image) => patchDraft({ has_image })}
          />
          <Input.Wrapper label={t("detail.userTags")} size="sm">
            <Select
              size="sm"
              w={160}
              clearable
              searchable
              placeholder={t("search.hasFilesAny")}
              data={(userTags?.items ?? []).map((tag) => ({
                value: String(tag.id),
                label: tag.name,
              }))}
              value={draft.user_tag_id != null ? String(draft.user_tag_id) : null}
              onChange={(value) =>
                patchDraft({ user_tag_id: value != null ? Number(value) : undefined })
              }
            />
          </Input.Wrapper>
        </Group>

        <Group gap="md" align="flex-end" wrap="wrap">
          <RangeNumberInputs
            label={t("actors.age")}
            min={draft.age_min}
            max={draft.age_max}
            onMinChange={(v) => patchDraft({ age_min: v })}
            onMaxChange={(v) => patchDraft({ age_max: v })}
          />
          {ACTOR_RANGE_FILTERS.map((range) => (
            <RangeFilterControl
              key={range.min}
              range={range}
              filters={draft}
              onChange={patchDraft}
            />
          ))}
          <TextInput
            label={t("browse.person.birthplace")}
            size="sm"
            value={draft.birthplace ?? ""}
            placeholder={t("actors.birthplacePlaceholder")}
            onChange={(e) => patchDraft({ birthplace: e.currentTarget.value || undefined })}
            w={160}
          />
        </Group>

        <Group gap="xs">
          <Button
            size="sm"
            disabled={!dirty}
            onClick={() => onApply(normalizeActorFilterValues(draft))}
          >
            {t("actors.filterApply")}
          </Button>
          <Button
            size="sm"
            variant="default"
            disabled={!dirty}
            onClick={() => setDraft(cloneActorFilterValues(committed))}
          >
            {t("common:actions.reset")}
          </Button>
        </Group>
      </Stack>
    </Collapse>
  );
}
