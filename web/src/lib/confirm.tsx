import { Button, Checkbox, getDefaultZIndex, Group, Modal, Stack, Text } from "@mantine/core";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

export type ConfirmCheckbox = {
  label: string;
  /** 默认勾选状态; 省略为不勾选. */
  defaultChecked?: boolean;
};

export type ConfirmOptions = {
  title?: string;
  /** 单句说明, 或条目清单. */
  message: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  /** 确认钮颜色; 未设置时 danger=true 用 red. */
  confirmColor?: string;
  /** 危险操作. 默认 true. */
  danger?: boolean;
  /** 底部的单个复选框; 状态须经 `confirmWithCheckbox` 读取. */
  checkbox?: ConfirmCheckbox;
};

export type ConfirmResult = {
  ok: boolean;
  checked: boolean;
};

type PendingConfirm = ConfirmOptions & {
  resolve: (result: ConfirmResult) => void;
};

type ConfirmBridge = {
  open: (pending: PendingConfirm) => void;
};

let bridge: ConfirmBridge | null = null;

/** 确认框须盖住任何已打开的弹窗: Modal 默认层级彼此相同, 同层时后挂载的盖住先挂载的. */
const CONFIRM_Z_INDEX = getDefaultZIndex("popover") + 1;

function openConfirm(options: ConfirmOptions): Promise<ConfirmResult> {
  return new Promise((resolve) => {
    if (bridge == null) {
      resolve({ ok: false, checked: options.checkbox?.defaultChecked ?? false });
      return;
    }
    bridge.open({ ...options, resolve });
  });
}

/** 屏幕居中确认框; 须在 MantineProvider 内挂载 `<ConfirmHost />`. */
export function confirm(options: ConfirmOptions): Promise<boolean> {
  return openConfirm(options).then((result) => result.ok);
}

/** 带复选框的确认框; 返回确认结果与复选框状态. */
export function confirmWithCheckbox(
  options: ConfirmOptions & { checkbox: ConfirmCheckbox },
): Promise<ConfirmResult> {
  return openConfirm(options);
}

export function ConfirmHost() {
  const { t } = useTranslation("common");
  const [pending, setPending] = useState<PendingConfirm | null>(null);
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    bridge = {
      open: (next) => {
        setPending((prev) => {
          prev?.resolve({ ok: false, checked: false });
          return next;
        });
        setChecked(next.checkbox?.defaultChecked ?? false);
      },
    };
    return () => {
      bridge = null;
    };
  }, []);

  function close(ok: boolean) {
    setPending((prev) => {
      prev?.resolve({ ok, checked });
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
        {/* component="div": 清单消息会传 <ul>, 套在 <p> 里是非法嵌套 */}
        <Text size="sm" component="div">
          {pending?.message}
        </Text>
        {pending?.checkbox && (
          <Checkbox
            label={pending.checkbox.label}
            checked={checked}
            onChange={(event) => setChecked(event.currentTarget.checked)}
          />
        )}
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
