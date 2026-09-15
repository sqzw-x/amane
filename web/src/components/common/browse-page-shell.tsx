import { Box, Group, Stack } from "@mantine/core";
import type { ReactNode } from "react";
import { APP_SHELL_MAIN_HEIGHT } from "@/components/layout/app-shell-metrics";

export interface BrowsePageShellProps {
  /** 标题区 (可含面包屑). */
  title: ReactNode;
  /**
   * 视图切换 (海报/列表, 词云/列表 等).
   * 与标题同一行居中, 作为全局模式总览.
   */
  viewSwitch?: ReactNode;
  /** 标题行右侧可选动作 (少用). */
  actions?: ReactNode;
  /** 搜索行最左侧: 总计数等摘要 (不参与居中组; 小屏移到搜索上方). */
  summary?: ReactNode;
  /** 搜索框 - 与 extras / pageSize 组成居中控件组. */
  search: ReactNode;
  /** 搜索右侧附属控件 (排序, 筛选). */
  extras?: ReactNode;
  /** 搜索右侧末尾 (分页大小). */
  pageSize?: ReactNode;
  /**
   * 撑满 AppShell.Main 剩余高度, children 吃掉标题/搜索之下的空间.
   * 给 list + ListToolbar; 不要给依赖 window 滚动的 grid/cloud.
   */
  fill?: boolean;
  children: ReactNode;
}

/**
 * 片库 / 分类共用的浏览页壳.
 *
 * 标头层级:
 * 1. 标题 | **居中视图切换** | 右侧动作
 * 2. 左侧摘要 | **(搜索+排序/筛选/每页条数) 整体居中**
 * 内容区不限宽.
 *
 * 搜索宽度为 min(480px, 可用宽), 控件组 max-width:100% + min-width:0, 避免小屏横向滚动.
 * fill 时高度钉在 Main 内容区, 标题/搜索不滚, children 必须自己消化剩余高度.
 */
export function BrowsePageShell({
  title,
  viewSwitch,
  actions,
  summary,
  search,
  extras,
  pageSize,
  fill = false,
  children,
}: BrowsePageShellProps) {
  return (
    <Stack
      gap="md"
      style={{
        minWidth: 0,
        // fill: 高度钉在 Main 内容区; 纵向仍可滚动, 避免 chrome 过高时把分页裁掉.
        ...(fill
          ? { height: APP_SHELL_MAIN_HEIGHT, overflowY: "auto", overflowX: "hidden" }
          : undefined),
      }}
    >
      <Box
        pb="md"
        style={{
          borderBottom: "1px solid var(--mantine-color-default-border)",
          minWidth: 0,
          flexShrink: 0,
        }}
      >
        <Stack gap="md" style={{ minWidth: 0 }}>
          {/* 三列标题行需要 md: sm 断点上导航栏刚展开, 内容宽度反而收窄. */}
          <Box
            visibleFrom="md"
            style={{
              display: "grid",
              gridTemplateColumns: "minmax(0, 1fr) auto minmax(0, 1fr)",
              gap: "var(--mantine-spacing-sm)",
              alignItems: "center",
              minWidth: 0,
            }}
          >
            <Box style={{ minWidth: 0 }}>{title}</Box>
            <Box style={{ minWidth: 0 }}>{viewSwitch}</Box>
            <Group justify="flex-end" gap="sm" wrap="wrap">
              {actions}
            </Group>
          </Box>

          {/* 小屏: 标题全宽, 视图切换/右侧动作换行到下 */}
          <Stack hiddenFrom="md" gap="sm" style={{ minWidth: 0 }}>
            <Box style={{ minWidth: 0 }}>{title}</Box>
            {viewSwitch != null && <Box style={{ minWidth: 0 }}>{viewSwitch}</Box>}
            {actions != null && (
              <Group justify="flex-end" gap="sm" wrap="wrap">
                {actions}
              </Group>
            )}
          </Stack>

          {/* 居中控件组在 lg 以下占满整行, 总数只能单独成行, 否则与搜索框重叠 */}
          {summary != null && <Box hiddenFrom="lg">{summary}</Box>}

          <Box pos="relative" w="100%" style={{ minWidth: 0 }}>
            {summary != null && (
              <Box
                visibleFrom="lg"
                style={{
                  position: "absolute",
                  left: 0,
                  top: "50%",
                  transform: "translateY(-50%)",
                  zIndex: 1,
                }}
              >
                {summary}
              </Box>
            )}
            <Group
              justify="center"
              align="center"
              gap="xs"
              wrap="wrap"
              w="100%"
              style={{ minWidth: 0 }}
            >
              <Box
                style={{
                  flex: "1 1 12rem",
                  maxWidth: 480,
                  minWidth: 0,
                  width: "100%",
                }}
              >
                {search}
              </Box>
              {extras}
              {pageSize}
            </Group>
          </Box>
        </Stack>
      </Box>

      <Stack gap="md" style={{ minWidth: 0, ...(fill ? { flex: 1, minHeight: 0 } : undefined) }}>
        {children}
      </Stack>
    </Stack>
  );
}
