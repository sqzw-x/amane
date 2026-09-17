import { Badge, Group, Text } from "@mantine/core";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useNarrowViewport } from "@/hooks/use-narrow-viewport";

export interface SelectionBarProps {
  count: number;
  /** 次要说明 (如合并指引); 仅在有选中时显示, 不另占一行. */
  hint?: ReactNode;
  /** 常驻批量动作; 无选中时由调用方 `disabled`. */
  children?: ReactNode;
}

/**
 * 列表多选工具条 - 按钮常驻, 选中数以 Badge 跟在按钮后;
 * 不因选中态增删整行, 避免把下方表格顶下去. 窄屏是例外: 无选中时整条不渲染 — 按钮本就全部禁用, 那一行
 * 只是占位.
 */
export function SelectionBar({ count, hint, children }: SelectionBarProps) {
  const { t } = useTranslation("common");
  const narrowViewport = useNarrowViewport();

  if (narrowViewport && count === 0) {
    return null;
  }

  return (
    <Group gap="xs" align="center" wrap="wrap">
      {children}
      <Badge
        size="sm"
        variant={count > 0 ? "light" : "outline"}
        color={count > 0 ? undefined : "gray"}
        tt="none"
      >
        {t("batch.selected", { count })}
      </Badge>
      {count > 0 && hint != null && hint !== "" && (
        <Text size="xs" c="dimmed">
          {hint}
        </Text>
      )}
    </Group>
  );
}
