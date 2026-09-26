import {
  Button,
  Group,
  Paper,
  Progress,
  RingProgress,
  Stack,
  Text,
  UnstyledButton,
} from "@mantine/core";
import { useReducedMotion } from "@mantine/hooks";
import { IconWorldSearch } from "@tabler/icons-react";
import { useTranslation } from "react-i18next";
import {
  SUMMARY_STATUSES,
  STATUS_COLOR,
  STATUS_ICON,
  STATUS_LABEL_KEY,
  type StatusCounts,
  type StatusFilter,
} from "@/components/network-check/network-check-meta";
import classes from "@/components/network-check/network-check.module.css";

interface NetworkCheckSummaryProps {
  counts: StatusCounts;
  /** 该次检测完成的时间戳 (毫秒); 汇总只在结果存在时渲染, 因此不必考虑未检测. */
  checkedAt: number;
  checking: boolean;
  busy: boolean;
  onCheck: () => void;
  /** 当前筛选状态; null 表示不筛选. */
  filter: StatusFilter | null;
  onFilterChange: (status: StatusFilter) => void;
}

/**
 * 结果汇总.
 * 圆环给整体印象, 三块数字给可点的入口 (点击即筛选, 再点取消) —— 数字块既是统计也是筛选器,
 * 因此筛选态只影响下方的列表, 不在这里重复显示.
 */
export function NetworkCheckSummary({
  counts,
  checkedAt,
  checking,
  busy,
  onCheck,
  filter,
  onFilterChange,
}: NetworkCheckSummaryProps) {
  const { t } = useTranslation("networkCheck");
  // Mantine 的条纹进度与 loader 是 JS 驱动的动画, CSS media query 管不到, 只能显式关闭.
  const reducedMotion = useReducedMotion();
  const ratio = counts.total === 0 ? 0 : Math.round((counts.ok / counts.total) * 100);
  // 圆环只画有来源的状态: 传 0 的段会留下 0 长度的曲线, 既无信息也让圆角端点叠成一坨.
  const sections = SUMMARY_STATUSES.filter((status) => counts[status] > 0).map((status) => ({
    value: (counts[status] / counts.total) * 100,
    color: STATUS_COLOR[status],
  }));

  return (
    <Paper withBorder radius="lg" p="md" className={classes.summary}>
      <div className={classes.summaryBody}>
        <RingProgress
          className={classes.ring}
          data-checking={checking}
          size={104}
          thickness={10}
          roundCaps
          transitionDuration={reducedMotion ? 0 : 400}
          sections={sections}
          role="img"
          aria-label={t("summary.ratioAria", { value: ratio })}
          label={
            <Stack gap={0} align="center">
              <Text className={classes.ratioValue} fw={700} size="lg" lh={1.1}>
                {t("summary.ratio", { value: ratio })}
              </Text>
              <Text size="xs" c="dimmed" lh={1.2}>
                {t("summary.ratioLabel")}
              </Text>
            </Stack>
          }
        />

        <div className={classes.stats} aria-live="polite">
          {SUMMARY_STATUSES.map((status) => {
            const StatusIcon = STATUS_ICON[status];
            const active = filter === status;
            return (
              <UnstyledButton
                key={status}
                className={classes.stat}
                data-status={status}
                data-active={active}
                aria-pressed={active}
                onClick={() => onFilterChange(status)}
              >
                <Group className={classes.statHead} gap={6} wrap="nowrap">
                  <StatusIcon size={14} />
                  <Text size="xs" fw={500} truncate>
                    {t(STATUS_LABEL_KEY[status])}
                  </Text>
                </Group>
                <Text className={classes.statValue} data-zero={counts[status] === 0}>
                  {counts[status]}
                </Text>
              </UnstyledButton>
            );
          })}
        </div>
      </div>

      <Group justify="space-between" align="center" gap="sm" mt="sm" wrap="wrap">
        <Text size="xs" c="dimmed">
          {t("lastChecked", { time: new Date(checkedAt).toLocaleString() })}
          {" · "}
          {t("summary.total", { total: counts.total })}
        </Text>
        {/* 唯一的检测入口: 挨着「上次检测」, 因为再跑一次总是相对上一次结论说的. */}
        <Button
          size="xs"
          variant="light"
          leftSection={<IconWorldSearch size={14} />}
          loading={checking && !reducedMotion}
          disabled={busy}
          onClick={onCheck}
        >
          {checking ? t("running") : t("rerun")}
        </Button>
      </Group>

      {/* 重新检测时保留上一次结论: 结论不清空, 只用条纹进度说明新的一轮还在跑. */}
      {checking ? (
        <Progress
          mt="sm"
          size="xs"
          radius="xl"
          value={100}
          striped
          animated={!reducedMotion}
          transitionDuration={200}
        />
      ) : null}
    </Paper>
  );
}
