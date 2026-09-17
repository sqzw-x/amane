import { ActionIcon, Box, Group, Loader, Portal, Text } from "@mantine/core";
import { useDisclosure, useHotkeys } from "@mantine/hooks";
import { IconChevronLeft, IconChevronRight, IconX } from "@tabler/icons-react";
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
} from "react";
import { proxyImageUrl } from "@/lib/utils";
import { useQueuedImageUrl } from "@/lib/image-loader";
import { ProxyImage } from "@/components/media/proxy-image";

/** 触屏切换的判定: 水平位移超过它, 且明显偏水平 (纵向留给页面滚动). */
const SWIPE_MIN_DISTANCE_PX = 48;
/** 滑动结束后这段时间内的 click 视为该次滑动带来的, 不当作点击遮罩关闭. */
const SWIPE_CLICK_SUPPRESS_MS = 500;

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
  const imgRef = useRef<HTMLImageElement>(null);

  // 触屏滑动切换: 起手点与"这次滑动已经切换过"的时刻. 横向拖动不算页面的滚动手势, 浏览器随后仍可能补一次
  // click, 那次不能把预览关掉.
  const swipeStartRef = useRef<{ id: number; x: number; y: number } | null>(null);
  const swipeHandledAtRef = useRef(0);

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

  const handlePointerDown = (event: PointerEvent<HTMLDivElement>) => {
    // 鼠标的按钮与方向键原样保留, 只接管触摸.
    swipeStartRef.current =
      event.pointerType === "touch"
        ? { id: event.pointerId, x: event.clientX, y: event.clientY }
        : null;
  };

  const handlePointerUp = (event: PointerEvent<HTMLDivElement>) => {
    const start = swipeStartRef.current;
    swipeStartRef.current = null;
    if (start == null || start.id !== event.pointerId) return;
    const dx = event.clientX - start.x;
    const dy = event.clientY - start.y;
    if (Math.abs(dx) < SWIPE_MIN_DISTANCE_PX || Math.abs(dx) <= Math.abs(dy)) return;
    swipeHandledAtRef.current = Date.now();
    goTo(dx < 0 ? 1 : -1);
  };

  const src = images.length > 0 ? (proxyImageUrl(images[index]) ?? images[index]) : "";
  const { src: queuedSrc, release } = useQueuedImageUrl(src);

  const [seenSrc, setSeenSrc] = useState(queuedSrc);
  if (queuedSrc !== seenSrc) {
    setSeenSrc(queuedSrc);
    setLoading(true);
    setResolution(null);
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

  // Portal 出 Modal: 打开态带 transform, 会把 position:fixed 的包含块收成弹窗,
  // 大图被 overflow 裁切. 高于 Overlay(400) / Affix, 低于 max.
  // 拦截 mousedown 以免点预览被 Modal 当成 click-outside.
  return (
    <Portal>
      <Box
        pos="fixed"
        inset={0}
        style={{ zIndex: 500, background: "rgba(0,0,0,0.92)", touchAction: "pan-y" }}
        onMouseDown={(e) => e.stopPropagation()}
        onPointerDown={handlePointerDown}
        onPointerUp={handlePointerUp}
        onPointerCancel={() => {
          swipeStartRef.current = null;
        }}
        onClick={(e) => {
          e.stopPropagation();
          // 刚结束的滑动不算点击: 横向拖动之后浏览器可能仍补一次 click.
          if (Date.now() - swipeHandledAtRef.current < SWIPE_CLICK_SUPPRESS_MS) return;
          onClose();
        }}
      >
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

        {multi && (
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

        {multi && (
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

        <Group justify="center" align="center" h="100%" p="xl" style={{ pointerEvents: "none" }}>
          {loading && (
            <Loader
              color="gray"
              style={{ position: "absolute", zIndex: 1, pointerEvents: "none" }}
            />
          )}
          <img
            ref={imgRef}
            key={queuedSrc}
            src={queuedSrc ?? undefined}
            alt={`fanart-${index}`}
            referrerPolicy="no-referrer"
            style={{
              maxHeight: "calc(var(--amane-vh) * 0.9)",
              maxWidth: "90vw",
              objectFit: "contain",
              pointerEvents: "auto",
              opacity: loading ? 0 : 1,
              transition: "opacity 150ms ease",
            }}
            onClick={(e) => e.stopPropagation()}
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
        </Group>
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
