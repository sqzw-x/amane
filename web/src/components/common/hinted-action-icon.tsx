import {
  ActionIcon,
  Tooltip,
  type ActionIconProps,
  type PolymorphicComponentProps,
} from "@mantine/core";
import { forwardRef, type ReactNode } from "react";

export type HintedActionIconProps = Omit<
  PolymorphicComponentProps<"button", ActionIconProps>,
  "title"
> & {
  /** 悬浮说明; 未另传 aria-label 时复用为无障碍名称. */
  label: string;
  children: ReactNode;
};

/**
 * 仅图标按钮的悬浮说明.
 * 不允许用 HTML `title`: 浏览器原生提示延迟出现、贴指针、深色小字, 与 Mantine Tooltip 不一致.
 */
export const HintedActionIcon = forwardRef<HTMLButtonElement, HintedActionIconProps>(
  function HintedActionIcon({ label, children, "aria-label": ariaLabel, ...props }, ref) {
    return (
      <Tooltip label={label}>
        <ActionIcon {...props} ref={ref} aria-label={ariaLabel ?? label}>
          {children}
        </ActionIcon>
      </Tooltip>
    );
  },
);
