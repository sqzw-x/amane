import type { RescrapeTarget, RoutineType } from "@/client/types.gen";
import { assertNever } from "@/lib/exhaustive";
import { RESCRAPE_TARGETS } from "@/lib/exhaustive-maps";

/**
 * 定时 payload 的展示形态. 缺字段回退与 CronScheduler 入队、Payload 模型默认值对齐
 * (`src/amane/scheduler/cron.py`, `src/amane/handlers/models.py`).
 */
export type SchedulePayloadView =
  | {
      type: "cleanup";
      remove_missing_files: boolean;
      remove_unreferenced_resources: boolean;
    }
  | {
      type: "upscale";
      max_dim_threshold: number | null;
      max_bytes_threshold: number | null;
      limit: number;
    }
  | {
      type: "r18_import";
      force: boolean;
    }
  | {
      type: "rescrape";
      targets: RescrapeTarget[];
      limit: number;
      min_age_days: number | null;
    };

function asBool(value: unknown, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function asNumber(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function asOptionalNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function rescrapeTargets(value: unknown): RescrapeTarget[] {
  if (!Array.isArray(value)) return ["metadata"];
  return RESCRAPE_TARGETS.filter((target) => value.includes(target));
}

export function parseSchedulePayload(
  taskType: RoutineType,
  payload: Record<string, unknown> | null | undefined,
): SchedulePayloadView {
  const raw = payload ?? {};
  switch (taskType) {
    case "cleanup":
      return {
        type: "cleanup",
        remove_missing_files: asBool(raw.remove_missing_files, true),
        remove_unreferenced_resources: asBool(raw.remove_unreferenced_resources, true),
      };
    case "upscale":
      return {
        type: "upscale",
        max_dim_threshold: asOptionalNumber(raw.max_dim_threshold),
        max_bytes_threshold: asOptionalNumber(raw.max_bytes_threshold),
        limit: asNumber(raw.limit, 200),
      };
    case "r18_import":
      return { type: "r18_import", force: asBool(raw.force, false) };
    case "rescrape":
      return {
        type: "rescrape",
        targets: rescrapeTargets(raw.targets),
        limit: asNumber(raw.limit, 100),
        min_age_days: asOptionalNumber(raw.min_age_days),
      };
    default:
      return assertNever(taskType, "RoutineType");
  }
}
