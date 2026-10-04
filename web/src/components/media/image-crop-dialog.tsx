import { Button, Group, Loader, Modal, NumberInput, Stack, Switch, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useCallback, useEffect, useRef, useState, type SyntheticEvent } from "react";
import ReactCrop, { type Crop, type PixelCrop } from "react-image-crop";
import "react-image-crop/dist/ReactCrop.css";
import { apiFetch } from "@/lib/api-token";
import { proxyImageUrl } from "@/lib/utils";

const MIN_ASPECT = 0.1;
const MAX_ASPECT = 5;

/** 自然像素框 (left/top/right/bottom, right/bottom 不含). */
export interface CropBox {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

export interface CropDialogLabels {
  title: string;
  confirm: string;
  needSelection: string;
  operationFailed: string;
  aspect: string;
  lockAspect: string;
  left: string;
  top: string;
  width: string;
  height: string;
  cancel: string;
}

export interface ImageCropDialogProps {
  opened: boolean;
  onClose: () => void;
  /** 源图 URL (外部或内部); 对话框显示与后端同一份字节. */
  imageUrl: string;
  /** 初始与锁定宽高比 (w/h); 变化时重置选区并重新装载图片. */
  defaultAspect: number;
  /** 高度受限时初始框的水平位置: center 居中, right 靠右. */
  initialAnchor: "center" | "right";
  labels: CropDialogLabels;
  /** 提交自然像素框; 提交后保持打开, 由调用方在成功后关闭. */
  onSubmit: (box: CropBox) => void;
  submitting: boolean;
}

/** 显示像素选区 → 原图像素框 (left/top/right/bottom, right/bottom 不含). */
function toNaturalBox(
  crop: PixelCrop,
  image: HTMLImageElement,
): { left: number; top: number; right: number; bottom: number } {
  const scaleX = image.naturalWidth / image.width;
  const scaleY = image.naturalHeight / image.height;
  const left = Math.round(crop.x * scaleX);
  const top = Math.round(crop.y * scaleY);
  const right = Math.round((crop.x + crop.width) * scaleX);
  const bottom = Math.round((crop.y + crop.height) * scaleY);
  return {
    left: Math.max(0, left),
    top: Math.max(0, top),
    right: Math.min(image.naturalWidth, right),
    bottom: Math.min(image.naturalHeight, bottom),
  };
}

/** 原图像素框 → 显示像素选区. */
function fromNaturalBox(
  box: { left: number; top: number; right: number; bottom: number },
  image: HTMLImageElement,
): PixelCrop {
  const scaleX = image.width / image.naturalWidth;
  const scaleY = image.height / image.naturalHeight;
  return {
    unit: "px",
    x: box.left * scaleX,
    y: box.top * scaleY,
    width: (box.right - box.left) * scaleX,
    height: (box.bottom - box.top) * scaleY,
  };
}

/** 默认选区: 符合 aspect (w/h) 的最大内接框; 高度受限时打满高度, 宽度受限时打满宽度靠上. */
function initialAspectCrop(
  naturalWidth: number,
  naturalHeight: number,
  aspect: number,
  anchor: "center" | "right",
): Crop {
  const imageRatio = naturalWidth / naturalHeight;
  if (imageRatio >= aspect) {
    const widthPct = (aspect / imageRatio) * 100;
    const x = anchor === "right" ? 100 - widthPct : (100 - widthPct) / 2;
    return { unit: "%", x, y: 0, width: widthPct, height: 100 };
  }
  const heightPct = (imageRatio / aspect) * 100;
  return { unit: "%", x: 0, y: 0, width: 100, height: heightPct };
}

/** Percent/pixel Crop → 显示像素 PixelCrop. */
function toPixelCrop(crop: Crop, image: HTMLImageElement): PixelCrop {
  if (crop.unit === "px") {
    return {
      unit: "px",
      x: crop.x,
      y: crop.y,
      width: crop.width,
      height: crop.height,
    };
  }
  return {
    unit: "px",
    x: (crop.x / 100) * image.width,
    y: (crop.y / 100) * image.height,
    width: (crop.width / 100) * image.width,
    height: (crop.height / 100) * image.height,
  };
}

function clampAspect(value: number, fallback: number): number {
  if (!Number.isFinite(value) || value <= 0) return fallback;
  return Math.min(MAX_ASPECT, Math.max(MIN_ASPECT, value));
}

type BoxState = { left: number; top: number; width: number; height: number };

function fitLockedBox(
  next: BoxState,
  imageSize: { w: number; h: number },
  aspect: number,
  anchor: "width" | "height",
): BoxState {
  let left = Math.max(0, Math.min(next.left, imageSize.w - 1));
  let top = Math.max(0, Math.min(next.top, imageSize.h - 1));
  let width = Math.max(1, Math.round(next.width));
  let height = Math.max(1, Math.round(next.height));

  if (anchor === "width") {
    width = Math.min(width, imageSize.w - left);
    height = Math.max(1, Math.round(width / aspect));
    if (top + height > imageSize.h) {
      height = imageSize.h - top;
      width = Math.max(1, Math.round(height * aspect));
      if (left + width > imageSize.w) {
        width = imageSize.w - left;
        height = Math.max(1, Math.round(width / aspect));
      }
    }
  } else {
    height = Math.min(height, imageSize.h - top);
    width = Math.max(1, Math.round(height * aspect));
    if (left + width > imageSize.w) {
      // 优先右对齐腾出宽度
      left = Math.max(0, imageSize.w - width);
      if (left + width > imageSize.w) {
        width = imageSize.w - left;
        height = Math.max(1, Math.round(width / aspect));
        if (top + height > imageSize.h) {
          height = imageSize.h - top;
          width = Math.max(1, Math.round(height * aspect));
          left = Math.max(0, imageSize.w - width);
        }
      }
    }
  }

  return { left, top, width, height };
}

/**
 * 通用像素框裁切对话框: 显示 `imageUrl` 当前字节, 只提交自然像素框.
 *
 * 调用方负责 mutation 与成功后的关闭; 本组件在提交后保持打开.
 */
export function ImageCropDialog({
  opened,
  onClose,
  imageUrl,
  defaultAspect,
  initialAnchor,
  labels,
  onSubmit,
  submitting,
}: ImageCropDialogProps) {
  const imgRef = useRef<HTMLImageElement>(null);
  const objectUrlRef = useRef<string | null>(null);
  const [crop, setCrop] = useState<Crop>();
  const [completedCrop, setCompletedCrop] = useState<PixelCrop>();
  const [lockAspect, setLockAspect] = useState(true);
  const [aspectRatio, setAspectRatio] = useState(defaultAspect);
  const [naturalSize, setNaturalSize] = useState<{ w: number; h: number } | null>(null);
  const [box, setBox] = useState<BoxState>({ left: 0, top: 0, width: 0, height: 0 });
  // 裁切必须与后端 acquire 的同一份字节对齐: 绕过 HTTP 磁盘缓存强制拉取.
  const [displaySrc, setDisplaySrc] = useState<string | null>(null);
  const [loadError, setLoadError] = useState(false);

  // 比例变化只重置选区 (img key=defaultAspect 触发 onLoad 重算); 同一张图不重拉.
  const imageKey = opened ? imageUrl : "";
  const sessionKey = `${imageKey}:${defaultAspect}`;
  const [prevSession, setPrevSession] = useState(sessionKey);
  const [prevImage, setPrevImage] = useState(imageKey);
  if (sessionKey !== prevSession) {
    setPrevSession(sessionKey);
    setCrop(undefined);
    setCompletedCrop(undefined);
    setBox({ left: 0, top: 0, width: 0, height: 0 });
    setLockAspect(true);
    setAspectRatio(defaultAspect);
    if (imageKey !== prevImage) {
      setPrevImage(imageKey);
      setNaturalSize(null);
      setDisplaySrc(null);
      setLoadError(false);
    }
  }

  useEffect(() => {
    if (!opened) return;

    const proxied = proxyImageUrl(imageUrl) ?? imageUrl;
    const ac = new AbortController();

    void (async () => {
      try {
        const res = await apiFetch(proxied, { cache: "no-store", signal: ac.signal });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const blob = await res.blob();
        if (ac.signal.aborted) return;
        if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
        const obj = URL.createObjectURL(blob);
        objectUrlRef.current = obj;
        setDisplaySrc(obj);
      } catch {
        if (ac.signal.aborted) return;
        setLoadError(true);
      }
    })();

    return () => {
      ac.abort();
      if (objectUrlRef.current) {
        URL.revokeObjectURL(objectUrlRef.current);
        objectUrlRef.current = null;
      }
    };
  }, [opened, imageUrl]);

  const syncBoxFromCrop = useCallback((pixelCrop: PixelCrop, image: HTMLImageElement) => {
    const natural = toNaturalBox(pixelCrop, image);
    setBox({
      left: natural.left,
      top: natural.top,
      width: natural.right - natural.left,
      height: natural.bottom - natural.top,
    });
  }, []);

  // 初始 percent crop / 拖拽中同步像素框到 NumberInput
  useEffect(() => {
    const image = imgRef.current;
    if (!crop || !image || image.width === 0 || !naturalSize) return;
    const pixel = toPixelCrop(crop, image);
    if (pixel.width < 1 || pixel.height < 1) return;
    setCompletedCrop(pixel);
    syncBoxFromCrop(pixel, image);
  }, [crop, naturalSize, syncBoxFromCrop]);

  const onImageLoad = (e: SyntheticEvent<HTMLImageElement>) => {
    const image = e.currentTarget;
    setNaturalSize({ w: image.naturalWidth, h: image.naturalHeight });
    setCrop(initialAspectCrop(image.naturalWidth, image.naturalHeight, aspectRatio, initialAnchor));
  };

  const commitBox = (next: BoxState) => {
    const image = imgRef.current;
    if (!image) return;
    const pixel = fromNaturalBox(
      {
        left: next.left,
        top: next.top,
        right: next.left + next.width,
        bottom: next.top + next.height,
      },
      image,
    );
    setCrop(pixel);
    setCompletedCrop(pixel);
    setBox(next);
  };

  const applyNaturalBox = (
    next: BoxState,
    options?: { locked?: boolean; aspect?: number; anchor?: "width" | "height" },
  ) => {
    if (!naturalSize) return;
    const locked = options?.locked ?? lockAspect;
    const aspect = clampAspect(options?.aspect ?? aspectRatio, defaultAspect);
    const anchor = options?.anchor ?? "height";

    if (!locked) {
      const left = Math.max(0, Math.min(next.left, naturalSize.w - 1));
      const top = Math.max(0, Math.min(next.top, naturalSize.h - 1));
      const width = Math.max(1, Math.min(Math.round(next.width), naturalSize.w - left));
      const height = Math.max(1, Math.min(Math.round(next.height), naturalSize.h - top));
      commitBox({ left, top, width, height });
      return;
    }

    commitBox(fitLockedBox(next, naturalSize, aspect, anchor));
  };

  const reshapeWithAspect = (nextAspect: number) => {
    const aspect = clampAspect(nextAspect, defaultAspect);
    setAspectRatio(aspect);
    if (!naturalSize) return;
    // 未锁定时只更新比例值, 不强制修改选区; 锁定时按新比例重塑
    if (!lockAspect) return;
    const height = box.height > 0 ? box.height : naturalSize.h;
    const top = box.height > 0 ? box.top : 0;
    const preferRight = box.width === 0 || box.left + box.width >= naturalSize.w - 1;
    let width = Math.max(1, Math.round(height * aspect));
    let left = box.left;
    if (preferRight || left + width > naturalSize.w) {
      left = Math.max(0, naturalSize.w - width);
    }
    applyNaturalBox({ left, top, width, height }, { locked: true, aspect, anchor: "height" });
  };

  const handleConfirm = () => {
    const image = imgRef.current;
    if (!image) return;
    const pixel =
      completedCrop && completedCrop.width >= 1 && completedCrop.height >= 1
        ? completedCrop
        : crop
          ? toPixelCrop(crop, image)
          : undefined;
    if (!pixel || pixel.width < 1 || pixel.height < 1) {
      notifications.show({ message: labels.needSelection, color: "red" });
      return;
    }
    const natural = toNaturalBox(pixel, image);
    if (natural.right <= natural.left || natural.bottom <= natural.top) {
      notifications.show({ message: labels.needSelection, color: "red" });
      return;
    }
    onSubmit(natural);
  };

  return (
    <Modal opened={opened} onClose={onClose} title={labels.title} size="min(92vw, 64rem)" centered>
      <Stack gap="md">
        {/* 舞台随视口缩放并居中; 图片 maxHeight 保证整图可见, 小屏时仍可滚动兜底. */}
        <div style={{ maxHeight: "64vh", overflow: "auto", textAlign: "center" }}>
          {loadError ? (
            <Text c="red" size="sm">
              {labels.operationFailed}
            </Text>
          ) : !displaySrc ? (
            <Group justify="center" p="xl">
              <Loader size="sm" />
            </Group>
          ) : (
            <div style={{ position: "relative", display: "inline-block", maxWidth: "100%" }}>
              <ReactCrop
                crop={crop}
                onChange={(c) => setCrop(c)}
                onComplete={(c) => {
                  setCompletedCrop(c);
                  if (imgRef.current && c.width > 0 && c.height > 0) {
                    syncBoxFromCrop(c, imgRef.current);
                  }
                }}
                aspect={lockAspect ? aspectRatio : undefined}
                keepSelection
              >
                <img
                  key={defaultAspect}
                  ref={imgRef}
                  src={displaySrc}
                  alt="crop source"
                  referrerPolicy="no-referrer"
                  onLoad={onImageLoad}
                  style={{
                    maxWidth: "100%",
                    maxHeight: "60vh",
                    display: "block",
                    margin: "0 auto",
                  }}
                />
              </ReactCrop>
              {naturalSize && (
                <Text
                  size="xs"
                  c="dimmed"
                  style={{
                    position: "absolute",
                    right: 8,
                    bottom: 8,
                    pointerEvents: "none",
                    padding: "2px 6px",
                    borderRadius: 4,
                    background: "color-mix(in srgb, var(--mantine-color-body) 75%, transparent)",
                  }}
                >
                  {naturalSize.w}×{naturalSize.h}
                </Text>
              )}
            </div>
          )}
        </div>

        <Group align="flex-start" wrap="wrap" gap="sm">
          <Stack gap={6} style={{ width: 120 }}>
            <NumberInput
              label={labels.aspect}
              value={aspectRatio}
              min={MIN_ASPECT}
              max={MAX_ASPECT}
              step={0.01}
              decimalScale={3}
              onChange={(v) => {
                if (typeof v !== "number") return;
                reshapeWithAspect(v);
              }}
            />
            <Switch
              label={labels.lockAspect}
              checked={lockAspect}
              onChange={(e) => {
                const checked = e.currentTarget.checked;
                setLockAspect(checked);
                if (checked) {
                  // 按输入框中的宽高比约束当前选区 (不反推覆盖输入值)
                  const height = box.height > 0 ? box.height : (naturalSize?.h ?? 0);
                  const top = box.height > 0 ? box.top : 0;
                  const width = box.width > 0 ? box.width : height;
                  const left = box.width > 0 ? box.left : 0;
                  applyNaturalBox(
                    { left, top, width, height },
                    { locked: true, aspect: aspectRatio, anchor: "height" },
                  );
                }
              }}
            />
          </Stack>
          <NumberInput
            label={labels.left}
            value={box.left}
            min={0}
            max={naturalSize ? naturalSize.w - 1 : undefined}
            allowDecimal={false}
            onChange={(v) =>
              applyNaturalBox({
                ...box,
                left: typeof v === "number" ? v : 0,
              })
            }
            style={{ width: 110 }}
          />
          <NumberInput
            label={labels.top}
            value={box.top}
            min={0}
            max={naturalSize ? naturalSize.h - 1 : undefined}
            allowDecimal={false}
            onChange={(v) =>
              applyNaturalBox({
                ...box,
                top: typeof v === "number" ? v : 0,
              })
            }
            style={{ width: 110 }}
          />
          <NumberInput
            label={labels.width}
            value={box.width}
            min={1}
            max={naturalSize ? naturalSize.w : undefined}
            allowDecimal={false}
            onChange={(v) =>
              applyNaturalBox({ ...box, width: typeof v === "number" ? v : 1 }, { anchor: "width" })
            }
            style={{ width: 110 }}
          />
          <NumberInput
            label={labels.height}
            value={box.height}
            min={1}
            max={naturalSize ? naturalSize.h : undefined}
            allowDecimal={false}
            disabled={lockAspect}
            onChange={(v) =>
              applyNaturalBox({
                ...box,
                height: typeof v === "number" ? v : 1,
              })
            }
            style={{ width: 110 }}
          />
        </Group>

        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            {labels.cancel}
          </Button>
          <Button
            onClick={handleConfirm}
            loading={submitting}
            disabled={!displaySrc || loadError || !naturalSize}
          >
            {labels.confirm}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
