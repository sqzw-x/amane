import { ActionIcon, Badge, Box, Group } from "@mantine/core";
import { IconGripVertical, IconTrash } from "@tabler/icons-react";
import { Fragment, type DragEvent, useCallback, useRef, useState } from "react";
import { proxyImageUrl } from "@/lib/utils";
import { ProxyImage } from "@/components/media/proxy-image";

const THUMB: { width: number; height: number } = { width: 80, height: 108 };

interface SortableImageListProps {
  urls: string[];
  onChange: (urls: string[]) => void;
  onOpen: (index: number) => void;
  primaryLabel: string;
  removeLabel: string;
  reorderLabel: string;
}

/**
 * Visual reorder of image URLs: drag cards, click to preview, first is primary.
 * HTML5 DnD (same approach as schema-form DraggableChips) — no extra dependency.
 */
export function SortableImageList({
  urls,
  onChange,
  onOpen,
  primaryLabel,
  removeLabel,
  reorderLabel,
}: SortableImageListProps) {
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [dropIndex, setDropIndex] = useState<number | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const cardsRef = useRef<(HTMLElement | null)[]>([]);
  const draggedRef = useRef(false);

  const handleDragStart = useCallback((index: number) => {
    draggedRef.current = true;
    setDragIndex(index);
  }, []);

  const handleDragEnd = useCallback(() => {
    if (dragIndex !== null && dropIndex !== null && dragIndex !== dropIndex) {
      const next = [...urls];
      const [moved] = next.splice(dragIndex, 1);
      const insertIndex = dropIndex > dragIndex ? dropIndex - 1 : dropIndex;
      next.splice(insertIndex, 0, moved);
      onChange(next);
    }
    setDragIndex(null);
    setDropIndex(null);
  }, [dragIndex, dropIndex, onChange, urls]);

  const handleDragOver = useCallback(
    (e: DragEvent) => {
      e.preventDefault();
      if (dragIndex === null) return;

      const container = containerRef.current;
      if (!container) return;

      const containerRect = container.getBoundingClientRect();
      const mouseX = e.clientX - containerRect.left;
      const mouseY = e.clientY - containerRect.top;

      let targetIndex = urls.length;
      for (let i = 0; i < cardsRef.current.length; i++) {
        const card = cardsRef.current[i];
        if (!card) continue;
        const cardRect = card.getBoundingClientRect();
        const relativeX = cardRect.left - containerRect.left;
        const relativeY = cardRect.top - containerRect.top;
        const centerX = relativeX + cardRect.width / 2;
        const centerY = relativeY + cardRect.height / 2;
        if (mouseY < centerY || (mouseY < centerY + cardRect.height && mouseX < centerX)) {
          targetIndex = i;
          break;
        }
      }
      setDropIndex(targetIndex);
    },
    [dragIndex, urls.length],
  );

  if (urls.length === 0) return null;

  return (
    <Group ref={containerRef} onDragOver={handleDragOver} gap="sm" wrap="wrap">
      {urls.map((url, index) => {
        const showIndicatorBefore = dropIndex === index;
        const isDragging = dragIndex === index;
        return (
          <Fragment key={`${url}-${index}`}>
            {showIndicatorBefore && <DropIndicator />}
            <Box
              ref={(el: HTMLDivElement | null) => {
                cardsRef.current[index] = el;
              }}
              draggable
              role="button"
              tabIndex={0}
              aria-label={reorderLabel}
              onDragStart={(e: DragEvent<HTMLDivElement>) => {
                e.dataTransfer.effectAllowed = "move";
                e.dataTransfer.setData("text/plain", url);
                handleDragStart(index);
              }}
              onDragEnd={handleDragEnd}
              onClick={() => {
                if (draggedRef.current) {
                  draggedRef.current = false;
                  return;
                }
                onOpen(index);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onOpen(index);
                }
              }}
              style={{
                position: "relative",
                cursor: "grab",
                opacity: isDragging ? 0.35 : 1,
                userSelect: "none",
                borderRadius: "var(--mantine-radius-sm)",
                overflow: "hidden",
                border: "1px solid var(--mantine-color-default-border)",
                lineHeight: 0,
                flexShrink: 0,
              }}
            >
              <ProxyImage
                src={proxyImageUrl(url) ?? url}
                alt=""
                referrerPolicy="no-referrer"
                draggable={false}
                style={{
                  display: "block",
                  width: THUMB.width,
                  height: THUMB.height,
                  objectFit: "cover",
                  pointerEvents: "none",
                }}
                placeholder={
                  <span
                    style={{ display: "block", width: THUMB.width, height: THUMB.height }}
                    aria-hidden
                  />
                }
              />
              <IconGripVertical
                size={14}
                style={{
                  position: "absolute",
                  left: 4,
                  bottom: 4,
                  opacity: 0.85,
                  color: "white",
                  filter: "drop-shadow(0 0 2px rgba(0,0,0,0.8))",
                  pointerEvents: "none",
                }}
              />
              {index === 0 && (
                <Badge
                  size="xs"
                  variant="filled"
                  pos="absolute"
                  top={4}
                  left={4}
                  style={{ pointerEvents: "none" }}
                >
                  {primaryLabel}
                </Badge>
              )}
              <ActionIcon
                variant="filled"
                color="red"
                size="xs"
                pos="absolute"
                top={4}
                right={4}
                aria-label={removeLabel}
                onClick={(e) => {
                  e.stopPropagation();
                  onChange(urls.filter((_, i) => i !== index));
                }}
                onMouseDown={(e) => e.stopPropagation()}
                draggable={false}
              >
                <IconTrash size={12} />
              </ActionIcon>
            </Box>
          </Fragment>
        );
      })}
      {dropIndex === urls.length && <DropIndicator />}
    </Group>
  );
}

function DropIndicator() {
  return (
    <Box
      w={3}
      h={THUMB.height}
      bg="var(--mantine-primary-color-filled)"
      style={{ borderRadius: 2, flexShrink: 0 }}
    />
  );
}
