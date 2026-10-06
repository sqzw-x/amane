import { Alert, Button, Group, Loader, Modal, Stack, Tabs, Text } from "@mantine/core";
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
        <Tabs.Panel value="rules">
          <RulesTab library={library} enabled={opened && tab === "rules"} onDone={onClose} />
        </Tabs.Panel>
        <Tabs.Panel value="trash">
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
      <Group justify="center" p="lg">
        <Loader size="sm" />
      </Group>
    );
  }
  const plan = planQuery.data;
  const scanning = Boolean(plan?.scan_running) || scanMutation.isPending;
  const scan = () =>
    scanMutation.mutate({ body: { type: "scan_invalid", library_id: library.id } });
  if (!plan?.exists) {
    // 扫描期间只渲染扫描中: 面板可以关掉, 扫描在后台继续.
    if (plan?.scan_running) {
      return (
        <Stack gap={6} align="center" py="xl">
          <Loader size="sm" />
          <Text size="sm">{t("cleanup.scanning")}</Text>
          <Text size="xs" c="dimmed">
            {t("cleanup.scanningHint")}
          </Text>
        </Stack>
      );
    }
    return (
      <Stack gap="sm">
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
        <Text size="sm" c="dimmed">
          {t("cleanup.noPlanHint")}
        </Text>
        <Group>
          {/* 提交后立即关面板: 进度由「扫描中」状态与任务列表表达, 不在这里再转一次圈. */}
          <Button
            leftSection={<IconRefresh size={16} />}
            onClick={() =>
              scanMutation.mutate(
                { body: { type: "scan_invalid", library_id: library.id } },
                { onSuccess: onDone },
              )
            }
          >
            {t("cleanup.scan")}
          </Button>
        </Group>
      </Stack>
    );
  }
  return (
    <Stack gap="xs">
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
      <Group justify="center" p="lg">
        <Loader size="sm" />
      </Group>
    );
  }
  const trash = trashQuery.data;
  if (!trash?.exists) {
    return (
      <Text size="sm" c="dimmed">
        {t("cleanup.trashEmpty")}
      </Text>
    );
  }
  return (
    <Stack gap="xs">
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
