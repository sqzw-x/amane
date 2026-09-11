import { Badge, Group, Stack, Text } from "@mantine/core";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import type { RescrapeTarget, RoutineType, ScheduleResponse } from "@/client/types.gen";
import { assertNever } from "@/lib/exhaustive";
import { parseSchedulePayload, type SchedulePayloadView } from "@/lib/schedules/payload";

type Translate = TFunction<["schedules", "tasks"]>;

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

function summaryParts(view: SchedulePayloadView, t: Translate): string[] {
  switch (view.type) {
    case "cleanup": {
      const parts: string[] = [];
      if (view.remove_missing_files) {
        parts.push(t("tasks:submit.cleanup.remove_missing_files.label"));
      }
      if (view.remove_unreferenced_resources) {
        parts.push(t("tasks:submit.cleanup.remove_unreferenced_resources.label"));
      }
      return parts.length > 0 ? parts : [t("schedules:payload.cleanup.none")];
    }
    case "upscale": {
      const parts: string[] = [];
      if (view.max_dim_threshold != null) {
        parts.push(
          t("schedules:payload.upscale.maxDim", { value: formatNumber(view.max_dim_threshold) }),
        );
      }
      if (view.max_bytes_threshold != null) {
        parts.push(
          t("schedules:payload.upscale.maxBytes", {
            value: formatNumber(view.max_bytes_threshold),
          }),
        );
      }
      parts.push(t("schedules:payload.upscale.limit", { limit: formatNumber(view.limit) }));
      return parts;
    }
    case "r18_import":
      return [
        view.force
          ? t("tasks:submit.r18_import.force.label")
          : t("schedules:payload.r18_import.skipUnchanged"),
      ];
    case "rescrape": {
      const parts: string[] = [];
      if (view.targets.length > 0) {
        parts.push(
          view.targets
            .map((target) => targetLabel(t, target))
            .join(t("schedules:payload.listJoiner")),
        );
      }
      parts.push(t("schedules:payload.rescrape.limit", { limit: formatNumber(view.limit) }));
      if (view.min_age_days != null) {
        parts.push(
          t("schedules:payload.rescrape.minAge", { days: formatNumber(view.min_age_days) }),
        );
      }
      return parts;
    }
    default:
      return assertNever(view, "SchedulePayloadView");
  }
}

function factRows(view: SchedulePayloadView, t: Translate): { label: string; value: string }[] {
  switch (view.type) {
    case "cleanup":
      return [
        {
          label: t("tasks:submit.cleanup.remove_missing_files.label"),
          value: yesNo(t, view.remove_missing_files),
        },
        {
          label: t("tasks:submit.cleanup.remove_unreferenced_resources.label"),
          value: yesNo(t, view.remove_unreferenced_resources),
        },
      ];
    case "upscale":
      return [
        {
          label: t("tasks:submit.upscale.max_dim_threshold.label"),
          value:
            view.max_dim_threshold == null
              ? t("schedules:payload.unset")
              : formatNumber(view.max_dim_threshold),
        },
        {
          label: t("tasks:submit.upscale.max_bytes_threshold.label"),
          value:
            view.max_bytes_threshold == null
              ? t("schedules:payload.unset")
              : formatNumber(view.max_bytes_threshold),
        },
        {
          label: t("tasks:submit.upscale.limit.label"),
          value: formatNumber(view.limit),
        },
      ];
    case "r18_import":
      return [{ label: t("tasks:submit.r18_import.force.label"), value: yesNo(t, view.force) }];
    case "rescrape":
      return [
        {
          label: t("tasks:submit.rescrape.targets.label"),
          value:
            view.targets.length > 0
              ? view.targets
                  .map((target) => targetLabel(t, target))
                  .join(t("schedules:payload.listJoiner"))
              : t("schedules:payload.unset"),
        },
        {
          label: t("tasks:submit.rescrape.limit.label"),
          value: formatNumber(view.limit),
        },
        {
          label: t("tasks:submit.rescrape.min_age_days.label"),
          value:
            view.min_age_days == null
              ? t("schedules:payload.unset")
              : formatNumber(view.min_age_days),
        },
      ];
    default:
      return assertNever(view, "SchedulePayloadView");
  }
}

export function SchedulePayloadSummary({
  taskType,
  payload,
}: {
  taskType: RoutineType;
  payload: ScheduleResponse["payload"];
}) {
  const { t } = useTranslation(["schedules", "tasks"]);
  const view = parseSchedulePayload(taskType, payload);
  const text = summaryParts(view, t).join(t("schedules:payload.joiner"));
  return (
    <Text size="xs" c="dimmed">
      {text}
    </Text>
  );
}

export function SchedulePayloadFacts({
  taskType,
  payload,
}: {
  taskType: RoutineType;
  payload: ScheduleResponse["payload"];
}) {
  const { t } = useTranslation(["schedules", "tasks"]);
  const view = parseSchedulePayload(taskType, payload);
  return (
    <Stack gap="sm">
      <Text size="sm" fw={500}>
        {t("schedules:payload.title")}
      </Text>
      <Badge size="sm" variant="light" w="fit-content">
        {typeLabel(t, taskType)}
      </Badge>
      <Stack gap={6}>
        {factRows(view, t).map((row) => (
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
