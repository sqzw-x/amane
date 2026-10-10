import { Alert, Badge, Group, Stack, Text, ThemeIcon } from "@mantine/core";
import {
  IconAlertCircle,
  IconAlertTriangle,
  IconCheck,
  IconDatabase,
  IconExternalLink,
  IconUser,
} from "@tabler/icons-react";
import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { OrganizeResult, SiteOutcomeKind, SiteOutcomeRecord } from "@/client/types.gen";
import { assertNever } from "@/lib/exhaustive";
import { SITE_OUTCOME_KINDS } from "@/lib/exhaustive-maps";
import type { TaskResult } from "@/lib/task/display";

interface TaskResultPanelProps {
  result: TaskResult | null;
  headline: string | null | undefined;
  failed: boolean;
}

export function TaskResultPanel({ result, headline, failed }: TaskResultPanelProps) {
  return (
    <Stack gap="md">
      <ResultHeadline headline={headline} failed={failed} />
      {result == null ? null : resultBranch(result, failed)}
    </Stack>
  );
}

/** 结果成员 → 面板分支: 新增成员时 `assertNever` 处编译报错. */
function resultBranch(result: TaskResult, failed: boolean): ReactNode {
  switch (result.type) {
    case "scrape": {
      const outcomes = result.outcomes ?? [];
      // 结果里的 id 可能是手写或坏行留下的 0 / 负数, 只给正数渲染链接.
      const metadataId =
        result.metadata_id != null && result.metadata_id > 0 ? result.metadata_id : null;
      return (
        <>
          <MetadataLink metadataId={metadataId} />
          <OutcomeGroups outcomes={outcomes} />
          <EmptyHint visible={!failed && metadataId == null && outcomes.length === 0} />
        </>
      );
    }
    case "actor_scrape": {
      const outcomes = result.outcomes ?? [];
      return (
        <>
          <ActorLink actorId={result.actor_id} />
          <OutcomeGroups outcomes={outcomes} />
          <EmptyHint visible={!failed && outcomes.length === 0} />
        </>
      );
    }
    case "organize": {
      const empty =
        result.organized === 0 &&
        result.skipped === 0 &&
        result.conflicted === 0 &&
        result.failed === 0;
      return (
        <>
          <OrganizeCounts result={result} />
          <OrganizeConflicts result={result} />
          <EmptyHint visible={!failed && empty} />
        </>
      );
    }
    case "emby_sync": {
      return <EmbySyncCounts result={result} />;
    }
    case "refresh":
    case "cleanup":
    case "upscale":
    case "r18_import":
    case "rescrape":
    case "scan_invalid":
    case "delete":
      return <EmptyHint visible={!failed} />;
    default:
      return assertNever(result, "TaskResult");
  }
}

function ResultHeadline({
  headline,
  failed,
}: {
  headline: string | null | undefined;
  failed: boolean;
}) {
  const { t } = useTranslation("tasks");

  if (headline == null) {
    return null;
  }

  return failed ? (
    <Alert
      color="red"
      variant="light"
      icon={<IconAlertCircle size={16} />}
      title={t("result.headline")}
    >
      <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
        {headline}
      </Text>
    </Alert>
  ) : (
    <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
      {headline}
    </Text>
  );
}

function MetadataLink({ metadataId }: { metadataId: number | null }) {
  const { t } = useTranslation("tasks");

  if (metadataId == null) {
    return null;
  }

  return (
    <Group gap="sm" wrap="nowrap">
      <ThemeIcon size="md" radius="md" variant="light" color="teal">
        <IconExternalLink size={14} />
      </ThemeIcon>
      <div style={{ minWidth: 0 }}>
        <Text size="xs" c="dimmed" lh={1.3}>
          {t("result.metadata")}
        </Text>
        <Link
          to="/meta/$metadataId"
          params={{ metadataId: String(metadataId) }}
          style={{ textDecoration: "none" }}
        >
          <Text component="span" size="sm" fw={600} c="var(--mantine-color-anchor)">
            {t("result.viewMetadata", { id: metadataId })}
          </Text>
        </Link>
      </div>
    </Group>
  );
}

function ActorLink({ actorId }: { actorId: number | null | undefined }) {
  const { t } = useTranslation("tasks");

  if (actorId == null) {
    return null;
  }

  return (
    <Group gap="sm" wrap="nowrap">
      <ThemeIcon size="md" radius="md" variant="light" color="teal">
        <IconUser size={14} />
      </ThemeIcon>
      <div style={{ minWidth: 0 }}>
        <Text size="xs" c="dimmed" lh={1.3}>
          {t("result.actor")}
        </Text>
        <Link
          to="/actors/$actorId"
          params={{ actorId: String(actorId) }}
          style={{ textDecoration: "none" }}
        >
          <Text component="span" size="sm" fw={600} c="var(--mantine-color-anchor)">
            {t("result.viewActor", { id: actorId })}
          </Text>
        </Link>
      </div>
    </Group>
  );
}

function EmptyHint({ visible }: { visible: boolean }) {
  const { t } = useTranslation("tasks");

  if (!visible) {
    return null;
  }

  return (
    <Text size="sm" c="dimmed">
      {t("result.successEmpty")}
    </Text>
  );
}

function EmbySyncCounts({ result }: { result: Extract<TaskResult, { type: "emby_sync" }> }) {
  const { t } = useTranslation("tasks");

  return (
    <Stack gap="xs">
      <Group gap="xs">
        <Badge color="gray" variant="light">
          {t("result.emby_sync.actors", { count: result.actors })}
        </Badge>
        {result.persons > 0 ? (
          <Badge color="blue" variant="light">
            {t("result.emby_sync.persons", { count: result.persons })}
          </Badge>
        ) : null}
        {result.images > 0 ? (
          <Badge color="teal" variant="light">
            {t("result.emby_sync.images", { count: result.images })}
          </Badge>
        ) : null}
        {result.updated > 0 ? (
          <Badge color="grape" variant="light">
            {t("result.emby_sync.updated", { count: result.updated })}
          </Badge>
        ) : null}
        {result.skipped > 0 ? (
          <Badge color="gray" variant="light">
            {t("result.emby_sync.skipped", { count: result.skipped })}
          </Badge>
        ) : null}
        {result.not_found > 0 ? (
          <Badge color="yellow" variant="light">
            {t("result.emby_sync.notFound", { count: result.not_found })}
          </Badge>
        ) : null}
        {result.no_image > 0 ? (
          <Badge color="yellow" variant="light">
            {t("result.emby_sync.noImage", { count: result.no_image })}
          </Badge>
        ) : null}
        {result.failed > 0 ? (
          <Badge color="red" variant="light">
            {t("result.emby_sync.failed", { count: result.failed })}
          </Badge>
        ) : null}
      </Group>
      {result.failures.length > 0 ? (
        <Stack gap={4}>
          <Text size="xs" fw={500}>
            {t("result.emby_sync.failures")}
          </Text>
          {result.failures.map((failure) => (
            <Text key={failure} size="xs" c="dimmed" style={{ whiteSpace: "pre-wrap" }}>
              {failure}
            </Text>
          ))}
        </Stack>
      ) : null}
    </Stack>
  );
}

function OrganizeCounts({ result }: { result: OrganizeResult }) {
  const { t } = useTranslation("tasks");

  return (
    <Group gap="xs">
      <Badge color="teal" variant="light">
        {t("result.organize.organized", { count: result.organized })}
      </Badge>
      {result.skipped > 0 ? (
        <Badge color="gray" variant="light">
          {t("result.organize.skipped", { count: result.skipped })}
        </Badge>
      ) : null}
      {result.conflicted > 0 ? (
        <Badge color="orange" variant="light">
          {t("result.organize.conflicted", { count: result.conflicted })}
        </Badge>
      ) : null}
      {result.failed > 0 ? (
        <Badge color="red" variant="light">
          {t("result.organize.failed", { count: result.failed })}
        </Badge>
      ) : null}
      {result.pruned_dirs > 0 ? (
        <Badge color="gray" variant="light">
          {t("result.organize.prunedDirs", { count: result.pruned_dirs })}
        </Badge>
      ) : null}
    </Group>
  );
}

function OrganizeConflicts({ result }: { result: OrganizeResult }) {
  const { t } = useTranslation("tasks");
  const conflicts = result.conflicts;

  if (conflicts.length === 0) {
    return null;
  }

  // 结果里的条目是 conflicted 计数的前缀, 差额只留在任务日志里.
  const dropped = result.conflicted - conflicts.length;

  return (
    <Stack gap={6}>
      <Group gap={6} wrap="nowrap">
        <ThemeIcon size={22} radius="sm" variant="light" color="orange">
          <IconAlertTriangle size={14} />
        </ThemeIcon>
        <Text size="sm" fw={600}>
          {t("result.organize.conflicts")}
        </Text>
      </Group>
      <Stack gap="xs">
        {conflicts.map((conflict) => (
          <div key={`${conflict.path}\u0000${conflict.target}`} style={{ minWidth: 0 }}>
            <Text size="xs" style={{ wordBreak: "break-all" }}>
              {conflict.path}
            </Text>
            <Text size="xs" c="dimmed" style={{ wordBreak: "break-all" }}>
              {conflict.target}
            </Text>
            <Text size="xs" c="orange">
              {t(`result.organize.reason.${conflict.reason}`)}
            </Text>
          </div>
        ))}
      </Stack>
      {dropped > 0 ? (
        <Text size="xs" c="dimmed">
          {t("result.organize.conflictsTruncated", { count: dropped })}
        </Text>
      ) : null}
    </Stack>
  );
}

function OutcomeGroups({ outcomes }: { outcomes: SiteOutcomeRecord[] }) {
  return (
    <>
      {groupByOutcome(outcomes).map(({ kind, rows }) => (
        <OutcomeGroup key={kind} kind={kind} rows={rows} />
      ))}
    </>
  );
}

function OutcomeGroup({ kind, rows }: { kind: SiteOutcomeKind; rows: SiteOutcomeRecord[] }) {
  const { t } = useTranslation("tasks");
  const color = outcomeColor(kind);

  return (
    <Stack gap={6}>
      <Group gap={6} wrap="nowrap">
        <ThemeIcon size={22} radius="sm" variant="light" color={color}>
          {outcomeIcon(kind)}
        </ThemeIcon>
        <Text size="sm" fw={600}>
          {t(`result.group.${kind}`)}
        </Text>
        <Text size="xs" c="dimmed">
          {rows.length}
        </Text>
      </Group>

      {kind === "failed" ? (
        <Stack gap={4}>
          {rows.map((row) => (
            <FailedSiteRow key={row.site} row={row} />
          ))}
        </Stack>
      ) : (
        <Group gap={6}>
          {rows.map((row) => (
            <Badge key={row.site} size="sm" variant="light" color={color} tt="none" radius="sm">
              {row.site}
            </Badge>
          ))}
        </Group>
      )}
    </Stack>
  );
}

function FailedSiteRow({ row }: { row: SiteOutcomeRecord }) {
  const { t } = useTranslation("tasks");
  // 原因/状态码/详情均为结构化字段, 不做任何文本解析.
  const reasonText = row.reason != null ? t(`reason.${row.reason}`) : null;
  const statusText = row.http_status != null ? `HTTP ${row.http_status}` : null;
  const primary = reasonText ?? statusText ?? row.detail ?? t("result.detail.unknown");
  const suffix = row.detail && (reasonText || statusText) ? ` · ${row.detail}` : "";

  return (
    <Group gap="xs" wrap="nowrap" align="baseline">
      <Text size="sm" ff="monospace" style={{ flexShrink: 0, minWidth: "4.5rem" }}>
        {row.site}
      </Text>
      <Text size="sm" c="dimmed" style={{ wordBreak: "break-word" }}>
        {primary}
        {suffix}
      </Text>
      {row.detail_truncated ? (
        <Badge size="xs" variant="light" color="gray" tt="none" style={{ flexShrink: 0 }}>
          {t("result.detailTruncated")}
        </Badge>
      ) : null}
    </Group>
  );
}

function groupByOutcome(
  outcomes: SiteOutcomeRecord[],
): { kind: SiteOutcomeKind; rows: SiteOutcomeRecord[] }[] {
  const buckets: Record<SiteOutcomeKind, SiteOutcomeRecord[]> = {
    failed: [],
    ok: [],
    cache_hit: [],
  };
  for (const row of outcomes) {
    buckets[row.outcome].push(row);
  }
  return SITE_OUTCOME_KINDS.filter((kind) => buckets[kind].length > 0).map((kind) => ({
    kind,
    rows: buckets[kind].toSorted((a, b) => a.site.localeCompare(b.site)),
  }));
}

function outcomeColor(outcome: SiteOutcomeKind): string {
  switch (outcome) {
    case "failed":
      return "red";
    case "ok":
      return "green";
    case "cache_hit":
      return "gray";
    default:
      return assertNever(outcome);
  }
}

function outcomeIcon(outcome: SiteOutcomeKind) {
  switch (outcome) {
    case "failed":
      return <IconAlertCircle size={12} />;
    case "ok":
      return <IconCheck size={12} />;
    case "cache_hit":
      return <IconDatabase size={12} />;
    default:
      return assertNever(outcome);
  }
}
