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

function joinCommas(items: string[]): string {
  return items.join(", ");
}

function joinLines(items: string[]): string {
  return items.join("\n");
}

interface ArrayTextDraft {
  text: string;
  /** 输入时更新文本, 解析结果与表单值不同则立即写回表单. */
  edit: (text: string) => void;
  /** 失焦时解析文本并写回表单, 同时把文本规范化为 `join` 的结果. */
  commit: () => void;
}

/**
 * 列表文本与表单值之间的编辑状态.
 *
 * 输入时写回表单值, 保存条才能反映编辑中的改动. 文本保留输入原样: 若每次输入都把解析结果
 * 回填, 尾随分隔符与空格会被立即丢弃, 无法在末尾继续追加; 因此仅在解析结果与表单值内容
 * 不一致时才重新同步文本.
 */
function useArrayTextDraft(
  value: string[],
  onChange: (value: string[]) => void,
  parse: (text: string) => string[],
  join: (items: string[]) => string,
): ArrayTextDraft {
  const [text, setText] = useState(() => join(value));
  const [synced, setSynced] = useState(value);

  // 表单值由外部改变 (重置 / 加载) 时重新同步文本. 编辑期间写回的值与解析结果内容相同, 文本因此不被覆盖.
  if (!sameItems(synced, value)) {
    setSynced(value);
    if (!sameItems(parse(text), value)) {
      setText(join(value));
    }
  }

  const edit = (next: string) => {
    setText(next);
    const items = parse(next);
    if (!sameItems(items, value)) {
      onChange(items);
    }
  };

  const commit = () => {
    const items = parse(text);
    // 解析结果未变化时, 文本仍可能残留尾随分隔符; 失焦后按解析结果规范化文本.
    setText(join(items));
    if (!sameItems(items, value)) {
      onChange(items);
    }
  };

  return { text, edit, commit };
}

function CommaArrayInput({ id, value, onChange }: ArrayInputProps) {
  const { text, edit, commit } = useArrayTextDraft(value, onChange, parseCommas, joinCommas);

  return (
    <TextInput
      id={id}
      value={text}
      onChange={(e) => edit(e.target.value)}
      onBlur={commit}
      placeholder="Comma-separated values"
    />
  );
}

/** x-long 的多行列表输入. */
function MultilineArrayInput({ id, value, onChange }: ArrayInputProps) {
  const { text, edit, commit } = useArrayTextDraft(value, onChange, parseLines, joinLines);

  return (
    <Textarea
      id={id}
      value={text}
      onChange={(e) => edit(e.target.value)}
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
