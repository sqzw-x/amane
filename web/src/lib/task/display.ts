import {
  type Icon,
  IconArrowsDiagonal,
  IconDatabase,
  IconEraser,
  IconFolder,
  IconRefresh,
  IconRepeat,
  IconSearch,
  IconTrashX,
  IconUser,
  IconZoomExclamation,
} from "@tabler/icons-react";
import type {
  TaskChildStatusCounts,
  TaskListItem,
  TaskResponse,
  TaskStatus,
  TaskType,
} from "@/client/types.gen";
import { assertNever, exhaustiveRecord } from "@/lib/exhaustive";

/** 任务结果: 详情响应里按类型判别的联合; 用其字段类型, 避免依赖内联联合的名字. */
export type TaskResult = NonNullable<TaskResponse["result"]>;

/** 任务类型 → 图标; 与 TaskType 穷尽对应. */
export const TASK_ICONS: Record<TaskType, Icon> = exhaustiveRecord<TaskType>()({
  refresh: IconRefresh,
  organize: IconFolder,
  cleanup: IconEraser,
  scrape: IconSearch,
  upscale: IconArrowsDiagonal,
  r18_import: IconDatabase,
  actor_scrape: IconUser,
  rescrape: IconRepeat,
  scan_invalid: IconZoomExclamation,
  delete: IconTrashX,
});

const EMPTY_CHILD_STATUS: Required<TaskChildStatusCounts> = {
  queued: 0,
  running: 0,
  done: 0,
  failed: 0,
};

export function formatDuration(
  startedAt: string | null | undefined,
  finishedAt: string | null | undefined,
): string | null {
  if (!startedAt) return null;
  const end = finishedAt ? new Date(finishedAt).getTime() : Date.now();
  const seconds = Math.max(0, Math.round((end - new Date(startedAt).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m ${s}s`;
}

export function statusColor(status: TaskStatus): string {
  switch (status) {
    case "failed":
      return "red";
    case "done":
      return "teal";
    case "running":
      return "blue";
    case "queued":
      return "gray";
    default:
      return assertNever(status, "TaskStatus");
  }
}

export function childCountOf(task: TaskListItem): number {
  return task.child_count ?? 0;
}

export function childStatusOf(task: TaskListItem): Required<TaskChildStatusCounts> {
  const s = task.child_status;
  if (s == null) return EMPTY_CHILD_STATUS;
  return {
    queued: s.queued ?? 0,
    running: s.running ?? 0,
    done: s.done ?? 0,
    failed: s.failed ?? 0,
  };
}
