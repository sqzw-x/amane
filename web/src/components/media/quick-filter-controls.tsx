import { Button, Collapse, Group, Input, Stack, Text, UnstyledButton } from "@mantine/core";
import { useMediaQuery } from "@mantine/hooks";
import { IconChevronDown, IconPlus } from "@tabler/icons-react";
import type { ParseKeys } from "i18next";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { HintedActionIcon } from "@/components/common/hinted-action-icon";
import { birthdayBoundsFromAge } from "@/lib/actors/browse";
import {
  type FilterEntity,
  type FilterField,
  type FilterRule,
  type FilterValue,
  filterFieldsFor,
  filterRuleError,
} from "@/lib/filter-expression";
import { FilterValueInput, type FilterLibrary } from "./advanced-filter-editor/value-input";
import { FilterSection } from "./filter-section";
import classes from "./quick-filter-controls.module.css";

const SECTIONS = ["identity", "profile", "files"] as const;

/** 控件按取值方式排序: 一次点击的布尔在最先, 区间最末. */
const CONTROL_ORDER: Record<FilterField["control"], number> = {
  boolean: 0,
  enum: 1,
  facet: 1,
  library: 1,
  number: 2,
  date: 2,
  cup: 2,
  text: 3,
};

const AGE_MAX = 150;

export interface QuickFilterControlsProps {
  entity: FilterEntity;
  libraries?: readonly FilterLibrary[];
  disabled?: boolean;
  /** 把校验通过的条件追加到公式; 控件自身不保留取值. */
  onInsert: (rules: readonly FilterRule[]) => void;
}

function rule(
  field: FilterField,
  operator: FilterRule["operator"],
  value: FilterValue,
): FilterRule {
  return { type: "rule", field: field.field, operator, value };
}

/** 区间两侧的空位提示; 日期固定显示格式, 数字与罩杯才区分最小/最大. */
function rangePlaceholder(
  field: FilterField,
  side: "min" | "max",
): ParseKeys<"metadata"> | undefined {
  switch (field.control) {
    case "date":
      return undefined;
    case "cup":
      return side === "min" ? "actors.cupMinPlaceholder" : "actors.cupMaxPlaceholder";
    default:
      return side === "min" ? "actors.rangeMin" : "actors.rangeMax";
  }
}

/** 是否按区间取值: 两个比较符都能用的字段配最小/最大两个输入. */
function isRange(field: FilterField): boolean {
  return field.operators.includes("gte") && field.operators.includes("lte");
}

function BooleanInserter({
  label,
  disabled,
  onInsert,
}: {
  label: string;
  disabled: boolean;
  onInsert: (value: boolean) => void;
}) {
  const { t } = useTranslation("metadata");
  return (
    <Input.Wrapper label={label} labelElement="div" size="sm" role="group" aria-label={label}>
      <Button.Group>
        <Button
          size="compact-sm"
          variant="default"
          disabled={disabled}
          onClick={() => onInsert(true)}
        >
          {t("search.yes")}
        </Button>
        <Button
          size="compact-sm"
          variant="default"
          disabled={disabled}
          onClick={() => onInsert(false)}
        >
          {t("search.no")}
        </Button>
      </Button.Group>
    </Input.Wrapper>
  );
}

/** 选定取值即插入一条; 校验不过的值不插入, 控件退回空. */
function ValueInserter({
  entity,
  field,
  label,
  libraries,
  disabled,
  onInsert,
}: {
  entity: FilterEntity;
  field: FilterField;
  label: string;
  libraries?: readonly FilterLibrary[];
  disabled: boolean;
  onInsert: (rules: readonly FilterRule[]) => void;
}) {
  return (
    <Input.Wrapper label={label} labelElement="div" size="sm" className={classes.compactField}>
      <FilterValueInput
        field={field}
        value=""
        ariaLabel={label}
        placeholder={label}
        disabled={disabled}
        libraries={libraries}
        onSubmit={() => undefined}
        onChange={(value) => {
          const next = rule(field, field.operators[0] ?? "eq", value);
          if (filterRuleError(entity, next) === null) onInsert([next]);
        }}
      />
    </Input.Wrapper>
  );
}

function isAge(value: number | undefined): boolean {
  return value === undefined || (Number.isInteger(value) && value >= 0 && value <= AGE_MAX);
}

function RangeInserter({
  entity,
  field,
  disabled,
  onInsert,
  age = false,
}: {
  entity: FilterEntity;
  field: FilterField;
  disabled: boolean;
  onInsert: (rules: readonly FilterRule[]) => void;
  age?: boolean;
}) {
  const { t } = useTranslation("metadata");
  const label = age ? t("browse.person.age") : t(field.labelKey);
  const [min, setMin] = useState<FilterValue>("");
  const [max, setMax] = useState<FilterValue>("");
  const rules = age
    ? []
    : [
        ...(min === "" ? [] : [rule(field, "gte", min)]),
        ...(max === "" ? [] : [rule(field, "lte", max)]),
      ];
  const minAge = typeof min === "number" ? min : undefined;
  const maxAge = typeof max === "number" ? max : undefined;
  const invalidAge =
    !isAge(minAge) || !isAge(maxAge) || (minAge != null && maxAge != null && minAge > maxAge);
  const error = age
    ? invalidAge && "advancedFilter.errors.age"
    : rules.map((item) => filterRuleError(entity, item)).find((item) => item != null);
  const canInsert = (min !== "" || max !== "") && !error && !disabled;

  function insert() {
    if (!canInsert) return;
    if (age) {
      const bounds = birthdayBoundsFromAge(minAge, maxAge);
      onInsert([
        ...(bounds.birthday_max === undefined ? [] : [rule(field, "lte", bounds.birthday_max)]),
        ...(bounds.birthday_min === undefined ? [] : [rule(field, "gte", bounds.birthday_min)]),
      ]);
    } else {
      onInsert(rules);
    }
    setMin("");
    setMax("");
  }

  function renderBound(
    side: "min" | "max",
    value: FilterValue,
    setValue: (value: FilterValue) => void,
  ) {
    const bound = t(side === "min" ? "actors.rangeMin" : "actors.rangeMax");
    const ariaLabel = `${label} · ${bound}`;
    const placeholder = rangePlaceholder(field, side);
    return (
      <div className={classes.bound}>
        {age ? (
          <Input
            type="number"
            size="sm"
            inputMode="numeric"
            min={0}
            max={AGE_MAX}
            aria-label={ariaLabel}
            placeholder={bound}
            value={typeof value === "number" ? value : ""}
            disabled={disabled}
            onChange={({ currentTarget }) =>
              setValue(currentTarget.value === "" ? "" : Number(currentTarget.value))
            }
          />
        ) : (
          <FilterValueInput
            field={field}
            value={value}
            ariaLabel={ariaLabel}
            placeholder={placeholder === undefined ? undefined : t(placeholder)}
            disabled={disabled}
            onSubmit={insert}
            onChange={setValue}
          />
        )}
      </div>
    );
  }

  return (
    <Input.Wrapper label={label} labelElement="div" size="sm" className={classes.rangeField}>
      <Group gap={4} wrap="nowrap" align="flex-start">
        {renderBound("min", min, setMin)}
        <Text size="sm" c="dimmed" aria-hidden="true">
          –
        </Text>
        {renderBound("max", max, setMax)}
        <HintedActionIcon
          size={26}
          variant="light"
          label={t("search.insertRule")}
          disabled={!canInsert}
          onClick={insert}
        >
          <IconPlus size={14} />
        </HintedActionIcon>
      </Group>
      {error && (
        <Text size="xs" c="var(--mantine-color-error)" role="alert">
          {t(error)}
        </Text>
      )}
    </Input.Wrapper>
  );
}

function FieldInserter({
  entity,
  field,
  libraries,
  disabled,
  onInsert,
}: {
  entity: FilterEntity;
  field: FilterField;
  libraries?: readonly FilterLibrary[];
  disabled: boolean;
  onInsert: (rules: readonly FilterRule[]) => void;
}) {
  const { t } = useTranslation("metadata");
  const label = t(field.labelKey);
  if (field.control === "boolean") {
    return (
      <BooleanInserter
        label={label}
        disabled={disabled}
        onInsert={(value) => onInsert([rule(field, field.operators[0] ?? "eq", value)])}
      />
    );
  }
  if (isRange(field)) {
    return <RangeInserter entity={entity} field={field} disabled={disabled} onInsert={onInsert} />;
  }
  return (
    <ValueInserter
      entity={entity}
      field={field}
      label={label}
      libraries={libraries}
      disabled={disabled}
      onInsert={onInsert}
    />
  );
}

/**
 * 快速添加: 每个控件填好后把条件追加到公式, 自身不保留取值.
 *
 * 控件由 `filterFieldsFor` 的字段表生成, 片库与演员页共用同一套语义与控件:
 * 布尔点一下即插入, 单选选定即插入, 区间填好点一次插入两条; 文本字段不在此列,
 * 由搜索框承担. 插入前一律走 `filterRuleError`, 不合法的取值进不了公式.
 */
export function QuickFilterControls({
  entity,
  libraries,
  disabled = false,
  onInsert,
}: QuickFilterControlsProps) {
  const { t } = useTranslation("metadata");
  const wide = useMediaQuery("(min-width: 48em)", true, { getInitialValueInEffect: false });
  const [quickOverride, setQuickOverride] = useState<boolean | null>(null);
  // 宽屏默认展开, 窄屏默认收起: 抽屉里的表单长, 收起后公式才露头.
  const quickOpen = quickOverride ?? wide;
  const fields = filterFieldsFor(entity)
    .filter((field) => field.control !== "text")
    .toSorted((a, b) => CONTROL_ORDER[a.control] - CONTROL_ORDER[b.control]);

  return (
    <section className={classes.quick} aria-label={t("search.quickAdd")}>
      <UnstyledButton
        className={classes.quickToggle}
        onClick={() => setQuickOverride(!quickOpen)}
        aria-expanded={quickOpen}
        mb={quickOpen ? "sm" : 0}
      >
        <span className={classes.quickLabel}>{t("search.quickAdd")}</span>
        <IconChevronDown
          size={14}
          className={quickOpen ? classes.chevronOpen : classes.chevron}
          aria-hidden="true"
        />
      </UnstyledButton>
      <Collapse expanded={quickOpen}>
        <Stack gap="md">
          {SECTIONS.map((section) => {
            const items = fields.filter((field) => field.section === section);
            const birthday =
              entity === "actor" ? items.find((item) => item.field === "birthday") : undefined;
            if (!items.length && !birthday) return null;
            return (
              <FilterSection key={section} label={t(`advancedFilter.sections.${section}`)}>
                {items.map((field) => (
                  <FieldInserter
                    key={field.field}
                    entity={entity}
                    field={field}
                    libraries={libraries}
                    disabled={disabled}
                    onInsert={onInsert}
                  />
                ))}
                {birthday && (
                  <RangeInserter
                    age
                    entity={entity}
                    field={birthday}
                    disabled={disabled}
                    onInsert={onInsert}
                  />
                )}
              </FilterSection>
            );
          })}
        </Stack>
      </Collapse>
    </section>
  );
}
