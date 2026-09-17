import { Stack, Text, Title } from "@mantine/core";
import { createFileRoute } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { ClientSettings } from "@/components/shell/client-settings";
import { shellEnvironment } from "@/lib/shell";

export const Route = createFileRoute("/client")({ component: ClientSettingsPage });

/** 客户端设置页: 只在壳内出现 (导航项同样如此). */
function ClientSettingsPage() {
  const { t } = useTranslation("settings");
  return (
    <Stack gap="md" pb={160}>
      <Title order={2}>{t("client.title")}</Title>
      {shellEnvironment() ? (
        <ClientSettings />
      ) : (
        <Text size="sm" c="dimmed">
          {t("client.unavailable")}
        </Text>
      )}
    </Stack>
  );
}
