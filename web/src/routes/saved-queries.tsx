import { createFileRoute, Outlet } from "@tanstack/react-router";

function SavedQueriesLayout() {
  return <Outlet />;
}

export const Route = createFileRoute("/saved-queries")({ component: SavedQueriesLayout });
