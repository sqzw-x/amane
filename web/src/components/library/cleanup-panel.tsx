import { Alert, Box, Button, Center, Group, Loader, Modal, Stack, Tabs, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconAlertTriangle, IconRefresh } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  getCleanupInventoryOptions,
  getCleanupInventoryQueryKey,
  getCleanupTrashOptions,
  submitTaskMutation,
} from "@/client/@tanstack/react-query.gen";
import type { LibraryResponse, InventorySummaryResponse } from "@/client/types.gen";
import { InventoryTree } from "@/components/library/inventory-tree";
import { extractErrorMessage } from "@/lib/api-error";

/** 两个页签共用同一块高度: 弹窗居中, 高度一变表头就会跟着上下跳. */
const PANEL_HEIGHT = "52vh";

interface CleanupPanelProps {
  library: LibraryResponse;
  opened: boolean;
  onClose: () => void;
}

export function CleanupPanel({ library, opened, onClose }: CleanupPanelProps) {
  const { t } = useTranslation(["library", "common"]);
  const [tab, setTab] = useState<string | null>("rules");
  // 回收站是历史遗留: 里面没东西时连页签都不渲染, 用户看不到它; 有遗留才出现.
  // 展开是有副作用的读取 (每次都产出新清单), 因此不跟着窗口聚焦重跑.
  const trashQuery = useQuery({
    ...getCleanupTrashOptions({ path: { library_id: library.id } }),
    enabled: opened,
    refetchOnWindowFocus: false,
  });
  const trash = trashQuery.data?.exists ? trashQuery.data : null;

  return (
    <Modal opened={opened} onClose={onClose} title={t("cleanup.title")} size="xl" centered>
      {trash ? (
        <Tabs value={tab} onChange={setTab} keepMounted={false}>
          <Tabs.List mb="sm">
            <Tabs.Tab value="rules">{t("cleanup.tabRules")}</Tabs.Tab>
            <Tabs.Tab value="trash">{t("cleanup.tabTrash")}</Tabs.Tab>
          </Tabs.List>
          <Tabs.Panel value="rules" h={PANEL_HEIGHT}>
            <RulesTab library={library} enabled={opened && tab !== "trash"} onDone={onClose} />
          </Tabs.Panel>
          <Tabs.Panel value="trash" h={PANEL_HEIGHT}>
            <Stack gap="xs" h="100%">
              <TruncationNotice truncated={trash.truncated} dropped={trash.dropped} />
              <InventoryTree
                key={trash.inventory_id}
                libraryId={library.id}
                inventoryId={trash.inventory_id ?? ""}
                path={trash.path ?? ""}
                onDone={onClose}
              />
            </Stack>
          </Tabs.Panel>
        </Tabs>
      ) : (
        <Box h={PANEL_HEIGHT}>
          <RulesTab library={library} enabled={opened} onDone={onClose} />
        </Box>
      )}
    </Modal>
  );
}

interface TabProps {
  library: LibraryResponse;
  enabled: boolean;
  onDone: () => void;
}

function RulesTab({ library, enabled, onDone }: TabProps) {
  const { t } = useTranslation(["library", "common"]);
  const queryClient = useQueryClient();
  const inventoryQuery = useQuery({
    ...getCleanupInventoryOptions({ path: { library_id: library.id } }),
    enabled,
    // 扫描在跑时轮询, 结束后停止.
    refetchInterval: (query) => (query.state.data?.scan_running ? 2000 : false),
  });
  const scanMutation = useMutation({
    ...submitTaskMutation(),
    onSuccess: () => {
      notifications.show({ message: t("cleanup.scanStarted"), color: "blue" });
      void queryClient.invalidateQueries({
        queryKey: getCleanupInventoryQueryKey({ path: { library_id: library.id } }),
      });
    },
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      }),
  });

  if (inventoryQuery.isLoading) {
    return (
      <Center h="100%">
        <Loader size="sm" />
      </Center>
    );
  }
  const inventory = inventoryQuery.data;
  const scanning = Boolean(inventory?.scan_running) || scanMutation.isPending;
  const scan = () =>
    scanMutation.mutate({ body: { type: "scan_invalid", library_id: library.id } });
  if (!inventory?.exists) {
    // 空清单与扫描中共用一块居中区域: 空时只有一个按钮, 提交后原地变成加载的圈.
    return (
      <Stack gap="sm" h="100%">
        {inventory?.last_scan_error ? (
          <Alert
            color="red"
            variant="light"
            icon={<IconAlertTriangle size={16} />}
            title={t("cleanup.scanFailed")}
          >
            <Text size="xs">{inventory.last_scan_error}</Text>
          </Alert>
        ) : null}
        <Center style={{ flex: 1 }}>
          {scanning ? (
            <Stack gap={6} align="center">
              <Loader size="sm" />
              <Text size="sm">{t("cleanup.scanning")}</Text>
              <Text size="xs" c="dimmed">
                {t("cleanup.scanningHint")}
              </Text>
            </Stack>
          ) : (
            <Button leftSection={<IconRefresh size={16} />} onClick={scan}>
              {t("cleanup.scan")}
            </Button>
          )}
        </Center>
      </Stack>
    );
  }
  return (
    <Stack gap="xs" h="100%">
      <InventoryNotices inventory={inventory} />
      <InventoryTree
        key={inventory.inventory_id}
        libraryId={library.id}
        inventoryId={inventory.inventory_id ?? ""}
        onDone={onDone}
        header={
          <Button
            variant="subtle"
            size="compact-sm"
            leftSection={<IconRefresh size={14} />}
            loading={scanning}
            onClick={scan}
          >
            {t("cleanup.rescan")}
          </Button>
        }
      />
    </Stack>
  );
}

function InventoryNotices({ inventory }: { inventory: InventorySummaryResponse }) {
  const { t } = useTranslation("library");
  return (
    <Stack gap={4}>
      {inventory.scope_path ? (
        <Text size="xs" c="dimmed">
          {t("cleanup.scope", { path: inventory.scope_path })}
        </Text>
      ) : null}
      <TruncationNotice truncated={inventory.truncated} dropped={inventory.dropped} />
      {inventory.skipped_dirs || inventory.skipped_files ? (
        <Group gap={4} c="yellow.7">
          <IconAlertTriangle size={14} />
          <Text size="xs">
            {t("cleanup.skipped", { dirs: inventory.skipped_dirs, files: inventory.skipped_files })}
          </Text>
        </Group>
      ) : null}
      {inventory.blocked_dirs ? (
        <Text size="xs" c="dimmed">
          {t("cleanup.blocked", { count: inventory.blocked_dirs })}
        </Text>
      ) : null}
    </Stack>
  );
}

/** 触顶提示: 清单只包含部分条目, 并给出还有多少没纳入 — 用户据此把扫描限定到子目录. */
export function TruncationNotice({
  truncated,
  dropped,
}: {
  truncated?: boolean;
  dropped?: number;
}) {
  const { t } = useTranslation("library");
  if (!truncated) return null;
  return (
    <Group gap={4} c="yellow.7">
      <IconAlertTriangle size={14} />
      <Text size="xs">{t("cleanup.truncated")}</Text>
      {dropped ? <Text size="xs">{t("cleanup.truncatedMore", { count: dropped })}</Text> : null}
    </Group>
  );
}
