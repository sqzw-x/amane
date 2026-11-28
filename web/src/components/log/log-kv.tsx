import { Text, type MantineColor } from "@mantine/core";
import { LOG_ENTRY_BASE_KEYS, type LogEntry } from "@/stores/logs";

/**
 * 结构化日志 extra 字段: 按值类型上色; error / HTTP status 采用语义色.
 * 键名开放, 不要维护 key→color 表.
 *
 * 每对键值左侧用空白文本节点分隔 (不用 margin): 行是 flex 容器, 只有空白会随选区一起被复制,
 * 否则粘出来的日志会把相邻的 `k=v` 连成一串; 空白同时终止双击的取词范围 —— 没有它时
 * `GET` 与相邻的 `url` 会被并成一个词, 双击跨过键值边界.
 */
export function LogKvPairs({ entry }: { entry: LogEntry }) {
  const pairs = Object.entries(entry).filter(([k]) => !LOG_ENTRY_BASE_KEYS.has(k));
  if (pairs.length === 0) return null;
  return (
    <>
      {pairs.map(([key, value]) => (
        <Text key={key} component="span" size="xs" ff="monospace">
          {" "}
          <Text component="span" c="yellow.6">
            {key}
          </Text>
          <Text component="span" c="dimmed">
            =
          </Text>
          <Text component="span" c={kvValueColor(key, value)}>
            {formatKvValue(value)}
          </Text>
        </Text>
      ))}
    </>
  );
}

function formatKvValue(value: unknown): string {
  if (value === null) return "null";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function kvValueColor(key: string, value: unknown): MantineColor {
  if (key === "error" || key.endsWith("_error") || key === "reason") return "red.5";
  const status = statusCode(key, value);
  if (status != null) {
    if (status >= 500) return "red.5";
    if (status >= 400) return "orange.5";
    if (status >= 300) return "yellow.5";
    return "teal.5";
  }
  if (value === null) return "dimmed";
  switch (typeof value) {
    case "number":
      return "blue.4";
    case "boolean":
      return "violet.4";
    case "string":
      return "teal.5";
    default:
      return "grape.4";
  }
}

function statusCode(key: string, value: unknown): number | null {
  if (key !== "status" && key !== "status_code") return null;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && /^\d{3}$/.test(value)) return Number(value);
  return null;
}
