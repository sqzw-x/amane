import { Button, NumberInput, Select, Stack, Text, TextInput } from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { useQuery } from "@tanstack/react-query";
import { type KeyboardEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  getFacetOptions,
  listFacetsOptions,
  listLibrariesOptions,
} from "@/client/@tanstack/react-query.gen";
import type { FacetKind, LibraryResponse } from "@/client/types.gen";
import type { FilterValue } from "@/lib/filter-expression";
import type { FilterField } from "./fields";
import { PREDICATE_SELECT_PROPS } from "./predicate-combobox";
import classes from "./predicate-editor.module.css";

export type FilterLibrary = Pick<LibraryResponse, "id" | "name">;

/** 只留数字并按 YYYY-MM-DD 补分隔符: 输入框永远显示同一种格式, 不受浏览器语言影响. */
export function formatDateInput(raw: string): string {
  const digits = raw.replace(/\D/gu, "").slice(0, 8);
  if (digits.length <= 4) return digits;
  if (digits.length <= 6) return `${digits.slice(0, 4)}-${digits.slice(4)}`;
  return `${digits.slice(0, 4)}-${digits.slice(4, 6)}-${digits.slice(6)}`;
}

interface ValueInputProps {
  field: FilterField;
  value: FilterValue;
  onChange: (value: FilterValue) => void;
  onSubmit: () => void;
  disabled: boolean;
  errorId?: string;
  /** 取值控件的可访问名; 快速添加按字段标签区分同一页里的多个控件. */
  ariaLabel?: string;
  /** 空值时的提示; 快速添加用字段标签或最小/最大代替通用占位. */
  placeholder?: string;
  libraries?: readonly FilterLibrary[];
}

interface PickerProps extends Pick<
  ValueInputProps,
  "value" | "onChange" | "libraries" | "placeholder"
> {
  inputProps: {
    "aria-label": string;
    "aria-errormessage"?: string;
    disabled: boolean;
    error: boolean;
  };
}

function FacetValueInput({
  kind,
  inputProps,
  value,
  onChange,
  placeholder,
}: PickerProps & { kind: FacetKind }) {
  const { t } = useTranslation(["metadata", "common"]);
  const { disabled } = inputProps;
  const [search, setSearch] = useState("");
  const [debouncedSearch] = useDebouncedValue(search, 200);
  const [dropdownOpened, setDropdownOpened] = useState(false);
  const selectedId = typeof value === "number" ? value : null;
  const facets = useQuery({
    ...listFacetsOptions({
      path: { kind },
      query: { search: debouncedSearch || undefined, limit: 40, offset: 0 },
    }),
    // 快速添加一屏十来个分类选择器, 只在展开下拉时才查询.
    enabled: !disabled && (dropdownOpened || selectedId !== null),
  });
  const options = (facets.data?.items ?? []).map((facet) => ({
    value: String(facet.id),
    label: facet.name,
  }));
  const selectedInOptions = options.some((option) => option.value === String(selectedId));
  const selected = useQuery({
    ...getFacetOptions({ path: { kind, facet_id: selectedId ?? 0 } }),
    enabled: selectedId !== null && !selectedInOptions && !disabled,
  });
  if (selectedId !== null && !selectedInOptions) {
    options.unshift({
      value: String(selectedId),
      label: selected.data?.name ?? t("advancedFilter.selectedId", { id: selectedId }),
    });
  }

  return (
    <>
      <Select
        {...PREDICATE_SELECT_PROPS}
        {...inputProps}
        placeholder={placeholder ?? t("advancedFilter.facetPlaceholder")}
        data={options}
        value={selectedId === null ? null : String(selectedId)}
        searchable
        clearable
        allowDeselect
        searchValue={search}
        onSearchChange={setSearch}
        dropdownOpened={!disabled && dropdownOpened}
        onDropdownOpen={() => setDropdownOpened(true)}
        onDropdownClose={() => setDropdownOpened(false)}
        onChange={(next) => {
          onChange(next === null ? "" : Number(next));
          setSearch("");
        }}
        nothingFoundMessage={
          facets.isFetching ? t("common:status.loading") : t("search.facetEmpty")
        }
      />
      {facets.isError && (
        <PickerLoadFeedback disabled={disabled} retry={() => void facets.refetch()} />
      )}
    </>
  );
}

function LibraryValueInput({ inputProps, value, onChange, libraries, placeholder }: PickerProps) {
  const { t } = useTranslation(["metadata", "common"]);
  const { disabled } = inputProps;
  const query = useQuery({
    ...listLibrariesOptions(),
    enabled: libraries === undefined && !disabled,
  });
  const options = (libraries ?? query.data?.items ?? []).map((library) => ({
    value: String(library.id),
    label: library.name,
  }));
  const selectedId = typeof value === "number" ? value : null;
  if (selectedId !== null && !options.some((option) => option.value === String(selectedId))) {
    options.unshift({
      value: String(selectedId),
      label: t("advancedFilter.selectedId", { id: selectedId }),
    });
  }
  return (
    <>
      <Select
        {...PREDICATE_SELECT_PROPS}
        {...inputProps}
        placeholder={placeholder ?? t("advancedFilter.facetPlaceholder")}
        data={options}
        value={selectedId === null ? null : String(selectedId)}
        searchable
        clearable
        allowDeselect
        onChange={(next) => onChange(next === null ? "" : Number(next))}
        nothingFoundMessage={query.isFetching ? t("common:status.loading") : t("search.facetEmpty")}
      />
      {libraries === undefined && query.isError && (
        <PickerLoadFeedback disabled={disabled} retry={() => void query.refetch()} />
      )}
    </>
  );
}

function PickerLoadFeedback({ disabled, retry }: { disabled: boolean; retry: () => void }) {
  const { t } = useTranslation(["metadata", "common"]);
  return (
    <Stack gap={2} align="flex-start" className={classes.loadFeedback}>
      <Text size="sm" c="var(--mantine-color-error)" role="alert">
        {t("advancedFilter.loadError")}
      </Text>
      <Button type="button" size="compact-sm" variant="subtle" onClick={retry} disabled={disabled}>
        {t("common:actions.retry")}
      </Button>
    </Stack>
  );
}

export function FilterValueInput({
  field,
  value,
  onChange,
  onSubmit,
  disabled,
  errorId,
  ariaLabel,
  placeholder,
  libraries,
}: ValueInputProps) {
  const { t } = useTranslation("metadata");
  const inputProps = {
    "aria-label": ariaLabel ?? t("advancedFilter.value"),
    "aria-errormessage": errorId,
    disabled,
    error: !!errorId,
  };
  const textProps = { ...inputProps, classNames: { input: classes.input } };
  const submitOnEnter = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== "Enter" || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229)
      return;
    event.preventDefault();
    onSubmit();
  };

  switch (field.control) {
    case "facet":
      return (
        <FacetValueInput
          inputProps={inputProps}
          value={value}
          onChange={onChange}
          placeholder={placeholder}
          kind={field.kind}
        />
      );
    case "library":
      return (
        <LibraryValueInput
          inputProps={inputProps}
          value={value}
          onChange={onChange}
          placeholder={placeholder}
          libraries={libraries}
        />
      );
    case "boolean":
      return (
        <Select
          {...PREDICATE_SELECT_PROPS}
          {...inputProps}
          placeholder={placeholder ?? t("advancedFilter.value")}
          data={[
            { value: "true", label: t("search.yes") },
            { value: "false", label: t("search.no") },
          ]}
          value={typeof value === "boolean" ? String(value) : null}
          onChange={(next) => {
            if (next === "true" || next === "false") onChange(next === "true");
          }}
        />
      );
    case "enum":
      return (
        <Select
          {...PREDICATE_SELECT_PROPS}
          {...inputProps}
          placeholder={placeholder ?? t("advancedFilter.value")}
          data={field.options.map((option) => ({
            value: option.value,
            label: option.labelKey ? t(option.labelKey) : option.value,
          }))}
          value={typeof value === "string" && value !== "" ? value : null}
          onChange={(next) => {
            if (next !== null) onChange(next);
          }}
        />
      );
    case "cup":
      return (
        <TextInput
          {...textProps}
          value={typeof value === "string" ? value : ""}
          onChange={(event) => onChange(event.currentTarget.value.toUpperCase())}
          maxLength={2}
          autoCapitalize="characters"
          autoComplete="off"
          placeholder={placeholder ?? t("actors.cupMinPlaceholder")}
          onKeyDown={submitOnEnter}
        />
      );
    case "number":
      return (
        <NumberInput
          {...textProps}
          value={typeof value === "number" || typeof value === "string" ? value : ""}
          onChange={onChange}
          min={field.min}
          max={field.max}
          allowDecimal={false}
          hideControls
          inputMode="numeric"
          placeholder={placeholder ?? t("advancedFilter.valuePlaceholder")}
          onKeyDown={submitOnEnter}
        />
      );
    case "date":
      return (
        <TextInput
          {...textProps}
          value={typeof value === "string" ? value : ""}
          onChange={(event) => onChange(formatDateInput(event.currentTarget.value))}
          inputMode="numeric"
          maxLength={10}
          placeholder={placeholder ?? t("advancedFilter.datePlaceholder")}
          onKeyDown={submitOnEnter}
        />
      );
    case "text":
      return (
        <TextInput
          {...textProps}
          value={typeof value === "string" ? value : ""}
          onChange={(event) => onChange(event.currentTarget.value)}
          placeholder={placeholder ?? t("advancedFilter.valuePlaceholder")}
          onKeyDown={submitOnEnter}
        />
      );
  }
}
