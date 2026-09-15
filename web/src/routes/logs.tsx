import {
  Badge,
  Button,
  Group,
  Stack,
  Switch,
  Text,
  TextInput,
  Title,
  Tooltip,
} from "@mantine/core";
import { IconAlertCircle, IconInfoCircle, IconSearch, IconTrash } from "@tabler/icons-react";
import { createFileRoute } from "@tanstack/react-router";
import type { ParseKeys } from "i18next";
import { filter, LiqeError, parse } from "liqe";
import { useMemo, useRef, useState, type RefObject } from "react";
import { useTranslation } from "react-i18next";
import { Virtuoso, type VirtuosoHandle } from "react-virtuoso";
import { APP_SHELL_MAIN_HEIGHT } from "@/components/layout/app-shell-metrics";
import { exhaustiveRecord } from "@/lib/exhaustive";
import { useNarrowViewport } from "@/hooks/use-narrow-viewport";
import { LogKvPairs } from "@/components/log/log-kv";
import { LogTimestamp } from "@/components/log/log-timestamp";
import {
  LOG_LEVELS,
  type LogEntry,
  type LogLevel,
  logLevelMantineColor,
  useLogStore,
} from "@/stores/logs";
import { useUIStore } from "@/stores/ui";

export const Route = createFileRoute("/logs")({ component: LogsPage });

/** 进入页 / 打开自动滚动: 滚到 scroller 真正的底, 而不是把末项 align end (单行会裁掉下边距). */
function pinLogList(ref: RefObject<VirtuosoHandle | null>) {
  ref.current?.scrollTo({ top: Number.MAX_SAFE_INTEGER });
}

function LogListFooter() {
  return <div style={{ height: 8 }} />;
}

const LOG_VIRTUOSO_COMPONENTS = { Footer: LogListFooter };

const LEVEL_I18N_KEY = exhaustiveRecord<LogLevel>()({
  DEBUG: "logs:levels.debug",
  INFO: "logs:levels.info",
  WARNING: "logs:levels.warn",
  ERROR: "logs:levels.error",
  CRITICAL: "logs:levels.critical",
} as const satisfies Record<LogLevel, ParseKeys<["logs", "common"]>>);

function matchesText(entry: LogEntry, query: string): boolean {
  try {
    const ast = parse(query);
    return filter(ast, [entry]).length > 0;
  } catch (err) {
    if (err instanceof LiqeError) {
      const needle = query.toLowerCase();
      return (
        entry.message.toLowerCase().includes(needle) || entry.source.toLowerCase().includes(needle)
      );
    }
    throw err;
  }
}

function renderLogRow(_index: number, entry: LogEntry) {
  return <LogRow entry={entry} />;
}

/**
 * 判定「已滚到底」的余量.
 * 宽屏行高约 40px, 200px 相当于未滑过数行仍判定在底部;
 * 窄屏行高随消息换行涨到 100px 以上, 沿用该值会使上滑阅读被 `followOutput` 重新滚到底端.
 */
const AT_BOTTOM_THRESHOLD = 200;
const NARROW_AT_BOTTOM_THRESHOLD = 40;

function LogVirtuoso({
  data,
  autoScroll,
  virtuosoRef,
}: {
  data: LogEntry[];
  autoScroll: boolean;
  virtuosoRef: RefObject<VirtuosoHandle | null>;
}) {
  const narrow = useNarrowViewport();
  const didPinBottom = useRef(false);
  return (
    <Virtuoso
      ref={virtuosoRef}
      style={{ height: "100%" }}
      data={data}
      atBottomThreshold={narrow ? NARROW_AT_BOTTOM_THRESHOLD : AT_BOTTOM_THRESHOLD}
      initialTopMostItemIndex={data.length - 1}
      followOutput={autoScroll ? () => "auto" : false}
      components={LOG_VIRTUOSO_COMPONENTS}
      itemsRendered={() => {
        if (didPinBottom.current) return;
        didPinBottom.current = true;
        pinLogList(virtuosoRef);
      }}
      itemContent={renderLogRow}
    />
  );
}

function LogRow({ entry }: { entry: LogEntry }) {
  return (
    <Group
      gap="sm"
      wrap="nowrap"
      px="sm"
      py={6}
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
      <Text
        size="xs"
        c="dimmed"
        ff="monospace"
        style={{ flexShrink: 0, minWidth: 90 }}
        lineClamp={1}
        visibleFrom="sm"
      >
        {entry.source}
      </Text>
      <Text size="sm" style={{ flex: 1, minWidth: 0, wordBreak: "break-word" }}>
        <Text component="span" fw={700}>
          {entry.message}
        </Text>
        <LogKvPairs entry={entry} />
      </Text>
    </Group>
  );
}

function LogsPage() {
  const { t } = useTranslation(["logs", "common"]);
  const entries = useLogStore((s) => s.entries);
  const clear = useLogStore((s) => s.clear);
  const autoScroll = useUIStore((s) => s.autoScroll);
  const setAutoScroll = useUIStore((s) => s.setAutoScroll);
  const levelFilter = useUIStore((s) => s.logLevelFilter);
  const setLevelFilter = useUIStore((s) => s.setLogLevelFilter);

  const [query, setQuery] = useState("");
  const virtuosoRef = useRef<VirtuosoHandle>(null);

  function toggleLevel(level: LogLevel) {
    setLevelFilter(
      levelFilter.includes(level)
        ? levelFilter.filter((l) => l !== level)
        : [...levelFilter, level],
    );
  }

  const { items: filtered, error: queryError } = useMemo(() => {
    const byLevel =
      levelFilter.length === 0 ? entries : entries.filter((e) => levelFilter.includes(e.level));
    const q = query.trim();
    if (!q) return { items: byLevel, error: null };
    try {
      parse(q);
    } catch (err) {
      return {
        items: byLevel,
        error: err instanceof LiqeError ? err.message : t("common:status.error"),
      };
    }
    return { items: byLevel.filter((e) => matchesText(e, q)), error: null };
  }, [entries, levelFilter, query, t]);

  function handleAutoScrollChange(checked: boolean) {
    setAutoScroll(checked);
    if (checked) pinLogList(virtuosoRef);
  }

  return (
    <Stack gap="md" style={{ height: APP_SHELL_MAIN_HEIGHT, overflow: "hidden" }}>
      <Group justify="space-between" wrap="wrap" style={{ flexShrink: 0 }}>
        <Title order={2}>{t("title")}</Title>
        <Group gap="xs">
          <Switch
            label={t("actions.autoScroll")}
            checked={autoScroll}
            onChange={(e) => handleAutoScrollChange(e.currentTarget.checked)}
          />
          <Button
            size="xs"
            variant="light"
            color="red"
            leftSection={<IconTrash size={14} />}
            onClick={clear}
          >
            {t("actions.clear")}
          </Button>
        </Group>
      </Group>

      <Group gap="xs" style={{ flexShrink: 0 }}>
        <TextInput
          flex={1}
          leftSection={<IconSearch size={16} />}
          placeholder={t("searchPlaceholder")}
          value={query}
          onChange={(e) => setQuery(e.currentTarget.value)}
          error={queryError}
          rightSection={
            <Tooltip label={t("queryHelp.fallback")} multiline w={240}>
              <IconInfoCircle size={16} style={{ opacity: 0.6 }} />
            </Tooltip>
          }
        />
      </Group>

      <Group gap="xs" style={{ flexShrink: 0 }}>
        {LOG_LEVELS.map((level) => (
          <Button
            key={level}
            size="xs"
            variant={levelFilter.includes(level) ? "filled" : "light"}
            color={logLevelMantineColor(level)}
            onClick={() => toggleLevel(level)}
          >
            {t(LEVEL_I18N_KEY[level])}
          </Button>
        ))}
      </Group>

      <div
        style={{
          flex: 1,
          minHeight: 0,
          border: "1px solid var(--mantine-color-default-border)",
          borderRadius: "var(--mantine-radius-md)",
        }}
      >
        {filtered.length === 0 ? (
          <Group justify="center" py="xl">
            <IconAlertCircle size={16} style={{ opacity: 0.5 }} />
            <Text c="dimmed" size="sm">
              {entries.length === 0 ? t("empty") : t("noMatch")}
            </Text>
          </Group>
        ) : (
          <LogVirtuoso data={filtered} autoScroll={autoScroll} virtuosoRef={virtuosoRef} />
        )}
      </div>
    </Stack>
  );
}
