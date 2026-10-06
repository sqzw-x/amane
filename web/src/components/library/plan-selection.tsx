import {
  ActionIcon,
  Badge,
  Box,
  Button,
  Center,
  Checkbox,
  Group,
  Loader,
  ScrollArea,
  Stack,
  Text,
  Tooltip,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconChevronRight, IconFile, IconFolder } from "@tabler/icons-react";
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { memo, type ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  getCleanupPlanNodesInfiniteOptions,
  getCleanupPlanQueryKey,
  submitTaskMutation,
} from "@/client/@tanstack/react-query.gen";
import type { PlanNodeResponse } from "@/client/types.gen";
import { InfiniteScrollSentinel } from "@/components/common/infinite-scroll-sentinel";
import { extractErrorMessage } from "@/lib/api-error";
import { confirm } from "@/lib/confirm";
import { nextOffsetPageParam } from "@/lib/infinite-list";
import { formatFileSize } from "@/lib/utils";
import classes from "./plan-selection.module.css";

/** 一层最多渲染这么多条, 滚到底再取下一页: 一份清单可能有上万条候选. */
const NODE_PAGE_SIZE = 200;

/** 清单里的路径前缀匹配: 与后端一致按路径分量, 不用字符串前缀. */
function isUnder(path: string, prefix: string): boolean {
  if (path === prefix) return true;
  return path.startsWith(prefix.endsWith("/") ? prefix : `${prefix}/`);
}

function coveringPrefix(excluded: string[], path: string): string | undefined {
  return excluded.find((prefix) => isUnder(path, prefix));
}

export interface PlanSelectionProps {
  libraryId: number;
  planId: string;
  /** 要展开的目录: 库内相对路径, 空串为库根; 回收站传其相对路径. */
  path?: string;
  onDone: () => void;
  header?: ReactNode;
}

/** 一份清单的勾选与确认: 规则来源、回收站与选中项预览共用. 选择随清单标识重置 (父组件用 key 重建). */
export function PlanSelection({
  libraryId,
  planId,
  path = "",
  onDone,
  header,
}: PlanSelectionProps) {
  const { t } = useTranslation(["library", "common"]);
  const queryClient = useQueryClient();
  const [excluded, setExcluded] = useState<string[]>([]);
  const [loadedNodes, setLoadedNodes] = useState<Record<string, PlanNodeResponse>>({});
  const level = usePlanNodeLevel({ libraryId, planId, path });
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
    for (const prefix of excluded) {
      const node =
        loadedNodes[prefix] ?? level.nodes.find((candidate) => candidate.path === prefix);
      if (!node) continue;
      entries += node.entry_count;
      bytes += node.entry_bytes;
    }
    return {
      entries: Math.max(0, level.entryCount - entries),
      bytes: Math.max(0, level.entryBytes - bytes),
    };
  }, [excluded, loadedNodes, level.nodes, level.entryCount, level.entryBytes]);

  const registerNodes = useCallback(
    (loaded: PlanNodeResponse[]) =>
      setLoadedNodes((prev) => {
        const next = { ...prev };
        for (const node of loaded) next[node.path] = node;
        return next;
      }),
    [],
  );

  // 依赖为空: 翻页只新增行, 已渲染的行靠 memo 挡住重渲染.
  const toggle = useCallback((node: PlanNodeResponse) => {
    setExcluded((prev) => {
      const covering = coveringPrefix(prev, node.path);
      if (covering === node.path) return prev.filter((prefix) => prefix !== node.path);
      if (covering) return prev; // 祖先已排除: 恢复本节点会连带兄弟节点.
      return [...prev, node.path];
    });
  }, []);

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
    // 面板给固定高度时撑满它, 让按钮行贴底; 嵌在自适应高度的弹窗里时按内容收缩.
    <Stack gap="xs" style={{ flex: "1 1 auto", minHeight: 0 }}>
      <Group justify="space-between">
        <Text size="sm">
          {t("cleanup.selected", { count: totals.entries, size: formatFileSize(totals.bytes) })}
        </Text>
        {header}
      </Group>
      <ScrollArea.Autosize
        mah="46vh"
        className={classes.scroll}
        py="sm"
        style={{ flex: "1 1 auto", minHeight: 0 }}
      >
        {level.isLoading ? (
          <Center py="lg">
            <Loader size="sm" />
          </Center>
        ) : level.total === 0 ? (
          <Text size="sm" c="dimmed">
            {t("cleanup.empty")}
          </Text>
        ) : (
          <Stack gap={6}>
            {level.nodes.map((node) => (
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
            <InfiniteScrollSentinel
              hasNextPage={level.hasNextPage}
              isFetchingNextPage={level.isFetchingNextPage}
              fetchNextPage={level.fetchNextPage}
              loadedLabel={t("common:pagination.loadedOfTotal", {
                loaded: level.nodes.length,
                total: level.total,
              })}
            />
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

interface PlanNodeLevelProps {
  libraryId: number;
  planId: string;
  path: string;
  enabled?: boolean;
}

/** 一层子节点: 只取一页, 滚到底再取下一页. 展开任意目录都走这里. */
function usePlanNodeLevel({ libraryId, planId, path, enabled = true }: PlanNodeLevelProps) {
  const query = useInfiniteQuery({
    ...getCleanupPlanNodesInfiniteOptions({
      path: { library_id: libraryId },
      query: { path, plan_id: planId, limit: NODE_PAGE_SIZE },
    }),
    enabled,
    initialPageParam: 0,
    getNextPageParam: nextOffsetPageParam,
  });
  const nodes = useMemo(() => query.data?.pages.flatMap((page) => page.items) ?? [], [query.data]);
  return {
    nodes,
    total: query.data?.pages[0]?.total ?? 0,
    entryCount: query.data?.pages[0]?.entry_count ?? 0,
    entryBytes: query.data?.pages[0]?.entry_bytes ?? 0,
    isLoading: query.isLoading,
    hasNextPage: query.hasNextPage,
    isFetchingNextPage: query.isFetchingNextPage,
    fetchNextPage: query.fetchNextPage,
  };
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

/** 行是纯展示 + 一层子节点查询: memo 让翻页只挂载新增的行, 不重渲染已加载的. */
const PlanNodeRow = memo(function PlanNodeRow({
  libraryId,
  planId,
  node,
  depth,
  excluded,
  onToggle,
  onNodes,
}: PlanNodeRowProps) {
  const { t } = useTranslation(["library", "common"]);
  const [expanded, setExpanded] = useState(false);
  const covering = coveringPrefix(excluded, node.path);
  const covered = covering !== undefined;
  // 子树里只要有一项被取消勾选, 整份清单执行完这个目录也不会空.
  const keepsSomething = excluded.some(
    (prefix) => prefix !== node.path && isUnder(prefix, node.path),
  );
  const children = usePlanNodeLevel({
    libraryId,
    planId,
    path: node.path,
    enabled: expanded && node.has_children,
  });

  useEffect(() => {
    if (children.nodes.length > 0) onNodes(children.nodes);
  }, [children.nodes, onNodes]);

  const marker = node.reason ? t(`cleanup.reason.${node.reason}`) : null;
  return (
    <Box
      className={classes.row}
      py={6}
      pr="sm"
      pl={`calc(var(--mantine-spacing-xs) + ${depth * 20}px)`}
    >
      <Group gap="sm" wrap="nowrap">
        {node.has_children ? (
          <ActionIcon
            variant="subtle"
            size="sm"
            aria-label={t("cleanup.expand")}
            onClick={() => setExpanded((prev) => !prev)}
          >
            <IconChevronRight
              size={16}
              style={{
                transform: expanded ? "rotate(90deg)" : undefined,
                transition: "transform 120ms",
              }}
            />
          </ActionIcon>
        ) : (
          <Box w={26} />
        )}
        <Checkbox
          size="sm"
          checked={!covered}
          disabled={covered && covering !== node.path}
          onChange={() => onToggle(node)}
        />
        {node.kind === "dir" ? <IconFolder size={16} /> : <IconFile size={16} />}
        {node.kind === "symlink" ? (
          <Tooltip label={t("cleanup.symlinkHint")}>
            <Badge size="sm" variant="light" color="blue">
              {t("cleanup.symlink")}
            </Badge>
          </Tooltip>
        ) : null}
        <Text size="sm" fw={500} truncate title={node.path}>
          {node.name}
        </Text>
        {node.will_be_empty &&
        node.kind === "dir" &&
        !node.reason &&
        !covered &&
        !keepsSomething ? (
          <Badge size="sm" variant="light" color="orange">
            {t("cleanup.willBeEmpty")}
          </Badge>
        ) : null}
        {node.hardlink ? (
          <Tooltip label={t("cleanup.hardlinkHint")}>
            <Badge size="sm" variant="light" color="gray">
              {t("cleanup.hardlink")}
            </Badge>
          </Tooltip>
        ) : null}
        {marker ? (
          <Badge size="sm" variant="default">
            {marker}
          </Badge>
        ) : null}
        {node.kind !== "dir" ? (
          <Text size="sm" c="dimmed">
            {formatFileSize(node.size)}
          </Text>
        ) : null}
        {node.entry_count > 1 ? (
          <Text size="sm" c="dimmed">
            {t("cleanup.nodeCount", { count: node.entry_count })}
          </Text>
        ) : null}
      </Group>
      {expanded && node.has_children ? (
        <Stack gap={6} mt={2}>
          {children.isLoading ? (
            <Group pl={(depth + 1) * 16} gap="xs">
              <Loader size="xs" />
            </Group>
          ) : (
            <>
              {children.nodes.map((child) => (
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
              ))}
              <InfiniteScrollSentinel
                hasNextPage={children.hasNextPage}
                isFetchingNextPage={children.isFetchingNextPage}
                fetchNextPage={children.fetchNextPage}
                loadedLabel={t("common:pagination.loadedOfTotal", {
                  loaded: children.nodes.length,
                  total: children.total,
                })}
              />
            </>
          )}
        </Stack>
      ) : null}
    </Box>
  );
});
