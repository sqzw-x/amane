import { Text } from "@mantine/core";
import type { CSSProperties } from "react";

const TIMESTAMP_STYLE: CSSProperties = { whiteSpace: "nowrap", flexShrink: 0 };

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

/**
 * 日志行的时间戳列.
 *
 * 两种形态同时渲染, 由断点隐藏其一: 窄屏只保留 HH:MM:SS, 宽屏沿用 `toLocaleTimeString()` 的完整结果.
 * 不用脚本切换文本, 避免挂载后整行重新排版.
 */
export function LogTimestamp({ timestamp }: { timestamp: number }) {
  const date = new Date(timestamp);
  return (
    <>
      <Text size="xs" c="dimmed" ff="monospace" visibleFrom="sm" style={TIMESTAMP_STYLE}>
        {date.toLocaleTimeString()}
      </Text>
      <Text size="xs" c="dimmed" ff="monospace" hiddenFrom="sm" style={TIMESTAMP_STYLE}>
        {`${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`}
      </Text>
    </>
  );
}
