import { Badge, Button, Code, Group, Loader, Progress, Text, Tooltip } from "@mantine/core";
import { IconChevronRight } from "@tabler/icons-react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { type KeyboardEvent, useLayoutEffect, useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";
import { getTaskChildrenInfiniteOptions } from "@/client/@tanstack/react-query.gen";
import type { TaskChildResponse, TaskResponse } from "@/client/types.gen";
import {
  TaskDetailPanel,
  type TaskNodeActions,
  TaskRowActions,
} from "@/components/task/task-detail-panel";
import { useNarrowViewport } from "@/hooks/use-narrow-viewport";
import { nextOffsetPageParam } from "@/lib/infinite-list";
import {
  childCountOf,
  childStatusOf,
  formatDuration,
  statusColor,
  TASK_ICONS,
} from "@/lib/task/display";
import { bindExpandedPane } from "@/lib/task/focus-pane";
import type { TaskProgress } from "@/stores/progress";
import classes from "./task-tree.module.css";

const CHILD_PAGE_SIZE = 200;
const TREE_INDENT = 28;
const TREE_LINE_INSET = 8;
const ROW_PAD_LEFT = 6;
/** 窄屏脊线横坐标上限: 深度继续增加时, 详情内容与脊线不再左移. */
const NARROW_SPINE_MAX = 24;
/** 详情内容与脊线的间距. */
const DETAIL_GAP = 12;

function spineLeft(depth: number): number {
  return ROW_PAD_LEFT + depth * TREE_INDENT + TREE_LINE_INSET;
}

/** 脊线横坐标; 窄屏封顶, 避免深层节点的详情内容宽度被缩进占用. */
function spineLeftAt(depth: number, narrow: boolean): number {
  const left = spineLeft(depth);
  return narrow ? Math.min(left, NARROW_SPINE_MAX) : left;
}

function TreePaneRails({
  depth,
  continuations,
  isLast,
  hasChildren,
  status,
  narrow,
}: {
  depth: number;
  continuations: readonly boolean[];
  isLast: boolean;
  hasChildren: boolean;
  status: TaskResponse["status"];
  narrow: boolean;
}) {
  // 根节点不把森林兄弟画进树; 更深的节点把「自己不是末子」并进祖先贯通线.
  const rails = depth === 0 ? continuations : [...continuations, !isLast];
  return (
    <>
      {rails.map((cont, i) =>
        cont ? (
          <span
            key={i}
            className={classes.paneRiser}
            style={{ left: spineLeftAt(i, narrow) }}
            aria-hidden
          />
        ) : null,
      )}
      {hasChildren ? (
        <span
          className={classes.spine}
          data-status={status}
          style={{ left: spineLeftAt(depth, narrow) }}
          aria-hidden
        />
      ) : null}
    </>
  );
}

export interface TaskTreeProps {
  tasks: readonly TaskResponse[];
  progressByTask: Readonly<Record<number, TaskProgress | undefined>>;
  actions: TaskNodeActions;
  opened: ReadonlySet<number>;
  focusId: number | null;
  onToggle: (id: number) => void;
}

export function TaskTree({
  tasks,
  progressByTask,
  actions,
  opened,
  focusId,
  onToggle,
}: TaskTreeProps) {
  const narrow = useNarrowViewport();
  return (
    <ul className={classes.forest} role="tree">
      {tasks.map((task, index) => (
        <TaskTreeNode
          key={task.id}
          task={task}
          linkKey={null}
          depth={0}
          isLast={index === tasks.length - 1}
          continuations={[]}
          opened={opened}
          onToggle={onToggle}
          focusId={focusId}
          progressByTask={progressByTask}
          actions={actions}
          narrow={narrow}
        />
      ))}
    </ul>
  );
}

interface TaskTreeNodeProps {
  task: TaskResponse;
  linkKey: string | null;
  depth: number;
  isLast: boolean;
  continuations: readonly boolean[];
  opened: ReadonlySet<number>;
  onToggle: (id: number) => void;
  focusId: number | null;
  progressByTask: Readonly<Record<number, TaskProgress | undefined>>;
  actions: TaskNodeActions;
  /** 窄屏: 隐藏次要列并给详情缩进封顶. */
  narrow: boolean;
}

function TaskTreeNode({
  task,
  linkKey,
  depth,
  isLast,
  continuations,
  opened,
  onToggle,
  focusId,
  progressByTask,
  actions,
  narrow,
}: TaskTreeNodeProps) {
  const { t } = useTranslation("tasks");
  const open = opened.has(task.id);
  const childCount = childCountOf(task);
  const childStatus = childStatusOf(task);
  const progress = progressByTask[task.id];
  const duration = formatDuration(task.started_at, task.finished_at);
  const TypeIcon = TASK_ICONS[task.type];
  const rowRef = useRef<HTMLDivElement>(null);
  const paneRef = useRef<HTMLDivElement>(null);
  const focusIdRef = useRef(focusId);

  const childrenQuery = useInfiniteQuery({
    ...getTaskChildrenInfiniteOptions({
      path: { task_id: task.id },
      query: { limit: CHILD_PAGE_SIZE },
    }),
    enabled: open && childCount > 0,
    initialPageParam: 0,
    getNextPageParam: nextOffsetPageParam,
  });

  const children: TaskChildResponse[] = useMemo(
    () => childrenQuery.data?.pages.flatMap((page) => page.items) ?? [],
    [childrenQuery.data],
  );
  const remaining = Math.max(
    0,
    (childrenQuery.data?.pages[0]?.total ?? childCount) - children.length,
  );

  useLayoutEffect(() => {
    focusIdRef.current = focusId;
  }, [focusId]);

  useLayoutEffect(() => {
    if (!open) return;
    const row = rowRef.current;
    const pane = paneRef.current;
    if (row == null || pane == null) return;
    // 只在本节点打开时绑定. focusId 不列入 deps: 点开子节点时父面板不能拆掉重绑, 否则会滚回根行.
    return bindExpandedPane(row, pane, { focus: focusIdRef.current === task.id });
  }, [open, task.id]);

  function onRowKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onToggle(task.id);
    }
  }

  return (
    <li className={classes.node} role="treeitem" aria-expanded={open} data-open={open || undefined}>
      <div
        ref={rowRef}
        className={classes.row}
        data-status={task.status}
        data-open={open || undefined}
        tabIndex={0}
        aria-label={`${t(`filters.${task.type}`)}${task.title ? ` ${task.title}` : ""} #${task.id}`}
        onClick={() => onToggle(task.id)}
        onKeyDown={onRowKeyDown}
      >
        <div className={classes.identity}>
          {depth > 0 ? (
            <span
              className={classes.guides}
              style={{ width: (continuations.length + 1) * TREE_INDENT }}
              aria-hidden
            >
              {continuations.map((cont, i) => (
                <span key={i} className={classes.riser} data-cont={cont || undefined} />
              ))}
              <span
                className={classes.connector}
                data-status={task.status}
                data-last={isLast && (!open || childCount === 0) ? true : undefined}
              />
            </span>
          ) : null}
          <span className={classes.chevron} data-open={open || undefined} aria-hidden>
            <IconChevronRight size={16} />
          </span>
          <span className={classes.dot} data-status={task.status} />
          <TypeIcon size={15} style={{ flexShrink: 0 }} />
          <div className={classes.title}>
            <Text size="sm" fw={500} truncate>
              {t(`filters.${task.type}`)}
              {task.title ? `  ${task.title}` : ""}
            </Text>
            {childCount > 0 ? (
              <Tooltip label={t("childrenCount", { count: childCount })}>
                <Badge size="xs" variant="light" color="gray">
                  {childCount}
                </Badge>
              </Tooltip>
            ) : null}
            <ChildStatusPills status={childStatus} />
            {linkKey != null ? (
              <Tooltip label={t("tree.linkKey")}>
                <Code fz={11}>{linkKey}</Code>
              </Tooltip>
            ) : null}
          </div>
        </div>
        <Text
          className={classes.cell}
          size="xs"
          c="red"
          lineClamp={1}
          title={task.error ?? undefined}
          visibleFrom="sm"
        >
          {task.error ?? ""}
        </Text>
        <div className={classes.cell}>
          <Badge size="sm" variant="light" color={statusColor(task.status)}>
            {t(`status.${task.status}`)}
          </Badge>
        </div>
        <Text
          className={`${classes.cell} ${classes.cellNum}`}
          size="xs"
          c="dimmed"
          ff="monospace"
          visibleFrom="sm"
        >
          {duration ?? ""}
        </Text>
        <Text
          className={`${classes.cell} ${classes.cellNum}`}
          size="xs"
          c="dimmed"
          ff="monospace"
          visibleFrom="sm"
        >
          #{task.id}
        </Text>
        <div className={classes.cellEnd}>
          <TaskRowActions task={task} actions={actions} />
        </div>
        {task.status === "running" ? (
          <Progress
            className={classes.progress}
            size={2}
            value={progress && progress.total > 0 ? (progress.current / progress.total) * 100 : 100}
            animated={!progress || progress.total === 0}
          />
        ) : null}
      </div>

      {open ? (
        <div ref={paneRef} className={classes.pane} role="none">
          <div
            className={classes.detail}
            style={{ paddingLeft: spineLeftAt(depth, narrow) + DETAIL_GAP }}
          >
            <TreePaneRails
              depth={depth}
              continuations={continuations}
              isLast={isLast}
              hasChildren={childCount > 0}
              status={task.status}
              narrow={narrow}
            />
            <TaskDetailPanel task={task} linkKey={linkKey} actions={actions} />
          </div>
          {childCount > 0 ? (
            <ul className={classes.kids} role="group">
              {childrenQuery.isLoading ? (
                <li className={classes.node}>
                  <div
                    className={classes.ghost}
                    style={{ paddingLeft: spineLeftAt(depth + 1, narrow) }}
                  >
                    <Loader size="xs" />
                    <Text size="xs" c="dimmed">
                      {t("tree.loading")}
                    </Text>
                  </div>
                </li>
              ) : null}
              {childrenQuery.isError ? (
                <li className={classes.node}>
                  <div
                    className={classes.ghost}
                    style={{ paddingLeft: spineLeftAt(depth + 1, narrow) }}
                  >
                    <Text size="xs" c="red">
                      {t("tree.loadError")}
                    </Text>
                    <Button
                      size="compact-xs"
                      variant="subtle"
                      onClick={() => void childrenQuery.refetch()}
                    >
                      {t("tree.retry")}
                    </Button>
                  </div>
                </li>
              ) : null}
              {children.map((child, index) => (
                <TaskTreeNode
                  key={child.id}
                  task={child}
                  linkKey={child.link_key}
                  depth={depth + 1}
                  isLast={index === children.length - 1 && remaining === 0}
                  continuations={depth === 0 ? continuations : [...continuations, !isLast]}
                  opened={opened}
                  onToggle={onToggle}
                  focusId={focusId}
                  progressByTask={progressByTask}
                  actions={actions}
                  narrow={narrow}
                />
              ))}
              {remaining > 0 && childrenQuery.hasNextPage ? (
                <li className={classes.node}>
                  <div
                    className={classes.ghost}
                    style={{ paddingLeft: spineLeftAt(depth + 1, narrow) }}
                  >
                    <Button
                      size="compact-xs"
                      variant="subtle"
                      loading={childrenQuery.isFetchingNextPage}
                      onClick={() => void childrenQuery.fetchNextPage()}
                    >
                      {t("loadMoreChildren", { count: remaining })}
                    </Button>
                  </div>
                </li>
              ) : null}
            </ul>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

function ChildStatusPills({ status }: { status: { running: number; failed: number } }) {
  const { t } = useTranslation("tasks");
  if (status.failed === 0 && status.running === 0) return null;
  return (
    <Group gap={4} wrap="nowrap">
      {status.failed > 0 ? (
        <Badge size="xs" variant="light" color="red">
          {status.failed} {t("status.failed")}
        </Badge>
      ) : null}
      {status.running > 0 ? (
        <Badge size="xs" variant="light" color="blue">
          {status.running} {t("status.running")}
        </Badge>
      ) : null}
    </Group>
  );
}
