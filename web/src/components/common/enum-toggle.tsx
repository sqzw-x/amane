import { Button, SegmentedControl, Select } from "@mantine/core";
import { useNarrowViewport } from "@/hooks/use-narrow-viewport";
import classes from "./enum-toggle.module.css";

type EnumToggleBase<T extends string | number> = {
  options: readonly T[];
  getLabel: (opt: T, index: number) => string;
  disabled?: boolean;
  /** Stretch to the parent row; default hugs label width. */
  fullWidth?: boolean;
};

type EnumToggleSingle<T extends string | number> = EnumToggleBase<T> & {
  multiple?: false;
  value: T | null | undefined;
  onChange: (next: T) => void;
};

type EnumToggleMultiple<T extends string | number> = EnumToggleBase<T> & {
  multiple: true;
  value: readonly T[] | null | undefined;
  onChange: (next: T[]) => void;
};

/** 窄屏回退为下拉菜单的选项数阈值: 选项数大于该值时, SegmentedControl 的段宽不足以容纳标签. */
const NARROW_SELECT_MIN_OPTIONS = 4;

/**
 * Segmented enum: sliding indicator, item separators; `fullWidth` fills the row.
 * 窄屏下 (宽度小于 sm) 且选项数大于 `NARROW_SELECT_MIN_OPTIONS` 时改用 `Select`,
 * 多选分支改为允许换行的按钮组.
 */
export function EnumToggle<T extends string | number>(
  props: EnumToggleSingle<T> | EnumToggleMultiple<T>,
) {
  const { options, getLabel, multiple, disabled, fullWidth = false } = props;
  const narrow = useNarrowViewport();

  if (multiple) {
    return (
      <Button.Group
        className={classes.group}
        // `maxWidth` 限制分组宽度, 使窄屏换行发生在可视区域内.
        w={fullWidth ? "100%" : "max-content"}
        maw="100%"
        style={{ flexWrap: narrow ? "wrap" : undefined }}
      >
        {options.map((opt, i) => {
          const selected = (props.value ?? []).includes(opt);
          return (
            <Button
              key={String(opt)}
              type="button"
              size="compact-sm"
              variant={selected ? "filled" : "default"}
              disabled={disabled}
              // 窄屏按钮按内容宽度排列; 逐项 flex:1 会把同排按钮拉成等宽并重新压出省略号.
              style={fullWidth && !narrow ? { flex: 1 } : undefined}
              onClick={() => {
                const arr = [...(props.value ?? [])];
                const idx = arr.indexOf(opt);
                if (idx >= 0) arr.splice(idx, 1);
                else arr.push(opt);
                props.onChange(arr);
              }}
            >
              {getLabel(opt, i)}
            </Button>
          );
        })}
      </Button.Group>
    );
  }

  if (narrow && options.length > NARROW_SELECT_MIN_OPTIONS) {
    return (
      <Select
        size="sm"
        disabled={disabled}
        w={fullWidth ? undefined : "max-content"}
        maw="100%"
        allowDeselect={false}
        comboboxProps={{ withinPortal: true }}
        value={props.value == null ? null : String(props.value)}
        onChange={(v) => {
          const next = options.find((opt) => String(opt) === v);
          if (next === undefined || props.value === next) return;
          props.onChange(next);
        }}
        data={options.map((opt, i) => ({
          value: String(opt),
          label: getLabel(opt, i),
        }))}
      />
    );
  }

  return (
    <SegmentedControl
      size="sm"
      radius="sm"
      withItemsBorders
      fullWidth={fullWidth}
      disabled={disabled}
      w={fullWidth ? undefined : "max-content"}
      maw="100%"
      styles={{
        control: fullWidth ? undefined : { flex: "0 0 auto" },
      }}
      value={props.value == null ? "" : String(props.value)}
      onChange={(v) => {
        const next = options.find((opt) => String(opt) === v);
        if (next === undefined || props.value === next) return;
        props.onChange(next);
      }}
      data={options.map((opt, i) => ({
        value: String(opt),
        label: getLabel(opt, i),
      }))}
    />
  );
}
