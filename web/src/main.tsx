import "@/i18n";
import "@mantine/core/styles.css";
import "@mantine/notifications/styles.css";

import {
  ColorSchemeScript,
  Center,
  Loader,
  MantineProvider,
  v8CssVariablesResolver,
} from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { QueryClientProvider } from "@tanstack/react-query";
import { createRouter, RouterProvider } from "@tanstack/react-router";
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";

import { client } from "@/client/client.gen";
import { LoginGate } from "@/components/auth/login-gate";
import { ConfirmHost } from "@/lib/confirm";
import { apiFetch, AUTH_EXPIRED_EVENT } from "@/lib/api-token";
import { initConnection } from "@/lib/connection";
import { queryClient } from "@/lib/query-client";
import { useUIStore } from "@/stores/ui";
import { routeTree } from "./routeTree.gen";
import { theme } from "./theme";

client.setConfig({
  baseUrl: import.meta.env.VITE_API_URL || "",
  fetch: apiFetch,
});
initConnection(queryClient);

// 路由组件按路由拆分, 悬停/聚焦时预取对应 chunk, 点击后无需等待下载.
const router = createRouter({
  routeTree,
  scrollRestoration: true,
  defaultPreload: "intent",
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("Root element not found");

/** 认证经 HttpOnly cookie: 挂载时探活 /api/system/desktop 判断 cookie 是否有效,
 * 之后任何请求 401 (cookie 过期/被重置) 由 apiFetch 发事件切换回登录门. */
function Root() {
  const colorScheme = useUIStore((s) => s.theme);
  const [authed, setAuthed] = useState<boolean | null>(null);

  useEffect(() => {
    const onExpired = () => setAuthed(false);
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired);
  }, []);

  // 挂载探活: cookie 有效则直接进入应用, 无 cookie 则等登录门引导.
  useEffect(() => {
    let alive = true;
    void apiFetch(`${import.meta.env.VITE_API_URL || ""}/api/system/desktop`)
      .then((resp) => {
        if (alive) setAuthed(resp.ok);
      })
      .catch(() => {
        if (alive) setAuthed(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  return (
    <MantineProvider
      theme={theme}
      cssVariablesResolver={v8CssVariablesResolver}
      forceColorScheme={colorScheme === "auto" ? undefined : colorScheme}
      defaultColorScheme={colorScheme}
    >
      <Notifications position="top-right" />
      <ConfirmHost />
      {authed === null ? (
        <Center h="100dvh">
          <Loader size="sm" />
        </Center>
      ) : authed ? (
        <QueryClientProvider client={queryClient}>
          <RouterProvider router={router} />
        </QueryClientProvider>
      ) : (
        <LoginGate onAuthed={() => setAuthed(true)} />
      )}
    </MantineProvider>
  );
}

createRoot(rootEl).render(
  <StrictMode>
    <ColorSchemeScript defaultColorScheme="dark" />
    <Root />
  </StrictMode>,
);
