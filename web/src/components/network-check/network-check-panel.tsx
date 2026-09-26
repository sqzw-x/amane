import { Button, Group, Paper, Skeleton, Stack, Text, ThemeIcon } from "@mantine/core";
import { useReducedMotion } from "@mantine/hooks";
import { IconFilterOff, IconPlugOff, IconWorldSearch } from "@tabler/icons-react";
import { useMemo, useState } from "react";
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
  retryingSourceId,
  onCheck,
  onRetry,
}: NetworkCheckPanelProps) {
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
    return checking ? <NetworkCheckRunning /> : <NetworkCheckStart onCheck={onCheck} busy={busy} />;
  }

  if (report.items.length === 0) {
    return <NetworkCheckNoSources />;
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
 * 未检测: 页面此刻只有一件事可做, 所以只呈现那件事 —— 上三分之一的圆形按钮, 没有卡片框, 也没有字.
 * 圆 + 无文字让点击目标落在视线起点上, 名字只留 `aria-label` (读屏要, 屏幕上看不到).
 */
function NetworkCheckStart({ onCheck, busy }: { onCheck: () => void; busy: boolean }) {
  const { t } = useTranslation("networkCheck");

  return (
    <div className={classes.stageIdle}>
      <Button
        className={classes.startButton}
        aria-label={t("run")}
        disabled={busy}
        h={52}
        miw={52}
        onClick={onCheck}
        p={0}
        radius={9999}
        variant="light"
        w={52}
      >
        <IconWorldSearch size={22} />
      </Button>
    </div>
  );
}

/**
 * 检测中且还没有结论: 开始按钮原地变成一个转动的圈, 下面接着骨架行给出结果的形状.
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

/** 配置里没有任何可探测的来源: 这是配置问题, 不是筛选结果为空, 所以说明白而不是劝人再点一次. */
function NetworkCheckNoSources() {
  const { t } = useTranslation("networkCheck");

  return (
    <Paper withBorder radius="lg" p="xl" className={classes.placeholder}>
      <Stack align="center" gap="xs">
        <ThemeIcon variant="light" color="gray" size={48} radius="xl">
          <IconPlugOff size={24} />
        </ThemeIcon>
        <Text fw={600}>{t("emptySources.title")}</Text>
      </Stack>
    </Paper>
  );
}

/** 筛选后为空: 结论里没有这一档, 不是加载失败, 所以给出回到全量的入口. */
function NetworkCheckFilterEmpty({ onClear }: { onClear: () => void }) {
  const { t } = useTranslation("networkCheck");

  return (
    <Paper withBorder radius="lg" p="lg" className={classes.placeholder}>
      <Stack align="center" gap="xs">
        <ThemeIcon variant="light" color="gray" size={40} radius="xl">
          <IconFilterOff size={20} />
        </ThemeIcon>
        <Text size="sm" fw={500}>
          {t("emptyFilter.title")}
        </Text>
        <Button variant="subtle" size="xs" onClick={onClear}>
          {t("emptyFilter.clear")}
        </Button>
      </Stack>
    </Paper>
  );
}
