import { isDict, isHidden, resolveSchema } from "@/components/schema-form/schema";
import type { JSONSchemaObject } from "@/components/schema-form/schema";
import { isRecord } from "@/lib/utils";

function isPluginSchema(value: unknown): value is JSONSchemaObject {
  if (!isRecord(value)) return false;
  return (
    value.type === "object" ||
    Object.prototype.hasOwnProperty.call(value, "properties") ||
    Object.prototype.hasOwnProperty.call(value, "additionalProperties") ||
    Object.prototype.hasOwnProperty.call(value, "$ref")
  );
}

/**
 * 插件 schema 经 API 送达, 形状在运行时才确定; schema-form 是校验其 JSON Schema 形状的边界.
 * 无法识别时退回空对象 schema, 插件仍可启停与卸载.
 */
export function resolvePluginSchema(raw: Record<string, unknown>): JSONSchemaObject {
  const schema: JSONSchemaObject = isPluginSchema(raw) ? raw : { type: "object", properties: {} };
  return resolveSchema(schema, schema);
}

/** 是否存在可渲染字段; `false` 表示设置弹窗没有内容, 设置入口不应出现. */
export function hasConfigurableFields(schema: JSONSchemaObject): boolean {
  const properties = Object.values(schema.properties ?? {});
  if (properties.some((field) => typeof field !== "boolean" && !isHidden(field))) return true;
  if (properties.length > 0) return false;
  // 无 properties 时只有字典形态还有可编辑内容 (DictField 逐项渲染).
  return isDict(schema) && !isHidden(schema.additionalProperties);
}
