/**
 * 全局图片加载信号量.
 *
 * 浏览器对同一 host 的 HTTP/1.1 连接数有限 (Chrome 6), 而 proxy-image 请求
 * 可能挂起数秒 (上游慢/失效, 首次下载). 若不限流, 大量图片请求会占满连接池,
 * 同 host 的 API 请求 (列表/详情/任务) 全部排队, 表现为页面转圈/加载不出内容.
 *
 * 经 `nextHopProtocol` 探测实际协商协议: HTTP/2+ 单连接多路复用, 无连接池
 * 瓶颈, 直接放行; 仅 http/1.1 下把 proxy 图片请求限制为
 * MAX_CONCURRENT_PROXY_IMAGES 个并发, 始终给 API 请求留出连接.
 * 本地资源 (`/api/resources/*`) 秒回, 不经此队列.
 */

import { useCallback, useEffect, useRef, useState } from "react";

const MAX_CONCURRENT_PROXY_IMAGES = 4;

// 浏览器与服务器实际协商的 HTTP 版本. HTTP/2+ 单连接多路复用 (Chrome 默认
// 100 并发流), 不存在 6 连接瓶颈, 无需限流; 仅 http/1.1 下保护连接池.
// 探测前按 http/1.1 保守处理 (问题场景正是 http/1.1).
type Transport = "unknown" | "h1" | "h2plus";
let transport: Transport = "unknown";

function detectTransport(): void {
  if (transport !== "unknown") return;
  for (const entry of performance.getEntriesByType("resource")) {
    const proto = (entry as PerformanceResourceTiming).nextHopProtocol;
    if (!proto) continue;
    transport = proto.startsWith("h2") || proto.startsWith("h3") ? "h2plus" : "h1";
    return;
  }
}

let active = 0;
const waiters: Array<() => void> = [];

function acquire(): Promise<void> {
  detectTransport();
  if (transport === "h2plus") return Promise.resolve();
  if (active < MAX_CONCURRENT_PROXY_IMAGES) {
    active += 1;
    return Promise.resolve();
  }
  // tsconfig lib 为 ES2023, Promise.withResolvers (ES2024) 不可用, 用 executor 形式
  return new Promise<void>((resolve) => {
    waiters.push(() => {
      active += 1;
      resolve();
    });
  });
}

export function releaseImageSlot(): void {
  // h2+ 模式下 acquire 不计数, 此处保证 active 不为负 (幂等)
  if (active > 0) active -= 1;
  const next = waiters.shift();
  if (next) next();
}

/**
 * 排队加载图片 URL: 取得信号量前返回 null, 取得后返回 url.
 *
 * 调用方在图片 onLoad / onError 时调用 release (请求结束, 让出连接);
 * 组件卸载时自动释放 (浏览器会取消未挂载 img 的请求并释放连接).
 *
 * 设置 src 即视为请求已开始, 与 loading=lazy 互斥: lazy 会让屏外 img 占槽不发请求.
 * ProxyImage 只对邻近视口的 URL 才传入此 hook.
 */
export function useQueuedImageUrl(url: string | null | undefined): {
  src: string | null;
  release: () => void;
} {
  const [src, setSrc] = useState<string | null>(null);
  const releasedRef = useRef(true);

  useEffect(() => {
    if (!url) return;
    let cancelled = false;
    void acquire().then(() => {
      if (cancelled) {
        releaseImageSlot();
        return;
      }
      releasedRef.current = false; // 持有 ticket 后才标记, 避免 url 往返变化时误释放
      setSrc(url);
    });
    return () => {
      cancelled = true;
      if (!releasedRef.current) {
        releasedRef.current = true;
        releaseImageSlot();
      }
    };
  }, [url]);

  const release = useCallback(() => {
    if (!releasedRef.current) {
      releasedRef.current = true;
      releaseImageSlot();
    }
  }, []);

  return { src: src === url ? src : null, release };
}

export const PROXY_IMAGE_PREFIX = "/api/resources/proxy";
