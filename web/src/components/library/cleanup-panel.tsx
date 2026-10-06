import {
  ActionIcon,
  Badge,
  Box,
  Button,
  Checkbox,
  Group,
  Loader,
  Modal,
  ScrollArea,
  Stack,
  Tabs,
  Text,
  Tooltip,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import {
  IconAlertTriangle,
  IconChevronRight,
  IconExternalLink,
  IconFile,
  IconFolder,
  IconRefresh,
} from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  getCleanupPlanNodesOptions,
  getCleanupPlanOptions,
  getCleanupPlanQueryKey,
  getCleanupTrashOptions,
  submitTaskMutation,
} from "@/client/@tanstack/react-query.gen";
import type { LibraryResponse, PlanNodeResponse, PlanSummaryResponse } from "@/client/types.gen";
import { extractErrorMessage } from "@/lib/api-error";
import { confirm } from "@/lib/confirm";
import { formatFileSize } from "@/lib/utils";

interface CleanupPanelProps {
  library: LibraryResponse;
  opened: boolean;
  onClose: () => void;
}

/** 清单里的路径前缀匹配: 与后端一致按路径分量, 不用字符串前缀. */
function isUnder(path: string, prefix: string): boolean {
  if (path === prefix) return true;
  return path.startsWith(prefix.endsWith("/") ? prefix : `${prefix}/`);
}

function coveringPrefix(excluded: string[], path: string): string | undefined {
  return excluded.find((prefix) => isUnder(path, prefix));
}

export function CleanupPanel({ library, opened, onClose }: CleanupPanelProps) {
  const { t } = useTranslation(["library", "common"]);
  const [tab, setTab] = useState<string | null>("rules");

  return (
    <Modal opened={opened} onClose={onClose} title={t("cleanup.title")} size="xl">
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
    return (
      <Stack gap="sm">
        <Text size="sm" c="dimmed">
          {t("cleanup.noPlanHint")}
        </Text>
        {scanning ? (
          <Group gap="xs">
            <Loader size="xs" />
            <Text size="sm">{t("cleanup.generating")}</Text>
          </Group>
        ) : null}
        <Group>
          <Button leftSection={<IconRefresh size={16} />} loading={scanning} onClick={scan}>
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
        nodes={plan.nodes ?? []}
        entryCount={plan.entry_count ?? 0}
        entryBytes={plan.entry_bytes ?? 0}
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
        nodes={trash.nodes ?? []}
        entryCount={trash.entry_count ?? 0}
        entryBytes={trash.entry_bytes ?? 0}
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

interface PlanSelectionProps {
  libraryId: number;
  planId: string;
  nodes: PlanNodeResponse[];
  entryCount: number;
  entryBytes: number;
  onDone: () => void;
  header?: ReactNode;
}

/** 一份清单的勾选与确认: 规则来源与回收站共用. 选择随清单标识重置 (父组件用 key 重建). */
function PlanSelection({
  libraryId,
  planId,
  nodes,
  entryCount,
  entryBytes,
  onDone,
  header,
}: PlanSelectionProps) {
  const { t } = useTranslation(["library", "common"]);
  const queryClient = useQueryClient();
  const [excluded, setExcluded] = useState<string[]>([]);
  const [loadedNodes, setLoadedNodes] = useState<Record<string, PlanNodeResponse>>({});
  const deleteMutation = useMutation({
    ...submitTaskMutation(),
    onSuccess: () => {
      notifications.show({ message: t("cleanup.deleteStarted"), color: "blue" });
      void queryClient.invalidateQueries({
        queryKey: getCleanupPlanQueryKey({ path: { library_id: libraryId } }),
      });
      onDone();
    },
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      }),
  });

  // 排除项按前缀记录, 因此选中量 = 清单总量减去被排除节点的子树量.
  const totals = useMemo(() => {
    let entries = 0;
    let bytes = 0;
    for (const path of excluded) {
      const node = loadedNodes[path] ?? nodes.find((candidate) => candidate.path === path);
      if (!node) continue;
      entries += node.entry_count;
      bytes += node.entry_bytes;
    }
    return { entries: Math.max(0, entryCount - entries), bytes: Math.max(0, entryBytes - bytes) };
  }, [excluded, loadedNodes, nodes, entryCount, entryBytes]);

  const registerNodes = useCallback(
    (loaded: PlanNodeResponse[]) =>
      setLoadedNodes((prev) => {
        const next = { ...prev };
        for (const node of loaded) next[node.path] = node;
        return next;
      }),
    [],
  );

  const toggle = (node: PlanNodeResponse) => {
    const covering = coveringPrefix(excluded, node.path);
    if (covering === node.path) {
      setExcluded(excluded.filter((prefix) => prefix !== node.path));
      return;
    }
    if (covering) return; // 祖先已排除: 恢复本节点会连带兄弟节点.
    setExcluded([...excluded, node.path]);
  };

  const handleDelete = async () => {
    const ok = await confirm({
      title: t("cleanup.confirmTitle"),
      message: t("cleanup.confirmMessage", {
        count: totals.entries,
        size: formatFileSize(totals.bytes),
      }),
      confirmLabel: t("cleanup.confirmLabel"),
    });
    if (!ok) return;
    deleteMutation.mutate({
      body: {
        type: "delete",
        library_id: libraryId,
        plan_id: planId,
        exclude: excluded,
        prune_empty_dirs: true,
      },
    });
  };

  return (
    <Stack gap="xs">
      <Group justify="space-between">
        <Text size="sm">
          {t("cleanup.selected", { count: totals.entries, size: formatFileSize(totals.bytes) })}
        </Text>
        {header}
      </Group>
      <ScrollArea.Autosize mah="50vh">
        {nodes.length === 0 ? (
          <Text size="sm" c="dimmed">
            {t("cleanup.empty")}
          </Text>
        ) : (
          <Stack gap={2}>
            {nodes.map((node) => (
              <PlanNodeRow
                key={node.path}
                libraryId={libraryId}
                planId={planId}
                node={node}
                depth={0}
                excluded={excluded}
                onToggle={toggle}
                onNodes={registerNodes}
              />
            ))}
          </Stack>
        )}
      </ScrollArea.Autosize>
      <Group justify="flex-end">
        <Button variant="default" onClick={onDone}>
          {t("common:actions.cancel")}
        </Button>
        <Button
          color="red"
          loading={deleteMutation.isPending}
          disabled={totals.entries === 0}
          onClick={() => void handleDelete()}
        >
          {t("cleanup.deleteSelected")}
        </Button>
      </Group>
    </Stack>
  );
}

interface PlanNodeRowProps {
  libraryId: number;
  planId: string;
  node: PlanNodeResponse;
  depth: number;
  excluded: string[];
  onToggle: (node: PlanNodeResponse) => void;
  onNodes: (nodes: PlanNodeResponse[]) => void;
}

function PlanNodeRow({
  libraryId,
  planId,
  node,
  depth,
  excluded,
  onToggle,
  onNodes,
}: PlanNodeRowProps) {
  const { t } = useTranslation("library");
  const [expanded, setExpanded] = useState(false);
  const covering = coveringPrefix(excluded, node.path);
  const covered = covering !== undefined;
  const childrenQuery = useQuery({
    ...getCleanupPlanNodesOptions({
      path: { library_id: libraryId },
      query: { path: node.path, plan_id: planId },
    }),
    enabled: expanded && node.has_children,
  });
  const childNodes = childrenQuery.data?.nodes;

  useEffect(() => {
    if (childNodes) onNodes(childNodes);
  }, [childNodes, onNodes]);

  const marker = node.reason ? t(`cleanup.reason.${node.reason}`) : null;
  return (
    <Box>
      <Group gap={4} wrap="nowrap" pl={depth * 16}>
        {node.has_children ? (
          <ActionIcon
            variant="subtle"
            size="sm"
            aria-label={t("cleanup.expand")}
            onClick={() => setExpanded((prev) => !prev)}
          >
            <IconChevronRight
              size={14}
              style={{
                transform: expanded ? "rotate(90deg)" : undefined,
                transition: "transform 120ms",
              }}
            />
          </ActionIcon>
        ) : (
          <Box w={22} />
        )}
        <Checkbox
          size="xs"
          checked={!covered}
          disabled={covered && covering !== node.path}
          onChange={() => onToggle(node)}
        />
        {node.kind === "dir" ? (
          <IconFolder size={14} />
        ) : node.outside ? (
          <IconExternalLink size={14} />
        ) : (
          <IconFile size={14} />
        )}
        <Text size="sm" truncate title={node.path}>
          {node.name}
        </Text>
        {node.will_be_empty && node.kind === "dir" && !node.reason ? (
          <Badge size="xs" variant="light" color="orange">
            {t("cleanup.willBeEmpty")}
          </Badge>
        ) : null}
        {node.outside ? (
          <Badge size="xs" variant="light" color="gray">
            {t("cleanup.outside")}
          </Badge>
        ) : null}
        {node.hardlink ? (
          <Tooltip label={t("cleanup.hardlinkHint")}>
            <Badge size="xs" variant="light" color="gray">
              {t("cleanup.hardlink")}
            </Badge>
          </Tooltip>
        ) : null}
        {marker ? (
          <Badge size="xs" variant="default">
            {marker}
          </Badge>
        ) : null}
        {node.kind !== "dir" ? (
          <Text size="xs" c="dimmed">
            {formatFileSize(node.size)}
          </Text>
        ) : null}
        {node.entry_count > 1 ? (
          <Text size="xs" c="dimmed">
            {t("cleanup.nodeCount", { count: node.entry_count })}
          </Text>
        ) : null}
      </Group>
      {expanded && node.has_children ? (
        <Stack gap={2} mt={2}>
          {childrenQuery.isLoading ? (
            <Group pl={(depth + 1) * 16} gap="xs">
              <Loader size="xs" />
            </Group>
          ) : (
            (childNodes ?? []).map((child) => (
              <PlanNodeRow
                key={child.path}
                libraryId={libraryId}
                planId={planId}
                node={child}
                depth={depth + 1}
                excluded={excluded}
                onToggle={onToggle}
                onNodes={onNodes}
              />
            ))
          )}
        </Stack>
      ) : null}
    </Box>
  );
}
