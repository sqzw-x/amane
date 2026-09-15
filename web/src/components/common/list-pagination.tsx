import { Pagination } from "@mantine/core";

export interface ListPaginationProps {
  totalPages: number;
  page: number;
  onChange: (page: number) => void;
}

/**
 * 列表底部分页; 单页时自动隐藏. 锚定视口底由 ListToolbar / 阅读器布局负责.
 * `layout="responsive"` 用容器查询测量自身宽度, 而查询容器作为 flex 行内的项会被收缩为零宽,
 * 故根元素撑满可用宽度并自行居中: 容器不足 400px 时只保留「当前页 / 总页数」, 页码按钮不折行.
 */
export function ListPagination({ totalPages, page, onChange }: ListPaginationProps) {
  if (totalPages <= 1) return null;
  return (
    <Pagination
      total={totalPages}
      value={page}
      onChange={onChange}
      layout="responsive"
      w="100%"
      styles={{ root: { display: "flex", justifyContent: "center" } }}
    />
  );
}
