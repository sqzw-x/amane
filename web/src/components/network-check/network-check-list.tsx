import { Badge, Group, Paper, Stack, Table, Text } from "@mantine/core";
import { IconRefresh } from "@tabler/icons-react";
import { useTranslation } from "react-i18next";
import type { ConnectivityItemResponse, ConnectivityStatus } from "@/client/types.gen";
import { HintedActionIcon } from "@/components/common/hinted-action-icon";
import {
  KIND_LABEL_KEY,
  SKIP_LABEL_KEY,
  STATUS_COLOR,
  STATUS_ICON,
  STATUS_LABEL_KEY,
} from "@/components/network-check/network-check-meta";
import classes from "@/components/network-check/network-check.module.css";
import { useNarrowViewport } from "@/hooks/use-narrow-viewport";

/** 固定表格布局下单元格裁剪的可见范围, 长名称与 url 否则会撑破列宽. */
const CELL_CLIP = { overflow: "hidden" } as const;

/**
 * 行进入的错峰间隔与上限.
 * 上限让来源很多时最后一行也不会等太久 —— 错峰只用来让列表"长出来", 不是排队演出.
 */
const STAGGER_STEP_MS = 28;
const STAGGER_MAX_STEPS = 10;

function staggerDelay(index: number): string {
  return `${Math.min(index, STAGGER_MAX_STEPS) * STAGGER_STEP_MS}ms`;
}

export interface NetworkCheckListProps {
  /** 已按「不可访问优先」排序并应用筛选的结果. */
  items: ConnectivityItemResponse[];
  /** 在途请求期间禁用全部重试按钮: 同一时刻只允许一次探测, 行状态才能与实际请求一一对应. */
  busy: boolean;
  retryingSourceId: string | null;
  onRetry: (sourceId: string) => void;
}

/**
 * 结果列表.
 * md 以上是表格 (横向比对耗时与原因更省事), md 以下是卡片 (表格列在窄屏只能靠隐藏收敛,
 * 隐藏掉的正是耗时与原因). 两套 DOM 不同时存在, 判定只此一处, 与 `hiddenFrom="md"` 同断点.
 */
export function NetworkCheckList({
  items,
  busy,
  retryingSourceId,
  onRetry,
}: NetworkCheckListProps) {
  const { t } = useTranslation("networkCheck");
  const narrow = useNarrowViewport("md");

  if (narrow) {
    return (
      <Stack gap="sm">
        {items.map((item, index) => (
          <Paper
            key={item.source_id}
            withBorder
            radius="md"
            p="sm"
            className={classes.card}
            data-status={item.status}
            data-retrying={retryingSourceId === item.source_id}
            style={{ animationDelay: staggerDelay(index) }}
          >
            <Group justify="space-between" align="flex-start" gap="sm" wrap="nowrap">
              <SourceIdentity item={item} showUrl={false} />
              <RetryButton
                item={item}
                busy={busy}
                retrying={retryingSourceId === item.source_id}
                onRetry={onRetry}
              />
            </Group>
            {/* 卡片上耗时靠右: 贴着状态徽章读起来像第四个并列字段, 分开才看得出它是同一行结论的量. */}
            <Group gap="sm" align="center" wrap="nowrap">
              <StatusBadge status={item.status} />
              <Text size="xs" c="dimmed" ml="auto">
                <ElapsedLabel item={item} />
              </Text>
            </Group>
            <ReasonCell item={item} />
          </Paper>
        ))}
      </Stack>
    );
  }

  return (
    <Paper withBorder radius="lg" style={CELL_CLIP}>
      <Table verticalSpacing="sm" horizontalSpacing="md" layout="fixed" w="100%">
        <Table.Thead>
          <Table.Tr>
            <Table.Th w="34%">{t("columns.source")}</Table.Th>
            <Table.Th w={110}>{t("columns.status")}</Table.Th>
            <Table.Th>{t("columns.reason")}</Table.Th>
            <Table.Th w={90} ta="right">
              {t("columns.elapsed")}
            </Table.Th>
            <Table.Th w={80} ta="right">
              {t("columns.actions")}
            </Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {items.map((item, index) => (
            <NetworkCheckRow
              key={item.source_id}
              item={item}
              index={index}
              busy={busy}
              retrying={retryingSourceId === item.source_id}
              onRetry={onRetry}
            />
          ))}
        </Table.Tbody>
      </Table>
    </Paper>
  );
}

interface NetworkCheckRowProps {
  item: ConnectivityItemResponse;
  index: number;
  busy: boolean;
  retrying: boolean;
  onRetry: (sourceId: string) => void;
}

function NetworkCheckRow({ item, index, busy, retrying, onRetry }: NetworkCheckRowProps) {
  return (
    <Table.Tr
      className={classes.row}
      data-status={item.status}
      data-retrying={retrying}
      style={{ animationDelay: staggerDelay(index) }}
    >
      <Table.Td className={classes.cellSource} style={CELL_CLIP}>
        <SourceIdentity item={item} showUrl />
      </Table.Td>
      <Table.Td>
        <StatusBadge status={item.status} />
      </Table.Td>
      <Table.Td>
        <ReasonCell item={item} />
      </Table.Td>
      <Table.Td ta="right">
        <Text size="sm" c="dimmed">
          <ElapsedLabel item={item} />
        </Text>
      </Table.Td>
      <Table.Td ta="right">
        <RetryButton item={item} busy={busy} retrying={retrying} onRetry={onRetry} />
      </Table.Td>
    </Table.Tr>
  );
}

/**
 * 来源身份, 按信息主次排三层: 名称 → (仅当与名称不同时的) source_id → 探测地址.
 * 多数来源的 id 就是名称, 重复显示一次只会让每行都多一行噪音; 类型徽章跟名称同行, 它是对名称的归类.
 */
function SourceIdentity({ item, showUrl }: { item: ConnectivityItemResponse; showUrl: boolean }) {
  const { t } = useTranslation("networkCheck");

  return (
    <Stack gap={2} style={{ minWidth: 0 }}>
      <Group gap={6} wrap="nowrap">
        <Text size="sm" fw={500} truncate>
          {item.name}
        </Text>
        <Badge size="xs" variant="light" color="gray" tt="none">
          {t(KIND_LABEL_KEY[item.kind])}
        </Badge>
      </Group>
      {item.source_id !== item.name ? (
        <Text size="xs" c="dimmed" ff="monospace" truncate className={classes.sourceId}>
          {item.source_id}
        </Text>
      ) : null}
      {showUrl && item.url != null ? (
        <Text size="xs" c="dimmed" ff="monospace" truncate className={classes.sourceUrl}>
          {item.url}
        </Text>
      ) : null}
    </Stack>
  );
}

function StatusBadge({ status }: { status: ConnectivityStatus }) {
  const { t } = useTranslation("networkCheck");
  const StatusIcon = STATUS_ICON[status];

  return (
    <Badge
      className={classes.statusBadge}
      size="sm"
      variant="light"
      color={STATUS_COLOR[status]}
      tt="none"
      leftSection={<StatusIcon size={12} />}
    >
      {t(STATUS_LABEL_KEY[status])}
    </Badge>
  );
}

interface RetryButtonProps {
  item: ConnectivityItemResponse;
  busy: boolean;
  retrying: boolean;
  onRetry: (sourceId: string) => void;
}

/**
 * 行内重试.
 * 不使用 `loading` 属性: 它会把子节点换成 Mantine 的 loader, 重试中的行就只剩一个转圈,
 * 与其它行的图标无法对照. 这里改成图标自转 (CSS, 受 reduce 守卫) 并显式标注 aria-busy.
 */
function RetryButton({ item, busy, retrying, onRetry }: RetryButtonProps) {
  const { t } = useTranslation("networkCheck");

  return (
    <HintedActionIcon
      className={classes.retry}
      variant="subtle"
      data-retrying={retrying}
      aria-busy={retrying}
      label={retrying ? t("retrying") : t("retry")}
      disabled={busy}
      onClick={() => onRetry(item.source_id)}
    >
      <span className={classes.retryIcon}>
        <IconRefresh size={16} />
      </span>
    </HintedActionIcon>
  );
}

function ElapsedLabel({ item }: { item: ConnectivityItemResponse }) {
  const { t } = useTranslation("networkCheck");
  // 未探测的来源耗时字段为 null: 显示 0 ms 会与「无法探测」互相矛盾.
  return (
    <>{item.elapsed_ms == null ? t("emptyValue") : t("elapsedMs", { value: item.elapsed_ms })}</>
  );
}

function ReasonCell({ item }: { item: ConnectivityItemResponse }) {
  const { t } = useTranslation("networkCheck");
  const { t: tTasks } = useTranslation("tasks");
  // 原因与状态码均为结构化字段, 不解析文本; 失败原因文案复用任务报告 (tasks:report.reason.*),
  // 未探测的原因只在本页 (networkCheck:skip.*) — 它在任务报告里没有对应语义.
  const reason =
    item.reason != null
      ? tTasks(`report.reason.${item.reason}`)
      : item.skip_reason != null
        ? t(SKIP_LABEL_KEY[item.skip_reason])
        : null;
  const httpStatus = item.http_status != null ? `HTTP ${item.http_status}` : null;

  return (
    <Stack gap={2}>
      {reason != null || httpStatus != null ? (
        <Group gap={6} wrap="nowrap">
          {reason != null ? <Text size="sm">{reason}</Text> : null}
          {httpStatus != null ? (
            <Text size="xs" c="dimmed">
              {httpStatus}
            </Text>
          ) : null}
        </Group>
      ) : null}
      {item.detail != null ? (
        <Text size="xs" c="dimmed" style={{ wordBreak: "break-word" }}>
          {item.detail}
        </Text>
      ) : null}
      {reason == null && httpStatus == null && item.detail == null ? (
        <Text size="sm" c="dimmed">
          {t("emptyValue")}
        </Text>
      ) : null}
    </Stack>
  );
}
