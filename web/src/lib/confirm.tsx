import { Button, getDefaultZIndex, Group, Modal, Stack, Text } from "@mantine/core";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

export type ConfirmOptions = {
  title?: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** 确认钮颜色; 未设置时 danger=true 用 red. */
  confirmColor?: string;
  /** 危险操作. 默认 true. */
  danger?: boolean;
};

type PendingConfirm = ConfirmOptions & {
  resolve: (ok: boolean) => void;
};

type ConfirmBridge = {
  open: (pending: PendingConfirm) => void;
};

let bridge: ConfirmBridge | null = null;

/** 确认框须盖住任何已打开的弹窗: Modal 默认层级彼此相同, 同层时后挂载的盖住先挂载的. */
const CONFIRM_Z_INDEX = getDefaultZIndex("popover") + 1;

/** 屏幕居中确认框; 须在 MantineProvider 内挂载 `<ConfirmHost />`. */
export function confirm(options: ConfirmOptions): Promise<boolean> {
  return new Promise((resolve) => {
    if (bridge == null) {
      resolve(false);
      return;
    }
    bridge.open({ ...options, resolve });
  });
}

export function ConfirmHost() {
  const { t } = useTranslation("common");
  const [pending, setPending] = useState<PendingConfirm | null>(null);

  useEffect(() => {
    bridge = {
      open: (next) => {
        setPending((prev) => {
          prev?.resolve(false);
          return next;
        });
      },
    };
    return () => {
      bridge = null;
    };
  }, []);

  function close(ok: boolean) {
    setPending((prev) => {
      prev?.resolve(ok);
      return null;
    });
  }

  const danger = pending?.danger !== false;
  const confirmColor = pending?.confirmColor ?? (danger ? "red" : undefined);

  return (
    <Modal
      opened={pending != null}
      onClose={() => close(false)}
      title={pending?.title}
      centered
      size="sm"
      radius="md"
      zIndex={CONFIRM_Z_INDEX}
    >
      <Stack gap="md">
        <Text size="sm">{pending?.message}</Text>
        <Group justify="flex-end" gap="sm">
          <Button variant="default" onClick={() => close(false)}>
            {pending?.cancelLabel ?? t("actions.cancel")}
          </Button>
          <Button color={confirmColor} onClick={() => close(true)}>
            {pending?.confirmLabel ?? t("actions.confirm")}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
