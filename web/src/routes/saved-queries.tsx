import { createFileRoute, Outlet } from "@tanstack/react-router";

/** 列表页与数据页共用外层; 两个页面各自维护布局. */
function SavedQueriesLayout() {
  return <Outlet />;
}

export const Route = createFileRoute("/saved-queries")({ component: SavedQueriesLayout });
