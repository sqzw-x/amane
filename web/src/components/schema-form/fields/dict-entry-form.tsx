import type { AnyFieldApi } from "@tanstack/react-form";
import { createContext, useContext, useId, type ReactNode } from "react";
import { isRecord } from "@/lib/utils";
import { isEmptyDictValue } from "../encode";
import type { SchemaFormInstance } from "../schema";

/**
 * Dict 条目绑定: 用户 key 是对象上的字面量, 不拼进 TanStack 点路径.
 * 叶子控件仍使用 `form.Field`; 本 scope 把读写转到父级 dict 的 `[entryKey]`.
 *
 * `bindEntry`: 值本身是 scalar / array, Field `name` 只作 DOM id.
 * 否则 `name` 是条目内部的 schema 相对路径 (属性名, 可再嵌套).
 *
 * Tabs 同时挂载全部条目, 叶子 `id`/`htmlFor` 必须经 `useFieldDomId` 加上条目前缀,
 * 不能只用 schema 相对名, 否则同名 Switch 的 label 会命中第一份控件.
 */
const DictEntryContext = createContext<{
  parentField: AnyFieldApi;
  entryKey: string;
  bindEntry: boolean;
  pruneEmpty: boolean;
  scopeId: string;
} | null>(null);

export function DictEntryScope({
  parentField,
  entryKey,
  bindEntry,
  pruneEmpty = false,
  children,
}: {
  parentField: AnyFieldApi;
  entryKey: string;
  bindEntry: boolean;
  /** 可增减 key 时, 条目值被清空则删除该 key. `x-frozen-keys` 必须为 false. */
  pruneEmpty?: boolean;
  children: ReactNode;
}) {
  const scopeId = useId();
  return (
    <DictEntryContext.Provider value={{ parentField, entryKey, bindEntry, pruneEmpty, scopeId }}>
      {children}
    </DictEntryContext.Provider>
  );
}

/** 叶子控件 DOM id. 处于 DictEntryScope 时附加本条目前缀, 避免多条目同名碰撞. */
export function useFieldDomId(name: string): string {
  const ctx = useContext(DictEntryContext);
  if (ctx == null) return name;
  return `${ctx.scopeId}${name}`;
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
  const { parentField, entryKey, bindEntry, pruneEmpty } = ctx;
  const dict = asDict(parentField.state.value);
  const parts = bindEntry ? [] : name.split(".").filter((part) => part.length > 0);
  const value = getAt(dict[entryKey], parts);

  // 只实现叶子控件用到的 value / handleChange / meta.errors; 完整 AnyFieldApi 过宽.
  const field = {
    state: { value, meta: { errors: [] } },
    handleChange: (next: unknown) => {
      const latest = asDict(parentField.state.value);
      // 仅可增减 key 的 dict 在值被清空时删除该 key; frozen key 的空列表必须保留.
      if (pruneEmpty && bindEntry && isEmptyDictValue(next)) {
        const rest = { ...latest };
        delete rest[entryKey];
        parentField.handleChange(rest);
        return;
      }
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
