/**
 * 5-field cron (minute hour day-of-month month day-of-week).
 * 与后端 croniter 默认格式一致; 只解析可视化选择器能往返的常见模式, 其余视为 custom.
 *
 * CronValue 的 hour / minute / days / day 是浏览器本地墙钟.
 * formatCron 写出 UTC 字段; parseCron 把 UTC 字段换算回本地.
 * interval 与 custom 不换算 — custom 按 UTC 手写.
 */

import { assertNever, exhaustiveTuple, isOneOf } from "./exhaustive";

export type CronKind = "interval" | "daily" | "weekly" | "monthly" | "custom";
export type IntervalUnit = "minutes" | "hours";
export type Weekday = 0 | 1 | 2 | 3 | 4 | 5 | 6;

export const CRON_KINDS = exhaustiveTuple<CronKind>()(
  "daily",
  "weekly",
  "monthly",
  "interval",
  "custom",
);

export const INTERVAL_UNITS = exhaustiveTuple<IntervalUnit>()("minutes", "hours");

export const WEEKDAYS = exhaustiveTuple<Weekday>()(0, 1, 2, 3, 4, 5, 6);

/** 周一至周日的展示顺序 (周日排最后). */
export const WEEKDAY_DISPLAY_ORDER = [1, 2, 3, 4, 5, 6, 0] as const satisfies readonly Weekday[];

export type IntervalCron = {
  kind: "interval";
  unit: IntervalUnit;
  every: number;
  minute: number;
};
export type DailyCron = { kind: "daily"; hour: number; minute: number };
export type WeeklyCron = { kind: "weekly"; hour: number; minute: number; days: Weekday[] };
export type MonthlyCron = { kind: "monthly"; hour: number; minute: number; day: number };
export type CustomCron = { kind: "custom"; expression: string };
export type CronValue = IntervalCron | DailyCron | WeeklyCron | MonthlyCron | CustomCron;

const MACROS: Record<string, CronValue> = {
  "@hourly": { kind: "interval", unit: "hours", every: 1, minute: 0 },
  "@daily": { kind: "daily", hour: 0, minute: 0 },
  "@midnight": { kind: "daily", hour: 0, minute: 0 },
  "@weekly": { kind: "weekly", hour: 0, minute: 0, days: [0] },
  "@monthly": { kind: "monthly", hour: 0, minute: 0, day: 1 },
};

export function formatTime(hour: number, minute: number): string {
  return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
}

export function parseCron(expression: string): CronValue {
  const trimmed = expression.trim();
  if (!trimmed) return { kind: "custom", expression: "" };

  const macro = MACROS[trimmed.toLowerCase()];
  if (macro) return fromUtcValue(macro);

  const fields = trimmed.split(/\s+/);
  if (fields.length !== 5) return { kind: "custom", expression: trimmed };

  const [minuteF, hourF, domF, monthF, dowF] = fields;
  if (monthF !== "*") return { kind: "custom", expression: trimmed };
  // DOM 与 DOW 同时约束时 cron 是 OR 语义, 选择器不表达, 回退 custom.
  if (domF !== "*" && dowF !== "*") return { kind: "custom", expression: trimmed };

  if (hourF === "*" && domF === "*" && dowF === "*") {
    const minStep = parseStarOrStep(minuteF);
    if (minStep === "star") {
      return { kind: "interval", unit: "minutes", every: 1, minute: 0 };
    }
    if (minStep !== null && minStep.every <= 59) {
      return { kind: "interval", unit: "minutes", every: minStep.every, minute: 0 };
    }
    const minute = parseSingle(minuteF, 0, 59);
    if (minute !== null) {
      return { kind: "interval", unit: "hours", every: 1, minute };
    }
    return { kind: "custom", expression: trimmed };
  }

  if (domF === "*" && dowF === "*") {
    const hourStep = parseStarOrStep(hourF);
    const minute = parseSingle(minuteF, 0, 59);
    if (minute !== null && hourStep !== null && hourStep !== "star" && hourStep.every <= 23) {
      return { kind: "interval", unit: "hours", every: hourStep.every, minute };
    }
  }

  const minute = parseSingle(minuteF, 0, 59);
  const hour = parseSingle(hourF, 0, 23);
  if (minute === null || hour === null) return { kind: "custom", expression: trimmed };

  if (domF === "*" && dowF === "*") {
    return fromUtcValue({ kind: "daily", hour, minute });
  }

  if (domF === "*") {
    const days = parseDowList(dowF);
    if (days) return fromUtcValue({ kind: "weekly", hour, minute, days });
    return { kind: "custom", expression: trimmed };
  }

  const day = parseSingle(domF, 1, 31);
  if (day !== null && dowF === "*") {
    return fromUtcValue({ kind: "monthly", hour, minute, day });
  }

  return { kind: "custom", expression: trimmed };
}

export function formatCron(value: CronValue): string {
  switch (value.kind) {
    case "interval": {
      const every = clampInt(value.every, 1, value.unit === "minutes" ? 59 : 23);
      const minute = clampInt(value.minute, 0, 59);
      if (value.unit === "minutes") {
        return every === 1 ? "* * * * *" : `*/${every} * * * *`;
      }
      return every === 1 ? `${minute} * * * *` : `${minute} */${every} * * *`;
    }
    case "daily": {
      const utc = toUtcValue({
        kind: "daily",
        hour: clampInt(value.hour, 0, 23),
        minute: clampInt(value.minute, 0, 59),
      });
      return `${utc.minute} ${utc.hour} * * *`;
    }
    case "weekly": {
      const utc = toUtcValue({
        kind: "weekly",
        hour: clampInt(value.hour, 0, 23),
        minute: clampInt(value.minute, 0, 59),
        days: uniqueSortedDays(value.days),
      });
      const dow = utc.days.length === 0 ? "*" : utc.days.join(",");
      return `${utc.minute} ${utc.hour} * * ${dow}`;
    }
    case "monthly": {
      const utc = toUtcValue({
        kind: "monthly",
        hour: clampInt(value.hour, 0, 23),
        minute: clampInt(value.minute, 0, 59),
        day: clampInt(value.day, 1, 31),
      });
      return `${utc.minute} ${utc.hour} ${utc.day} * *`;
    }
    case "custom":
      return value.expression.trim();
    default:
      return assertNever(value, "CronValue");
  }
}

/** 每天本地 02:00, 写出时换算为 UTC. */
export const DEFAULT_CRON = formatCron({ kind: "daily", hour: 2, minute: 0 });

export function toInterval(value: CronValue): IntervalCron {
  if (value.kind === "interval") return value;
  return { kind: "interval", unit: "hours", every: 1, minute: extractTime(value).minute };
}

export function toDaily(value: CronValue): DailyCron {
  if (value.kind === "daily") return value;
  const time = extractTime(value);
  return { kind: "daily", hour: time.hour, minute: time.minute };
}

export function toWeekly(value: CronValue): WeeklyCron {
  if (value.kind === "weekly") return value;
  const time = extractTime(value);
  return { kind: "weekly", hour: time.hour, minute: time.minute, days: [1, 2, 3, 4, 5] };
}

export function toMonthly(value: CronValue): MonthlyCron {
  if (value.kind === "monthly") return value;
  const time = extractTime(value);
  return { kind: "monthly", hour: time.hour, minute: time.minute, day: 1 };
}

export function toCustom(value: CronValue): CustomCron {
  if (value.kind === "custom") return value;
  return { kind: "custom", expression: formatCron(value) };
}

export function cronValueWithKind(value: CronValue, kind: CronKind): CronValue {
  switch (kind) {
    case "interval":
      return toInterval(value);
    case "daily":
      return toDaily(value);
    case "weekly":
      return toWeekly(value);
    case "monthly":
      return toMonthly(value);
    case "custom":
      return toCustom(value);
    default:
      return assertNever(kind, "CronKind");
  }
}

export function isUsableCron(expression: string): boolean {
  const trimmed = expression.trim();
  if (!trimmed) return false;
  const value = parseCron(trimmed);
  if (value.kind === "weekly") return value.days.length > 0;
  if (value.kind === "custom") {
    return trimmed.startsWith("@") || trimmed.split(/\s+/).length >= 5;
  }
  return true;
}

function tzOffsetMin(): number {
  return new Date().getTimezoneOffset();
}

function shiftClock(
  hour: number,
  minute: number,
  offsetMin: number,
): { hour: number; minute: number; dayDelta: number } {
  const total = hour * 60 + minute + offsetMin;
  const dayDelta = Math.floor(total / (24 * 60));
  const wrapped = ((total % (24 * 60)) + 24 * 60) % (24 * 60);
  return { hour: Math.floor(wrapped / 60), minute: wrapped % 60, dayDelta };
}

function shiftWeekdays(days: readonly Weekday[], dayDelta: number): Weekday[] {
  const shifted: Weekday[] = [];
  for (const day of days) {
    const next = (((day + dayDelta) % 7) + 7) % 7;
    if (isOneOf(WEEKDAYS, next)) shifted.push(next);
  }
  return uniqueSortedDays(shifted);
}

function shiftMonthDay(day: number, dayDelta: number): number {
  return ((((day - 1 + dayDelta) % 31) + 31) % 31) + 1;
}

function shiftWallClock<T extends DailyCron | WeeklyCron | MonthlyCron>(
  value: T,
  offsetMin: number,
): T {
  const clock = shiftClock(value.hour, value.minute, offsetMin);
  switch (value.kind) {
    case "daily":
      return { ...value, hour: clock.hour, minute: clock.minute };
    case "weekly":
      return {
        ...value,
        hour: clock.hour,
        minute: clock.minute,
        days: shiftWeekdays(value.days, clock.dayDelta),
      };
    case "monthly":
      return {
        ...value,
        hour: clock.hour,
        minute: clock.minute,
        day: shiftMonthDay(value.day, clock.dayDelta),
      };
    default:
      return assertNever(value, "wall-clock CronValue");
  }
}

function fromUtcValue(value: CronValue): CronValue {
  if (value.kind === "daily" || value.kind === "weekly" || value.kind === "monthly") {
    return shiftWallClock(value, -tzOffsetMin());
  }
  return value;
}

function toUtcValue<T extends DailyCron | WeeklyCron | MonthlyCron>(value: T): T {
  return shiftWallClock(value, tzOffsetMin());
}

function extractTime(value: CronValue): { hour: number; minute: number } {
  switch (value.kind) {
    case "interval":
      return { hour: 2, minute: value.unit === "hours" ? value.minute : 0 };
    case "daily":
    case "weekly":
    case "monthly":
      return { hour: value.hour, minute: value.minute };
    case "custom":
      return { hour: 2, minute: 0 };
    default:
      return assertNever(value, "CronValue");
  }
}

function parseStarOrStep(raw: string): { every: number } | "star" | null {
  if (raw === "*") return "star";
  const match = /^\*\/(\d+)$/.exec(raw);
  if (match?.[1] === undefined) return null;
  const every = Number(match[1]);
  if (!Number.isInteger(every) || every < 1) return null;
  return { every };
}

function parseSingle(raw: string, min: number, max: number): number | null {
  if (!/^\d+$/.test(raw)) return null;
  const n = Number(raw);
  if (!Number.isInteger(n) || n < min || n > max) return null;
  return n;
}

function parseDowList(raw: string): Weekday[] | null {
  const days: Weekday[] = [];
  const seen = new Set<Weekday>();
  for (const token of raw.split(",")) {
    const range = /^(\d+)-(\d+)$/.exec(token);
    if (range?.[1] !== undefined && range[2] !== undefined) {
      const start = Number(range[1]);
      const end = Number(range[2]);
      if (start > end) return null;
      for (let n = start; n <= end; n++) {
        const day = normalizeDow(n);
        if (day === null) return null;
        if (!seen.has(day)) {
          seen.add(day);
          days.push(day);
        }
      }
      continue;
    }
    if (!/^\d+$/.test(token)) return null;
    const day = normalizeDow(Number(token));
    if (day === null) return null;
    if (!seen.has(day)) {
      seen.add(day);
      days.push(day);
    }
  }
  if (days.length === 0) return null;
  return days.toSorted((a, b) => a - b);
}

function normalizeDow(n: number): Weekday | null {
  if (n === 7) return 0;
  return isOneOf(WEEKDAYS, n) ? n : null;
}

function uniqueSortedDays(days: readonly Weekday[]): Weekday[] {
  const seen = new Set<Weekday>();
  const out: Weekday[] = [];
  for (const day of days) {
    const normalized = normalizeDow(day);
    if (normalized === null || seen.has(normalized)) continue;
    seen.add(normalized);
    out.push(normalized);
  }
  return out.toSorted((a, b) => a - b);
}

function clampInt(n: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, Math.trunc(n)));
}
