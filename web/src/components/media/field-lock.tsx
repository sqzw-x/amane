import { ActionIcon, Button, Tooltip } from "@mantine/core";
import { IconLock, IconLockOpen } from "@tabler/icons-react";
import { useTranslation } from "react-i18next";

export type LockProps<F extends string> = {
  field: F;
  locked: boolean;
  busy: boolean;
  onToggleLock: (field: F) => void;
};

/** 字段旁的锁开关, 点击切换. */
export function LockToggle<F extends string>({ field, locked, busy, onToggleLock }: LockProps<F>) {
  const { t } = useTranslation("metadata");
  return (
    <Tooltip label={locked ? t("lock.toggleLocked") : t("lock.toggleUnlocked")} withArrow>
      <ActionIcon
        size="xs"
        variant="subtle"
        color={locked ? "yellow" : "gray"}
        aria-label={locked ? t("lock.locked") : t("lock.unlocked")}
        disabled={busy}
        onClick={() => onToggleLock(field)}
        style={{ opacity: locked ? 1 : 0.45 }}
      >
        {locked ? <IconLock size={12} /> : <IconLockOpen size={12} />}
      </ActionIcon>
    </Tooltip>
  );
}

/** 带文字的锁开关. */
export function LockChip<F extends string>({ label, ...lock }: LockProps<F> & { label: string }) {
  const { t } = useTranslation("metadata");
  return (
    <Tooltip label={lock.locked ? t("lock.toggleLocked") : t("lock.toggleUnlocked")} withArrow>
      <Button
        size="compact-xs"
        variant={lock.locked ? "light" : "subtle"}
        color={lock.locked ? "yellow" : "gray"}
        leftSection={lock.locked ? <IconLock size={12} /> : <IconLockOpen size={12} />}
        disabled={lock.busy}
        onClick={() => lock.onToggleLock(lock.field)}
        style={lock.locked ? undefined : { opacity: 0.65 }}
      >
        {label}
      </Button>
    </Tooltip>
  );
}
