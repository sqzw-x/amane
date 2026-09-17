import { Alert, Anchor, Button, Code, Group, Stack, Text } from "@mantine/core";
import { IconAlertTriangle, IconServer } from "@tabler/icons-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  MIN_CHROMIUM_MAJOR,
  shellEnvironment,
  shellSwitchServer,
  webViewStoreUrl,
} from "@/lib/shell";

/**
 * 客户端设置: 服务器、登录状态与壳的运行期版本.
 * 只在壳内渲染; 这些是客户端自身的状态, 与服务端配置无关, 因此不进入 `SchemaForm`.
 */
export function ClientSettings() {
  const { t } = useTranslation("settings");
  const [bridgeFailure, setBridgeFailure] = useState(false);
  const shell = shellEnvironment();
  if (!shell) return null;

  const run = (action: () => boolean) => {
    if (!action()) setBridgeFailure(true);
  };

  const storeUrl = webViewStoreUrl(shell.packageLabel.split(" ")[0] ?? "");

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
        <Group mt="xs">
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
        <Text fw={600}>{t("client.appVersion")}</Text>
        <Code>{shell.version}</Code>
      </Stack>

      <Stack gap={4}>
        <Text fw={600}>{t("client.webView")}</Text>
        <Code>{shell.packageLabel || t("client.webViewUnknown")}</Code>
      </Stack>

      <Stack gap={4}>
        <Text fw={600}>{t("client.chromium")}</Text>
        <Code>
          {shell.chromiumVersion ? `Chromium ${shell.chromiumVersion}` : t("client.webViewUnknown")}
        </Code>
        {shell.chromiumOutdated ? (
          <Alert color="red" variant="light" icon={<IconAlertTriangle size={16} />} mt="xs">
            <Stack gap="xs">
              <Text size="sm">
                {t("client.chromiumOutdated", {
                  major: shell.chromiumMajor,
                  min: MIN_CHROMIUM_MAJOR,
                })}
              </Text>
              {storeUrl ? (
                <Anchor href={storeUrl} target="_blank" rel="noreferrer" size="sm">
                  {t("client.updateWebView")}
                </Anchor>
              ) : null}
            </Stack>
          </Alert>
        ) : null}
      </Stack>
    </Stack>
  );
}
