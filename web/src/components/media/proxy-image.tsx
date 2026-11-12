import { useEffect, useRef, useState, type ImgHTMLAttributes, type ReactNode } from "react";
import { PROXY_IMAGE_PREFIX, useQueuedImageUrl } from "@/lib/image-loader";

export interface ProxyImageProps extends ImgHTMLAttributes<HTMLImageElement> {
  /** 排队等待期间渲染的内容. 默认空 div: 在 Mantine AspectRatio 内会被
   *  施加 aspect-ratio 撑起高度 (容器高度由唯一直接子元素决定, 渲染 null
   *  会让卡片塌陷, 破坏 scrollRestoration 依赖的文档高度). */
  placeholder?: ReactNode;
}

/** 邻近视口才申请全局槽; 与哨兵 200px 错开, 让将入视口的图先排到队列前面. */
const VIEWPORT_ROOT_MARGIN_PX = 400;

/**
 * <img> 包装: 外链反代图 (proxy-image) 经全局信号量限流加载,
 * 避免大量慢速图片请求占满浏览器 HTTP/1.1 连接池阻塞同 host 的 API 请求
 * (见 lib/image-loader.ts). 本地资源 (`/api/resources/*`) 直接渲染不排队.
 *
 * 排队期间渲染 placeholder (默认空 div) 且不发请求; src 就绪后按正常
 * <img> 行为加载, onLoad/onError 释放信号量槽位.
 *
 * 只对进入/邻近视口的 proxy 图 acquire. 若先设置 src 再靠 loading=lazy,
 * 屏外图占着槽却不发请求, 视口内的图会一直空白.
 */
export function ProxyImage({
  src,
  onLoad,
  onError,
  placeholder = <div aria-hidden />,
  loading,
  ...rest
}: ProxyImageProps) {
  const isProxied = Boolean(src && src.startsWith(PROXY_IMAGE_PREFIX));
  const hostRef = useRef<HTMLDivElement>(null);
  const [nearViewport, setNearViewport] = useState(!isProxied);
  if (!isProxied && !nearViewport) {
    setNearViewport(true);
  }

  useEffect(() => {
    if (!isProxied) return;
    const el = hostRef.current;
    if (!el) return;

    const margin = VIEWPORT_ROOT_MARGIN_PX;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setNearViewport(true);
          observer.disconnect();
        }
      },
      { rootMargin: `${margin}px` },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [isProxied]);

  const { src: queuedSrc, release } = useQueuedImageUrl(isProxied && nearViewport ? src : null);

  if (isProxied) {
    if (!queuedSrc) {
      return (
        <div
          ref={hostRef}
          aria-hidden
          className={rest.className}
          style={{ display: "block", width: "100%", height: "100%", ...rest.style }}
        >
          {placeholder}
        </div>
      );
    }
    return (
      <img
        {...rest}
        src={queuedSrc}
        fetchPriority="low"
        onLoad={(e) => {
          release();
          onLoad?.(e);
        }}
        onError={(e) => {
          release();
          onError?.(e);
        }}
      />
    );
  }
  if (!src) return placeholder;
  // onLoad/onError 已从 rest 中解构, 本分支要显式接回: 本地资源图的自然尺寸与失败回退同样依赖它们
  return <img {...rest} src={src} loading={loading} onLoad={onLoad} onError={onError} />;
}
