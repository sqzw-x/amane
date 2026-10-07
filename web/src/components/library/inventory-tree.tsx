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

/** 清单里的路径前缀匹配: 与后端一致按路径分量, 不用字符串前缀. 空前缀即库根, 一切都在它之下. */
function isUnder(path: string, prefix: string): boolean {
  if (prefix === "" || path === prefix) return true;
  return path.startsWith(prefix.endsWith("/") ? prefix : `${prefix}/`);
}

/**
 * 一条勾选规则. `keep` 是排除项 (不删), `restore` 是在排除项内重新纳入 (删).
 *
 * 只有前缀集合表达不了「保留这个目录, 但删掉里面的某一项」, 因此规则可以互相嵌套,
 * 路径互为祖先时按最深的一条判定 — 用户刚点的那条总是更深.
 */
interface SelectionRule {
  path: string;
  kind: "keep" | "restore";
  count: number;
  bytes: number;
}

function ruleDepth(path: string): number {
  return path.split("/").length;
}

/** 决定这一项自身状态的规则: 命中它的最深一条; 没有任何规则即默认勾选 (删). */
function deepestRule(rules: readonly SelectionRule[], path: string): SelectionRule | undefined {
  let best: SelectionRule | undefined;
  for (const rule of rules) {
    if (!isUnder(path, rule.path)) continue;
    if (best === undefined || ruleDepth(rule.path) > ruleDepth(best.path)) best = rule;
  }
  return best;
}

/** 子树里不会被删除的条目数与字节数: 自身规则按整棵算, 内部规则按它与自身相反的方向加减. */
function keptTotals(
  rules: readonly SelectionRule[],
  path: string,
  count: number,
  bytes: number,
): { entries: number; bytes: number } {
  const own = deepestRule(rules, path);
  const ownKept = own?.kind === "keep";
  let entries = ownKept ? count : 0;
  let keptBytes = ownKept ? bytes : 0;
  for (const rule of rules) {
    if (rule.path === path || !isUnder(rule.path, path)) continue;
    const sign = rule.kind === "keep" ? 1 : -1;
    entries += sign * rule.count;
    keptBytes += sign * rule.bytes;
  }
  return {
    entries: Math.min(Math.max(entries, 0), count),
    bytes: Math.min(Math.max(keptBytes, 0), bytes),
  };
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
  const [rules, setRules] = useState<SelectionRule[]>([]);
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

  // 选中量 = 清单总量减去规则保下来的子树量.
  const totals = useMemo(() => {
    const kept = keptTotals(rules, path, level.entryCount, level.entryBytes);
    return {
      entries: Math.max(0, level.entryCount - kept.entries),
      bytes: Math.max(0, level.entryBytes - kept.bytes),
    };
  }, [rules, path, level.entryCount, level.entryBytes]);

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
    setRules((prev) => {
      // 后代随本项一起定: 本项一旦有规则, 内部的规则就被它覆盖, 留着只会让计数绕圈.
      const outside = prev.filter(
        (rule) => rule.path !== node.path && !isUnder(rule.path, node.path),
      );
      const own = deepestRule(prev, node.path);
      // 本项自己就有规则: 点一下翻掉它, 回到祖先 (或默认) 的状态.
      if (own?.path === node.path) return outside;
      // 被祖先的规则覆盖或没有任何规则: 补一条与当前状态相反的规则, 本项自己的勾选随之翻转.
      return [
        ...outside,
        {
          path: node.path,
          kind: own?.kind === "keep" ? "restore" : "keep",
          count: node.entry_count,
          bytes: node.entry_bytes,
        },
      ];
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
        exclude: rules.filter((rule) => rule.kind === "keep").map((rule) => rule.path),
        include: rules.filter((rule) => rule.kind === "restore").map((rule) => rule.path),
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
                rules={rules}
                showNoise={showNoise}
                expandAll={expandAll}
                expanded={expanded}
                collapsed={collapsed}
                onToggle={toggle}
                onToggleExpand={toggleExpand}
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
  rules: readonly SelectionRule[];
  showNoise: boolean;
  expandAll: boolean;
  expanded: ReadonlySet<string>;
  collapsed: ReadonlySet<string>;
  onToggle: (node: InventoryNodeResponse) => void;
  onToggleExpand: (path: string) => void;
}

/** 行是纯展示 + 一层子节点查询: memo 让翻页只挂载新增的行, 不重渲染已加载的. */
const InventoryNodeRow = memo(function InventoryNodeRow({
  libraryId,
  inventoryId,
  node,
  depth,
  rules,
  showNoise,
  expandAll,
  expanded,
  collapsed,
  onToggle,
  onToggleExpand,
}: InventoryNodeRowProps) {
  const { t } = useTranslation(["library", "common"]);
  // 子树里保下来的条目数决定这一项的勾选状态: 全保即不勾, 保一部分即半选.
  const kept = keptTotals(rules, node.path, node.entry_count, node.entry_bytes);
  const checked = kept.entries < node.entry_count;
  const partial = checked && kept.entries > 0;
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

  const marker = node.reason ? t(`cleanup.reason.${node.reason}`) : null;
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
      {/* 点击范围是整个行: 内容上下方的内边距也算, 只有勾选框留给切换选中. */}
      <div className={classes.row} data-clickable onClick={activate}>
        <span className={classes.checkbox} onClick={(event) => event.stopPropagation()}>
          <Checkbox
            className={classes.checkboxBox}
            size="sm"
            checked={checked}
            indeterminate={partial}
            onChange={() => onToggle(node)}
          />
        </span>
        <div
          className={classes.body}
          role="button"
          tabIndex={0}
          aria-expanded={expandable ? isOpen : undefined}
          onKeyDown={onActivateKeyDown}
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
            size="sm"
            fw={500}
            c={node.noise ? "dimmed" : undefined}
            truncate
            title={node.path}
          >
            {node.name}
          </Text>
          {/* 徽章与体积整体换行 (窄屏) 或整体保持不压缩, 都不拆开单个元素. */}
          <span className={classes.meta}>
            {node.will_be_empty && node.kind === "dir" && !node.reason && kept.entries === 0 ? (
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
                  rules={rules}
                  showNoise={showNoise}
                  expandAll={expandAll}
                  expanded={expanded}
                  collapsed={collapsed}
                  onToggle={onToggle}
                  onToggleExpand={onToggleExpand}
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
