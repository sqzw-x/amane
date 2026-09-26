import { notifications } from "@mantine/notifications";
import { useMutation } from "@tanstack/react-query";
import { useCallback } from "react";
import { useTranslation } from "react-i18next";
import { checkConnectivityMutation } from "@/client/@tanstack/react-query.gen";
import { extractErrorMessage } from "@/lib/api-error";
import { useNetworkCheckStore, type NetworkCheckReport } from "@/stores/network-check";

export interface NetworkCheckState {
  /** 尚未检测时为 null; 条目为空是「没有可探测的来源」, 与未检测区分. */
  report: NetworkCheckReport | null;
  checking: boolean;
  /** 正在重试的来源 id, 其余行为 null. */
  retryingSourceId: string | null;
  /** 请求本身失败 (网络中断 / 鉴权失效); 来源级的失败体现在条目里, 不会置起它. */
  failed: boolean;
  check: () => void;
  retry: (sourceId: string) => void;
}

/**
 * 逐来源的连通性探测, 由「网络检测」页消费.
 *
 * 端点始终返回 200, 单个来源的失败体现在条目里; 只有请求本身失败 (网络中断 / 鉴权失效) 才走 onError,
 * 此时保留上一次的结果并弹出提示 — 清空会让用户失去已经拿到的逐来源结论.
 *
 * 状态写在 `stores/network-check`: 本 hook 随路由卸载, 而在途标记必须在离开页面再回来时仍然是
 * 「检测中」 —— 请求没有随卸载停止, 丢掉标记会让用户以为可以再发一次. 结论同理.
 */
export function useNetworkCheck(): NetworkCheckState {
  const { t } = useTranslation("common");
  const report = useNetworkCheckStore((state) => state.report);
  const run = useNetworkCheckStore((state) => state.run);

  const { mutate, isError } = useMutation({
    ...checkConnectivityMutation(),
    onSuccess: (data, variables) => {
      const received = data.items ?? [];
      const requested = variables.body?.source_ids ?? null;
      const store = useNetworkCheckStore.getState();
      // 行内重试只提交一个来源; 其余情况都是全量检测.
      if (requested?.length === 1) {
        store.mergeSource(requested[0], received);
        return;
      }
      store.setReport(received);
    },
    onError: (error) => {
      notifications.show({
        color: "red",
        message: extractErrorMessage(error, t("toast.operationFailed")),
      });
    },
    onSettled: () => {
      useNetworkCheckStore.getState().setRun(null);
    },
  });

  const check = useCallback(() => {
    useNetworkCheckStore.getState().setRun({ kind: "all" });
    mutate({ body: {} });
  }, [mutate]);

  const retry = useCallback(
    (sourceId: string) => {
      useNetworkCheckStore.getState().setRun({ kind: "source", sourceId });
      mutate({ body: { source_ids: [sourceId] } });
    },
    [mutate],
  );

  return {
    report,
    checking: run?.kind === "all",
    retryingSourceId: run?.kind === "source" ? run.sourceId : null,
    failed: isError,
    check,
    retry,
  };
}
