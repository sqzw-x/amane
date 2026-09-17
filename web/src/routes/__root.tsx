import { createRootRoute } from "@tanstack/react-router";
import { useEffect } from "react";
import { AppShellLayout } from "@/components/layout/app-shell";
import { ErrorBoundary } from "@/components/error-boundary";
import { installPullRefreshGate } from "@/lib/pull-refresh";

export const Route = createRootRoute({ component: RootLayout });

function RootLayout() {
  // 在根上装一次: 未登录时渲染的是登录门而不是 AppShell, 那条路径也要参与下拉刷新的优先级判断.
  useEffect(() => installPullRefreshGate(), []);

  return (
    <ErrorBoundary>
      <AppShellLayout />
    </ErrorBoundary>
  );
}
