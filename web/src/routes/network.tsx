import { Stack, Title } from "@mantine/core";
import { createFileRoute } from "@tanstack/react-router";
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
 * 页头只有标题, 页面自己说明自己是什么; 检测入口跟着结果走 —— 没结果时是正中那个按钮,
 * 有结果时在汇总里挨着「上次检测」, 同屏只有一个.
 */
function NetworkCheckPage() {
  const { t } = useTranslation("networkCheck");
  const { report, checking, retryingSourceId, check, retry } = useNetworkCheck();
  // 全量检测与行内重试共用同一个 mutation: 任一在途时就禁止发起第二个请求, 否则行状态会与实际请求错位.
  const busy = checking || retryingSourceId != null;

  return (
    <Stack gap="lg" maw={PAGE_MAX_WIDTH} mx="auto" w="100%">
      <Title order={2}>{t("title")}</Title>

      <NetworkCheckPanel
        report={report}
        checking={checking}
        busy={busy}
        retryingSourceId={retryingSourceId}
        onCheck={check}
        onRetry={retry}
      />
    </Stack>
  );
}
