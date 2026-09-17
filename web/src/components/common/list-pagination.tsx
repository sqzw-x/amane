import { Pagination } from "@mantine/core";
import { useElementSize } from "@mantine/hooks";
import { useTranslation } from "react-i18next";

export interface ListPaginationProps {
  totalPages: number;
  page: number;
  onChange: (page: number) => void;
}

/** 收合阈值; 与 Mantine 响应式分页的收合同取 400px. */
const COMPACT_MAX_WIDTH = 400;

/**
 * 列表底部分页; 单页时自动隐藏. 锚定视口底由 ListToolbar / 阅读器布局负责.
 * 根元素撑满可用宽度并自行居中 (flex 行内的项会被收缩为零宽), 宽度不足收合阈值时只保留「当前页 / 总页数」.
 *
 * 收合判定按实测宽度写成内联样式, 不用 `layout="responsive"` 的容器查询 (理由见 docs/dev/frontend.md).
 */
export function ListPagination({ totalPages, page, onChange }: ListPaginationProps) {
  const { t } = useTranslation("common");
  const { ref, width } = useElementSize<HTMLDivElement>();
  if (totalPages <= 1) return null;
  const compact = width > 0 && width <= COMPACT_MAX_WIDTH;

  return (
    <Pagination
      ref={ref}
      total={totalPages}
      value={page}
      onChange={onChange}
      layout="responsive"
      formatLabel={({ page: active, totalPages: total }) =>
        t("pagination.pageOf", { page: active, total })
      }
      w="100%"
      styles={{
        root: { display: "flex", justifyContent: "center" },
        items: compact ? { display: "none" } : undefined,
        label: compact ? { display: "flex" } : undefined,
      }}
    />
  );
}
