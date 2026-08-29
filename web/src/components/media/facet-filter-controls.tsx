/**
 * 片库列表的高级 facet 筛选: 展开后每个 kind 独立短搜索框, 选中即追加到 URL search.
 * 另含关联文件与文件相位筛选.
 */

import { Collapse, Select, SimpleGrid, Stack, Text } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { listFacetsOptions } from "@/client/@tanstack/react-query.gen";
import type { ContentType, FacetKind, Mosaic } from "@/client/types.gen";
import { CONTENT_TYPES, FACET_KINDS, FILE_DEFINITIONS, MOSAICS } from "@/lib/exhaustive-maps";
import { facetIdsOf, type FacetFilters } from "@/lib/facets";

const PICKER_LIMIT = 40;

/** URL / 控件用的三态; null = 不限. */
export type HasFilesFilter = boolean | null;

export type FilePhaseFilters = {
  has_subtitle: HasFilesFilter;
  uncensored: HasFilesFilter;
  mosaic: Mosaic | null;
  definition: (typeof FILE_DEFINITIONS)[number] | null;
  content_type: ContentType | null;
};

interface FacetFilterControlsProps {
  /** 是否展开高级筛选表单. */
  opened: boolean;
  /** 当前已激活的过滤参数; 已选同一实体时禁用对应选项. */
  filters: FacetFilters;
  onSelect: (kind: FacetKind, id: number) => void;
  /** 当前关联文件筛选; null = 不限. */
  hasFiles: HasFilesFilter;
  onHasFilesChange: (value: HasFilesFilter) => void;
  filePhase: FilePhaseFilters;
  onFilePhaseChange: (value: FilePhaseFilters) => void;
}

function KindFacetPicker({
  kind,
  filters,
  onSelect,
  enabled,
}: {
  kind: FacetKind;
  filters: FacetFilters;
  onSelect: (kind: FacetKind, id: number) => void;
  enabled: boolean;
}) {
  const { t } = useTranslation("metadata");
  const [search, setSearch] = useState("");

  const { data, isFetching } = useQuery({
    ...listFacetsOptions({
      path: { kind },
      query: { search: search || undefined, limit: PICKER_LIMIT, offset: 0 },
    }),
    enabled,
  });

  const selected = new Set(facetIdsOf(filters, kind));
  const options = (data?.items ?? []).map((facet) => ({
    value: String(facet.id),
    label: `${facet.name} (${facet.count})`,
    disabled: selected.has(facet.id),
  }));

  return (
    <Select
      label={t(`browse.kinds.${kind}`)}
      placeholder={t("search.facetPlaceholder")}
      data={options}
      value={null}
      searchable
      searchValue={search}
      onSearchChange={setSearch}
      nothingFoundMessage={isFetching ? "…" : t("search.facetEmpty")}
      onChange={(v) => {
        if (v == null) return;
        const id = Number(v);
        if (!Number.isInteger(id) || id <= 0) return;
        onSelect(kind, id);
        setSearch("");
      }}
      clearable
      size="sm"
    />
  );
}

function triSelectValue(value: HasFilesFilter): string | null {
  if (value === true) return "true";
  if (value === false) return "false";
  return null;
}

function parseTriSelect(value: string | null): HasFilesFilter {
  if (value === "true") return true;
  if (value === "false") return false;
  return null;
}

function parseMosaic(value: string | null): Mosaic | null {
  for (const mosaic of MOSAICS) {
    if (mosaic === value) return mosaic;
  }
  return null;
}

function parseContentType(value: string | null): ContentType | null {
  for (const contentType of CONTENT_TYPES) {
    if (contentType === value) return contentType;
  }
  return null;
}

function parseDefinition(value: string | null): (typeof FILE_DEFINITIONS)[number] | null {
  for (const definition of FILE_DEFINITIONS) {
    if (definition === value) return definition;
  }
  return null;
}

export function FacetFilterControls({
  opened,
  filters,
  onSelect,
  hasFiles,
  onHasFilesChange,
  filePhase,
  onFilePhaseChange,
}: FacetFilterControlsProps) {
  const { t } = useTranslation("metadata");
  const triData = [
    { value: "true", label: t("search.yes") },
    { value: "false", label: t("search.no") },
  ];

  return (
    <Collapse expanded={opened}>
      <Stack gap="xs">
        <Text size="sm" c="dimmed">
          {t("search.advancedHint")}
        </Text>
        <SimpleGrid cols={{ base: 1, xs: 2, sm: 3, md: 4 }} spacing="sm">
          {FACET_KINDS.map((kind) => (
            <KindFacetPicker
              key={kind}
              kind={kind}
              filters={filters}
              onSelect={onSelect}
              enabled={opened}
            />
          ))}
          <Select
            label={t("search.hasFiles")}
            placeholder={t("search.hasFilesAny")}
            data={[
              { value: "true", label: t("search.hasFilesYes") },
              { value: "false", label: t("search.hasFilesNo") },
            ]}
            value={triSelectValue(hasFiles)}
            onChange={(v) => onHasFilesChange(parseTriSelect(v))}
            clearable
            size="sm"
          />
          <Select
            label={t("search.hasSubtitle")}
            placeholder={t("search.any")}
            data={triData}
            value={triSelectValue(filePhase.has_subtitle)}
            onChange={(v) => onFilePhaseChange({ ...filePhase, has_subtitle: parseTriSelect(v) })}
            clearable
            size="sm"
          />
          <Select
            label={t("search.uncensored")}
            placeholder={t("search.any")}
            data={triData}
            value={triSelectValue(filePhase.uncensored)}
            onChange={(v) => onFilePhaseChange({ ...filePhase, uncensored: parseTriSelect(v) })}
            clearable
            size="sm"
          />
          <Select
            label={t("search.mosaic")}
            placeholder={t("search.any")}
            data={MOSAICS.map((mosaic) => ({
              value: mosaic,
              label: t(`search.mosaics.${mosaic}`),
            }))}
            value={filePhase.mosaic}
            onChange={(v) =>
              onFilePhaseChange({
                ...filePhase,
                mosaic: parseMosaic(v),
              })
            }
            clearable
            size="sm"
          />
          <Select
            label={t("search.definition")}
            placeholder={t("search.any")}
            data={FILE_DEFINITIONS.map((definition) => ({
              value: definition,
              label: definition,
            }))}
            value={filePhase.definition}
            onChange={(v) => onFilePhaseChange({ ...filePhase, definition: parseDefinition(v) })}
            clearable
            size="sm"
          />
          <Select
            label={t("search.contentType")}
            placeholder={t("search.any")}
            data={CONTENT_TYPES.map((contentType) => ({
              value: contentType,
              label: t(`search.contentTypes.${contentType}`),
            }))}
            value={filePhase.content_type}
            onChange={(v) =>
              onFilePhaseChange({
                ...filePhase,
                content_type: parseContentType(v),
              })
            }
            clearable
            size="sm"
          />
        </SimpleGrid>
      </Stack>
    </Collapse>
  );
}
