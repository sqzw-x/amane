import type { TaskLogEntry } from "@/client/types.gen";
import { type LogEntry, normalizeLogEntry } from "@/stores/logs";

/** 与 WebSocket 日志 store 的窗口一致; 更早的历史不在界面里展示. */
export const TASK_LOG_LIMIT = 5000;

function logEntryKey(entry: LogEntry): string {
  // 历史与实时来自同一日志管线, 时间戳与消息足以识别同一条记录.
  return `${entry.timestamp}\u0000${entry.level}\u0000${entry.source}\u0000${entry.message}`;
}

/** 历史在前, 实时追加; 同一条记录只保留一份, 按时间排序后截到窗口上限. */
export function mergeTaskLogEntries(
  history: readonly TaskLogEntry[],
  live: readonly LogEntry[],
): LogEntry[] {
  const merged: LogEntry[] = [];
  const seen = new Set<string>();

  history.forEach((entry, index) => {
    const normalized = normalizeLogEntry(
      {
        ...entry.context,
        timestamp: entry.timestamp ?? 0,
        level: entry.level,
        source: entry.source,
        message: entry.message,
      },
      -(index + 1),
    );
    const key = logEntryKey(normalized);
    if (seen.has(key)) return;
    seen.add(key);
    merged.push(normalized);
  });

  for (const entry of live) {
    const key = logEntryKey(entry);
    if (seen.has(key)) continue;
    seen.add(key);
    merged.push(entry);
  }

  merged.sort((a, b) => a.timestamp - b.timestamp);
  return merged.slice(-TASK_LOG_LIMIT);
}
