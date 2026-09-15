import { Box, Group, Stack } from "@mantine/core";
import { type ReactNode, useEffect, useRef } from "react";
import { ListPagination, type ListPaginationProps } from "./list-pagination";

export interface ListToolbarProps extends ListPaginationProps {
  /** 顶栏左侧 (多选条 / 创建行); 不随表体滚动. */
  header?: ReactNode;
  /** 顶栏右侧动作 (如规则入口); 不随表体滚动. */
  trailing?: ReactNode;
  children: ReactNode;
}

const FILL: { flex: 1; minHeight: number; minWidth: number } = {
  flex: 1,
  minHeight: 0,
  minWidth: 0,
};

/** 表体下界: 顶栏与分页都不可压缩, 缺下界时窄屏会把表体压成 0 高且无从滚动. */
const SCROLL_MIN_HEIGHT = 128;

/**
 * 列表体: 顶栏 chrome + 内部滚动的 children + 视口底部锚定分页.
 * 父级必须是有界高度的 flex 列 (`BrowsePageShell fill`, 或同等的 `APP_SHELL_MAIN_HEIGHT`).
 * 翻页时把表体滚回顶部.
 */
export function ListToolbar({
  totalPages,
  page,
  onChange,
  header,
  trailing,
  children,
}: ListToolbarProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (page < 1) return;
    scrollRef.current?.scrollTo(0, 0);
  }, [page]);

  const hasChrome = header != null || trailing != null;

  return (
    <Stack gap="sm" style={FILL}>
      {hasChrome &&
        (header != null && trailing != null ? (
          <Group
            justify="space-between"
            align="flex-start"
            wrap="wrap"
            gap="sm"
            style={{ flexShrink: 0 }}
          >
            <Box style={{ flex: 1, minWidth: 0 }}>{header}</Box>
            {trailing}
          </Group>
        ) : trailing != null ? (
          <Group justify="flex-end" style={{ flexShrink: 0 }}>
            {trailing}
          </Group>
        ) : (
          <Box style={{ flexShrink: 0 }}>{header}</Box>
        ))}

      <Box ref={scrollRef} style={{ ...FILL, minHeight: SCROLL_MIN_HEIGHT, overflow: "auto" }}>
        {children}
      </Box>

      {totalPages > 1 && (
        <Group justify="center" style={{ flexShrink: 0 }}>
          <ListPagination totalPages={totalPages} page={page} onChange={onChange} />
        </Group>
      )}
    </Stack>
  );
}
