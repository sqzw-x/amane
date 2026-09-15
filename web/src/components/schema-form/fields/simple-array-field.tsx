import { ActionIcon, Group, ScrollArea, Textarea, TextInput } from "@mantine/core";
import { IconPlus } from "@tabler/icons-react";
import type { AnyFieldApi } from "@tanstack/react-form";
import { useState } from "react";
import { useNarrowViewport } from "@/hooks/use-narrow-viewport";
import type { ArrayFieldProps, JSONSchemaObject } from "../schema";
import { isOrdered } from "../schema";
import { useFieldDomId } from "./dict-entry-form";
import { DraggableChips } from "./draggable-chips";
import { FieldChrome } from "./field-chrome";

export function SimpleArrayField({
  name,
  label,
  description,
  schema,
  form,
  variant,
}: ArrayFieldProps<JSONSchemaObject>) {
  const id = useFieldDomId(name);
  const ordered = isOrdered(schema);
  const long = schema["x-long"] === true;

  return (
    <form.Field name={name}>
      {(field: AnyFieldApi) => {
        const value = Array.isArray(field.state.value) ? (field.state.value as string[]) : [];

        const control = ordered ? (
          <OrderedArrayBody value={value} onChange={field.handleChange} long={long} />
        ) : long ? (
          // x-long, unordered: 多行文本域, 一行一个值
          <MultilineArrayInput id={id} value={value} onChange={field.handleChange} />
        ) : (
          <CommaArrayInput id={id} value={value} onChange={field.handleChange} />
        );

        return (
          <FieldChrome variant={variant} htmlFor={id} label={label} description={description}>
            {control}
          </FieldChrome>
        );
      }}
    </form.Field>
  );
}

/** 按行拆分并规范化; 空行与首尾空白不算值. */
function parseLines(text: string): string[] {
  return text
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
}

/** 按逗号或换行拆分并规范化; 空项与首尾空白不算值. */
function parseCommas(text: string): string[] {
  return text
    .split(/[,\n]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

function sameItems(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((item, i) => item === b[i]);
}

interface ArrayInputProps {
  id: string;
  value: string[];
  onChange: (value: string[]) => void;
}

/**
 * 逗号分隔的列表输入.
 *
 * 输入框持有原始文本, 只在失焦时解析并写回表单. 每次按键都解析会把末尾的 `,` 与空格
 * 立刻吃掉 (`"a, "` → `["a"]` → 显示 `"a"`), 导致无法在尾部继续追加.
 */
function CommaArrayInput({ id, value, onChange }: ArrayInputProps) {
  const [draft, setDraft] = useState(() => value.join(", "));
  const [synced, setSynced] = useState(value);

  // 表单值由外部改变 (重置 / 加载) 时重新同步文本.
  if (synced !== value) {
    setSynced(value);
    setDraft(value.join(", "));
  }

  const commit = () => {
    const items = parseCommas(draft);
    if (!sameItems(items, value)) {
      onChange(items);
      return;
    }
    // 值没变也可能只是末尾多了 `,` / 空格, 提交后回到规范化文本.
    setDraft(items.join(", "));
  };

  return (
    <TextInput
      id={id}
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      placeholder="Comma-separated values"
    />
  );
}

/** x-long 的多行列表输入, 同样保留原始文本直到失焦. */
function MultilineArrayInput({ id, value, onChange }: ArrayInputProps) {
  const [draft, setDraft] = useState(() => value.join("\n"));
  const [synced, setSynced] = useState(value);

  if (synced !== value) {
    setSynced(value);
    setDraft(value.join("\n"));
  }

  const commit = () => {
    const items = parseLines(draft);
    if (!sameItems(items, value)) {
      onChange(items);
      return;
    }
    setDraft(items.join("\n"));
  };

  return (
    <Textarea
      id={id}
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      placeholder="One value per line"
      rows={8}
    />
  );
}

interface OrderedArrayBodyProps {
  value: string[];
  onChange: (v: string[]) => void;
  /** x-long: render the chip list inside a fixed-height scrollable container. */
  long?: boolean;
}

/** x-ordered mode body: draggable chips + add input. Chrome handled by parent. */
function OrderedArrayBody({ value, onChange, long }: OrderedArrayBodyProps) {
  const [newItem, setNewItem] = useState("");
  // 触屏设备无法执行 HTML5 拖拽, 窄屏改由上移 / 下移按钮调整顺序.
  const narrow = useNarrowViewport();

  const handleAdd = () => {
    const trimmed = newItem.trim();
    if (trimmed && !value.includes(trimmed)) {
      onChange([...value, trimmed]);
      setNewItem("");
    }
  };

  const chips = (
    <DraggableChips
      items={value}
      getKey={(item) => item}
      getLabel={(item) => item}
      onChange={onChange}
      onDelete={(item) => onChange(value.filter((v) => v !== item))}
      onMove={narrow ? onChange : undefined}
    />
  );

  return (
    <>
      {long ? (
        <ScrollArea.Autosize mah={200} type="auto">
          {chips}
        </ScrollArea.Autosize>
      ) : (
        chips
      )}
      <Group gap={6} mt={4} wrap="nowrap">
        <TextInput
          size="xs"
          value={newItem}
          onChange={(e) => setNewItem(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              handleAdd();
            }
          }}
          onBlur={handleAdd}
          placeholder="Add item..."
          style={{ flex: 1 }}
        />
        <ActionIcon
          variant="default"
          size="sm"
          onClick={handleAdd}
          disabled={!newItem.trim()}
          aria-label="Add item"
        >
          <IconPlus size={14} />
        </ActionIcon>
      </Group>
    </>
  );
}
