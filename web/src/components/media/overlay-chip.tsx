import { Group, Text, Tooltip } from "@mantine/core";
import type { ReactNode } from "react";

/**
 * 片库评分/日期与相位水印共用.
 *
 * 不加 `backdrop-filter`: 每个 chip 都要单独成层并回读背景, 滚动时逐层合成; 实测栅格滚动
 * 6 秒里有 1.3 秒耗在 Layerize, 去掉后减半, 半透明底本身已够读.
 */
export const OVERLAY_CHIP_STYLE = {
  zIndex: 1,
  borderRadius: "var(--mantine-radius-xl)",
  background: "rgba(0, 0, 0, 0.72)",
} as const;

interface OverlayChipProps {
  children: ReactNode;
  title?: string;
  "aria-label"?: string;
}

export function OverlayChip({ children, title, "aria-label": ariaLabel }: OverlayChipProps) {
  const chip = (
    <Group gap={4} wrap="nowrap" px={7} py={3} style={OVERLAY_CHIP_STYLE} aria-label={ariaLabel}>
      {children}
    </Group>
  );
  if (title == null) return chip;
  return <Tooltip label={title}>{chip}</Tooltip>;
}

interface OverlayChipLabelProps {
  children: ReactNode;
  ff?: string;
  lts?: number;
}

export function OverlayChipLabel({ children, ff, lts }: OverlayChipLabelProps) {
  return (
    <Text size="xs" c="white" fw={700} lh={1} ff={ff} lts={lts}>
      {children}
    </Text>
  );
}
