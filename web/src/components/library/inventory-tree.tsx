import {
  Badge,
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
import {
  IconArrowsDiagonal,
  IconArrowsDiagonalMinimize,
  IconFile,
  IconFolder,
  IconFolderOpen,
} from "@tabler/icons-react";
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  type CSSProperties,
  type KeyboardEvent,
  memo,
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import { useTranslation } from "react-i18next";
import {
  getCleanupInventoryNodesInfiniteOptions,
  getCleanupInventoryQueryKey,
  submitTaskMutation,
} from "@/client/@tanstack/react-query.gen";
import type { InventoryNodeResponse } from "@/client/types.gen";
import { InfiniteScrollSentinel } from "@/components/common/infinite-scroll-sentinel";
import { extractErrorMessage } from "@/lib/api-error";
import { confirm } from "@/lib/confirm";
import { nextOffsetPageParam } from "@/lib/infinite-list";
import { formatFileSize } from "@/lib/utils";
import classes from "./inventory-tree.module.css";

/** 一层最多渲染这么多条, 滚到底再取下一页: 一份清单可能有上万条候选. */
const NODE_PAGE_SIZE = 200;

/** 深度交给样式表算缩进与底色; React 的 CSSProperties 不含自定义属性, 这里显式补上. */
type DepthStyle = CSSProperties & { "--row-depth": number };

function depthStyle(depth: number): DepthStyle {
  return { "--row-depth": depth };
}

/** 清单里的路径前缀匹配: 与后端一致按路径分量, 不用字符串前缀. */
function isUnder(path: string, prefix: string): boolean {
  if (path === prefix) return true;
  return path.startsWith(prefix.endsWith("/") ? prefix : `${prefix}/`);
}

function coveringPrefix(excluded: string[], path: string): string | undefined {
  return excluded.find((prefix) => isUnder(path, prefix));
}

export interface InventoryTreeProps {
  libraryId: number;
  inventoryId: string;
  /** 要展开的目录: 库内相对路径, 空串为库根; 回收站传其相对路径. */
  path?: string;
  onDone: () => void;
  header?: ReactNode;
}

/** 一份清单的勾选与确认: 规则来源、回收站与选中项预览共用. 选择随清单标识重置 (父组件用 key 重建). */
export function InventoryTree({
  libraryId,
  inventoryId,
  path = "",
  onDone,
  header,
}: InventoryTreeProps) {
  const { t } = useTranslation(["library", "common"]);
  const queryClient = useQueryClient();
  const [excluded, setExcluded] = useState<string[]>([]);
  const [loadedNodes, setLoadedNodes] = useState<Record<string, InventoryNodeResponse>>({});
  // 系统与同步工具的产物默认折叠: 它们也会被删除, 但多数时候只是噪音; 用户可展开核对.
  const [showNoise, setShowNoise] = useState(false);
  // 展开状态提在树上: 全局展开是模式, 逐个收起记进 collapsed, 因此新挂载的行也跟着展开.
  const [expandAll, setExpandAll] = useState(false);
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set());
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(() => new Set());
  const level = useInventoryNodeLevel({ libraryId, inventoryId, path, noise: showNoise });
  const deleteMutation = useMutation({
    ...submitTaskMutation(),
    onSuccess: () => {
      notifications.show({ message: t("cleanup.deleteStarted"), color: "blue" });
      void queryClient.invalidateQueries({
        queryKey: getCleanupInventoryQueryKey({ path: { library_id: libraryId } }),
      });
      onDone();
    },
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      }),
  });

  // 排除项按前缀记录且互不嵌套 (见 toggle), 因此选中量 = 清单总量减去被排除节点的子树量.
  const totals = useMemo(() => {
    let entries = 0;
    let bytes = 0;
    const kept: Record<string, number> = {};
    for (const prefix of excluded) {
      const node =
        loadedNodes[prefix] ?? level.nodes.find((candidate) => candidate.path === prefix);
      if (!node) continue;
      entries += node.entry_count;
      bytes += node.entry_bytes;
      kept[prefix] = node.entry_count;
    }
    return {
      entries: Math.max(0, level.entryCount - entries),
      bytes: Math.max(0, level.entryBytes - bytes),
      kept,
    };
  }, [excluded, loadedNodes, level.nodes, level.entryCount, level.entryBytes]);

  const registerNodes = useCallback(
    (loaded: InventoryNodeResponse[]) =>
      setLoadedNodes((prev) => {
        const next = { ...prev };
        for (const node of loaded) next[node.path] = node;
        return next;
      }),
    [],
  );

  // 全局展开时「收起一个」记进 collapsed, 而不是抹掉模式本身: 之后挂载的行仍应展开.
  const toggleExpand = useCallback(
    (nodePath: string) => {
      const update = (prev: ReadonlySet<string>) => {
        const next = new Set(prev);
        if (next.has(nodePath)) next.delete(nodePath);
        else next.add(nodePath);
        return next;
      };
      if (expandAll) setCollapsed(update);
      else setExpanded(update);
    },
    [expandAll],
  );

  // 依赖为空: 翻页只新增行, 已渲染的行靠 memo 挡住重渲染.
  const toggle = useCallback((node: InventoryNodeResponse) => {
    setExcluded((prev) => {
      const covering = coveringPrefix(prev, node.path);
      if (covering === node.path) {
        // 取消整棵: 后代本来就是「不删」, 不保留多余的排除项.
        return prev.filter((prefix) => prefix !== node.path);
      }
      if (covering) {
        // 祖先被排除时仍然可以直接点这一项: 把祖先换成「祖先之下除它以外全部排除」.
        return [
          ...prev.filter((prefix) => prefix !== covering && !isUnder(prefix, covering)),
          node.path,
        ];
      }
      // 已排除的后代并入本节点: 两个前缀会各减一次同一棵子树, 选中量就比实际执行集合少.
      return [...prev.filter((prefix) => !isUnder(prefix, node.path)), node.path];
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
        inventory_id: inventoryId,
        exclude: excluded,
        prune_empty_dirs: true,
      },
    });
  };

  return (
    // 面板给固定高度时撑满它, 让按钮行贴底; 嵌在自适应高度的弹窗里时按内容收缩.
    <Stack gap="xs" style={{ flex: "1 1 auto", minHeight: 0 }}>
      <Group justify="space-between" gap="sm" wrap="wrap">
        <Text size="sm">
          {t("cleanup.selected", { count: totals.entries, size: formatFileSize(totals.bytes) })}
        </Text>
        <Group gap="sm" wrap="wrap" justify="flex-end">
          <Checkbox
            size="xs"
            checked={showNoise}
            label={t("cleanup.showNoise")}
            onChange={(event) => setShowNoise(event.currentTarget.checked)}
          />
          <Button
            size="xs"
            variant="light"
            leftSection={
              expandAll ? (
                <IconArrowsDiagonalMinimize size={14} />
              ) : (
                <IconArrowsDiagonal size={14} />
              )
            }
            onClick={() => {
              setExpandAll((prev) => !prev);
              setExpanded(new Set());
              setCollapsed(new Set());
            }}
          >
            {expandAll ? t("cleanup.collapseAll") : t("cleanup.expandAll")}
          </Button>
          {header}
        </Group>
      </Group>
      <ScrollArea.Autosize
        mah={{ base: "68vh", sm: "46vh" }}
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
          <Stack gap={0}>
            {level.nodes.map((node) => (
              <InventoryNodeRow
                key={node.path}
                libraryId={libraryId}
                inventoryId={inventoryId}
                node={node}
                depth={0}
                excluded={excluded}
                kept={totals.kept}
                showNoise={showNoise}
                expandAll={expandAll}
                expanded={expanded}
                collapsed={collapsed}
                onToggle={toggle}
                onToggleExpand={toggleExpand}
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

interface InventoryNodeLevelProps {
  libraryId: number;
  inventoryId: string;
  path: string;
  noise?: boolean;
  enabled?: boolean;
}

/** 一层子节点: 只取一页, 滚到底再取下一页. 展开任意目录都走这里. */
function useInventoryNodeLevel({
  libraryId,
  inventoryId,
  path,
  noise = false,
  enabled = true,
}: InventoryNodeLevelProps) {
  const query = useInfiniteQuery({
    ...getCleanupInventoryNodesInfiniteOptions({
      path: { library_id: libraryId },
      query: { path, inventory_id: inventoryId, limit: NODE_PAGE_SIZE, noise },
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

interface InventoryNodeRowProps {
  libraryId: number;
  inventoryId: string;
  node: InventoryNodeResponse;
  depth: number;
  excluded: string[];
  kept: Record<string, number>;
  showNoise: boolean;
  expandAll: boolean;
  expanded: ReadonlySet<string>;
  collapsed: ReadonlySet<string>;
  onToggle: (node: InventoryNodeResponse) => void;
  onToggleExpand: (path: string) => void;
  onNodes: (nodes: InventoryNodeResponse[]) => void;
}

/** 行是纯展示 + 一层子节点查询: memo 让翻页只挂载新增的行, 不重渲染已加载的. */
const InventoryNodeRow = memo(function InventoryNodeRow({
  libraryId,
  inventoryId,
  node,
  depth,
  excluded,
  kept,
  showNoise,
  expandAll,
  expanded,
  collapsed,
  onToggle,
  onToggleExpand,
  onNodes,
}: InventoryNodeRowProps) {
  const { t } = useTranslation(["library", "common"]);
  const covering = coveringPrefix(excluded, node.path);
  const covered = covering !== undefined;
  // 子树里只要有一项被取消勾选, 整份清单执行完这个目录也不会空.
  const keepsSomething = excluded.some(
    (prefix) => prefix !== node.path && isUnder(prefix, node.path),
  );
  // 子树里的条目全被取消时这个目录实际什么都不会删: 勾选框与整体取消一致, 不再半选.
  const keptBelow = Object.entries(kept).reduce(
    (sum, [prefix, count]) => (isUnder(prefix, node.path) ? sum + count : sum),
    0,
  );
  const allKept = keptBelow > 0 && keptBelow >= node.entry_count;
  const partial = keepsSomething && !allKept;
  // 有子节点的目录靠点条目本身展开; 其余条目点条目本身即切换选中.
  const expandable = node.kind === "dir" && Boolean(node.has_children);
  const isOpen = expandAll ? !collapsed.has(node.path) : expanded.has(node.path);
  const children = useInventoryNodeLevel({
    libraryId,
    inventoryId,
    path: node.path,
    noise: showNoise,
    enabled: isOpen && expandable,
  });

  useEffect(() => {
    if (children.nodes.length > 0) onNodes(children.nodes);
  }, [children.nodes, onNodes]);

  const marker = node.reason ? t(`cleanup.reason.${node.reason}`) : null;
  // 信息项随宿主条目一起删除: 不给勾选框, 也不显示「将变空」这类只对可执行条目有意义的标记.
  const informational = Boolean(node.informational);
  const activate = () => {
    if (expandable) onToggleExpand(node.path);
    else onToggle(node);
  };
  const onActivateKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    activate();
  };

  return (
    <div className={classes.node} style={depthStyle(depth)}>
      <div className={classes.row} data-clickable={informational ? undefined : true}>
        {/* 信息项没有勾选框, 空槽让两类的图标与名字仍然对齐. */}
        <span className={classes.checkbox}>
          {informational ? null : (
            <Checkbox
              className={classes.checkboxBox}
              size="sm"
              checked={!covered && !allKept}
              indeterminate={partial}
              onChange={() => onToggle(node)}
            />
          )}
        </span>
        <div
          className={classes.body}
          role={informational ? undefined : "button"}
          tabIndex={informational ? undefined : 0}
          aria-expanded={expandable ? isOpen : undefined}
          onClick={informational ? undefined : activate}
          onKeyDown={informational ? undefined : onActivateKeyDown}
        >
          {node.kind === "dir" ? (
            isOpen ? (
              <IconFolderOpen size={18} />
            ) : (
              <IconFolder size={18} />
            )
          ) : (
            <IconFile size={18} />
          )}
          {node.kind === "symlink" ? (
            <Tooltip label={t("cleanup.symlinkHint")}>
              <Badge size="sm" variant="light" color="blue">
                {t("cleanup.symlink")}
              </Badge>
            </Tooltip>
          ) : null}
          <Text
            className={classes.name}
            size={informational ? "xs" : "sm"}
            fw={informational ? undefined : 500}
            c={node.noise ? "dimmed" : undefined}
            truncate
            title={node.path}
          >
            {node.name}
          </Text>
          {/* 徽章与体积整体换行 (窄屏) 或整体保持不压缩, 都不拆开单个元素. */}
          <span className={classes.meta}>
            {!informational &&
            node.will_be_empty &&
            node.kind === "dir" &&
            !node.reason &&
            !covered &&
            !keepsSomething ? (
              <Badge size="sm" variant="light" color="orange">
                {t("cleanup.willBeEmpty")}
              </Badge>
            ) : null}
            {!informational && node.hardlink ? (
              <Tooltip label={t("cleanup.hardlinkHint")}>
                <Badge size="sm" variant="light" color="gray">
                  {t("cleanup.hardlink")}
                </Badge>
              </Tooltip>
            ) : null}
            {!informational && marker ? (
              <Badge size="sm" variant="default">
                {marker}
              </Badge>
            ) : null}
            {node.kind !== "dir" ? (
              <Text size={informational ? "xs" : "sm"} c="dimmed">
                {formatFileSize(node.size)}
              </Text>
            ) : null}
            {!informational && node.entry_count > 1 ? (
              <Text size="sm" c="dimmed">
                {t("cleanup.nodeCount", { count: node.entry_count })}
              </Text>
            ) : null}
          </span>
        </div>
      </div>
      {isOpen && expandable ? (
        <div className={classes.children}>
          {children.isLoading ? (
            <div className={classes.pending} style={depthStyle(depth + 1)}>
              <Loader size="xs" />
            </div>
          ) : (
            <>
              {children.nodes.map((child) => (
                <InventoryNodeRow
                  key={child.path}
                  libraryId={libraryId}
                  inventoryId={inventoryId}
                  node={child}
                  depth={depth + 1}
                  excluded={excluded}
                  kept={kept}
                  showNoise={showNoise}
                  expandAll={expandAll}
                  expanded={expanded}
                  collapsed={collapsed}
                  onToggle={onToggle}
                  onToggleExpand={onToggleExpand}
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
        </div>
      ) : null}
    </div>
  );
});
