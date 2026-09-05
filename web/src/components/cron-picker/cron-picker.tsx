import { Chip, Group, Input, NumberInput, Select, Stack, Text, TextInput } from "@mantine/core";
import type { TFunction } from "i18next";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { EnumToggle } from "@/components/common/enum-toggle";
import {
  type CronKind,
  type CronValue,
  CRON_KINDS,
  INTERVAL_UNITS,
  type Weekday,
  WEEKDAY_DISPLAY_ORDER,
  WEEKDAYS,
  cronValueWithKind,
  formatCron,
  formatTime,
  parseCron,
  toDaily,
  toInterval,
  toMonthly,
  toWeekly,
} from "@/lib/cron";
import { assertNever, isOneOf } from "@/lib/exhaustive";

const HOUR_OPTIONS = Array.from({ length: 24 }, (_, hour) => ({
  value: String(hour),
  label: String(hour).padStart(2, "0"),
}));

const MINUTE_OPTIONS = Array.from({ length: 60 }, (_, minute) => ({
  value: String(minute),
  label: String(minute).padStart(2, "0"),
}));

const SELECT_COMBOBOX = { withinPortal: true } as const;

export interface CronPickerProps {
  value: string;
  onChange: (expression: string) => void;
  label?: string;
  disabled?: boolean;
}

export function CronPicker({ value, onChange, label, disabled = false }: CronPickerProps) {
  const { t } = useTranslation("schedules");
  const parsed = parseCron(value);
  // 模式粘在本地: 「高级」下即使表达式恰好能解析成每天/每周, 也不跳回可视化, 避免编辑到一半被打断.
  const [kind, setKind] = useState<CronKind>(() => parsed.kind);

  function emit(next: CronValue) {
    onChange(formatCron(next));
  }

  function handleKindChange(next: CronKind) {
    setKind(next);
    emit(cronValueWithKind(parsed, next));
  }

  return (
    <Input.Wrapper label={label ?? t("fields.cron")} size="sm">
      <Stack gap="sm" mt={6}>
        <EnumToggle
          fullWidth
          options={CRON_KINDS}
          value={kind}
          disabled={disabled}
          onChange={handleKindChange}
          getLabel={(mode) => t(`cron.modes.${mode}`)}
        />
        <KindFields kind={kind} parsed={parsed} raw={value} disabled={disabled} onEmit={emit} />
        <Stack gap={2}>
          {kind !== "custom" && (
            <Text size="xs" c="dimmed" ff="monospace">
              {formatCron(cronValueWithKind(parsed, kind))}
            </Text>
          )}
          <CronSummary expression={value} />
        </Stack>
      </Stack>
    </Input.Wrapper>
  );
}

export function CronSummary({ expression }: { expression: string }) {
  const { t } = useTranslation("schedules");
  const text = summarizeCron(parseCron(expression), t);
  if (!text) return null;
  return (
    <Text size="xs" c="dimmed">
      {text}
    </Text>
  );
}

function KindFields({
  kind,
  parsed,
  raw,
  disabled,
  onEmit,
}: {
  kind: CronKind;
  parsed: CronValue;
  raw: string;
  disabled: boolean;
  onEmit: (next: CronValue) => void;
}) {
  const { t } = useTranslation("schedules");

  switch (kind) {
    case "interval": {
      const interval = toInterval(parsed);
      return (
        <Group gap="sm" align="flex-end" wrap="wrap">
          <NumberInput
            label={t("cron.every")}
            value={interval.every}
            min={1}
            max={interval.unit === "minutes" ? 59 : 23}
            allowDecimal={false}
            clampBehavior="strict"
            disabled={disabled}
            w={88}
            onChange={(rawValue) => {
              const every = parseNumberInput(rawValue);
              if (every === null) return;
              onEmit({ ...interval, every });
            }}
          />
          <EnumToggle
            options={INTERVAL_UNITS}
            value={interval.unit}
            disabled={disabled}
            onChange={(unit) => onEmit({ ...interval, unit, every: 1 })}
            getLabel={(unit) => t(`cron.units.${unit}`)}
          />
          {interval.unit === "hours" && (
            <TimeSelects
              hour={0}
              minute={interval.minute}
              disabled={disabled}
              hourHidden
              onChange={(_hour, minute) => onEmit({ ...interval, minute })}
            />
          )}
        </Group>
      );
    }
    case "daily": {
      const daily = toDaily(parsed);
      return (
        <TimeSelects
          hour={daily.hour}
          minute={daily.minute}
          disabled={disabled}
          onChange={(hour, minute) => onEmit({ kind: "daily", hour, minute })}
        />
      );
    }
    case "weekly": {
      const weekly = toWeekly(parsed);
      return (
        <Stack gap="sm">
          <Input.Wrapper label={t("cron.onDays")} size="sm">
            <Chip.Group
              multiple
              value={weekly.days.map(String)}
              onChange={(values) => {
                const days = parseWeekdaySelection(values);
                if (days.length === 0) return;
                onEmit({ ...weekly, days });
              }}
            >
              <Group gap={6} mt={4}>
                {WEEKDAY_DISPLAY_ORDER.map((day) => (
                  <Chip
                    key={day}
                    value={String(day)}
                    size="sm"
                    variant="outline"
                    disabled={disabled}
                  >
                    {weekdayLabel(day, t)}
                  </Chip>
                ))}
              </Group>
            </Chip.Group>
          </Input.Wrapper>
          <TimeSelects
            hour={weekly.hour}
            minute={weekly.minute}
            disabled={disabled}
            onChange={(hour, minute) => onEmit({ ...weekly, hour, minute })}
          />
        </Stack>
      );
    }
    case "monthly": {
      const monthly = toMonthly(parsed);
      return (
        <Group gap="sm" align="flex-end" wrap="wrap">
          <NumberInput
            label={t("cron.onDay")}
            value={monthly.day}
            min={1}
            max={31}
            allowDecimal={false}
            clampBehavior="strict"
            disabled={disabled}
            w={100}
            onChange={(rawValue) => {
              const day = parseNumberInput(rawValue);
              if (day === null) return;
              onEmit({ ...monthly, day });
            }}
          />
          <TimeSelects
            hour={monthly.hour}
            minute={monthly.minute}
            disabled={disabled}
            onChange={(hour, minute) => onEmit({ ...monthly, hour, minute })}
          />
        </Group>
      );
    }
    case "custom":
      return (
        <TextInput
          value={raw}
          disabled={disabled}
          placeholder={t("cron.customPlaceholder")}
          description={t("cron.customHint")}
          onChange={(e) => onEmit({ kind: "custom", expression: e.currentTarget.value })}
        />
      );
    default:
      return assertNever(kind, "CronKind");
  }
}

function TimeSelects({
  hour,
  minute,
  disabled,
  hourHidden = false,
  onChange,
}: {
  hour: number;
  minute: number;
  disabled: boolean;
  hourHidden?: boolean;
  onChange: (hour: number, minute: number) => void;
}) {
  const { t } = useTranslation("schedules");
  return (
    <Input.Wrapper description={hourHidden ? undefined : t("cron.localTime")} size="sm">
      <Group gap="xs" align="flex-end" wrap="nowrap">
        {!hourHidden && (
          <Select
            label={t("cron.hour")}
            data={HOUR_OPTIONS}
            value={String(hour)}
            allowDeselect={false}
            searchable
            disabled={disabled}
            w={88}
            comboboxProps={SELECT_COMBOBOX}
            onChange={(v) => {
              const next = parseClockField(v, 0, 23);
              if (next === null) return;
              onChange(next, minute);
            }}
          />
        )}
        <Select
          label={hourHidden ? t("cron.minuteOfHour") : t("cron.minute")}
          data={MINUTE_OPTIONS}
          value={String(minute)}
          allowDeselect={false}
          searchable
          disabled={disabled}
          w={88}
          comboboxProps={SELECT_COMBOBOX}
          onChange={(v) => {
            const next = parseClockField(v, 0, 59);
            if (next === null) return;
            onChange(hour, next);
          }}
        />
      </Group>
    </Input.Wrapper>
  );
}

function summarizeCron(value: CronValue, t: TFunction<"schedules">): string {
  switch (value.kind) {
    case "interval":
      if (value.unit === "minutes") {
        return t("cron.summary.intervalMinutes", { every: value.every });
      }
      if (value.every === 1) {
        return t("cron.summary.intervalHoursOnHour", {
          minute: String(value.minute).padStart(2, "0"),
        });
      }
      return t("cron.summary.intervalHours", {
        every: value.every,
        minute: String(value.minute).padStart(2, "0"),
      });
    case "daily":
      return t("cron.summary.daily", { time: formatTime(value.hour, value.minute) });
    case "weekly": {
      const days = value.days
        .toSorted((a, b) => WEEKDAY_DISPLAY_ORDER.indexOf(a) - WEEKDAY_DISPLAY_ORDER.indexOf(b))
        .map((day) => weekdayLabel(day, t))
        .join(t("cron.dayJoiner"));
      return t("cron.summary.weekly", { days, time: formatTime(value.hour, value.minute) });
    }
    case "monthly":
      return t("cron.summary.monthly", {
        day: value.day,
        time: formatTime(value.hour, value.minute),
      });
    case "custom":
      return "";
    default:
      return assertNever(value, "CronValue");
  }
}

function weekdayLabel(day: Weekday, t: TFunction<"schedules">): string {
  switch (day) {
    case 0:
      return t("cron.weekdays.sun");
    case 1:
      return t("cron.weekdays.mon");
    case 2:
      return t("cron.weekdays.tue");
    case 3:
      return t("cron.weekdays.wed");
    case 4:
      return t("cron.weekdays.thu");
    case 5:
      return t("cron.weekdays.fri");
    case 6:
      return t("cron.weekdays.sat");
    default:
      return assertNever(day, "Weekday");
  }
}

function parseWeekdaySelection(values: string[]): Weekday[] {
  const days: Weekday[] = [];
  for (const item of values) {
    const n = Number.parseInt(item, 10);
    if (isOneOf(WEEKDAYS, n)) days.push(n);
  }
  return days.toSorted((a, b) => a - b);
}

function parseNumberInput(value: string | number): number | null {
  if (value === "") return null;
  const n = typeof value === "number" ? value : Number.parseInt(value, 10);
  return Number.isFinite(n) ? n : null;
}

function parseClockField(value: string | null, min: number, max: number): number | null {
  if (value == null) return null;
  const n = Number.parseInt(value, 10);
  if (!Number.isInteger(n) || n < min || n > max) return null;
  return n;
}
