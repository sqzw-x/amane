import { Menu, Text, UnstyledButton } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconExternalLink, IconRefresh, IconRotateClockwise } from "@tabler/icons-react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  desktopInfoOptions,
  getReleaseOptions,
  healthCheckOptions,
} from "@/client/@tanstack/react-query.gen";
import { listTasks, restartServer } from "@/client/sdk.gen";
import { extractErrorMessage } from "@/lib/api-error";
import { confirm } from "@/lib/confirm";

function displayTag(tag: string): string {
  return tag.replace(/^v/i, "");
}

export function VersionMenu({ visibleFrom }: { visibleFrom?: "sm" }) {
  const { t } = useTranslation("common");
  const [opened, setOpened] = useState(false);
  const health = useQuery({ ...healthCheckOptions(), staleTime: 60_000 });
  const desktop = useQuery({ ...desktopInfoOptions(), enabled: opened });
  // 挂载即检查更新, 与菜单开合无关: 未展开菜单的会话看不到更新提示.
  // `staleTime` 限制标签页聚焦触发的重取次数 (后端 `src/amane/release.py` 的缓存 TTL 为 1 小时, 重取通常只得到同一快照);
  // 重取也是检查失败的唯一恢复途径 — 失败不写入缓存时刻, 故失败后不得依赖缓存判定.
  const update = useQuery({ ...getReleaseOptions(), staleTime: 5 * 60_000, retry: 2 });
  const version = health.data?.version;
  const supervised = desktop.data?.supervised === true;
  const latest = update.data?.latest;
  const newer = update.data?.newer === true;
  const htmlUrl = update.data?.html_url;

  const restart = useMutation({
    mutationFn: async () => {
      const { data } = await listTasks({
        query: { status: ["running"], limit: 1 },
        throwOnError: true,
      });
      const running = data.total;
      const ok = await confirm({
        title: t("about.restart"),
        message:
          running > 0 ? t("about.restartRunning", { count: running }) : t("about.restartConfirm"),
        danger: running > 0,
      });
      if (!ok) return false;
      await restartServer({ throwOnError: true });
      return true;
    },
    onSuccess: (did) => {
      if (did) {
        notifications.show({ color: "blue", message: t("about.restarting") });
      }
    },
    onError: (error) => {
      notifications.show({
        color: "red",
        message: extractErrorMessage(error, t("toast.operationFailed")),
      });
    },
  });

  if (!version) return null;

  return (
    <Menu opened={opened} onChange={setOpened} position="bottom-start" shadow="md" width={240}>
      <Menu.Target>
        <UnstyledButton visibleFrom={visibleFrom} aria-label={t("about.menu")}>
          <Text
            size="xs"
            c={newer ? "blue" : "dimmed"}
            ff="monospace"
            style={{ whiteSpace: "nowrap" }}
          >
            {t("about.version", { version })}
          </Text>
        </UnstyledButton>
      </Menu.Target>
      <Menu.Dropdown>
        <Menu.Label>
          {t("about.current")}: v{displayTag(version)}
        </Menu.Label>
        <Menu.Label>
          {update.isFetching && !latest
            ? t("status.loading")
            : latest
              ? `${t("about.latest")}: v${displayTag(latest)}`
              : t("about.checkFailed")}
        </Menu.Label>
        <Menu.Divider />
        <Menu.Item
          leftSection={<IconRefresh size={14} />}
          onClick={() => void update.refetch()}
          disabled={update.isFetching}
        >
          {t("about.check")}
        </Menu.Item>
        {newer && htmlUrl ? (
          <Menu.Item
            leftSection={<IconExternalLink size={14} />}
            component="a"
            href={htmlUrl}
            target="_blank"
            rel="noreferrer"
          >
            {t("about.openRelease")}
          </Menu.Item>
        ) : null}
        {supervised ? (
          <Menu.Item
            color="red"
            leftSection={<IconRotateClockwise size={14} />}
            onClick={() => restart.mutate()}
            disabled={restart.isPending}
          >
            {t("about.restart")}
          </Menu.Item>
        ) : null}
      </Menu.Dropdown>
    </Menu>
  );
}
