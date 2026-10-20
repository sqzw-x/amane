import {
  ActionIcon,
  Button,
  Group,
  Loader,
  Modal,
  NumberInput,
  Stack,
  Switch,
  Text,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconRestore, IconRotate } from "@tabler/icons-react";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
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

/** 裁切前顺时针旋转的角度; 与后端 `CropBoxRequest.rotation` 一致. */
export type CropRotation = 0 | 90 | 180 | 270;

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
  rotateLeft: string;
  rotateRight: string;
  resetRotation: string;
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
  /** 提交自然像素框与旋转角度; 提交后保持打开, 由调用方在成功后关闭. */
  onSubmit: (box: CropBox, rotation: CropRotation) => void;
  submitting: boolean;
}

interface DecodedImage {
  bitmap: ImageBitmap;
  width: number;
  height: number;
}

interface Size {
  width: number;
  height: number;
}

function rotatedSize(size: Size, rotation: CropRotation): Size {
  return rotation % 180 === 0 ? size : { width: size.height, height: size.width };
}

function nextRotation(current: CropRotation, delta: 90 | 270): CropRotation {
  const next = (current + delta) % 360;
  if (next === 90) return 90;
  if (next === 180) return 180;
  if (next === 270) return 270;
  return 0;
}

/**
 * 顺时针 90° 整数倍旋转: 纯像素重排, 不重采样.
 *
 * 不走 CSS `transform`: 布局盒与 react-image-crop 的坐标基准都不会随之改变, 选框与指针会错位.
 */
function drawRotated(canvas: HTMLCanvasElement, image: DecodedImage, rotation: CropRotation): void {
  const { width, height } = rotatedSize(image, rotation);
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.save();
  if (rotation === 90) {
    ctx.translate(width, 0);
    ctx.rotate(Math.PI / 2);
  } else if (rotation === 180) {
    ctx.translate(width, height);
    ctx.rotate(Math.PI);
  } else if (rotation === 270) {
    ctx.translate(0, height);
    ctx.rotate(-Math.PI / 2);
  }
  ctx.drawImage(image.bitmap, 0, 0);
  ctx.restore();
}

/** 显示像素 (CSS px) → 自然像素的缩放; canvas 后备存储是全分辨率, 显示尺寸受 CSS 约束. */
function displayScale(canvas: HTMLCanvasElement): { x: number; y: number } {
  return {
    x: canvas.width / (canvas.clientWidth || canvas.width),
    y: canvas.height / (canvas.clientHeight || canvas.height),
  };
}

/** 显示像素选区 → 原图像素框 (left/top/right/bottom, right/bottom 不含). */
function toNaturalBox(crop: PixelCrop, canvas: HTMLCanvasElement): CropBox {
  const scale = displayScale(canvas);
  const left = Math.round(crop.x * scale.x);
  const top = Math.round(crop.y * scale.y);
  const right = Math.round((crop.x + crop.width) * scale.x);
  const bottom = Math.round((crop.y + crop.height) * scale.y);
  return {
    left: Math.max(0, left),
    top: Math.max(0, top),
    right: Math.min(canvas.width, right),
    bottom: Math.min(canvas.height, bottom),
  };
}

/** 原图像素框 → 显示像素选区. */
function fromNaturalBox(box: CropBox, canvas: HTMLCanvasElement): PixelCrop {
  const scale = displayScale(canvas);
  return {
    unit: "px",
    x: box.left * scale.x,
    y: box.top * scale.y,
    width: (box.right - box.left) * scale.x,
    height: (box.bottom - box.top) * scale.y,
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
function toPixelCrop(crop: Crop, canvas: HTMLCanvasElement): PixelCrop {
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
    x: (crop.x / 100) * canvas.clientWidth,
    y: (crop.y / 100) * canvas.clientHeight,
    width: (crop.width / 100) * canvas.clientWidth,
    height: (crop.height / 100) * canvas.clientHeight,
  };
}

function clampAspect(value: number, fallback: number): number {
  if (!Number.isFinite(value) || value <= 0) return fallback;
  return Math.min(MAX_ASPECT, Math.max(MIN_ASPECT, value));
}

type BoxState = { left: number; top: number; width: number; height: number };

const EMPTY_BOX: BoxState = { left: 0, top: 0, width: 0, height: 0 };

function fitLockedBox(
  next: BoxState,
  imageSize: Size,
  aspect: number,
  anchor: "width" | "height",
): BoxState {
  let left = Math.max(0, Math.min(next.left, imageSize.width - 1));
  let top = Math.max(0, Math.min(next.top, imageSize.height - 1));
  let width = Math.max(1, Math.round(next.width));
  let height = Math.max(1, Math.round(next.height));

  if (anchor === "width") {
    width = Math.min(width, imageSize.width - left);
    height = Math.max(1, Math.round(width / aspect));
    if (top + height > imageSize.height) {
      height = imageSize.height - top;
      width = Math.max(1, Math.round(height * aspect));
      if (left + width > imageSize.width) {
        width = imageSize.width - left;
        height = Math.max(1, Math.round(width / aspect));
      }
    }
  } else {
    height = Math.min(height, imageSize.height - top);
    width = Math.max(1, Math.round(height * aspect));
    if (left + width > imageSize.width) {
      // 优先右对齐腾出宽度
      left = Math.max(0, imageSize.width - width);
      if (left + width > imageSize.width) {
        width = imageSize.width - left;
        height = Math.max(1, Math.round(width / aspect));
        if (top + height > imageSize.height) {
          height = imageSize.height - top;
          width = Math.max(1, Math.round(height * aspect));
          left = Math.max(0, imageSize.width - width);
        }
      }
    }
  }

  return { left, top, width, height };
}

/**
 * 通用像素框裁切对话框: 显示 `imageUrl` 当前字节, 只提交自然像素框与旋转角度.
 *
 * 角度变化时重绘 canvas (解码只做一次), 坐标始终是旋转后的像素系;
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
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [source, setSource] = useState<DecodedImage | null>(null);
  const [rotation, setRotation] = useState<CropRotation>(0);
  const [crop, setCrop] = useState<Crop>();
  const [completedCrop, setCompletedCrop] = useState<PixelCrop>();
  const [lockAspect, setLockAspect] = useState(true);
  const [aspectRatio, setAspectRatio] = useState(defaultAspect);
  const [box, setBox] = useState<BoxState>(EMPTY_BOX);
  const [loadError, setLoadError] = useState(false);
  const aspectRef = useRef(aspectRatio);
  const naturalSize = useMemo(
    () => (source ? rotatedSize(source, rotation) : null),
    [source, rotation],
  );

  // 比例变化只重置选区; 同一张图不重拉. 换图时连位图与角度一起重置.
  const imageKey = opened ? imageUrl : "";
  const sessionKey = `${imageKey}:${defaultAspect}`;
  const [prevSession, setPrevSession] = useState(sessionKey);
  const [prevImage, setPrevImage] = useState(imageKey);
  if (sessionKey !== prevSession) {
    setPrevSession(sessionKey);
    setCompletedCrop(undefined);
    setBox(EMPTY_BOX);
    setLockAspect(true);
    setAspectRatio(defaultAspect);
    if (imageKey !== prevImage) {
      setPrevImage(imageKey);
      setSource(null);
      setRotation(0);
      setLoadError(false);
      setCrop(undefined);
    } else if (source) {
      const size = rotatedSize(source, rotation);
      setCrop(initialAspectCrop(size.width, size.height, defaultAspect, initialAnchor));
    }
  }

  useEffect(() => {
    aspectRef.current = aspectRatio;
  }, [aspectRatio]);

  // 裁切必须与后端 acquire 的同一份字节对齐: 绕过 HTTP 磁盘缓存强制拉取, 并且只解码一次.
  useEffect(() => {
    if (!opened) return;

    const proxied = proxyImageUrl(imageUrl) ?? imageUrl;
    const ac = new AbortController();

    void (async () => {
      try {
        const res = await apiFetch(proxied, { cache: "no-store", signal: ac.signal });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const blob = await res.blob();
        // 显式要求按 EXIF 方向解码: 与后端 `exif_transpose` 后的像素系一致.
        const bitmap = await createImageBitmap(blob, { imageOrientation: "from-image" });
        if (ac.signal.aborted) {
          bitmap.close();
          return;
        }
        setSource({ bitmap, width: bitmap.width, height: bitmap.height });
      } catch {
        if (!ac.signal.aborted) setLoadError(true);
      }
    })();

    return () => ac.abort();
  }, [opened, imageUrl]);

  // 位图被替换或对话框关闭时释放.
  useEffect(() => {
    const bitmap = source?.bitmap;
    return () => bitmap?.close();
  }, [source]);

  const syncBoxFromCrop = useCallback((pixelCrop: PixelCrop, canvas: HTMLCanvasElement) => {
    const natural = toNaturalBox(pixelCrop, canvas);
    setBox({
      left: natural.left,
      top: natural.top,
      width: natural.right - natural.left,
      height: natural.bottom - natural.top,
    });
  }, []);

  // 换角度后按新坐标系重绘, 并把选区重置为该比例下的默认框 (原框已不在画布内).
  useLayoutEffect(() => {
    const canvas = canvasRef.current;
    if (!source || !canvas) return;
    drawRotated(canvas, source, rotation);
    const next = initialAspectCrop(canvas.width, canvas.height, aspectRef.current, initialAnchor);
    setCrop(next);
    setCompletedCrop(undefined);
    if (canvas.clientWidth > 0) {
      const pixel = toPixelCrop(next, canvas);
      setCompletedCrop(pixel);
      syncBoxFromCrop(pixel, canvas);
    } else {
      setBox(EMPTY_BOX);
    }
  }, [source, rotation, initialAnchor, syncBoxFromCrop]);

  // 初始 percent crop / 拖拽中同步像素框到 NumberInput
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!crop || !canvas || canvas.width === 0 || canvas.clientWidth === 0 || !naturalSize) return;
    const pixel = toPixelCrop(crop, canvas);
    if (pixel.width < 1 || pixel.height < 1) return;
    setCompletedCrop(pixel);
    syncBoxFromCrop(pixel, canvas);
  }, [crop, naturalSize, syncBoxFromCrop]);

  const commitBox = (next: BoxState) => {
    const canvas = canvasRef.current;
    if (!canvas || canvas.clientWidth === 0) return;
    const pixel = fromNaturalBox(
      {
        left: next.left,
        top: next.top,
        right: next.left + next.width,
        bottom: next.top + next.height,
      },
      canvas,
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
      const left = Math.max(0, Math.min(next.left, naturalSize.width - 1));
      const top = Math.max(0, Math.min(next.top, naturalSize.height - 1));
      const width = Math.max(1, Math.min(Math.round(next.width), naturalSize.width - left));
      const height = Math.max(1, Math.min(Math.round(next.height), naturalSize.height - top));
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
    const height = box.height > 0 ? box.height : naturalSize.height;
    const top = box.height > 0 ? box.top : 0;
    const preferRight = box.width === 0 || box.left + box.width >= naturalSize.width - 1;
    let width = Math.max(1, Math.round(height * aspect));
    let left = box.left;
    if (preferRight || left + width > naturalSize.width) {
      left = Math.max(0, naturalSize.width - width);
    }
    applyNaturalBox({ left, top, width, height }, { locked: true, aspect, anchor: "height" });
  };

  const rotateBy = (delta: 90 | 270) => {
    setRotation((current) => nextRotation(current, delta));
  };

  const handleConfirm = () => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const pixel =
      completedCrop && completedCrop.width >= 1 && completedCrop.height >= 1
        ? completedCrop
        : crop
          ? toPixelCrop(crop, canvas)
          : undefined;
    if (!pixel || pixel.width < 1 || pixel.height < 1) {
      notifications.show({ message: labels.needSelection, color: "red" });
      return;
    }
    const natural = toNaturalBox(pixel, canvas);
    if (natural.right <= natural.left || natural.bottom <= natural.top) {
      notifications.show({ message: labels.needSelection, color: "red" });
      return;
    }
    onSubmit(natural, rotation);
  };

  return (
    <Modal opened={opened} onClose={onClose} title={labels.title} size="min(92vw, 64rem)" centered>
      <Stack gap="md">
        {/* 舞台随视口缩放并居中; 画布 maxHeight 保证整图可见, 小屏时仍可滚动兜底. */}
        <div style={{ maxHeight: "64vh", overflow: "auto", textAlign: "center" }}>
          {loadError ? (
            <Text c="red" size="sm">
              {labels.operationFailed}
            </Text>
          ) : !source ? (
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
                  if (canvasRef.current && c.width > 0 && c.height > 0) {
                    syncBoxFromCrop(c, canvasRef.current);
                  }
                }}
                aspect={lockAspect ? aspectRatio : undefined}
                keepSelection
              >
                <canvas
                  ref={canvasRef}
                  role="img"
                  aria-label="crop source"
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
                  {naturalSize.width}×{naturalSize.height}
                </Text>
              )}
            </div>
          )}
        </div>

        <Group gap="xs">
          <ActionIcon
            variant="default"
            size="md"
            onClick={() => rotateBy(270)}
            disabled={!source}
            aria-label={labels.rotateLeft}
          >
            <IconRotate size={18} style={{ transform: "scaleX(-1)" }} />
          </ActionIcon>
          <ActionIcon
            variant="default"
            size="md"
            onClick={() => rotateBy(90)}
            disabled={!source}
            aria-label={labels.rotateRight}
          >
            <IconRotate size={18} />
          </ActionIcon>
          <ActionIcon
            variant="default"
            size="md"
            onClick={() => setRotation(0)}
            disabled={rotation === 0}
            aria-label={labels.resetRotation}
          >
            <IconRestore size={18} />
          </ActionIcon>
        </Group>

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
                  const height = box.height > 0 ? box.height : (naturalSize?.height ?? 0);
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
            max={naturalSize ? naturalSize.width - 1 : undefined}
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
            max={naturalSize ? naturalSize.height - 1 : undefined}
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
            max={naturalSize ? naturalSize.width : undefined}
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
            max={naturalSize ? naturalSize.height : undefined}
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
            disabled={!source || loadError || !naturalSize}
          >
            {labels.confirm}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
