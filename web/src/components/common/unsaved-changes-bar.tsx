import { Affix, Box, Button, Group, Paper, Text, Transition } from "@mantine/core";
import { useTranslation } from "react-i18next";
import classes from "./unsaved-changes-bar.module.css";

export type UnsavedChangesBarPlacement = "affix" | "sticky";

interface UnsavedChangesBarProps {
  dirty: boolean;
  saving: boolean;
  /** Disable save (e.g. form invalid). */
  saveDisabled?: boolean;
  /** Associate portaled / sticky submit with a `<form id>`. */
  formId?: string;
  onDiscard: () => void;
  submitLabel?: string;
  /**
   * - `affix`: viewport-bottom bar (settings + edit modals). z-index 300 to sit above Modal (200).
   * - `sticky`: sticks to the nearest scrollport (embedded forms).
   */
  placement?: UnsavedChangesBarPlacement;
}

/**
 * Dirty-gated discard/save bar. Same chrome as the settings page:
 * centered paper, slide-up, only visible when `dirty`.
 */
export function UnsavedChangesBar({
  dirty,
  saving,
  saveDisabled = false,
  formId,
  onDiscard,
  submitLabel,
  placement = "sticky",
}: UnsavedChangesBarProps) {
  const { t } = useTranslation("common");

  const bar = (
    <Paper withBorder shadow="md" px="md" py="sm" radius="md">
      <Group gap="md" wrap="nowrap" justify="space-between">
        <Text size="sm" className={classes.label}>
          {t("status.unsavedChanges")}
        </Text>
        <Group gap="sm" wrap="nowrap" className={classes.actions}>
          <Button type="button" variant="default" onClick={onDiscard} disabled={saving}>
            {t("actions.discard")}
          </Button>
          <Button type="submit" form={formId} disabled={saving || saveDisabled} loading={saving}>
            {saving ? t("actions.saving") : (submitLabel ?? t("actions.save"))}
          </Button>
        </Group>
      </Group>
    </Paper>
  );

  const overlay = (
    <Transition mounted={dirty} transition="slide-up" duration={180}>
      {(styles) => (
        <Box
          style={{
            ...styles,
            display: "flex",
            justifyContent: "center",
            pointerEvents: "none",
            paddingInline: 16,
          }}
        >
          <Box style={{ pointerEvents: "auto", maxWidth: 560, width: "100%" }}>{bar}</Box>
        </Box>
      )}
    </Transition>
  );

  if (placement === "affix") {
    return (
      <Affix
        position={{ bottom: 24, left: 0, right: 0 }}
        withinPortal
        zIndex={300}
        style={{ pointerEvents: dirty ? "auto" : "none" }}
      >
        {overlay}
      </Affix>
    );
  }

  return (
    <Box
      pos="sticky"
      bottom={16}
      mt={dirty ? "md" : 0}
      style={{ zIndex: 5, pointerEvents: dirty ? "auto" : "none" }}
    >
      {overlay}
    </Box>
  );
}
