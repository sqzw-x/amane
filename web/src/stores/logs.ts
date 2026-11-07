import { create } from "zustand";
import { assertNever, isOneOf } from "@/lib/exhaustive";
import { LOG_LEVELS, type LogLevel } from "@/lib/exhaustive-maps";

export { LOG_LEVELS, type LogLevel };

export interface LogEntry {
  id: number;
  /** Unix timestamp in ms (parsed from ISO string or generated at receive time) */
  timestamp: number;
  level: LogLevel;
  source: string;
  message: string;
  [key: string]: unknown;
}

export const LOG_ENTRY_BASE_KEYS = new Set(["id", "timestamp", "level", "source", "message"]);

interface LogState {
  entries: LogEntry[];
  addEntry: (raw: Record<string, unknown>) => void;
  /** 丢弃指定 task_id 的缓冲日志. 任务 id 复用 (删除后新建) 时清除已删除任务的过时日志. */
  purgeTask: (taskId: number) => void;
  clear: () => void;
}

let nextId = 1;

function normalizeLevel(raw: unknown): LogLevel {
  const s = typeof raw === "string" ? raw : "";
  return isOneOf(LOG_LEVELS, s) ? s : "INFO";
}

/** 实时事件与历史记录共用同一套归一化; 历史条目传负数 id 与实时条目区分. */
export function normalizeLogEntry(raw: Record<string, unknown>, id: number): LogEntry {
  // Parse timestamp: prefer the structlog ISO timestamp in data, fallback to now
  let ts: number;
  if (typeof raw.timestamp === "string") {
    ts = new Date(raw.timestamp).getTime();
    if (Number.isNaN(ts)) ts = Date.now();
  } else if (typeof raw.timestamp === "number") {
    ts = raw.timestamp;
  } else {
    ts = Date.now();
  }

  const entry: LogEntry = {
    id,
    timestamp: ts,
    level: normalizeLevel(raw.level),
    source: typeof raw.source === "string" ? raw.source : "server",
    message: typeof raw.message === "string" ? raw.message : "",
  };

  // Spread all extra fields flat onto the entry
  for (const [key, value] of Object.entries(raw)) {
    if (LOG_ENTRY_BASE_KEYS.has(key)) continue;
    entry[key] = value;
  }
  return entry;
}

export const useLogStore = create<LogState>((set) => ({
  entries: [],
  addEntry: (raw) => {
    const entry = normalizeLogEntry(raw, nextId++);
    set((s) => ({ entries: [...s.entries, entry].slice(-5000) }));
  },
  purgeTask: (taskId) =>
    // task_id 跨来源类型不一 (number/string), 统一按字符串比较, 与 TaskLogView 过滤保持一致
    set((s) => ({
      entries: s.entries.filter((e) => e.task_id == null || String(e.task_id) !== String(taskId)),
    })),
  clear: () => set({ entries: [] }),
}));

export function logLevelMantineColor(level: LogLevel): string {
  switch (level) {
    case "CRITICAL":
    case "ERROR":
      return "red";
    case "WARNING":
      return "yellow";
    case "INFO":
      return "blue";
    case "DEBUG":
      return "gray";
    default:
      return assertNever(level, "LogLevel");
  }
}
