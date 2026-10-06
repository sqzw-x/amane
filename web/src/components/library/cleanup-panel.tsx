import { Alert, Button, Center, Group, Loader, Modal, Stack, Tabs, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconAlertTriangle, IconRefresh } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  getCleanupPlanOptions,
  getCleanupPlanQueryKey,
  getCleanupTrashOptions,
  submitTaskMutation,
} from "@/client/@tanstack/react-query.gen";
import type { LibraryResponse, PlanSummaryResponse } from "@/client/types.gen";
import { PlanSelection } from "@/components/library/plan-selection";
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

  return (
    <Modal opened={opened} onClose={onClose} title={t("cleanup.title")} size="xl" centered>
      <Tabs value={tab} onChange={setTab} keepMounted={false}>
        <Tabs.List mb="sm">
          <Tabs.Tab value="rules">{t("cleanup.tabRules")}</Tabs.Tab>
          <Tabs.Tab value="trash">{t("cleanup.tabTrash")}</Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="rules" h={PANEL_HEIGHT}>
          <RulesTab library={library} enabled={opened && tab === "rules"} onDone={onClose} />
        </Tabs.Panel>
        <Tabs.Panel value="trash" h={PANEL_HEIGHT}>
          <TrashTab library={library} enabled={opened && tab === "trash"} onDone={onClose} />
        </Tabs.Panel>
      </Tabs>
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
  const planQuery = useQuery({
    ...getCleanupPlanOptions({ path: { library_id: library.id } }),
    enabled,
    // 扫描在跑时轮询, 结束后停止.
    refetchInterval: (query) => (query.state.data?.scan_running ? 2000 : false),
  });
  const scanMutation = useMutation({
    ...submitTaskMutation(),
    onSuccess: () => {
      notifications.show({ message: t("cleanup.scanStarted"), color: "blue" });
      void queryClient.invalidateQueries({
        queryKey: getCleanupPlanQueryKey({ path: { library_id: library.id } }),
      });
    },
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      }),
  });

  if (planQuery.isLoading) {
    return (
      <Center h="100%">
        <Loader size="sm" />
      </Center>
    );
  }
  const plan = planQuery.data;
  const scanning = Boolean(plan?.scan_running) || scanMutation.isPending;
  const scan = () =>
    scanMutation.mutate({ body: { type: "scan_invalid", library_id: library.id } });
  if (!plan?.exists) {
    // 空清单与扫描中共用一块居中区域: 空时只有一个按钮, 提交后原地变成加载的圈.
    return (
      <Stack gap="sm" h="100%">
        {plan?.last_scan_error ? (
          <Alert
            color="red"
            variant="light"
            icon={<IconAlertTriangle size={16} />}
            title={t("cleanup.scanFailed")}
          >
            <Text size="xs">{plan.last_scan_error}</Text>
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
      <PlanNotices plan={plan} />
      <PlanSelection
        key={plan.plan_id}
        libraryId={library.id}
        planId={plan.plan_id ?? ""}
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

function TrashTab({ library, enabled, onDone }: TabProps) {
  const { t } = useTranslation(["library", "common"]);
  const trashQuery = useQuery({
    ...getCleanupTrashOptions({ path: { library_id: library.id } }),
    enabled,
  });

  if (trashQuery.isLoading) {
    return (
      <Center h="100%">
        <Loader size="sm" />
      </Center>
    );
  }
  const trash = trashQuery.data;
  if (!trash?.exists) {
    return (
      <Center h="100%">
        <Text size="sm" c="dimmed">
          {t("cleanup.trashEmpty")}
        </Text>
      </Center>
    );
  }
  return (
    <Stack gap="xs" h="100%">
      <Text size="xs" c="dimmed">
        {t("cleanup.trashNote")}
      </Text>
      <PlanSelection
        key={trash.plan_id}
        libraryId={library.id}
        planId={trash.plan_id ?? ""}
        path={trash.path ?? ""}
        onDone={onDone}
      />
    </Stack>
  );
}

function PlanNotices({ plan }: { plan: PlanSummaryResponse }) {
  const { t } = useTranslation("library");
  return (
    <Stack gap={4}>
      {plan.scope_path ? (
        <Text size="xs" c="dimmed">
          {t("cleanup.scope", { path: plan.scope_path })}
        </Text>
      ) : null}
      {plan.truncated ? (
        <Group gap={4} c="yellow.7">
          <IconAlertTriangle size={14} />
          <Text size="xs">{t("cleanup.truncated")}</Text>
        </Group>
      ) : null}
      {plan.skipped_dirs || plan.skipped_files ? (
        <Group gap={4} c="yellow.7">
          <IconAlertTriangle size={14} />
          <Text size="xs">
            {t("cleanup.skipped", { dirs: plan.skipped_dirs, files: plan.skipped_files })}
          </Text>
        </Group>
      ) : null}
    </Stack>
  );
}
