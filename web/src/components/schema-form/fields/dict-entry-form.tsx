import type { AnyFieldApi } from "@tanstack/react-form";
import { createContext, useContext, type ReactNode } from "react";
import { isRecord } from "@/lib/utils";
import type { SchemaFormInstance } from "../schema";

/**
 * Dict 条目的表单绑定: 用户 key 是对象上的字面量, 不拼进 TanStack 点路径.
 * 叶子控件仍走 `form.Field`; 本 scope 把读写转到父级 dict 的 `[entryKey]`.
 *
 * `bindEntry`: 值本身是 scalar / array, Field `name` 只作 DOM id.
 * 否则 `name` 是条目内部的 schema 相对路径 (属性名, 可再嵌套).
 */
const DictEntryContext = createContext<{
  parentField: AnyFieldApi;
  entryKey: string;
  bindEntry: boolean;
} | null>(null);

export function DictEntryScope({
  parentField,
  entryKey,
  bindEntry,
  children,
}: {
  parentField: AnyFieldApi;
  entryKey: string;
  bindEntry: boolean;
  children: ReactNode;
}) {
  return (
    <DictEntryContext.Provider value={{ parentField, entryKey, bindEntry }}>
      {children}
    </DictEntryContext.Provider>
  );
}

function asDict(value: unknown): Record<string, unknown> {
  return isRecord(value) && !Array.isArray(value) ? value : {};
}

function getAt(value: unknown, parts: readonly string[]): unknown {
  let current = value;
  for (const part of parts) {
    if (!isRecord(current)) return undefined;
    current = current[part];
  }
  return current;
}

function setAt(value: unknown, parts: readonly string[], next: unknown): unknown {
  if (parts.length === 0) return next;
  const [head, ...rest] = parts;
  if (head === undefined) return next;
  if (Array.isArray(value)) {
    const copy = [...value];
    const index = Number(head);
    copy[index] = setAt(copy[index], rest, next);
    return copy;
  }
  const copy: Record<string, unknown> = isRecord(value) ? { ...value } : {};
  copy[head] = setAt(copy[head], rest, next);
  return copy;
}

function DictEntryField({
  name,
  children,
}: {
  name: string;
  children: (field: AnyFieldApi) => ReactNode;
}) {
  const ctx = useContext(DictEntryContext);
  if (ctx == null) {
    throw new Error("DictEntryField requires DictEntryScope");
  }
  const { parentField, entryKey, bindEntry } = ctx;
  const dict = asDict(parentField.state.value);
  const parts = bindEntry ? [] : name.split(".").filter((part) => part.length > 0);
  const value = getAt(dict[entryKey], parts);

  // 只实现叶子控件用到的 value / handleChange / meta.errors; 完整 AnyFieldApi 过宽.
  const field = {
    state: { value, meta: { errors: [] } },
    handleChange: (next: unknown) => {
      const latest = asDict(parentField.state.value);
      parentField.handleChange({
        ...latest,
        [entryKey]: setAt(latest[entryKey], parts, next),
      });
    },
  } as unknown as AnyFieldApi;

  return children(field);
}

export const dictEntryForm: SchemaFormInstance = {
  Field: DictEntryField,
};
