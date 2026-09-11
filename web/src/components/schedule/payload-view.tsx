import { Badge, Group, Stack, Text } from "@mantine/core";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import type { RescrapeTarget, RoutineType, ScheduleResponse } from "@/client/types.gen";
import { assertNever } from "@/lib/exhaustive";

type Translate = TFunction<["schedules", "tasks"]>;
type SchedulePayload = ScheduleResponse["payload"];

function typeLabel(t: Translate, type: RoutineType): string {
  return t(`tasks:filters.${type}`);
}

function targetLabel(t: Translate, target: RescrapeTarget): string {
  switch (target) {
    case "metadata":
      return t("tasks:submit.rescrape.targets.$.options.metadata");
    case "actor":
      return t("tasks:submit.rescrape.targets.$.options.actor");
    default:
      return assertNever(target, "RescrapeTarget");
  }
}

function yesNo(t: Translate, value: boolean): string {
  return value ? t("schedules:payload.yes") : t("schedules:payload.no");
}

function formatNumber(value: number): string {
  return value.toLocaleString();
}

function summaryParts(payload: SchedulePayload, t: Translate): string[] {
  switch (payload.type) {
    case "cleanup": {
      const parts: string[] = [];
      if (payload.remove_missing_files ?? true) {
        parts.push(t("tasks:submit.cleanup.remove_missing_files.label"));
      }
      if (payload.remove_unreferenced_resources ?? true) {
        parts.push(t("tasks:submit.cleanup.remove_unreferenced_resources.label"));
      }
      return parts.length > 0 ? parts : [t("schedules:payload.cleanup.none")];
    }
    case "upscale": {
      const parts: string[] = [];
      if (payload.max_dim_threshold != null) {
        parts.push(
          t("schedules:payload.upscale.maxDim", {
            value: formatNumber(payload.max_dim_threshold),
          }),
        );
      }
      if (payload.max_bytes_threshold != null) {
        parts.push(
          t("schedules:payload.upscale.maxBytes", {
            value: formatNumber(payload.max_bytes_threshold),
          }),
        );
      }
      parts.push(
        t("schedules:payload.upscale.limit", { limit: formatNumber(payload.limit ?? 200) }),
      );
      return parts;
    }
    case "r18_import":
      return [
        payload.force
          ? t("tasks:submit.r18_import.force.label")
          : t("schedules:payload.r18_import.skipUnchanged"),
      ];
    case "rescrape": {
      const targets = payload.targets ?? ["metadata"];
      const parts: string[] = [];
      if (targets.length > 0) {
        parts.push(
          targets.map((target) => targetLabel(t, target)).join(t("schedules:payload.listJoiner")),
        );
      }
      parts.push(
        t("schedules:payload.rescrape.limit", { limit: formatNumber(payload.limit ?? 100) }),
      );
      if (payload.min_age_days != null) {
        parts.push(
          t("schedules:payload.rescrape.minAge", { days: formatNumber(payload.min_age_days) }),
        );
      }
      return parts;
    }
    default:
      return assertNever(payload, "SchedulePayload");
  }
}

function factRows(payload: SchedulePayload, t: Translate): { label: string; value: string }[] {
  switch (payload.type) {
    case "cleanup":
      return [
        {
          label: t("tasks:submit.cleanup.remove_missing_files.label"),
          value: yesNo(t, payload.remove_missing_files ?? true),
        },
        {
          label: t("tasks:submit.cleanup.remove_unreferenced_resources.label"),
          value: yesNo(t, payload.remove_unreferenced_resources ?? true),
        },
      ];
    case "upscale":
      return [
        {
          label: t("tasks:submit.upscale.max_dim_threshold.label"),
          value:
            payload.max_dim_threshold == null
              ? t("schedules:payload.unset")
              : formatNumber(payload.max_dim_threshold),
        },
        {
          label: t("tasks:submit.upscale.max_bytes_threshold.label"),
          value:
            payload.max_bytes_threshold == null
              ? t("schedules:payload.unset")
              : formatNumber(payload.max_bytes_threshold),
        },
        {
          label: t("tasks:submit.upscale.limit.label"),
          value: formatNumber(payload.limit ?? 200),
        },
      ];
    case "r18_import":
      return [
        {
          label: t("tasks:submit.r18_import.force.label"),
          value: yesNo(t, payload.force ?? false),
        },
      ];
    case "rescrape": {
      const targets = payload.targets ?? ["metadata"];
      return [
        {
          label: t("tasks:submit.rescrape.targets.label"),
          value:
            targets.length > 0
              ? targets
                  .map((target) => targetLabel(t, target))
                  .join(t("schedules:payload.listJoiner"))
              : t("schedules:payload.unset"),
        },
        {
          label: t("tasks:submit.rescrape.limit.label"),
          value: formatNumber(payload.limit ?? 100),
        },
        {
          label: t("tasks:submit.rescrape.min_age_days.label"),
          value:
            payload.min_age_days == null
              ? t("schedules:payload.unset")
              : formatNumber(payload.min_age_days),
        },
      ];
    }
    default:
      return assertNever(payload, "SchedulePayload");
  }
}

export function SchedulePayloadSummary({ payload }: { payload: SchedulePayload }) {
  const { t } = useTranslation(["schedules", "tasks"]);
  const text = summaryParts(payload, t).join(t("schedules:payload.joiner"));
  return (
    <Text size="xs" c="dimmed">
      {text}
    </Text>
  );
}

export function SchedulePayloadFacts({ payload }: { payload: SchedulePayload }) {
  const { t } = useTranslation(["schedules", "tasks"]);
  return (
    <Stack gap="sm">
      <Text size="sm" fw={500}>
        {t("schedules:payload.title")}
      </Text>
      <Badge size="sm" variant="light" w="fit-content">
        {typeLabel(t, payload.type)}
      </Badge>
      <Stack gap={6}>
        {factRows(payload, t).map((row) => (
          <Group key={row.label} justify="space-between" wrap="nowrap" gap="md" align="flex-start">
            <Text size="sm">{row.label}</Text>
            <Text size="sm" c="dimmed" ta="right">
              {row.value}
            </Text>
          </Group>
        ))}
      </Stack>
    </Stack>
  );
}
