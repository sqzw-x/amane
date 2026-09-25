import { notifications } from "@mantine/notifications";
import { useMutation } from "@tanstack/react-query";
import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { checkConnectivityMutation } from "@/client/@tanstack/react-query.gen";
import type { ConnectivityItemResponse } from "@/client/types.gen";
import { extractErrorMessage } from "@/lib/api-error";

/** 全量检测与行内重试互斥, 因此同一时刻只记录一个在途目标. */
type Running = { kind: "all" } | { kind: "source"; sourceId: string };

export interface ConnectivityState {
  /** 尚未检测时为 null; 空数组是「没有可探测的来源」, 与未检测区分. */
  items: ConnectivityItemResponse[] | null;
  /** 最近一次全量检测完成的时间戳 (毫秒). */
  checkedAt: number | null;
  checking: boolean;
  /** 正在重试的来源 id, 其余行为 null. */
  retryingSourceId: string | null;
  check: () => void;
  retry: (sourceId: string) => void;
}

/**
 * 来源连通性检测.
 *
 * 端点始终返回 200, 单个来源的失败体现在条目里; 只有请求本身失败 (网络中断 / 鉴权失效) 才走 onError,
 * 此时保留上一次的结果并弹出提示 — 清空会让用户失去已经拿到的逐来源结论.
 *
 * 结果只存在于组件树中: 探测结论随配置与网络变化, 持久化会展示过期结论.
 */
export function useConnectivity(): ConnectivityState {
  const { t } = useTranslation("common");
  const [items, setItems] = useState<ConnectivityItemResponse[] | null>(null);
  const [checkedAt, setCheckedAt] = useState<number | null>(null);
  const [running, setRunning] = useState<Running | null>(null);

  const { mutate } = useMutation({
    ...checkConnectivityMutation(),
    onSuccess: (data, variables) => {
      const received = data.items ?? [];
      const requested = variables.body?.source_ids ?? null;
      // 行内重试只覆盖被重试的来源, 其余行保持原结论与相对顺序.
      if (requested?.length === 1) {
        const sourceId = requested[0];
        setItems((current) =>
          current == null
            ? received
            : current.map(
                (item) =>
                  (item.source_id === sourceId
                    ? received.find((row) => row.source_id === sourceId)
                    : undefined) ?? item,
              ),
        );
        return;
      }
      setItems(received);
      setCheckedAt(Date.now());
    },
    onError: (error) => {
      notifications.show({
        color: "red",
        message: extractErrorMessage(error, t("toast.operationFailed")),
      });
    },
    onSettled: () => {
      setRunning(null);
    },
  });

  const check = useCallback(() => {
    setRunning({ kind: "all" });
    mutate({ body: {} });
  }, [mutate]);

  const retry = useCallback(
    (sourceId: string) => {
      setRunning({ kind: "source", sourceId });
      mutate({ body: { source_ids: [sourceId] } });
    },
    [mutate],
  );

  return {
    items,
    checkedAt,
    checking: running?.kind === "all",
    retryingSourceId: running?.kind === "source" ? running.sourceId : null,
    check,
    retry,
  };
}
