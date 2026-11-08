import { Badge, Group, Text } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Virtuoso } from "react-virtuoso";
import { getTaskLogOptions } from "@/client/@tanstack/react-query.gen";
import { LogKvPairs } from "@/components/log/log-kv";
import { LogTimestamp } from "@/components/log/log-timestamp";
import { mergeTaskLogEntries, TASK_LOG_LIMIT } from "@/lib/task/log";
import { type LogEntry, logLevelMantineColor, useLogStore } from "@/stores/logs";

interface TaskLogViewProps {
  taskId: number;
  height?: number;
}

/**
 * 历史取任务记录里的 task.log 尾部, 实时取 WebSocket store; 两者按记录去重合并.
 * 挂载在任务详情内, 因此只在展开时请求历史.
 */
export function TaskLogView({ taskId, height = 220 }: TaskLogViewProps) {
  const { t } = useTranslation("tasks");
  const entries = useLogStore((s) => s.entries);
  const historyQuery = useQuery(
    getTaskLogOptions({
      path: { task_id: taskId },
      query: { limit: TASK_LOG_LIMIT },
    }),
  );

  const live = useMemo(
    // task_id 是动态字段, 跨来源类型不一 (number/string), 统一按字符串比较
    () => entries.filter((e) => e.task_id != null && String(e.task_id) === String(taskId)),
    [entries, taskId],
  );
  const merged = useMemo(
    () => mergeTaskLogEntries(historyQuery.data?.entries ?? [], live),
    [historyQuery.data, live],
  );
  const entriesToRender = merged.entries;
  const truncated = merged.truncated || Boolean(historyQuery.data?.truncated);

  if (entriesToRender.length === 0) {
    if (historyQuery.isPending) {
      return (
        <Text size="xs" c="dimmed" ta="center" py="md">
          {t("logView.loading")}
        </Text>
      );
    }
    return (
      <Text size="xs" c={historyQuery.isError ? "red" : "dimmed"} ta="center" py="md">
        {historyQuery.isError ? t("logView.loadFailed") : t("logView.empty")}
      </Text>
    );
  }

  return (
    <div>
      {truncated ? (
        <Text size="xs" c="dimmed" mb={4}>
          {t("logView.truncated")}
        </Text>
      ) : null}
      <div
        style={{
          height,
          border: "1px solid var(--mantine-color-default-border)",
          borderRadius: "var(--mantine-radius-sm)",
        }}
      >
        <Virtuoso
          style={{ height: "100%" }}
          data={entriesToRender}
          initialTopMostItemIndex={entriesToRender.length - 1}
          followOutput="auto"
          itemContent={renderTaskLogRow}
        />
      </div>
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
