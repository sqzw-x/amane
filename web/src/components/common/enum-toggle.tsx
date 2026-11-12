import { Button, SegmentedControl, Select } from "@mantine/core";
import { useWindowEvent } from "@mantine/hooks";
import { useLayoutEffect, useRef, useState } from "react";
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

/**
 * Segmented enum: sliding indicator, item separators; `fullWidth` fills the row.
 * 标签在容器内放不下时改用 `Select` —— SegmentedControl 自带 `overflow: hidden`,
 * 被裁掉的段点不到. 多选分支改用按钮组, 窄屏允许换行.
 */
export function EnumToggle<T extends string | number>(
  props: EnumToggleSingle<T> | EnumToggleMultiple<T>,
) {
  const { options, getLabel, multiple, disabled, fullWidth = false } = props;
  const narrow = useNarrowViewport();

  const data = options.map((opt, i) => ({ value: String(opt), label: getLabel(opt, i) }));
  const labelsKey = data.map((item) => item.label).join("\u0000");

  const controlRef = useRef<HTMLDivElement>(null);
  const [clipped, setClipped] = useState(false);

  // 窗口变宽时先回到 Segmented, 由下面的 effect 在绘制前重测; 否则一次变窄就永远停在下拉.
  useWindowEvent("resize", () => setClipped(false));

  // 裁切下 `scrollWidth` 仍是内容宽度, 与可视宽度比较即可知道放不放得下.
  // 用 ResizeObserver 而非只测一次: 弹窗动画期间宽度为 0, 容器宽度也常在挂载之后才确定.
  useLayoutEffect(() => {
    const el = controlRef.current;
    if (el === null) return;
    const measure = () => {
      if (!el.isConnected || el.clientWidth === 0) return;
      setClipped(el.scrollWidth > el.clientWidth + 1);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [labelsKey, clipped]);

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

  if (clipped) {
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
        data={data}
      />
    );
  }

  return (
    <SegmentedControl
      ref={controlRef}
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
      data={data}
    />
  );
}
