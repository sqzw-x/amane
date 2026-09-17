import { Alert, Anchor, Button, Code, Group, Stack, Text } from "@mantine/core";
import { IconAlertTriangle, IconLogout, IconServer } from "@tabler/icons-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  MIN_WEBVIEW_MAJOR,
  shellEnvironment,
  shellSignOut,
  shellSwitchServer,
  WEBVIEW_STORE_URL,
} from "@/lib/shell";

/**
 * 客户端设置: 服务器、登录状态与壳的运行期版本.
 *
 * 只在壳内渲染 (导航项与 `/client` 路由同样按 UA 标记判断). 这些是客户端自身的状态, 与服务端配置无关,
 * 因此不进入 `SchemaForm`, 也不放在设置页的分组里.
 */
export function ClientSettings() {
  const { t } = useTranslation("settings");
  const [bridgeFailure, setBridgeFailure] = useState(false);
  const shell = shellEnvironment();
  if (!shell) return null;

  const run = (action: () => boolean) => {
    if (!action()) setBridgeFailure(true);
  };

  return (
    <Stack gap="lg">
      {!shell.bridgeAvailable || bridgeFailure ? (
        <Alert color="red" variant="light" icon={<IconAlertTriangle size={16} />}>
          {t("client.bridgeMissing")}
        </Alert>
      ) : null}

      <Stack gap={4}>
        <Text fw={600}>{t("client.server")}</Text>
        <Code block>{window.location.origin}</Code>
        <Text size="xs" c="dimmed">
          {t("client.switchServerHint")}
        </Text>
        <Group>
          <Button
            size="xs"
            variant="light"
            leftSection={<IconServer size={14} />}
            onClick={() => run(shellSwitchServer)}
          >
            {t("client.switchServer")}
          </Button>
        </Group>
      </Stack>

      <Stack gap={4}>
        <Text fw={600}>{t("client.session")}</Text>
        <Text size="xs" c="dimmed">
          {t("client.signOutHint")}
        </Text>
        <Group>
          <Button
            size="xs"
            variant="light"
            color="red"
            leftSection={<IconLogout size={14} />}
            onClick={() => run(shellSignOut)}
          >
            {t("client.signOut")}
          </Button>
        </Group>
      </Stack>

      <Stack gap={4}>
        <Text fw={600}>{t("client.shellVersion")}</Text>
        <Code>{shell.version}</Code>
      </Stack>

      <Stack gap={4}>
        <Text fw={600}>{t("client.webView")}</Text>
        <Code>{shell.webViewVersion || t("client.webViewUnknown")}</Code>
        {shell.webViewOutdated ? (
          <Alert color="red" variant="light" icon={<IconAlertTriangle size={16} />} mt="xs">
            <Stack gap="xs">
              <Text size="sm">
                {t("client.webViewOutdated", {
                  version: shell.webViewMajor,
                  min: MIN_WEBVIEW_MAJOR,
                })}
              </Text>
              <Anchor href={WEBVIEW_STORE_URL} target="_blank" rel="noreferrer" size="sm">
                {t("client.updateWebView")}
              </Anchor>
            </Stack>
          </Alert>
        ) : null}
      </Stack>
    </Stack>
  );
}
