import { Pagination } from "@mantine/core";

export interface ListPaginationProps {
  totalPages: number;
  page: number;
  onChange: (page: number) => void;
}

/**
 * 列表底部分页; 单页时自动隐藏. 锚定视口底由 ListToolbar / 阅读器布局负责.
 * `layout="responsive"`: 容器过窄时 Mantine 只保留「当前页 / 总页数」, 页码按钮不折行.
 */
export function ListPagination({ totalPages, page, onChange }: ListPaginationProps) {
  if (totalPages <= 1) return null;
  return <Pagination total={totalPages} value={page} onChange={onChange} layout="responsive" />;
}
