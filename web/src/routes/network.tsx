import { Stack, Title } from "@mantine/core";
import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { NetworkCheckPanel } from "@/components/network-check/network-check-panel";
import { useNetworkCheck } from "@/hooks/use-network-check";

export const Route = createFileRoute("/network")({
  component: NetworkCheckPage,
});

/** 检测页的信息量小于片库类页面, 宽屏下限制内容宽度并居中; 与插件页同宽, 同类页面共用版心. */
const PAGE_MAX_WIDTH = 1120;

/**
 * 网络检测.
 * 进入即检测: 没有结论时首屏直接是检测中 (圈 + 骨架行), 不留一个只有按钮、不承载信息的屏.
 * 页头只有标题, 页面自己说明自己是什么; 检测入口跟着结果走 —— 有结果时在汇总里挨着「上次检测」,
 * 只有请求本身失败 (端点 4xx/5xx) 时才回到正中那个按钮, 同屏只有一个.
 */
function NetworkCheckPage() {
  const { t } = useTranslation("networkCheck");
  const { report, checking, retryingSourceId, failed, check, retry } = useNetworkCheck();
  // 全量检测与行内重试共用同一个 mutation: 任一在途时就禁止发起第二个请求, 否则行状态会与实际请求错位.
  const busy = checking || retryingSourceId != null;
  // 只在进入页面时判断一次; ref 挡住 StrictMode 的第二次 effect 与后续重渲染.
  const autoStarted = useRef(false);

  useEffect(() => {
    if (autoStarted.current || report != null || failed || checking) {
      return;
    }
    autoStarted.current = true;
    check();
  }, [check, checking, failed, report]);

  return (
    <Stack gap="lg" maw={PAGE_MAX_WIDTH} mx="auto" w="100%">
      <Title order={2}>{t("title")}</Title>

      <NetworkCheckPanel
        report={report}
        checking={checking}
        busy={busy}
        failed={failed}
        retryingSourceId={retryingSourceId}
        onCheck={check}
        onRetry={retry}
      />
    </Stack>
  );
}
