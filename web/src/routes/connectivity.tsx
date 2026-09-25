import { Anchor, Button, Group, Paper, Stack, Text, ThemeIcon, Title } from "@mantine/core";
import { IconRadar } from "@tabler/icons-react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { ConnectivityTable } from "@/components/connectivity/connectivity-table";
import { useConnectivity } from "@/hooks/use-connectivity";

export const Route = createFileRoute("/connectivity")({
  component: ConnectivityPage,
});

function ConnectivityPage() {
  const { t } = useTranslation("connectivity");
  const { items, checkedAt, checking, retryingSourceId, check, retry } = useConnectivity();
  // 全量检测与行内重试共用同一个 mutation: 任一在途时就禁止发起第二个请求, 否则行状态会与实际请求错位.
  const busy = checking || retryingSourceId != null;

  return (
    <Stack gap="md">
      <div>
        <Title order={2}>{t("title")}</Title>
        <Text size="sm" c="dimmed" mt={4}>
          {t("description")} {/* 探测结论指向网络配置, 由此处直达代理设置. */}
          <Link to="/settings" search={{ section: "network" }}>
            <Anchor component="span" size="sm">
              {t("settingsLink")}
            </Anchor>
          </Link>
        </Text>
      </div>

      <Paper withBorder p="md">
        <Group justify="space-between" align="center" gap="sm" wrap="wrap">
          <Text size="sm" c="dimmed">
            {checkedAt == null
              ? t("neverChecked")
              : t("lastChecked", { time: new Date(checkedAt).toLocaleString() })}
          </Text>
          <Button
            leftSection={<IconRadar size={16} />}
            loading={checking}
            disabled={busy}
            onClick={check}
          >
            {checking ? t("running") : t("run")}
          </Button>
        </Group>
      </Paper>

      {items == null ? <ConnectivityEmptyState /> : null}
      {items != null ? (
        <ConnectivityTable
          items={items}
          busy={busy}
          retryingSourceId={retryingSourceId}
          onRetry={retry}
        />
      ) : null}
    </Stack>
  );
}

/** 未检测时不渲染空表: 表头与空行不能说明这一页要做什么. */
function ConnectivityEmptyState() {
  const { t } = useTranslation("connectivity");

  return (
    <Paper withBorder p="xl">
      <Stack align="center" gap="xs">
        <ThemeIcon variant="light" color="gray" size="lg" radius="xl">
          <IconRadar size={20} />
        </ThemeIcon>
        <Text fw={600}>{t("empty.title")}</Text>
        <Text size="sm" c="dimmed" ta="center">
          {t("empty.hint")}
        </Text>
      </Stack>
    </Paper>
  );
}
