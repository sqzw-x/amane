import {
  Badge,
  Button,
  Code,
  Collapse,
  Group,
  Progress,
  ScrollArea,
  Stack,
  Text,
  UnstyledButton,
} from "@mantine/core";
import {
  IconChevronDown,
  IconChevronRight,
  IconDownload,
  IconPlayerStop,
  IconRefresh,
  IconTrash,
} from "@tabler/icons-react";
import { type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { getTaskOptions } from "@/client/@tanstack/react-query.gen";
import { client } from "@/client/client.gen";
import type { TaskListItem } from "@/client/types.gen";
import { HintedActionIcon } from "@/components/common/hinted-action-icon";
import { TaskLogView } from "@/components/log/task-log-view";
import { TaskResultPanel } from "@/components/task/task-result-panel";
import { formatDuration, statusColor } from "@/lib/task/display";
import { useProgressStore } from "@/stores/progress";

export interface TaskNodeActions {
  onCancel: (taskId: number) => void;
  onRetry: (taskId: number) => void;
  onDelete: (taskId: number) => void;
  pending: boolean;
}

interface TaskDetailPanelProps {
  task: TaskListItem;
  linkKey: string | null;
  actions: TaskNodeActions;
}

function CollapsibleJson({ title, value }: { title: string; value: unknown }) {
  const [opened, setOpened] = useState(false);

  return (
    <div>
      <UnstyledButton
        onClick={() => setOpened((v) => !v)}
        style={{ display: "block", width: "100%" }}
      >
        <Group gap={6} wrap="nowrap">
          {opened ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
          <Text size="sm" c="dimmed" fw={500}>
            {title}
          </Text>
        </Group>
      </UnstyledButton>
      <Collapse expanded={opened}>
        <ScrollArea.Autosize mah={220} type="auto" mt={6}>
          <Code block>{JSON.stringify(value, null, 2)}</Code>
        </ScrollArea.Autosize>
      </Collapse>
    </div>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <Text size="xs" c="dimmed">
        {label}
      </Text>
      <Text size="sm" mt={2} ff="monospace">
        {children}
      </Text>
    </div>
  );
}

/** JSON 默认折叠. 列表不带 payload 与 result, 展开时按 id 取详情. */
export function TaskDetailPanel({ task, linkKey, actions }: TaskDetailPanelProps) {
  const { t } = useTranslation(["tasks", "common"]);
  const { data: detail, isError } = useQuery({ ...getTaskOptions({ path: { task_id: task.id } }) });
  const progress = useProgressStore((s) => s.byTask[task.id]);
  const duration = formatDuration(task.started_at, task.finished_at);
  const isTerminal = task.status === "done" || task.status === "failed";
  const hasPayload = Object.keys(detail?.payload ?? {}).length > 0;
  const hasResult = detail?.result != null;

  return (
    <Stack gap="sm" p="sm">
      <Group gap="lg" wrap="wrap">
        <Fact label={t("detail.id")}>#{task.id}</Fact>
        {task.root_task_id != null && task.root_task_id !== task.id ? (
          <Fact label={t("detail.root")}>#{task.root_task_id}</Fact>
        ) : null}
        {linkKey != null ? <Fact label={t("detail.linkKey")}>{linkKey}</Fact> : null}
        <Fact label={t("detail.priority")}>{task.priority ?? 0}</Fact>
        <Fact label={t("detail.retries")}>{task.retries ?? 0}</Fact>
        <Fact label={t("detail.created")}>
          {task.created_at ? new Date(task.created_at).toLocaleString() : "-"}
        </Fact>
        <Fact label={t("detail.started")}>
          {task.started_at ? new Date(task.started_at).toLocaleString() : "-"}
        </Fact>
        <Fact label={t("detail.duration")}>{duration ?? "-"}</Fact>
        <div>
          <Text size="xs" c="dimmed">
            {t("detail.status")}
          </Text>
          <Badge size="sm" variant="light" color={statusColor(task.status)} mt={2}>
            {t(`status.${task.status}`)}
          </Badge>
        </div>
      </Group>

      {task.status === "running" && (
        <div>
          <Text size="sm" fw={600} mb={4}>
            {t("detail.progress")}
          </Text>
          <Progress
            size="sm"
            value={progress && progress.total > 0 ? (progress.current / progress.total) * 100 : 100}
            animated={!progress || progress.total === 0}
          />
          {progress && (progress.total > 0 || progress.message) && (
            <Text size="xs" c="dimmed" mt={4}>
              {progress.total > 0 ? `${progress.current} / ${progress.total}` : ""}
              {progress.total > 0 && progress.message ? " · " : ""}
              {progress.message}
            </Text>
          )}
        </div>
      )}

      {isTerminal && detail == null ? (
        <Text size="xs" c={isError ? "red" : "dimmed"}>
          {isError ? t("result.loadFailed") : t("result.loading")}
        </Text>
      ) : null}

      {isTerminal && detail != null ? (
        <TaskResultPanel
          result={detail.result ?? null}
          headline={task.error}
          failed={task.status === "failed"}
        />
      ) : null}

      {hasPayload && detail != null && (
        <CollapsibleJson title={t("detail.payload")} value={detail.payload} />
      )}

      {hasResult && detail != null && (
        <CollapsibleJson title={t("detail.result")} value={detail.result} />
      )}

      {isTerminal && detail != null && !hasResult ? (
        <Text size="xs" c="dimmed">
          {t("result.noResultHint")}
        </Text>
      ) : null}

      {(task.status === "queued" || task.status === "running") && (
        <div>
          <Text size="sm" fw={600} mb={4}>
            {t("detail.liveLogs")}
          </Text>
          <TaskLogView taskId={task.id} />
        </div>
      )}

      <Group gap="xs">
        {(task.status === "queued" || task.status === "running") && (
          <Button
            size="xs"
            variant="light"
            color="orange"
            loading={actions.pending}
            onClick={() => actions.onCancel(task.id)}
          >
            {t("actions.cancelTask")}
          </Button>
        )}
        {task.status === "failed" && (
          <Button
            size="xs"
            variant="light"
            loading={actions.pending}
            onClick={() => actions.onRetry(task.id)}
          >
            {t("common:actions.retry")}
          </Button>
        )}
        {isTerminal && (
          <Button
            size="xs"
            variant="light"
            leftSection={<IconDownload size={14} />}
            onClick={() =>
              window.open(`${client.getConfig().baseUrl}/api/tasks/${task.id}/record`, "_blank")
            }
          >
            {t("actions.record")}
          </Button>
        )}
        {isTerminal && (
          <Button
            size="xs"
            variant="light"
            color="red"
            loading={actions.pending}
            onClick={() => void actions.onDelete(task.id)}
          >
            {t("common:actions.delete")}
          </Button>
        )}
      </Group>
    </Stack>
  );
}

/** 行上的取消 / 重试 / 删除; 点击不触发行展开. */
export function TaskRowActions({
  task,
  actions,
}: {
  task: TaskListItem;
  actions: TaskNodeActions;
}) {
  const { t } = useTranslation(["tasks", "common"]);
  const isTerminal = task.status === "done" || task.status === "failed";

  return (
    <Group
      gap={4}
      wrap="nowrap"
      onClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => e.stopPropagation()}
    >
      {(task.status === "queued" || task.status === "running") && (
        <HintedActionIcon
          variant="subtle"
          color="orange"
          loading={actions.pending}
          label={t("actions.cancelTask")}
          onClick={() => actions.onCancel(task.id)}
        >
          <IconPlayerStop size={16} />
        </HintedActionIcon>
      )}
      {task.status === "failed" && (
        <HintedActionIcon
          variant="subtle"
          loading={actions.pending}
          label={t("common:actions.retry")}
          onClick={() => actions.onRetry(task.id)}
        >
          <IconRefresh size={16} />
        </HintedActionIcon>
      )}
      {isTerminal && (
        <HintedActionIcon
          variant="subtle"
          color="red"
          loading={actions.pending}
          label={t("common:actions.delete")}
          onClick={() => void actions.onDelete(task.id)}
        >
          <IconTrash size={16} />
        </HintedActionIcon>
      )}
    </Group>
  );
}
