import { Button, Code, Modal, Stack, Text } from "@mantine/core";
import { IconRulerMeasure } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

/** 自检弹窗里的填充高度: 必须高于任何手机视口, 否则测不出上限与滚动. */
const FILLER_HEIGHT = 1600;

/**
 * 布局自检: 用真实的 Mantine 弹窗测一遍决定高度与滚动的计算值, 并在末尾放一行标记.
 *
 * 存在理由: 厂商自带的 WebView () 对 `dvh`、`var()` 回退与内联样式的处理各不相同, 会让"弹窗没有上限
 * 也不滚动"这类问题只在真机复现. 用户在这里截图或读数即可定位, 不必猜内核行为.
 */
function measurements(): string[] {
  const content = document.querySelector("[data-modal-content]");
  const root = getComputedStyle(document.documentElement);
  const style = content ? getComputedStyle(content) : null;
  return [
    `--amane-vh: ${root.getPropertyValue("--amane-vh").trim() || "(unset)"}`,
    `--modal-y-offset: ${style?.getPropertyValue("--modal-y-offset").trim() || "(unset)"}`,
    `max-height: ${style?.maxHeight ?? "(no modal)"}`,
    `overflow-y: ${style?.overflowY ?? "(no modal)"}`,
    `clientHeight: ${content?.clientHeight ?? "-"}`,
    `scrollHeight: ${content?.scrollHeight ?? "-"}`,
    `innerHeight: ${window.innerHeight}`,
  ];
}

export function LayoutCheck() {
  const { t } = useTranslation("settings");
  const [opened, setOpened] = useState(false);
  const [values, setValues] = useState<string[] | null>(null);

  useEffect(() => {
    if (!opened) return;
    // 等弹窗完成布局再读计算值 (过渡动画期间读到的是中间态).
    const timer = window.setTimeout(() => setValues(measurements()), 350);
    return () => window.clearTimeout(timer);
  }, [opened]);

  const close = () => {
    setOpened(false);
    setValues(null);
  };

  return (
    <>
      <Stack gap={4}>
        <Text fw={600}>{t("client.layoutCheck")}</Text>
        <Text size="xs" c="dimmed">
          {t("client.layoutCheckHint")}
        </Text>
        <Button
          size="xs"
          variant="light"
          w="fit-content"
          leftSection={<IconRulerMeasure size={14} />}
          onClick={() => setOpened(true)}
        >
          {t("client.layoutCheckRun")}
        </Button>
      </Stack>

      <Modal opened={opened} onClose={close} title={t("client.layoutCheck")}>
        <Stack gap="sm">
          <Code block>{values == null ? t("client.layoutCheckPending") : values.join("\n")}</Code>
          <div style={{ height: FILLER_HEIGHT }} aria-hidden="true" />
          <Text size="sm" fw={600} c="teal">
            {t("client.layoutCheckBottom")}
          </Text>
        </Stack>
      </Modal>
    </>
  );
}
