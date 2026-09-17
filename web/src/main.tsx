import "@/i18n";
import "@mantine/core/styles.css";
import "@mantine/notifications/styles.css";
import "@/global.css";
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
import { StrictMode, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";

import { client } from "@/client/client.gen";
import { LoginGate } from "@/components/auth/login-gate";
import { ConfirmHost } from "@/lib/confirm";
import { apiFetch, AUTH_EXPIRED_EVENT } from "@/lib/api-token";
import { initConnection } from "@/lib/connection";
import { queryClient } from "@/lib/query-client";
import { shellEnvironment, shellSwitchServer } from "@/lib/shell";
import { useUIStore } from "@/stores/ui";
import { routeTree } from "./routeTree.gen";
import { theme } from "./theme";

// 支持 dvh 时替换 --amane-vh 的取值 (变量与默认值见 global.css).
// 判定用实测布局而不是 `CSS.supports`: 厂商自带的 WebView 可能在特性查询里声称支持 dvh 却算不出高度,
// 换过去会让依赖它的声明失效.
const viewportProbe = document.createElement("div");
viewportProbe.style.cssText = "position:absolute;height:100dvh;width:0;visibility:hidden";
document.body.appendChild(viewportProbe);
if (viewportProbe.offsetHeight > 0) {
  document.documentElement.style.setProperty("--amane-vh", "100dvh");
}
viewportProbe.remove();

// 壳内标记根元素: global.css 据此抑制内核的算法深色叠加. 桌面浏览器不加标记, 该规则不生效.
if (shellEnvironment() !== null) {
  document.documentElement.dataset.amaneShell = "";
}

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
 * 之后任何请求 401 (cookie 过期/被重置) 由 apiFetch 发事件切换回登录门.
 *
 * 壳内不显示登录门: 未认证时跳回壳的服务器页一次, 用户从那里返回后仍留在登录门
 * (理由见 docs/dev/android.md). */
function Root() {
  const colorScheme = useUIStore((s) => s.theme);
  const [authed, setAuthed] = useState<boolean | null>(null);
  const shell = shellEnvironment() !== null;
  const redirectedToSetup = useRef(false);

  useEffect(() => {
    const onExpired = () => setAuthed(false);
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired);
  }, []);

  // 每次页面加载只跳转一次, 用户从服务器页返回后不再跳转.
  useEffect(() => {
    if (authed !== false || !shell || redirectedToSetup.current) return;
    redirectedToSetup.current = true;
    shellSwitchServer();
  }, [authed, shell]);

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
        <Center h="var(--amane-vh)">
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
