import { Box, Group, Text, Tooltip } from "@mantine/core";
import {
  IconArrowDown,
  IconArrowUp,
  IconClock,
  IconDatabaseImport,
  IconDatabaseExport,
  IconPercentage,
  IconRepeat,
} from "@tabler/icons-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { RequestTokenUsage, TurnTokenUsage } from "@/client/types.gen";

function formatCount(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 10_000) return `${Math.round(n / 1000)}k`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

function formatHitRate(rate: number): string {
  if (rate >= 99.95) return "100%";
  if (rate < 0.05) return "0%";
  if (rate >= 10) return `${Math.round(rate)}%`;
  return `${rate.toFixed(1)}%`;
}

/** 秒以下保持毫秒: 短请求的差别在毫秒级. */
function formatDuration(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
}

type MetricKey = "requests" | "input" | "cacheRead" | "cacheWrite" | "output" | "duration";

type Metric = {
  key: MetricKey;
  value: number;
  color: string;
  Icon: typeof IconArrowDown;
};

/** 用量只在悬浮说明里给全: 芯片本身只放缩写数量. */
function MetricChip({
  label,
  display,
  exact,
  color,
  Icon,
  suffix,
}: {
  label: string;
  display: string;
  exact: string;
  color: string;
  Icon: typeof IconArrowDown;
  suffix?: ReactNode;
}) {
  return (
    <Tooltip label={`${label}: ${exact}`} withArrow openDelay={300}>
      <Group
        gap={4}
        wrap="nowrap"
        px={6}
        py={2}
        style={{
          borderRadius: 999,
          background: `color-mix(in srgb, var(--mantine-color-${color}-light) 70%, transparent)`,
          border: `1px solid color-mix(in srgb, var(--mantine-color-${color}-outline) 35%, transparent)`,
        }}
      >
        <Box c={color} style={{ display: "flex", lineHeight: 0 }}>
          <Icon size={11} stroke={1.75} />
        </Box>
        <Text size="xs" c="dimmed" ff="monospace" style={{ lineHeight: 1.2 }}>
          {display}
        </Text>
        {suffix}
      </Group>
    </Tooltip>
  );
}

/** 单次请求的用量: 跟在这次请求产出的内容之后, 只有 0 值的项不占位. */
export function RequestUsageBar({ usage }: { usage: RequestTokenUsage }) {
  const { t } = useTranslation("agent");
  const metrics: Metric[] = [
    { key: "input", value: usage.input, color: "blue", Icon: IconArrowDown },
    { key: "cacheRead", value: usage.cache_read, color: "teal", Icon: IconDatabaseImport },
    { key: "cacheWrite", value: usage.cache_write, color: "violet", Icon: IconDatabaseExport },
    { key: "output", value: usage.output, color: "orange", Icon: IconArrowUp },
  ];
  const visible = metrics.filter((m) => m.value > 0);
  const duration = usage.duration_ms ?? undefined;

  if (duration === undefined && visible.length === 0) return null;

  return (
    <Group gap={4} wrap="wrap" align="center" mt={2} mb="sm">
      {visible.map((m) => (
        <MetricChip
          key={m.key}
          label={t(`usage.${m.key}`)}
          display={formatCount(m.value)}
          exact={m.value.toLocaleString()}
          color={m.color}
          Icon={m.Icon}
        />
      ))}
      {duration !== undefined && (
        <MetricChip
          label={t("usage.duration")}
          display={formatDuration(duration)}
          exact={`${duration.toLocaleString()} ms`}
          color="gray"
          Icon={IconClock}
        />
      )}
    </Group>
  );
}

/** 回合汇总: 整轮总量与缓存命中率; 逐请求明细在对话流里各自成条. */
export function TokenUsageBar({ usage }: { usage: TurnTokenUsage }) {
  const { t } = useTranslation("agent");
  const metrics: Metric[] = [
    { key: "requests", value: usage.requests, color: "gray", Icon: IconRepeat },
    { key: "input", value: usage.input, color: "blue", Icon: IconArrowDown },
    { key: "cacheRead", value: usage.cache_read, color: "teal", Icon: IconDatabaseImport },
    { key: "cacheWrite", value: usage.cache_write, color: "violet", Icon: IconDatabaseExport },
    { key: "output", value: usage.output, color: "orange", Icon: IconArrowUp },
  ];

  const visible = metrics.filter((m) => m.value > 0);
  const promptTokens = usage.input + usage.cache_read + usage.cache_write;
  const hitRate = promptTokens > 0 ? (usage.cache_read / promptTokens) * 100 : null;

  if (visible.length === 0 && hitRate == null) return null;

  return (
    <Group gap={4} wrap="wrap" align="center" mt={2}>
      {visible.map((m) => (
        <MetricChip
          key={m.key}
          label={t(`usage.${m.key}`)}
          display={formatCount(m.value)}
          exact={m.value.toLocaleString()}
          color={m.color}
          Icon={m.Icon}
        />
      ))}
      {hitRate != null && (
        <MetricChip
          label={t("usage.cacheHit")}
          display={formatHitRate(hitRate)}
          exact={`${hitRate.toFixed(1)}% (${usage.cache_read.toLocaleString()} / ${promptTokens.toLocaleString()})`}
          color="cyan"
          Icon={IconPercentage}
        />
      )}
    </Group>
  );
}
