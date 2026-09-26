import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import type { ConnectivityItemResponse, ConnectivityStatus } from "@/client/types.gen";

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
const STATUSES: readonly ConnectivityStatus[] = ["ok", "failed", "skipped"];

/**
 * 存储里的形状校验.
 * 反序列化不校验时, 旧形状 (API 字段改名后仍开着的标签页) 会让状态的图标与排序取到 undefined,
 * 前者被当成组件渲染, 后者让比较函数返回 NaN; 名称类字段缺失则渲染出空标签. 校验不过就按未检测
 * 处理, 比让页面崩掉或显示一屏空行便宜.
 */
function isStoredReport(value: unknown): value is NetworkCheckReport {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const report = value as Partial<NetworkCheckReport>;
  return (
    typeof report.checkedAt === "number" &&
    Array.isArray(report.items) &&
    report.items.every(
      (item) =>
        item != null &&
        STATUSES.includes(item.status) &&
        typeof item.kind === "string" &&
        typeof item.source_id === "string",
    )
  );
}

/**
 * 网络检测的结论.
 *
 * 结论必须活过一次离开页面 (否则每次进入都要重跑一遍检测, 汇总区的「上次检测」也没有意义),
 * 因此不放在路由组件的 state 里. 但它同时随配置与网络变化, 长期留存会让过期结论继续以结论的
 * 样子出现 —— 折中是 `sessionStorage`: 路由切换与刷新都保留, 标签页关掉即消失, 不跨会话.
 *
 * 在途标记同样不持久化, 但归 store 管: 它要跨组件卸载存在 —— 离开页面再回来时请求仍在飞, 只有
 * store 能继续把它显示为「检测中」并挡住第二个请求; 页面重载后没有请求会回来清掉它, 因此不入存储.
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
      merge: (persisted, current) => {
        const stored = (persisted as { report?: unknown } | undefined)?.report;
        return isStoredReport(stored) ? { ...current, report: stored } : current;
      },
    },
  ),
);
