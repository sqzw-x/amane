import type { ComboboxProps, SelectProps } from "@mantine/core";
import classes from "./predicate-editor.module.css";

// 下拉脱离抽屉的滚动容器, 并随视觉视口调整方向与高度.
const predicateCombobox = {
  withinPortal: true,
  floatingStrategy: "fixed",
  position: "bottom-start",
  offset: 4,
  preventPositionChangeWhenVisible: false,
  middlewares: {
    // 只翻转上下; 左右对齐固定为与输入框左边缘对齐, 宽度不足由下面的 size 收窄.
    flip: { padding: 8, fallbackStrategy: "bestFit", flipAlignment: false },
    shift: { padding: 8, crossAxis: false },
    size: {
      padding: 8,
      apply({ availableHeight, elements }) {
        elements.floating.style.setProperty(
          "--predicate-dropdown-height",
          `${Math.max(0, availableHeight)}px`,
        );
      },
    },
  },
  transitionProps: { duration: 0 },
} satisfies ComboboxProps;

export const PREDICATE_SELECT_PROPS = {
  comboboxProps: predicateCombobox,
  withScrollArea: false,
  allowDeselect: false,
  classNames: { input: classes.input, dropdown: classes.dropdown },
} satisfies SelectProps;
