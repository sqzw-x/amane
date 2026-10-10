/**
 * 三态分段控件: 不限 / 是 / 否, 取值是 URL search 上 `"true"` / `"false"` 的字符串三态.
 *
 * 不用布尔: 默认查询串解析器不解析 `true` / `false`, 布尔 schema 会被 `catch` 吞掉. 缺省值
 * 表示不限, 与两个显式取值区分, 因此控件不能只靠二态开关表达.
 */

import { Input, SegmentedControl } from "@mantine/core";

/** URL 上的三态取值; 缺省为不限. */
export type TriState = "true" | "false";

export interface TriStateSegmentProps {
  label: string;
  /** 缺省为不限. */
  value: TriState | undefined;
  anyLabel: string;
  yesLabel: string;
  noLabel: string;
  onChange: (next: TriState | undefined) => void;
}

export function TriStateSegment({
  label,
  value,
  anyLabel,
  yesLabel,
  noLabel,
  onChange,
}: TriStateSegmentProps) {
  return (
    <Input.Wrapper label={label} size="sm">
      <SegmentedControl
        size="sm"
        value={value ?? "any"}
        onChange={(v) => {
          if (v === "true" || v === "false") onChange(v);
          else onChange(undefined);
        }}
        data={[
          { value: "any", label: anyLabel },
          { value: "true", label: yesLabel },
          { value: "false", label: noLabel },
        ]}
      />
    </Input.Wrapper>
  );
}
