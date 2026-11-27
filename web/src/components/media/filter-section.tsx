import { Text } from "@mantine/core";
import type { ReactNode } from "react";
import classes from "./filter-section.module.css";

export interface FilterSectionProps {
  label: string;
  children: ReactNode;
}

/**
 * 筛选面板内的一组分标题字段.
 *
 * 面板里的控件按含义成组, 否则一屏十几项连成一片, 找不到入口.
 */
export function FilterSection({ label, children }: FilterSectionProps) {
  return (
    <section className={classes.section} aria-label={label}>
      <Text component="h3" className={classes.title}>
        {label}
      </Text>
      <div className={classes.fields}>{children}</div>
    </section>
  );
}
