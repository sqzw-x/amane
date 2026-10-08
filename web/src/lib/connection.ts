import type { QueryClient } from "@tanstack/react-query";

import { listMediaQueryKey, listTasksQueryKey } from "@/client/@tanstack/react-query.gen";
import { assertNever, exhaustiveTuple, isOneOf } from "@/lib/exhaustive";
import { isRecord } from "@/lib/utils";
import { useConnectionStore } from "@/stores/connection";
import { useLogStore } from "@/stores/logs";
import { useProgressStore } from "@/stores/progress";

/* oxlint-disable unicorn/prefer-add-event-listener --
 * WebSocket 用 on* 才能在 teardown 里 `onclose = null`, 避免 close() 触发重连.
 */

/** 对应后端 broadcast 的 event type. */
export type EventType =
  | "task.started"
  | "task.progress"
  | "task.completed"
  | "task.failed"
  | "file.discovered"
  | "file.removed"
  | "log";

const EVENT_TYPES = exhaustiveTuple<EventType>()(
  "task.started",
  "task.progress",
  "task.completed",
  "task.failed",
  "file.discovered",
  "file.removed",
  "log",
);

export interface WSEvent {
  type: EventType;
  data: Record<string, unknown>;
  timestamp: string;
}

export type EventHandler = (event: WSEvent) => void;

function parseWSEvent(raw: unknown): WSEvent | null {
  if (!isRecord(raw)) return null;
  if (typeof raw.type !== "string" || !isOneOf(EVENT_TYPES, raw.type)) return null;
  if (typeof raw.timestamp !== "string") return null;
  return {
    type: raw.type,
    data: isRecord(raw.data) ? raw.data : {},
    timestamp: raw.timestamp,
  };
}

// WebSocket URL: 握手携带 cookie, token 不写入查询串
function getWsUrl(): string {
  // 浏览器 WebSocket 无法自定义 header; 握手即同源 HTTP GET, 自动携带
  // amane_token cookie (首次 Bearer 认证后由服务端下发) — token 不写入 URL.
  const apiUrl = import.meta.env.VITE_API_URL;
  if (apiUrl) {
    const url = new URL(apiUrl);
    const wsProtocol = url.protocol === "https:" ? "wss:" : "ws:";
    return `${wsProtocol}//${url.host}/api/ws`;
  }
  const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${wsProtocol}//${window.location.host}/api/ws`;
}

const RECONNECT_BASE_DELAY = 1000;
const RECONNECT_MAX_DELAY = 30000;

/**
 * 模块级单例, 与 React 生命周期解耦.
 * 首个订阅者触发连接, 末个订阅者取消后断开.
 */
class ConnectionManager {
  private ws: WebSocket | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private attempt = 0;
  private handlers = new Set<EventHandler>();

  /** 已存在连接或重连定时器时不再发起. */
  start(): void {
    if (this.ws?.readyState === WebSocket.OPEN || this.ws?.readyState === WebSocket.CONNECTING) {
      return;
    }
    if (this.reconnectTimer) {
      return; // 已有重连定时器在等待
    }
    this.establish();
  }

  /** 注册事件处理器, 返回取消函数. 末个订阅者取消时断开连接. */
  subscribe(handler: EventHandler): () => void {
    this.handlers.add(handler);
    return () => {
      this.handlers.delete(handler);
      if (this.handlers.size === 0) {
        this.teardown();
      }
    };
  }

  private establish(): void {
    this.ws = new WebSocket(getWsUrl());

    this.ws.onopen = () => {
      this.attempt = 0;
      useConnectionStore.getState().setStatus("connected");
    };

    this.ws.onmessage = (event: MessageEvent) => {
      try {
        const parsed = parseWSEvent(JSON.parse(String(event.data)));
        if (!parsed) return;
        useConnectionStore.getState().setLastEventAt(Date.now());
        for (const handler of this.handlers) {
          handler(parsed);
        }
      } catch {
        // 忽略格式错误的消息, 不断开连接.
      }
    };

    this.ws.onclose = () => {
      useConnectionStore.getState().setStatus("disconnected");
      this.scheduleReconnect();
    };

    this.ws.onerror = () => {
      // onclose 在 onerror 之后触发, 由 onclose 统一处理重连
    };
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    const delay = Math.min(RECONNECT_BASE_DELAY * 2 ** this.attempt, RECONNECT_MAX_DELAY);
    this.attempt += 1;
    useConnectionStore.getState().setStatus("reconnecting");
    this.reconnectTimer = setTimeout(() => this.establish(), delay);
  }

  private teardown(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.onclose = null; // 阻止 onclose 触发 scheduleReconnect
      this.ws.close();
      this.ws = null;
    }
    this.attempt = 0;
    useConnectionStore.getState().setStatus("disconnected");
  }
}

export const connectionManager = new ConnectionManager();

/** 从 event.data 取数字字段, 非数字回退到默认值. */
function numField(data: Record<string, unknown>, key: string, fallback: number): number {
  const v = data[key];
  return typeof v === "number" ? v : fallback;
}

/**
 * 失效任务列表 + 全部展开的子节点 query.
 * hey-api 的 query key 是 `[{ _id, ... }]`, 前缀匹配必须用同形对象, 不能写 `["getTaskChildren"]`.
 */
export function invalidateTaskQueries(queryClient: QueryClient): void {
  queryClient.invalidateQueries({ queryKey: listTasksQueryKey() });
  queryClient.invalidateQueries({ queryKey: [{ _id: "getTaskChildren" }] });
  queryClient.invalidateQueries({ queryKey: [{ _id: "getTask" }] });
}

// 按 event.type 更新 store 并失效对应 query
function handleEvent(queryClient: QueryClient, event: WSEvent) {
  switch (event.type) {
    case "task.started":
      // id 复用: 删除任务后新建会复用其 id, 缓冲区可能残留已删除任务的日志.
      // task.started 在该任务产生任何日志前广播, 此刻清除同 id 的旧条目只会命中过时日志.
      useLogStore.getState().purgeTask(numField(event.data, "task_id", -1));
      invalidateTaskQueries(queryClient);
      break;
    case "task.progress": {
      // 高频事件: 写 client 态 store, 不 invalidate REST (见 frontend.md 状态边界)
      const taskId = numField(event.data, "task_id", -1);
      if (taskId >= 0) {
        useProgressStore.getState().setProgress(taskId, {
          current: numField(event.data, "current", 0),
          total: numField(event.data, "total", 0),
          message: typeof event.data.message === "string" ? event.data.message : "",
        });
      }
      break;
    }
    case "task.completed":
      useProgressStore.getState().clearProgress(numField(event.data, "task_id", -1));
      invalidateTaskQueries(queryClient);
      queryClient.invalidateQueries({ queryKey: listMediaQueryKey() });
      break;
    case "task.failed":
      useProgressStore.getState().clearProgress(numField(event.data, "task_id", -1));
      invalidateTaskQueries(queryClient);
      break;
    case "file.discovered":
      queryClient.invalidateQueries({ queryKey: listMediaQueryKey() });
      break;
    case "file.removed":
      queryClient.invalidateQueries({ queryKey: listMediaQueryKey() });
      break;
    case "log": {
      useLogStore.getState().addEntry(event.data);
      break;
    }
    default:
      assertNever(event.type, "event.type");
  }
}

const POLL_TASKS_INTERVAL = 5000;

// 断连降级为轮询
function setupFallbackPolling(queryClient: QueryClient): () => void {
  let taskInterval: ReturnType<typeof setInterval> | null = null;

  const start = () => {
    if (!taskInterval) {
      taskInterval = setInterval(() => {
        invalidateTaskQueries(queryClient);
      }, POLL_TASKS_INTERVAL);
    }
  };

  const stop = () => {
    if (taskInterval) {
      clearInterval(taskInterval);
      taskInterval = null;
    }
  };

  // 断连开启轮询, 恢复后停止
  const unsub = useConnectionStore.subscribe((state, prevState) => {
    if (state.status === prevState.status) return;
    if (state.status === "connected") {
      stop();
    } else {
      start();
    }
  });

  // 当前已断连则立刻开始
  if (useConnectionStore.getState().status !== "connected") {
    start();
  }

  return () => {
    unsub();
    stop();
  };
}

/**
 * 应用入口调用一次: WebSocket 单连接 + 事件分发 + 断连降级为轮询.
 * 返回的清理函数通常仅测试使用.
 */
export function initConnection(queryClient: QueryClient): () => void {
  const unsubEvent = connectionManager.subscribe((event) => {
    handleEvent(queryClient, event);
  });
  connectionManager.start();
  const unsubPolling = setupFallbackPolling(queryClient);

  return () => {
    unsubEvent();
    unsubPolling();
  };
}
