import type { TaskLogEntry } from "@/client/types.gen";
import { type LogEntry, normalizeLogEntry } from "@/stores/logs";

/** 与 WebSocket 日志 store 的窗口一致; 更早的历史不在界面里展示. */
export const TASK_LOG_LIMIT = 5000;

export interface MergedTaskLog {
  entries: LogEntry[];
  truncated: boolean;
}

function logEntryKey(entry: LogEntry): string {
  return `${entry.timestamp}\u0000${entry.level}\u0000${entry.source}\u0000${entry.message}`;
}

/**
 * 历史在前, 实时追加. 历史与实时可能重叠: 每条实时记录只抵消一条同键历史记录,
 * 实时记录之间保持原样, 不因同毫秒同消息而丢行. 按时间排序后截到窗口上限.
 */
export function mergeTaskLogEntries(
  history: readonly TaskLogEntry[],
  live: readonly LogEntry[],
): MergedTaskLog {
  const liveRemaining = new Map<string, number>();
  for (const entry of live) {
    const key = logEntryKey(entry);
    liveRemaining.set(key, (liveRemaining.get(key) ?? 0) + 1);
  }

  const merged: LogEntry[] = [];
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
    const remaining = liveRemaining.get(key) ?? 0;
    if (remaining > 0) {
      liveRemaining.set(key, remaining - 1);
      return;
    }
    merged.push(normalized);
  });
  merged.push(...live);

  merged.sort((a, b) => a.timestamp - b.timestamp);
  return {
    entries: merged.slice(-TASK_LOG_LIMIT),
    truncated: merged.length > TASK_LOG_LIMIT,
  };
}
