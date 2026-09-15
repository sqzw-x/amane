import { useMediaQuery } from "@mantine/hooks";

/** Mantine `sm` 断点以下的媒体查询; 判定条件与 `hiddenFrom="sm"` 一致. */
export const NARROW_VIEWPORT_QUERY = "(max-width: 47.99em)";

/** Mantine `md` 断点以下的媒体查询; 判定条件与 `hiddenFrom="md"` 一致. */
export const BELOW_MD_VIEWPORT_QUERY = "(max-width: 61.99em)";

/**
 * 窄屏判定.
 * 仅在控件形态必须随宽度改变时使用 (枚举选择器改下拉, 侧栏改抽屉); 单纯显隐交给 `visibleFrom` / `hiddenFrom`.
 * `breakpoint` 取需要与之互斥的 `hiddenFrom` 断点. 首帧返回 `false`, 与桌面形态一致, 避免窄屏下先渲染桌面控件再重排.
 */
export function useNarrowViewport(breakpoint: "sm" | "md" = "sm"): boolean {
  return useMediaQuery(
    breakpoint === "sm" ? NARROW_VIEWPORT_QUERY : BELOW_MD_VIEWPORT_QUERY,
    false,
  );
}
