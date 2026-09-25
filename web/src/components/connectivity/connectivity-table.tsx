import { Badge, Group, Paper, Stack, Table, Text, Tooltip } from "@mantine/core";
import { IconRefresh } from "@tabler/icons-react";
import type { ParseKeys } from "i18next";
import { useTranslation } from "react-i18next";
import type { ConnectivityItemResponse, ConnectivityStatus, SourceKind } from "@/client/types.gen";
import { HintedActionIcon } from "@/components/common/hinted-action-icon";
import { exhaustiveRecord } from "@/lib/exhaustive";

/**
 * 结果排序权重: 不可访问优先, 其次无法探测, 最后可访问.
 * 同一权重内不比较, 依赖 `Array.prototype.toSorted` 的稳定排序保持后端的响应顺序.
 */
const STATUS_RANK = exhaustiveRecord<ConnectivityStatus>()({
  failed: 0,
  skipped: 1,
  ok: 2,
} as const);

const STATUS_COLOR = exhaustiveRecord<ConnectivityStatus>()({
  ok: "green",
  failed: "red",
  skipped: "gray",
} as const);

const STATUS_LABEL_KEY = exhaustiveRecord<ConnectivityStatus>()({
  ok: "status.ok",
  failed: "status.failed",
  skipped: "status.skipped",
} as const satisfies Record<ConnectivityStatus, ParseKeys<"connectivity">>);

const KIND_LABEL_KEY = exhaustiveRecord<SourceKind>()({
  film: "kind.film",
  actor: "kind.actor",
  plugin: "kind.plugin",
} as const satisfies Record<SourceKind, ParseKeys<"connectivity">>);

/** 固定表格布局下单元格裁剪的可见范围, 长名称与 url 否则会撑破列宽. */
const CELL_CLIP = { overflow: "hidden" } as const;

export interface ConnectivityTableProps {
  items: ConnectivityItemResponse[];
  /** 在途请求期间禁用全部重试按钮: 同一时刻只允许一次探测, 行状态才能与实际请求一一对应. */
  busy: boolean;
  retryingSourceId: string | null;
  onRetry: (sourceId: string) => void;
}

export function ConnectivityTable({
  items,
  busy,
  retryingSourceId,
  onRetry,
}: ConnectivityTableProps) {
  const { t } = useTranslation("connectivity");
  const sorted = items.toSorted(
    (left, right) => STATUS_RANK[left.status] - STATUS_RANK[right.status],
  );
  const failed = items.filter((item) => item.status === "failed").length;
  const skipped = items.filter((item) => item.status === "skipped").length;

  return (
    <Stack gap="sm">
      <Text size="sm" c="dimmed">
        {t("summary", { total: items.length, failed, skipped })}
      </Text>
      {/* 没有可探测的来源时只保留汇总行: 只有表头的空表不能说明任何结论. */}
      {items.length > 0 ? (
        <Paper withBorder>
          <Table highlightOnHover verticalSpacing="sm" layout="fixed" w="100%">
            <Table.Thead>
              <Table.Tr>
                <Table.Th w="38%">{t("columns.source")}</Table.Th>
                <Table.Th w={92}>{t("columns.status")}</Table.Th>
                <Table.Th>{t("columns.reason")}</Table.Th>
                <Table.Th w={80} visibleFrom="md">
                  {t("columns.elapsed")}
                </Table.Th>
                <Table.Th w={48} ta="right">
                  {t("columns.actions")}
                </Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {sorted.map((item) => (
                <ConnectivityRow
                  key={item.source_id}
                  item={item}
                  busy={busy}
                  retrying={retryingSourceId === item.source_id}
                  onRetry={onRetry}
                />
              ))}
            </Table.Tbody>
          </Table>
        </Paper>
      ) : null}
    </Stack>
  );
}

interface ConnectivityRowProps {
  item: ConnectivityItemResponse;
  busy: boolean;
  retrying: boolean;
  onRetry: (sourceId: string) => void;
}

function ConnectivityRow({ item, busy, retrying, onRetry }: ConnectivityRowProps) {
  const { t } = useTranslation("connectivity");

  return (
    <Table.Tr>
      <Table.Td style={CELL_CLIP}>
        <Stack gap={2}>
          <Text size="sm" fw={500} truncate>
            {item.name}
          </Text>
          <Group gap={6} wrap="nowrap">
            <Text size="xs" c="dimmed" ff="monospace" truncate>
              {item.source_id}
            </Text>
            <Badge size="xs" variant="light" color="gray" tt="none" visibleFrom="sm">
              {t(KIND_LABEL_KEY[item.kind])}
            </Badge>
          </Group>
          {/* 实际探测地址只在宽屏给出: 窄屏该列放不下, 且同来源的地址基本固定. */}
          {item.url != null ? (
            <Tooltip label={item.url} multiline maw={360}>
              <Text size="xs" c="dimmed" ff="monospace" truncate visibleFrom="md">
                {item.url}
              </Text>
            </Tooltip>
          ) : null}
        </Stack>
      </Table.Td>
      <Table.Td>
        <Badge size="sm" variant="light" color={STATUS_COLOR[item.status]} tt="none">
          {t(STATUS_LABEL_KEY[item.status])}
        </Badge>
      </Table.Td>
      <Table.Td>
        <ReasonCell item={item} />
      </Table.Td>
      <Table.Td visibleFrom="md">
        <Text size="sm" c="dimmed">
          {/* 未探测的来源耗时字段为 null: 显示 0 ms 会与「无法探测」互相矛盾. */}
          {item.elapsed_ms == null ? t("emptyValue") : t("elapsedMs", { value: item.elapsed_ms })}
        </Text>
      </Table.Td>
      <Table.Td ta="right">
        <HintedActionIcon
          variant="subtle"
          label={retrying ? t("retrying") : t("retry")}
          loading={retrying}
          disabled={busy}
          onClick={() => onRetry(item.source_id)}
        >
          <IconRefresh size={16} />
        </HintedActionIcon>
      </Table.Td>
    </Table.Tr>
  );
}

function ReasonCell({ item }: { item: ConnectivityItemResponse }) {
  const { t } = useTranslation("connectivity");
  const { t: tTasks } = useTranslation("tasks");
  // 原因与状态码均为结构化字段, 不解析文本; 失败原因文案复用任务报告 (tasks:report.reason.*).
  const reason = item.reason != null ? tTasks(`report.reason.${item.reason}`) : null;
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
