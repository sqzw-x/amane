import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Collapse,
  Group,
  Paper,
  Stack,
  Text,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { IconFilter, IconX } from "@tabler/icons-react";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { HintedActionIcon } from "@/components/common/hinted-action-icon";
import { useNarrowViewport } from "@/hooks/use-narrow-viewport";
import { andLegacyFilters, type BrowseFilter } from "@/lib/browse-filter";
import {
  FilterExpressionError,
  filterRuleCount,
  serializeFilterFormula,
  compileFilterFormula,
  type FilterFormula,
} from "@/lib/filter-expression";
import { AdvancedFilterEditor } from "./advanced-filter-editor/advanced-filter-editor";
import type { FilterLibrary } from "./advanced-filter-editor/value-input";
import classes from "./browse-filter-panel.module.css";

export function useBrowseFilterPanel(defaultOpened: boolean) {
  const [opened, { toggle }] = useDisclosure(defaultOpened);
  const narrow = useNarrowViewport("md");
  const [applyError, setApplyError] = useState<FilterExpressionError | null>(null);
  return { id: useId(), opened: opened || narrow, toggle, narrow, applyError, setApplyError };
}

type FilterPanelState = ReturnType<typeof useBrowseFilterPanel>;

export function BrowseFilterToggle({
  panel,
  active,
}: {
  panel: FilterPanelState;
  active: boolean;
}) {
  const { t } = useTranslation("metadata");
  if (panel.narrow) return null;
  return (
    <HintedActionIcon
      variant={panel.opened || active ? "filled" : "default"}
      size={36}
      aria-controls={panel.id}
      aria-expanded={panel.opened}
      onClick={panel.toggle}
      label={t("search.advanced")}
    >
      <IconFilter size={16} />
    </HintedActionIcon>
  );
}

export function BrowseFilterPanel({
  panel: { id, opened, applyError, setApplyError },
  filter,
  libraries,
  onApply,
}: {
  panel: FilterPanelState;
  filter: BrowseFilter;
  libraries?: readonly FilterLibrary[];
  onApply: (value: string | undefined) => void;
}) {
  const { t } = useTranslation("metadata");

  function apply(next: FilterFormula | null) {
    if (filter.presetId != null) return;
    let value: string | undefined;
    try {
      const formula = andLegacyFilters(next, filter.outerFilters);
      compileFilterFormula(formula, filter.entity);
      value = serializeFilterFormula(formula, filter.entity);
    } catch (error) {
      if (!(error instanceof FilterExpressionError)) throw error;
      setApplyError(error);
      return;
    }
    setApplyError(null);
    onApply(value);
  }

  return (
    <Collapse id={id} expanded={opened} w="100%" maw="64rem" mx="auto">
      <Paper withBorder radius="lg" p="md" className={classes.panel}>
        <Stack gap="md">
          {applyError && (
            <Alert color="red" role="alert">
              {t(applyError.messageKey)}
            </Alert>
          )}
          {!filter.conflict && (
            <AdvancedFilterEditor
              entity={filter.entity}
              value={filter.editorValue}
              onApply={apply}
              onCancel={() => setApplyError(null)}
              opened={opened}
              disabled={filter.presetId != null}
              libraries={libraries}
            />
          )}
        </Stack>
      </Paper>
    </Collapse>
  );
}

export function BrowseFilterAlerts({
  filter,
  failed,
  onRetry,
  onClear,
}: {
  filter: BrowseFilter;
  failed: boolean;
  onRetry: () => void;
  onClear: () => void;
}) {
  const { t } = useTranslation(["metadata", "common"]);
  const linkError = filter.conflict
    ? t("advancedFilter.savedQueryConflict")
    : filter.error && t("advancedFilter.invalidLink", { reason: t(filter.error.messageKey) });
  return (
    <>
      {linkError != null ? (
        <Alert color="red" role="alert" variant="light">
          <Group gap="sm" wrap="wrap">
            <Text size="sm">{linkError}</Text>
            <Button size="compact-xs" variant="light" color="red" onClick={onClear}>
              {t("advancedFilter.clearExpression")}
            </Button>
          </Group>
        </Alert>
      ) : (
        failed && (
          <Alert color="red" role="alert" variant="light">
            <Group justify="space-between" gap="xs">
              <Text>{t("advancedFilter.requestFailed")}</Text>
              <Button size="xs" variant="light" color="red" onClick={onRetry}>
                {t("common:actions.retry")}
              </Button>
            </Group>
          </Alert>
        )
      )}
      {filter.presetId != null && (
        <Alert color="yellow" variant="light">
          {t("search.legacyPresetNotice")}
        </Alert>
      )}
    </>
  );
}

export function ActiveFilterChip({ label, onClear }: { label: string; onClear: () => void }) {
  const { t } = useTranslation("common");
  return (
    <Group gap={4} wrap="nowrap">
      <Badge variant="outline">{label}</Badge>
      <ActionIcon
        size="sm"
        variant="subtle"
        color="gray"
        onClick={onClear}
        aria-label={`${t("actions.remove")} ${label}`}
      >
        <IconX size={14} />
      </ActionIcon>
    </Group>
  );
}

export function ActiveExpressionChip({
  filter,
  onClear,
}: {
  filter: BrowseFilter;
  onClear: () => void;
}) {
  const { t } = useTranslation("metadata");
  if (!filter.active) return null;
  const count = filter.editorValue ? filterRuleCount(filter.editorValue) : 0;
  return (
    <ActiveFilterChip
      label={`${t("advancedFilter.title")}: ${t("advancedFilter.ruleCount", { count })}`}
      onClear={onClear}
    />
  );
}
