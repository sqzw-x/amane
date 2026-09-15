import { ActionIcon, Badge, Box, Group } from "@mantine/core";
import { IconArrowDown, IconArrowUp, IconGripVertical, IconX } from "@tabler/icons-react";
import { Fragment, type DragEvent, useCallback, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { HintedActionIcon } from "@/components/common/hinted-action-icon";

interface DraggableChipsProps<T> {
  items: T[];
  getKey: (item: T, index: number) => string;
  getLabel: (item: T, index: number) => string;
  onChange: (newItems: T[]) => void;
  onDelete?: (item: T, index: number) => void;
  /**
   * 提供后每个条目显示上移 / 下移按钮, 用于触屏设备无法执行 HTML5 拖拽的场景.
   * 数组顺序即优先级, 位置 0 即默认探测来源, 移动后同样经 `onChange` 写回.
   */
  onMove?: (newItems: T[]) => void;
  disabled?: boolean;
}

export function DraggableChips<T>({
  items,
  getKey,
  getLabel,
  onChange,
  onDelete,
  onMove,
  disabled,
}: DraggableChipsProps<T>) {
  const { t } = useTranslation("common");
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [dropIndex, setDropIndex] = useState<number | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const chipsRef = useRef<(HTMLElement | null)[]>([]);

  const handleDragStart = useCallback((index: number) => {
    setDragIndex(index);
  }, []);

  const handleDragEnd = useCallback(() => {
    if (dragIndex !== null && dropIndex !== null && dragIndex !== dropIndex) {
      const newItems = [...items];
      const [draggedItem] = newItems.splice(dragIndex, 1);
      const insertIndex = dropIndex > dragIndex ? dropIndex - 1 : dropIndex;
      newItems.splice(insertIndex, 0, draggedItem);
      onChange(newItems);
    }
    setDragIndex(null);
    setDropIndex(null);
  }, [dragIndex, dropIndex, items, onChange]);

  const handleDragOver = useCallback(
    (e: DragEvent) => {
      e.preventDefault();
      if (dragIndex === null) return;

      const container = containerRef.current;
      if (!container) return;

      const containerRect = container.getBoundingClientRect();
      const mouseX = e.clientX - containerRect.left;
      const mouseY = e.clientY - containerRect.top;

      let targetIndex = items.length;

      for (let i = 0; i < chipsRef.current.length; i++) {
        const chip = chipsRef.current[i];
        if (!chip) continue;

        const chipRect = chip.getBoundingClientRect();
        const relativeX = chipRect.left - containerRect.left;
        const relativeY = chipRect.top - containerRect.top;
        const chipCenterX = relativeX + chipRect.width / 2;
        const chipCenterY = relativeY + chipRect.height / 2;

        if (
          mouseY < chipCenterY ||
          (mouseY < chipCenterY + chipRect.height && mouseX < chipCenterX)
        ) {
          targetIndex = i;
          break;
        }
      }

      setDropIndex(targetIndex);
    },
    [dragIndex, items.length],
  );

  /** 与相邻条目交换位置; 越界时保持原顺序. */
  const moveItem = (index: number, offset: -1 | 1) => {
    const target = index + offset;
    if (!onMove || target < 0 || target >= items.length) return;
    const newItems = [...items];
    const [moved] = newItems.splice(index, 1);
    newItems.splice(target, 0, moved);
    onMove(newItems);
  };

  const renderChip = (item: T, index: number, draggable: boolean) => {
    const key = getKey(item, index);
    const label = getLabel(item, index);
    const isDragging = dragIndex === index;

    return (
      <Badge
        ref={(el: HTMLDivElement | null) => {
          chipsRef.current[index] = el;
        }}
        component="div"
        size="lg"
        variant="light"
        color="gray"
        leftSection={
          draggable ? <IconGripVertical size={12} style={{ opacity: 0.55 }} /> : undefined
        }
        rightSection={
          onDelete ? (
            <ActionIcon
              size={16}
              radius="xl"
              variant="subtle"
              color="gray"
              aria-label={`Remove ${label}`}
              onClick={(e) => {
                e.stopPropagation();
                onDelete(item, index);
              }}
              onMouseDown={(e) => e.stopPropagation()}
              // Prevent HTML5 drag starting from the remove control.
              draggable={false}
            >
              <IconX size={10} />
            </ActionIcon>
          ) : undefined
        }
        draggable={draggable}
        onDragStart={
          draggable
            ? (e: DragEvent<HTMLDivElement>) => {
                e.dataTransfer.effectAllowed = "move";
                e.dataTransfer.setData("text/plain", key);
                handleDragStart(index);
              }
            : undefined
        }
        onDragEnd={draggable ? handleDragEnd : undefined}
        style={{
          cursor: draggable ? "grab" : "default",
          opacity: isDragging ? 0.35 : 1,
          userSelect: "none",
          textTransform: "none",
          paddingInlineEnd: onDelete ? 4 : undefined,
        }}
      >
        {label}
      </Badge>
    );
  };

  const renderMoveControls = (index: number) => (
    // 与条目同一行. 容器声明不可拖拽, 避免从按钮上按下时启动条目的 HTML5 拖拽.
    <Group gap={2} wrap="nowrap" draggable={false}>
      <HintedActionIcon
        size={18}
        variant="subtle"
        color="gray"
        label={t("actions.moveUp")}
        disabled={index === 0}
        onClick={() => moveItem(index, -1)}
      >
        <IconArrowUp size={12} />
      </HintedActionIcon>
      <HintedActionIcon
        size={18}
        variant="subtle"
        color="gray"
        label={t("actions.moveDown")}
        disabled={index === items.length - 1}
        onClick={() => moveItem(index, 1)}
      >
        <IconArrowDown size={12} />
      </HintedActionIcon>
    </Group>
  );

  if (disabled) {
    return (
      <Group gap={6} wrap="wrap">
        {items.map((item, index) => (
          <Fragment key={getKey(item, index)}>{renderChip(item, index, false)}</Fragment>
        ))}
      </Group>
    );
  }

  return (
    <Group ref={containerRef} onDragOver={handleDragOver} gap={6} wrap="wrap">
      {items.map((item, index) => {
        const key = getKey(item, index);
        const showIndicatorBefore = dropIndex === index;

        return (
          <Fragment key={key}>
            {showIndicatorBefore && <DropIndicator />}
            {onMove ? (
              <Group gap={0} wrap="nowrap">
                {renderChip(item, index, true)}
                {renderMoveControls(index)}
              </Group>
            ) : (
              renderChip(item, index, true)
            )}
          </Fragment>
        );
      })}
      {dropIndex === items.length && <DropIndicator />}
    </Group>
  );
}

function DropIndicator() {
  return (
    <Box
      w={2}
      h={22}
      bg="var(--mantine-primary-color-filled)"
      style={{ borderRadius: 2, flexShrink: 0 }}
    />
  );
}
