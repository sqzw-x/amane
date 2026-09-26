import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import type { ConnectivityItemResponse } from "@/client/types.gen";

/** 全量检测与行内重试互斥, 因此同一时刻只记录一个在途目标. */
export type NetworkCheckRun = { kind: "all" } | { kind: "source"; sourceId: string };

/**
 * 一次检测的结论.
 * 条目与「检测时间」同生共死, 所以合成一个对象: 只有条目没有时间的中间态在页面上无法表达.
 */
export interface NetworkCheckReport {
  items: ConnectivityItemResponse[];
  /** 该次检测完成的时间戳 (毫秒). */
  checkedAt: number;
}

interface NetworkCheckStore {
  /** 尚未检测时为 null; 空条目是「没有可探测的来源」, 与未检测区分. */
  report: NetworkCheckReport | null;
  run: NetworkCheckRun | null;
  /** 全量检测的结果整体替换, 并记为一次新的检测. */
  setReport: (items: ConnectivityItemResponse[]) => void;
  /** 行内重试只覆盖被重试的来源, 其余行保持原结论与相对顺序; 不计入「上次检测」. */
  mergeSource: (sourceId: string, items: ConnectivityItemResponse[]) => void;
  setRun: (run: NetworkCheckRun | null) => void;
}

const STORAGE_KEY = "amane-network-check";

/**
 * 网络检测的结论.
 *
 * 结论必须活过一次离开页面 (否则每次进入都要重跑一遍检测, 汇总区的「上次检测」也没有意义),
 * 因此不放在路由组件的 state 里. 但它同时随配置与网络变化, 长期留存会让过期结论继续以结论的
 * 样子出现 —— 折中是 `sessionStorage`: 路由切换与刷新都保留, 标签页关掉即消失, 不跨会话.
 *
 * `run` 不入存储: 重载后没有请求会回来清掉在途标记, 页面会永久停在「检测中」.
 */
export const useNetworkCheckStore = create<NetworkCheckStore>()(
  persist(
    (set) => ({
      report: null,
      run: null,
      setReport: (items) => set({ report: { items, checkedAt: Date.now() } }),
      mergeSource: (sourceId, received) =>
        set((state) => {
          if (state.report == null) {
            return { report: { items: received, checkedAt: Date.now() } };
          }
          return {
            report: {
              checkedAt: state.report.checkedAt,
              items: state.report.items.map(
                (item) =>
                  (item.source_id === sourceId
                    ? received.find((row) => row.source_id === sourceId)
                    : undefined) ?? item,
              ),
            },
          };
        }),
      setRun: (run) => set({ run }),
    }),
    {
      name: STORAGE_KEY,
      storage: createJSONStorage(() => sessionStorage),
      partialize: (state) => ({ report: state.report }),
    },
  ),
);
