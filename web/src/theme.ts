import { createTheme, Drawer, type MantineColorsTuple } from "@mantine/core";

const brand: MantineColorsTuple = [
  "#e8f3ff",
  "#d0e4ff",
  "#a1c6ff",
  "#6ea6ff",
  "#458bfe",
  "#2b7afe",
  "#1a71ff",
  "#0060e4",
  "#0055cc",
  "#0049b4",
];

export const theme = createTheme({
  primaryColor: "brand",
  colors: { brand },
  fontFamily: '"IBM Plex Sans", "Noto Sans SC", "Segoe UI", system-ui, -apple-system, sans-serif',
  fontFamilyMonospace: '"IBM Plex Mono", "JetBrains Mono", ui-monospace, monospace',
  defaultRadius: "md",
  headings: {
    fontFamily: '"IBM Plex Sans", "Noto Sans SC", system-ui, sans-serif',
    fontWeight: "600",
  },
  components: {
    Drawer: Drawer.extend({
      styles: {
        // 抽屉的定位容器贴住视觉视口: 上沿取 --amane-vv-top, 高度取 --amane-vvh (理由见 docs/dev/frontend.md).
        inner: { top: "var(--amane-vv-top)", bottom: "auto", height: "var(--amane-vvh)" },
      },
    }),
  },
});
