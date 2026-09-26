import { Button, Group, Paper, Skeleton, Stack, Text, ThemeIcon } from "@mantine/core";
import { useReducedMotion } from "@mantine/hooks";
import { IconAlertTriangle, IconFilterOff, IconPlugOff } from "@tabler/icons-react";
import { type ReactNode, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { NetworkCheckList } from "@/components/network-check/network-check-list";
import {
  countStatuses,
  STATUS_RANK,
  type StatusFilter,
} from "@/components/network-check/network-check-meta";
import { NetworkCheckSummary } from "@/components/network-check/network-check-summary";
import classes from "@/components/network-check/network-check.module.css";
import type { NetworkCheckReport } from "@/stores/network-check";

/** 骨架行的条数与身份无关, 只用来说明"这一块马上会变成结果列表". */
const SKELETON_ROWS = [0, 1, 2, 3] as const;

export interface NetworkCheckPanelProps {
  /** 尚未检测时为 null; 条目为空是「没有可探测的来源」, 与未检测区分. */
  report: NetworkCheckReport | null;
  checking: boolean;
  /** 全量检测或行内重试在途; 任一在途时不得再发起请求. */
  busy: boolean;
  /** 请求本身失败 (端点 4xx/5xx): 此时既没有结论也不会再自动重试, 要给出入口. */
  failed: boolean;
  retryingSourceId: string | null;
  onCheck: () => void;
  onRetry: (sourceId: string) => void;
}

/**
 * 结果面板: 汇总 / 列表 / 各种空态.
 * 筛选只在结果产生之后才有意义, 因此状态留在本组件 —— 它随结果一起出现, 也随结果一起消失.
 * 排序交给列表前的一次 `toSorted`, 与筛选同源, 保证「不可访问优先」在筛选后依然成立.
 */
export function NetworkCheckPanel({
  report,
  checking,
  busy,
  failed,
  retryingSourceId,
  onCheck,
  onRetry,
}: NetworkCheckPanelProps) {
  const { t } = useTranslation("networkCheck");
  const [filter, setFilter] = useState<StatusFilter | null>(null);

  const counts = useMemo(() => countStatuses(report?.items ?? []), [report]);
  const sorted = useMemo(
    () =>
      report?.items.toSorted(
        (left, right) => STATUS_RANK[left.status] - STATUS_RANK[right.status],
      ) ?? null,
    [report],
  );
  const visible = useMemo(
    () => sorted?.filter((item) => filter == null || item.status === filter) ?? null,
    [sorted, filter],
  );

  if (report == null || sorted == null || visible == null) {
    // 没有结论: 自动检测刚发起或正在跑时给加载中 (首屏就是它).
    if (!failed) {
      return <NetworkCheckRunning />;
    }
    // 请求本身失败: 与「没有可探测的来源」共用同一个占位, 差别只有文案与图标 —— 两者都是"这次没有结论,
    // 但还可以再试一次", 给两套外观只会让人以为是两类东西.
    return (
      <NetworkCheckPlaceholder
        icon={<IconAlertTriangle size={24} />}
        title={t("emptyFailed.title")}
        action={<NetworkCheckRetry busy={busy} onCheck={onCheck} />}
      />
    );
  }

  if (report.items.length === 0) {
    return (
      <NetworkCheckPlaceholder
        icon={<IconPlugOff size={24} />}
        title={t("emptySources.title")}
        action={<NetworkCheckRetry busy={busy} onCheck={onCheck} />}
      />
    );
  }

  return (
    <>
      <NetworkCheckSummary
        counts={counts}
        checkedAt={report.checkedAt}
        checking={checking}
        busy={busy}
        onCheck={onCheck}
        filter={filter}
        onFilterChange={(status) => setFilter((current) => (current === status ? null : status))}
      />

      {visible.length === 0 ? (
        <NetworkCheckFilterEmpty onClear={() => setFilter(null)} />
      ) : (
        <NetworkCheckList
          items={visible}
          busy={busy}
          retryingSourceId={retryingSourceId}
          onRetry={onRetry}
        />
      )}
    </>
  );
}

/**
 * 空态与故障态共用的占位: 图标 + 标题 + 一个出口.
 *
 * 两种情形的共同点是「这一次没有结论, 但还可以再来一次」, 所以共用同一套外观, 只有文案与图标不同 ——
 * 分开两套外观会让人把它们当成两类东西. 失败原因已经由 toast 报过, 这里不重复.
 */
function NetworkCheckPlaceholder({
  icon,
  title,
  action,
}: {
  icon: ReactNode;
  title: string;
  action: ReactNode;
}) {
  return (
    <Paper withBorder radius="lg" p="xl" className={classes.placeholder}>
      <Stack align="center" gap="xs">
        <ThemeIcon variant="light" color="gray" size={48} radius="xl">
          {icon}
        </ThemeIcon>
        <Text fw={600}>{title}</Text>
        {action}
      </Stack>
    </Paper>
  );
}

/** 空态与故障态唯一可做的事: 再跑一次. */
function NetworkCheckRetry({ busy, onCheck }: { busy: boolean; onCheck: () => void }) {
  const { t } = useTranslation("networkCheck");

  return (
    <Button variant="light" size="xs" loading={busy} disabled={busy} onClick={onCheck}>
      {t("rerun")}
    </Button>
  );
}

/**
 * 检测中且还没有结论: 转动的圈 + 骨架行给出结果的形状.
 * 圈只有环是主题蓝, 不铺底色 (铺底色会读成一块按钮 / 一张卡片); 文案不写"逐个" —— 探测是并发的.
 * 系统要求减少动态效果时不留 spinner, 只留这一行字: 旋转必须退化, 状态不能没有.
 */
function NetworkCheckRunning() {
  const { t } = useTranslation("networkCheck");
  const reducedMotion = useReducedMotion();

  return (
    <Stack gap="md">
      <div className={classes.stageBusy} aria-live="polite">
        {reducedMotion ? null : <div aria-hidden className={classes.spinner} />}
        <Text size="sm" c="dimmed">
          {t("running")}
        </Text>
      </div>
      {SKELETON_ROWS.map((row) => (
        <Paper key={row} withBorder radius="md" p="sm">
          <Group gap="md" wrap="nowrap">
            <Skeleton circle height={30} animate={!reducedMotion} />
            <Stack gap={6} style={{ flex: 1, minWidth: 0 }}>
              <Skeleton height={12} width="38%" animate={!reducedMotion} />
              <Skeleton height={10} width="65%" animate={!reducedMotion} />
            </Stack>
            <Skeleton height={20} width={84} radius="xl" animate={!reducedMotion} />
          </Group>
        </Paper>
      ))}
    </Stack>
  );
}

/** 筛选后为空: 结论里没有这一档, 不是加载失败, 所以给出回到全量的入口. */
function NetworkCheckFilterEmpty({ onClear }: { onClear: () => void }) {
  const { t } = useTranslation("networkCheck");

  return (
    <NetworkCheckPlaceholder
      icon={<IconFilterOff size={24} />}
      title={t("emptyFilter.title")}
      action={
        <Button variant="subtle" size="xs" onClick={onClear}>
          {t("emptyFilter.clear")}
        </Button>
      }
    />
  );
}
