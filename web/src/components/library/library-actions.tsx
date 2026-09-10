import { ActionIcon, Group, Menu, Text, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import {
  IconAdjustmentsHorizontal,
  IconFolderDown,
  IconScan,
  IconTrash,
} from "@tabler/icons-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  deleteLibraryMutation,
  listLibrariesQueryKey,
  submitTaskMutation,
} from "@/client/@tanstack/react-query.gen";
import { submitTask } from "@/client/sdk.gen";
import type { LibraryResponse } from "@/client/types.gen";
import { extractErrorMessage } from "@/lib/api-error";
import { confirm } from "@/lib/confirm";

interface LibraryActionButtonsProps {
  library: LibraryResponse;
  onConfigure: () => void;
  /** 删除成功后的额外动作 (详情页离开). 列表页不传. */
  onDeleted?: () => void;
}

/** 扫描 / 整理 / 配置 / 删除. 列表卡片与详情表体顶栏共用. */
export function LibraryActionButtons({
  library,
  onConfigure,
  onDeleted,
}: LibraryActionButtonsProps) {
  const { t } = useTranslation(["library", "common"]);
  const queryClient = useQueryClient();

  const invalidate = () =>
    void queryClient.invalidateQueries({ queryKey: listLibrariesQueryKey() });

  const scanMutation = useMutation({
    ...submitTaskMutation(),
    onSuccess: () => notifications.show({ message: t("common:toast.scanStarted"), color: "blue" }),
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      }),
  });
  const organizeMutation = useMutation({
    mutationFn: async () => {
      await submitTask({
        body: { type: "archive", library_id: library.id },
        throwOnError: true,
      });
      await submitTask({
        body: { type: "organize", library_id: library.id },
        throwOnError: true,
      });
    },
    onSuccess: () =>
      notifications.show({ message: t("common:toast.organizeStarted"), color: "blue" }),
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      }),
  });
  const deleteMutation = useMutation({
    ...deleteLibraryMutation(),
    onSuccess: () => {
      notifications.show({ message: t("common:toast.watchPathRemoved"), color: "blue" });
      invalidate();
      onDeleted?.();
    },
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      }),
  });

  async function handleDelete() {
    const ok = await confirm({
      title: t("deleteLibrary.confirmTitle"),
      message: t("deleteLibrary.confirmDescEmpty", { name: library.name }),
      confirmLabel: t("common:actions.delete"),
    });
    if (!ok) return;
    deleteMutation.mutate({ path: { library_id: library.id } });
  }

  return (
    <Group gap={4} wrap="nowrap">
      <Menu shadow="md" position="bottom-end">
        <Menu.Target>
          <Tooltip label={t("scan.menuLabel")}>
            <ActionIcon
              variant="light"
              loading={scanMutation.isPending}
              aria-label={t("scan.menuLabel")}
            >
              <IconScan size={16} />
            </ActionIcon>
          </Tooltip>
        </Menu.Target>
        <Menu.Dropdown>
          <Menu.Item
            onClick={() =>
              scanMutation.mutate({
                body: {
                  type: "refresh",
                  library_id: library.id,
                  scan: ["add"],
                  scrape: ["pending"],
                },
              })
            }
          >
            <Text size="sm">{t("scan.modes.add.label")}</Text>
            <Text size="xs" c="dimmed">
              {t("scan.modes.add.desc")}
            </Text>
          </Menu.Item>
          <Menu.Item
            onClick={() =>
              scanMutation.mutate({
                body: { type: "refresh", library_id: library.id, scan: ["remove"] },
              })
            }
          >
            <Text size="sm">{t("scan.modes.remove.label")}</Text>
            <Text size="xs" c="dimmed">
              {t("scan.modes.remove.desc")}
            </Text>
          </Menu.Item>
        </Menu.Dropdown>
      </Menu>
      <Tooltip label={t("organize.tooltip")}>
        <ActionIcon
          variant="light"
          loading={organizeMutation.isPending}
          aria-label={t("organize.tooltip")}
          onClick={() => void organizeMutation.mutate()}
        >
          <IconFolderDown size={16} />
        </ActionIcon>
      </Tooltip>
      <Tooltip label={t("configureLibrary")}>
        <ActionIcon variant="light" onClick={onConfigure} aria-label={t("configureLibrary")}>
          <IconAdjustmentsHorizontal size={16} />
        </ActionIcon>
      </Tooltip>
      <Tooltip label={t("deleteLibrary.tooltip")}>
        <ActionIcon
          variant="light"
          color="red"
          loading={deleteMutation.isPending}
          onClick={() => void handleDelete()}
          aria-label={t("deleteLibrary.tooltip")}
        >
          <IconTrash size={16} />
        </ActionIcon>
      </Tooltip>
    </Group>
  );
}
