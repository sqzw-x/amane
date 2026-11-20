import { ActionIcon, Box, Group, Loader, Portal, Text } from "@mantine/core";
import { useDisclosure, useHotkeys } from "@mantine/hooks";
import {
  IconChevronLeft,
  IconChevronRight,
  IconX,
  IconZoomIn,
  IconZoomOut,
} from "@tabler/icons-react";
import { useTranslation } from "react-i18next";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type MouseEvent,
  type PointerEvent,
  type ReactNode,
  type WheelEvent,
} from "react";
import { proxyImageUrl } from "@/lib/utils";
import { useQueuedImageUrl } from "@/lib/image-loader";
import { ProxyImage } from "@/components/media/proxy-image";
import { HintedActionIcon } from "@/components/common/hinted-action-icon";
import {
  focalFraction,
  focalScrollOffset,
  isDragGesture,
  isTwoFingerTap,
  maskClickCloses,
  nextZoomTier,
  swipeEnabled,
  type ZoomTier,
} from "./fanart-lightbox-zoom";

/** 触屏切换的判定: 水平位移超过它, 且明显偏水平 (纵向留给滚动画面). */
const SWIPE_MIN_DISTANCE_PX = 48;

/** 舞台留白: 图片不贴边, 同时给悬浮按钮让出位置. */
const STAGE_PADDING_Y = "calc(var(--amane-vh) * 0.05)";
const STAGE_PADDING_X = 64;

/** 一次触摸手势: 各指针的起点、起手时刻、最多同时按下几指与已发生的最大位移. */
interface TouchGesture {
  at: number;
  starts: Map<number, { x: number; y: number }>;
  maxPointers: number;
  maxMovedPx: number;
}

interface FanartLightboxProps {
  images: string[];
  initialIndex?: number;
  onClose: () => void;
}

/** Fullscreen image viewer with keyboard prev/next/escape and intrinsic resolution. */
export function FanartLightbox({ images, initialIndex = 0, onClose }: FanartLightboxProps) {
  const { t } = useTranslation("common");
  const [index, setIndex] = useState(() =>
    Math.min(Math.max(initialIndex, 0), Math.max(images.length - 1, 0)),
  );
  const [loading, setLoading] = useState(true);
  const [resolution, setResolution] = useState<{ w: number; h: number } | null>(null);
  const [tier, setTier] = useState<ZoomTier>("fit");
  const imgRef = useRef<HTMLImageElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  /** 放大时手势处的比例; 尺寸变化后据此重新落位 (缩放前后图片几何不同, 不能预先算出位置). */
  const pendingFocalRef = useRef<{ x: number; y: number } | null>(null);

  const gestureRef = useRef<TouchGesture | null>(null);
  const gestureHandledAtRef = useRef(0);

  const goTo = useCallback(
    (step: number) => {
      setIndex((i) => {
        const next = i + step;
        if (next < 0) return images.length - 1;
        if (next > images.length - 1) return 0;
        return next;
      });
    },
    [images.length],
  );

  /** 切换档位; 放大时记下点按处 (未给出则为画面中心) 在图片上的比例, 绘制后据此落位. */
  const toggleTier = useCallback(
    (focal: { clientX: number; clientY: number } | null) => {
      const next = nextZoomTier(tier);
      const rect = imgRef.current?.getBoundingClientRect();
      if (next === "actual" && rect) {
        const point = focal ?? {
          clientX: rect.left + rect.width / 2,
          clientY: rect.top + rect.height / 2,
        };
        pendingFocalRef.current = {
          x: focalFraction(point.clientX, rect.left, rect.width),
          y: focalFraction(point.clientY, rect.top, rect.height),
        };
      } else {
        pendingFocalRef.current = null;
      }
      setTier(next);
    },
    [tier],
  );

  // 拖动超过阈值视为滚动画面, 其后的 click 不得关闭预览.
  const noteDrag = (dx: number, dy: number) => {
    if (isDragGesture(Math.max(Math.abs(dx), Math.abs(dy)))) {
      gestureHandledAtRef.current = Date.now();
      return true;
    }
    return false;
  };

  const handlePointerDown = (event: PointerEvent<HTMLDivElement>) => {
    // 鼠标的按钮与方向键原样保留, 只接管触摸.
    if (event.pointerType !== "touch") return;
    const gesture = gestureRef.current;
    if (gesture === null) {
      gestureRef.current = {
        at: event.timeStamp,
        starts: new Map([[event.pointerId, { x: event.clientX, y: event.clientY }]]),
        maxPointers: 1,
        maxMovedPx: 0,
      };
      return;
    }
    gesture.starts.set(event.pointerId, { x: event.clientX, y: event.clientY });
    gesture.maxPointers = Math.max(gesture.maxPointers, gesture.starts.size);
  };

  const handlePointerUp = (event: PointerEvent<HTMLDivElement>) => {
    const gesture = gestureRef.current;
    if (event.pointerType !== "touch" || gesture === null) return;
    const start = gesture.starts.get(event.pointerId);
    gesture.starts.delete(event.pointerId);
    if (start === undefined) return;
    const dx = event.clientX - start.x;
    const dy = event.clientY - start.y;
    gesture.maxMovedPx = Math.max(gesture.maxMovedPx, Math.abs(dx), Math.abs(dy));
    // 还有手指按着: 等最后一个抬起再裁决, 否则双指点按会先被当成单指手势.
    if (gesture.starts.size > 0) return;
    gestureRef.current = null;

    // 双指同时落下又同时抬起算一次点按; 三指及以上或带拖动都不算.
    if (
      gesture.maxPointers === 2 &&
      isTwoFingerTap(gesture.maxMovedPx, event.timeStamp - gesture.at)
    ) {
      gestureHandledAtRef.current = Date.now();
      toggleTier({ clientX: event.clientX, clientY: event.clientY });
      return;
    }

    // 横向滑动切图优先于拖动判定: 两者位移都超过各自的阈值, 但只有明显偏横向的才算切图.
    // 1:1 档下横向拖动是在查看画面, 因此不参与.
    if (
      swipeEnabled(tier) &&
      Math.abs(dx) >= SWIPE_MIN_DISTANCE_PX &&
      Math.abs(dx) > Math.abs(dy)
    ) {
      gestureHandledAtRef.current = Date.now();
      goTo(dx < 0 ? 1 : -1);
      return;
    }

    // 其余超过阈值的触摸是拖动 (滚动画面), 收尾后的 click 不得关闭预览.
    // 双击由图片自身的 dblclick 处理: 触摸与鼠标都会补发, 与 click 无关.
    noteDrag(dx, dy);
  };

  // Ctrl + 滚轮与浏览器缩放冲突, 拦下后切换档位; 其余滚轮交给舞台原生滚动.
  const handleWheel = (event: WheelEvent<HTMLDivElement>) => {
    if (!event.ctrlKey) return;
    event.preventDefault();
    toggleTier({ clientX: event.clientX, clientY: event.clientY });
  };

  const src = images.length > 0 ? (proxyImageUrl(images[index]) ?? images[index]) : "";
  const { src: queuedSrc, release } = useQueuedImageUrl(src);

  const [seenSrc, setSeenSrc] = useState(queuedSrc);
  if (queuedSrc !== seenSrc) {
    setSeenSrc(queuedSrc);
    setLoading(true);
    setResolution(null);
    // 换图回到适应档: 上一张的放大比例与新图无关.
    setTier("fit");
  }

  // 缓存命中在 layout 阶段同步完成 (勿在 ref 回调里 setState).
  useLayoutEffect(() => {
    if (!queuedSrc) return;
    const el = imgRef.current;
    if (el?.complete && el.naturalWidth > 0) {
      setResolution({ w: el.naturalWidth, h: el.naturalHeight });
      setLoading(false);
    }
  }, [queuedSrc]);

  // 档位变化并完成绘制后, 按新尺寸把记下的比例送回视口中心;
  // 超出的部分由浏览器收敛到可滚范围, 因此不需要自行夹取.
  useLayoutEffect(() => {
    const focal = pendingFocalRef.current;
    pendingFocalRef.current = null;
    const stage = stageRef.current;
    const img = imgRef.current;
    if (!stage || !img) return;
    if (focal === null) {
      stage.scrollTo({ left: 0, top: 0 });
      return;
    }
    const stageRect = stage.getBoundingClientRect();
    const rect = img.getBoundingClientRect();
    stage.scrollTo({
      left: focalScrollOffset({
        contentOffset: rect.left - stageRect.left + stage.scrollLeft,
        contentSize: rect.width,
        viewportSize: stage.clientWidth,
        fraction: focal.x,
      }),
      top: focalScrollOffset({
        contentOffset: rect.top - stageRect.top + stage.scrollTop,
        contentSize: rect.height,
        viewportSize: stage.clientHeight,
        fraction: focal.y,
      }),
    });
  }, [tier]);

  // 预览铺满视口并自管滚动, 背后的页面不得跟着滚动 (遮罩不可滚, 不加锁则手势穿透).
  useEffect(() => {
    const root = document.documentElement;
    const previousOverflow = root.style.overflow;
    root.style.overflow = "hidden";
    return () => {
      root.style.overflow = previousOverflow;
    };
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      e.stopPropagation();
      onClose();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  useHotkeys([
    ["ArrowLeft", () => goTo(-1)],
    ["ArrowRight", () => goTo(1)],
  ]);

  if (images.length === 0 || !src) return null;

  const multi = images.length > 1;

  // 1:1 档按源图像素显示, 上限即自然宽度 (源图本身不放大).
  const imageStyle: CSSProperties =
    tier === "actual"
      ? {
          width: resolution ? `${resolution.w}px` : undefined,
          maxWidth: "none",
          maxHeight: "none",
        }
      : {
          maxHeight: "calc(var(--amane-vh) * 0.9)",
          maxWidth: `calc(100vw - ${STAGE_PADDING_X * 2}px)`,
          objectFit: "contain",
        };

  // Portal 出 Modal: 打开态带 transform, 会把 position:fixed 的包含块收成弹窗,
  // 大图被 overflow 裁切. 高于 Overlay(400) / Affix, 低于 max.
  // 拦截 mousedown 以免点预览被 Modal 当成 click-outside.
  return (
    <Portal>
      <Box
        pos="fixed"
        inset={0}
        style={{ zIndex: 500, background: "rgba(0,0,0,0.92)" }}
        onMouseDown={(e) => e.stopPropagation()}
        onPointerDown={handlePointerDown}
        onPointerUp={handlePointerUp}
        onPointerCancel={() => {
          // 手势被系统取消: 丢弃起点, 收尾后的 click 同样不得关闭预览.
          gestureRef.current = null;
          gestureHandledAtRef.current = Date.now();
        }}
        onClick={(e) => {
          e.stopPropagation();
          // 刚结束的滑动、拖动或双击不算点击: 浏览器随后可能仍补一次 click.
          if (!maskClickCloses(Date.now() - gestureHandledAtRef.current)) return;
          onClose();
        }}
      >
        {/*
          舞台: 图片矮时由 margin:auto 居中, 长图在档位内滚动.
          overscroll-behavior 阻断滚动链, 嵌套在 Modal 里时同样不把 Modal body 一起滚走.
        */}
        <Box
          ref={stageRef}
          pos="absolute"
          inset={0}
          onWheel={handleWheel}
          style={{
            overflow: "auto",
            overscrollBehavior: "contain",
            WebkitOverflowScrolling: "touch",
            // 适应档只放纵向平移: 横向留给切图手势. 两档都声明 pinch-zoom,
            // 浏览器双指缩放因此继续可用 (放大查看由档位承担, 不与之互斥).
            touchAction: swipeEnabled(tier) ? "pan-y pinch-zoom" : "pan-x pan-y pinch-zoom",
            display: "flex",
            padding: `${STAGE_PADDING_Y} ${STAGE_PADDING_X}px`,
            cursor: tier === "actual" ? "zoom-out" : "zoom-in",
          }}
        >
          {loading && (
            <Loader
              color="gray"
              style={{
                position: "absolute",
                inset: 0,
                margin: "auto",
                zIndex: 1,
                pointerEvents: "none",
              }}
            />
          )}
          <img
            ref={imgRef}
            key={queuedSrc}
            src={queuedSrc ?? undefined}
            alt={`fanart-${index}`}
            referrerPolicy="no-referrer"
            style={{
              ...imageStyle,
              margin: "auto",
              pointerEvents: "auto",
              opacity: loading ? 0 : 1,
              transition: "opacity 150ms ease",
            }}
            // 图片本身不关闭预览: 单击停在这里, 双击才切档.
            // click 先于 dblclick 到达, 因此第一次 click 不得关闭预览.
            onClick={(e) => e.stopPropagation()}
            onDoubleClick={(e) => {
              e.stopPropagation();
              // 双击会自动补两次 click, 都算作双击的一部分.
              gestureHandledAtRef.current = Date.now();
              toggleTier({ clientX: e.clientX, clientY: e.clientY });
            }}
            onLoad={(e) => {
              release();
              const img = e.currentTarget;
              setResolution(
                img.naturalWidth > 0 && img.naturalHeight > 0
                  ? { w: img.naturalWidth, h: img.naturalHeight }
                  : null,
              );
              setLoading(false);
            }}
            onError={() => {
              release();
              setResolution(null);
              setLoading(false);
            }}
          />
        </Box>

        <ActionIcon
          variant="subtle"
          color="gray"
          size="lg"
          pos="absolute"
          top={16}
          right={16}
          style={{ zIndex: 1 }}
          onClick={onClose}
          aria-label={t("actions.close")}
        >
          <IconX size={20} />
        </ActionIcon>

        <Group
          gap="sm"
          pos="absolute"
          top={20}
          left="50%"
          style={{ transform: "translateX(-50%)", zIndex: 1 }}
          wrap="nowrap"
        >
          <Text size="sm" c="dimmed">
            {index + 1} / {images.length}
          </Text>
          {resolution && (
            <Text size="sm" c="gray.5">
              {resolution.w} × {resolution.h}
            </Text>
          )}
        </Group>

        {resolution && (
          <HintedActionIcon
            variant="subtle"
            color="gray"
            size="lg"
            pos="absolute"
            bottom={16}
            right={16}
            style={{ zIndex: 1 }}
            label={t(tier === "fit" ? "actions.zoomIn" : "actions.zoomOut")}
            onClick={(e) => {
              e.stopPropagation();
              toggleTier(null);
            }}
          >
            {tier === "fit" ? <IconZoomIn size={20} /> : <IconZoomOut size={20} />}
          </HintedActionIcon>
        )}

        {multi && swipeEnabled(tier) && (
          <ActionIcon
            variant="subtle"
            color="gray"
            size="xl"
            pos="absolute"
            left={16}
            top="50%"
            style={{ transform: "translateY(-50%)", zIndex: 1 }}
            onClick={(e) => {
              e.stopPropagation();
              goTo(-1);
            }}
          >
            <IconChevronLeft size={28} />
          </ActionIcon>
        )}

        {multi && swipeEnabled(tier) && (
          <ActionIcon
            variant="subtle"
            color="gray"
            size="xl"
            pos="absolute"
            right={16}
            top="50%"
            style={{ transform: "translateY(-50%)", zIndex: 1 }}
            onClick={(e) => {
              e.stopPropagation();
              goTo(1);
            }}
          >
            <IconChevronRight size={28} />
          </ActionIcon>
        )}
      </Box>
    </Portal>
  );
}

interface ThumbStripProps {
  images: string[];
  /** Visible thumb limit; remainder shown as +N (still opens lightbox). Default: all. */
  maxVisible?: number;
  thumbStyle?: CSSProperties;
  /** Called after opening lightbox; use to stopPropagation from parent selectors. */
  onThumbClick?: (e: MouseEvent) => void;
  empty?: ReactNode;
}

/** Thumbnail strip that opens FanartLightbox on click. */
export function FanartStrip({
  images,
  maxVisible,
  thumbStyle,
  onThumbClick,
  empty = null,
}: ThumbStripProps) {
  const [opened, { open, close }] = useDisclosure(false);
  const [start, setStart] = useState(0);

  if (images.length === 0) return empty;

  const limit = maxVisible ?? images.length;
  const shown = images.slice(0, limit);
  const rest = images.length - shown.length;
  const size: CSSProperties = {
    width: 96,
    height: 64,
    objectFit: "cover",
    borderRadius: "var(--mantine-radius-sm)",
    display: "block",
    ...thumbStyle,
  };

  function openAt(i: number, e: MouseEvent) {
    e.stopPropagation();
    onThumbClick?.(e);
    setStart(i);
    open();
  }

  return (
    <>
      <Group gap={4} wrap="wrap">
        {shown.map((url, i) => (
          <Box
            key={`${url}-${i}`}
            component="button"
            type="button"
            onClick={(e) => openAt(i, e)}
            style={{
              padding: 0,
              border: "2px solid transparent",
              borderRadius: "var(--mantine-radius-sm)",
              cursor: "zoom-in",
              background: "none",
              lineHeight: 0,
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.borderColor = "var(--mantine-color-brand-5)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.borderColor = "transparent";
            }}
          >
            <ProxyImage
              src={proxyImageUrl(url) ?? url}
              alt={`thumb-${i}`}
              referrerPolicy="no-referrer"
              style={size}
              placeholder={<span style={size} aria-hidden />}
            />
          </Box>
        ))}
        {rest > 0 && (
          <Box
            component="button"
            type="button"
            onClick={(e) => openAt(shown.length, e)}
            style={{
              padding: "0 8px",
              height: size.height ?? 64,
              border: "1px dashed var(--mantine-color-default-border)",
              borderRadius: "var(--mantine-radius-sm)",
              cursor: "zoom-in",
              background: "var(--mantine-color-default-hover)",
              color: "var(--mantine-color-dimmed)",
              fontSize: "var(--mantine-font-size-xs)",
            }}
          >
            +{rest}
          </Box>
        )}
      </Group>
      {opened && <FanartLightbox images={images} initialIndex={start} onClose={close} />}
    </>
  );
}
