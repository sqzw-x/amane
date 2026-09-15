import { Badge, Group, Text } from "@mantine/core";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Virtuoso } from "react-virtuoso";
import { LogKvPairs } from "@/components/log/log-kv";
import { LogTimestamp } from "@/components/log/log-timestamp";
import { type LogEntry, logLevelMantineColor, useLogStore } from "@/stores/logs";

interface TaskLogViewProps {
  taskId: number;
  height?: number;
}

/**
 * 按 task_id 过滤全局日志 store (与 /logs 页同源的 WebSocket 缓冲).
 * 仅覆盖 store 窗口内 (最近 5000 条); 更早历史见任务记录内 task.log.
 */
export function TaskLogView({ taskId, height = 220 }: TaskLogViewProps) {
  const { t } = useTranslation("tasks");
  const entries = useLogStore((s) => s.entries);

  const filtered = useMemo(
    // task_id 是动态字段, 跨来源类型不一 (number/string), 统一按字符串比较
    () => entries.filter((e) => e.task_id != null && String(e.task_id) === String(taskId)),
    [entries, taskId],
  );

  if (filtered.length === 0) {
    return (
      <Text size="xs" c="dimmed" ta="center" py="md">
        {t("logView.empty")}
      </Text>
    );
  }

  return (
    <div
      style={{
        height,
        border: "1px solid var(--mantine-color-default-border)",
        borderRadius: "var(--mantine-radius-sm)",
      }}
    >
      <Virtuoso
        style={{ height: "100%" }}
        data={filtered}
        initialTopMostItemIndex={filtered.length - 1}
        followOutput="auto"
        itemContent={renderTaskLogRow}
      />
    </div>
  );
}

function renderTaskLogRow(_index: number, entry: LogEntry) {
  return <TaskLogRow entry={entry} />;
}

function TaskLogRow({ entry }: { entry: LogEntry }) {
  return (
    <Group
      gap="xs"
      wrap="nowrap"
      px="sm"
      py={4}
      align="flex-start"
      style={{ borderBottom: "1px solid var(--mantine-color-default-border)" }}
    >
      <LogTimestamp timestamp={entry.timestamp} />
      <Badge
        size="xs"
        color={logLevelMantineColor(entry.level)}
        variant="light"
        style={{ flexShrink: 0 }}
      >
        {entry.level}
      </Badge>
      <Text size="xs" style={{ flex: 1, minWidth: 0, wordBreak: "break-word" }}>
        <Text component="span" fw={700}>
          {entry.message}
        </Text>
        <LogKvPairs entry={entry} />
      </Text>
    </Group>
  );
}
