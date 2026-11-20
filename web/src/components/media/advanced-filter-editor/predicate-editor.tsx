import { Group, Select, Text, TextInput } from "@mantine/core";
import { IconCheck, IconX } from "@tabler/icons-react";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { HintedActionIcon } from "@/components/common/hinted-action-icon";
import type { FilterEntity, FilterRule } from "@/lib/filter-expression";
import { createFilterRule, filterFieldFor, filterFieldsFor, filterRuleError } from "./fields";
import { PREDICATE_SELECT_PROPS } from "./predicate-combobox";
import classes from "./predicate-editor.module.css";
import { FilterValueInput, type FilterLibrary } from "./value-input";

export interface PredicateEditorProps {
  entity: FilterEntity;
  rule: FilterRule | null;
  libraries?: readonly FilterLibrary[];
  disabled?: boolean;
  onSave: (rule: FilterRule) => void;
  onClose: () => void;
}

interface PredicateDraft {
  field: FilterRule["field"] | null;
  operator: FilterRule["operator"] | null;
  value: FilterRule["value"];
}

export function PredicateEditor({
  entity,
  rule,
  libraries,
  disabled = false,
  onSave,
  onClose,
}: PredicateEditorProps) {
  const { t } = useTranslation(["metadata", "common"]);
  const errorId = useId();
  const [draft, setDraft] = useState<PredicateDraft>(
    () => rule ?? { field: null, operator: null, value: "" },
  );
  const [validationRequested, setValidationRequested] = useState(false);
  const fields = filterFieldsFor(entity);
  const field = draft.field === null ? undefined : filterFieldFor(entity, draft.field);
  const operator = field?.operators.find((item) => item === draft.operator) ?? null;
  const nextRule: FilterRule | null =
    field && operator ? { type: "rule", field: field.field, operator, value: draft.value } : null;
  const error = validationRequested && nextRule ? filterRuleError(entity, nextRule) : null;
  const fieldOptions = (["identity", "profile", "files"] as const).flatMap((section) => {
    const items = fields
      .filter((item) => item.section === section)
      .map((item) => ({ value: item.field, label: t(item.labelKey) }));
    return items.length ? [{ group: t(`advancedFilter.sections.${section}`), items }] : [];
  });

  const save = () => {
    if (disabled || !nextRule) return;
    if (filterRuleError(entity, nextRule)) {
      setValidationRequested(true);
      return;
    }
    onSave(nextRule);
  };

  return (
    <div
      className={classes.editor}
      role="group"
      aria-label={t(rule ? "advancedFilter.formula.editPredicate" : "advancedFilter.addRule")}
      onKeyDown={(event) => {
        if (event.key !== "Escape" || event.defaultPrevented || event.nativeEvent.isComposing)
          return;
        event.stopPropagation();
        if (event.target instanceof HTMLElement && event.target.hasAttribute("data-expanded"))
          return;
        event.preventDefault();
        onClose();
      }}
    >
      <div className={classes.layout}>
        <div className={classes.row}>
          <div data-next={(!field && !disabled) || undefined}>
            <Select
              {...PREDICATE_SELECT_PROPS}
              aria-label={t("advancedFilter.field")}
              placeholder={t("advancedFilter.fieldPlaceholder")}
              data={fieldOptions}
              value={field?.field ?? null}
              searchable
              nothingFoundMessage={t("search.facetEmpty")}
              disabled={disabled}
              onChange={(value) => {
                if (value === null) return;
                const next = filterFieldFor(entity, value);
                if (!next) return;
                // 选定字段后直接落到该字段的第一个运算符与初始取值, 默认即可提交.
                const created = createFilterRule(next);
                setDraft({
                  field: created.field,
                  operator: created.operator,
                  value: created.value,
                });
                setValidationRequested(false);
              }}
            />
          </div>
          <div data-next={(!!field && operator === null && !disabled) || undefined}>
            <Select
              {...PREDICATE_SELECT_PROPS}
              aria-label={t("advancedFilter.operator")}
              placeholder={t("advancedFilter.operator")}
              value={operator}
              disabled={disabled || !field}
              data={(field?.operators ?? []).map((value) => ({
                value,
                label: t(`advancedFilter.operators.${value}`),
              }))}
              onChange={(value) => setDraft((current) => ({ ...current, operator: value }))}
            />
          </div>
          <div
            className={classes.value}
            data-next={(nextRule !== null && draft.value === "" && !disabled) || undefined}
          >
            {field && operator ? (
              <FilterValueInput
                key={field.field}
                field={field}
                value={draft.value}
                disabled={disabled}
                errorId={error ? errorId : undefined}
                libraries={libraries}
                onSubmit={save}
                onChange={(value) => setDraft((current) => ({ ...current, value }))}
              />
            ) : (
              <TextInput
                classNames={{ input: classes.input }}
                aria-label={t("advancedFilter.value")}
                placeholder={t("advancedFilter.value")}
                disabled
              />
            )}
          </div>
        </div>
        <Group gap={4} justify="flex-end" className={classes.actions}>
          <HintedActionIcon
            size={28}
            variant="subtle"
            color="gray"
            label={t("common:actions.cancel")}
            onClick={onClose}
          >
            <IconX size={16} />
          </HintedActionIcon>
          <HintedActionIcon
            size={28}
            label={t(rule ? "common:actions.save" : "advancedFilter.addRule")}
            disabled={disabled || !nextRule}
            onClick={save}
          >
            <IconCheck size={16} />
          </HintedActionIcon>
        </Group>
      </div>
      {error && (
        <Text
          id={errorId}
          role="alert"
          className={classes.validationError}
          size="sm"
          c="var(--mantine-color-error)"
        >
          {t(error)}
        </Text>
      )}
    </div>
  );
}
