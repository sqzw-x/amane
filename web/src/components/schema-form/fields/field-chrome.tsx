import { Group, Input, Stack, Text } from "@mantine/core";
import type * as React from "react";
import type { FieldVariant } from "../schema";
import classes from "./field-chrome.module.css";

/**
 * Layout direction of the label/description relative to the control.
 *
 * - `vertical` (default) - label + description above, control below.
 *   Used by text/numeric/enum/path fields.
 * - `horizontal` - label + description on the left, control on the right.
 *   Used by bool field (Switch is small enough to share a row).
 *   窄屏 (<sm) 纵向排列, 见 `field-chrome.module.css`.
 */
export type FieldChromeLayout = "vertical" | "horizontal";

interface FieldChromeProps {
  /** Visual mode. `bare` returns children unchanged with zero chrome. */
  variant?: FieldVariant;
  /** Label/control layout direction. */
  layout?: FieldChromeLayout;
  /** Maps the label's `htmlFor` to the inner control's `id` (for clickable labels). */
  htmlFor?: string;
  /** Resolved label text (i18n already applied by FieldRouter). */
  label: string;
  /** Optional description shown under the label. */
  description?: string;
  /** The actual input control. */
  children: React.ReactNode;
  /** 校验错误消息 (来自 field.state.meta.errors). 有值时在控件下方红字展示. */
  error?: string;
}

/**
 * Shared chrome wrapper for leaf field components.
 *
 * Centralizes the `variant === "bare"` short-circuit and the default
 * label/description/container styling so each leaf field defines only
 * its control, not the surrounding form-row layout.
 */
export function FieldChrome({
  variant = "default",
  layout = "vertical",
  htmlFor,
  label,
  description,
  children,
  error,
}: FieldChromeProps) {
  if (variant === "bare") {
    return (
      <>
        {children}
        {error && (
          <Text size="xs" c="red" mt={4}>
            {error}
          </Text>
        )}
      </>
    );
  }

  if (layout === "horizontal") {
    return (
      <Group
        className={classes.horizontalRow}
        justify="space-between"
        align="center"
        wrap="nowrap"
        gap="md"
        py="xs"
      >
        <Stack gap={2}>
          <Input.Label htmlFor={htmlFor} size="sm" fw={500}>
            {label}
          </Input.Label>
          {description && (
            <Text size="xs" c="dimmed">
              {description}
            </Text>
          )}
        </Stack>
        <Stack gap={4} align="flex-end" className={classes.controlRow}>
          {children}
          {error && <Input.Error size="xs">{error}</Input.Error>}
        </Stack>
      </Group>
    );
  }

  // vertical (default)
  // Input.Wrapper only injects description→control margin onto descendant
  // Input via context. Select / NumberInput nest their own wrapper (so they
  // never see it), and Button / Checkbox groups are not Input at all — those
  // fields would sit flush against the description.
  return (
    <Stack gap="xs" py="xs">
      <Stack gap={2}>
        <Input.Label htmlFor={htmlFor} size="sm" fw={500}>
          {label}
        </Input.Label>
        {description && (
          <Text size="xs" c="dimmed">
            {description}
          </Text>
        )}
      </Stack>
      {children}
      {error && <Input.Error size="xs">{error}</Input.Error>}
    </Stack>
  );
}
