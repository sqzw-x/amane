import { IconAlertTriangle, IconCircleCheck, IconHelpCircle, type Icon } from "@tabler/icons-react";
import type { ParseKeys } from "i18next";
import type { ConnectivityStatus, SkipReason, SourceKind } from "@/client/types.gen";
import { exhaustiveRecord } from "@/lib/exhaustive";

/**
 * 结果排序权重: 不可访问优先, 其次无法探测, 最后可访问.
 * 同一权重内不比较, 依赖 `Array.prototype.toSorted` 的稳定排序保持后端的响应顺序.
 */
export const STATUS_RANK = exhaustiveRecord<ConnectivityStatus>()({
  failed: 0,
  skipped: 1,
  ok: 2,
} as const);

export const STATUS_COLOR = exhaustiveRecord<ConnectivityStatus>()({
  ok: "teal",
  failed: "red",
  skipped: "gray",
} as const);

/** 状态图标与颜色并用: 只靠色相区分结论对色觉障碍用户不成立. */
export const STATUS_ICON = exhaustiveRecord<ConnectivityStatus>()({
  ok: IconCircleCheck,
  failed: IconAlertTriangle,
  skipped: IconHelpCircle,
} as const) satisfies Record<ConnectivityStatus, Icon>;

export const STATUS_LABEL_KEY = exhaustiveRecord<ConnectivityStatus>()({
  ok: "status.ok",
  failed: "status.failed",
  skipped: "status.skipped",
} as const satisfies Record<ConnectivityStatus, ParseKeys<"networkCheck">>);

export const KIND_LABEL_KEY = exhaustiveRecord<SourceKind>()({
  film: "kind.film",
  actor: "kind.actor",
  plugin: "kind.plugin",
} as const satisfies Record<SourceKind, ParseKeys<"networkCheck">>);

/** 未探测的原因不走任务报告 (它不是失败), 文案在本页的 `skip.*`. */
export const SKIP_LABEL_KEY = exhaustiveRecord<SkipReason>()({
  unknown_source: "skip.unknown_source",
  no_http_upstream: "skip.no_http_upstream",
  missing_credential: "skip.missing_credential",
  undeclared: "skip.undeclared",
  no_url: "skip.no_url",
} as const satisfies Record<SkipReason, ParseKeys<"networkCheck">>);

/** 汇总区的三块数字与结果行按同一组状态取值, 所以筛选态直接复用该联合. */
export type StatusFilter = ConnectivityStatus;

/** 汇总区数字块的展示顺序: 与默认排序一致, 先看不好的结论. */
export const SUMMARY_STATUSES = [
  "failed",
  "skipped",
  "ok",
] as const satisfies readonly ConnectivityStatus[];

export interface StatusCounts {
  total: number;
  ok: number;
  failed: number;
  skipped: number;
}

export function countStatuses(items: readonly { status: ConnectivityStatus }[]): StatusCounts {
  const counts: StatusCounts = { total: items.length, ok: 0, failed: 0, skipped: 0 };
  for (const item of items) {
    counts[item.status] += 1;
  }
  return counts;
}
