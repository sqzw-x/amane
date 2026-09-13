import type {
  ArrayJSONSchema,
  BooleanJSONSchema,
  ComposedJSONSchema,
  DictJSONSchema,
  EnumSchema,
  JSONSchemaObject,
  NumericEnumJSONSchema,
  NumericJSONSchema,
  LibraryJSONSchema,
  ObjectJSONSchema,
  PathJSONSchema,
  TextEnumJSONSchema,
  TextJSONSchema,
} from "./types";

// ==================== Type Guards ====================

/** Check if schema represents a boolean field */
export function isBool(schema: JSONSchemaObject): schema is BooleanJSONSchema {
  return schema.type === "boolean";
}

/** Check if schema represents a numeric (number/integer) field without enum or LibraryPicker */
export function isNumeric(schema: JSONSchemaObject): schema is NumericJSONSchema {
  return (
    (schema.type === "number" || schema.type === "integer") &&
    !isLibrary(schema) &&
    !("enum" in schema && Array.isArray(schema.enum))
  );
}

/** Check if schema represents a library picker (x-widget: "LibraryPicker") */
export function isLibrary(schema: JSONSchemaObject): schema is LibraryJSONSchema {
  return (
    schema.type === "integer" && "x-widget" in schema && schema["x-widget"] === "LibraryPicker"
  );
}

/** Check if schema represents a numeric field with enum */
export function isNumericEnum(schema: JSONSchemaObject): schema is NumericEnumJSONSchema {
  return (
    (schema.type === "number" || schema.type === "integer") &&
    "enum" in schema &&
    Array.isArray(schema.enum) &&
    schema.enum.length > 0
  );
}

/** Check if schema represents a text (string) field without enum and without PathPicker */
export function isText(schema: JSONSchemaObject): schema is TextJSONSchema {
  return schema.type === "string" && !isPath(schema) && !isTextEnum(schema);
}

/** Check if schema represents a text (string) field with enum */
export function isTextEnum(schema: JSONSchemaObject): schema is TextEnumJSONSchema {
  return (
    schema.type === "string" &&
    "enum" in schema &&
    Array.isArray(schema.enum) &&
    schema.enum.length > 0
  );
}

/** Check if schema represents a path field (x-widget: "PathPicker") */
export function isPath(schema: JSONSchemaObject): schema is PathJSONSchema {
  return "x-widget" in schema && schema["x-widget"] === "PathPicker";
}

/** Check if schema represents an array type */
export function isArray(schema: JSONSchemaObject): schema is ArrayJSONSchema {
  return schema.type === "array";
}

/** Check if schema represents an object type (with known properties, not a dict) */
export function isObject(schema: JSONSchemaObject): schema is ObjectJSONSchema {
  return schema.type === "object" && !isDict(schema);
}

/** Check if schema represents a dict/map (object with additionalProperties) */
export function isDict(schema: JSONSchemaObject): schema is DictJSONSchema {
  return (
    schema.type === "object" &&
    "additionalProperties" in schema &&
    typeof schema.additionalProperties === "object" &&
    schema.additionalProperties !== null
  );
}

/** Check if schema represents a composed schema (anyOf/oneOf/allOf/$ref without explicit type) */
export function isComposed(schema: JSONSchemaObject): schema is ComposedJSONSchema {
  return (
    !("type" in schema && schema.type) &&
    ("anyOf" in schema || "oneOf" in schema || "allOf" in schema || "$ref" in schema)
  );
}

/** Check if schema has enum values (numeric or text) */
export function isEnum(
  schema: JSONSchemaObject,
): schema is NumericEnumJSONSchema | TextEnumJSONSchema {
  return "enum" in schema && Array.isArray(schema.enum) && schema.enum.length > 0;
}

/**
 * Check if a schema renders as a single inline scalar control
 * (text/number/enum/bool/path - excluding multiline text).
 *
 * Used by DictField to decide between a compact key-value list layout
 * and the tabbed layout for complex (object/array/dict) values.
 */
export function isSimpleScalar(schema: JSONSchemaObject): boolean {
  // Multiline text expands vertically - don't squeeze it into a KV row
  if (isText(schema) && schema["x-long"] === true) return false;
  return (
    isText(schema) ||
    isPath(schema) ||
    isLibrary(schema) ||
    isBool(schema) ||
    isEnum(schema) ||
    isNumeric(schema)
  );
}

// ==================== Property Helpers ====================

/** Check if array schema has x-ordered */
export function isOrdered(schema: ArrayJSONSchema): boolean {
  return schema["x-ordered"] === true;
}

/** Check if field is hidden */
export function isHidden(schema: JSONSchemaObject): boolean {
  return schema["x-hidden"] === true;
}

/** Check if schema is nullable (explicit flag or anyOf with null) */
export function isNullable(schema: JSONSchemaObject): boolean {
  if (schema.nullable) return true;
  const anyOf = schema.anyOf;
  if (anyOf) {
    return anyOf.some((s) => s.type === "null");
  }
  return false;
}

/** Check if enum should use toggle group (x-simple or ≤5 options) */
export function isSimpleEnum(schema: EnumSchema): boolean {
  if (schema["x-simple"] === true) return true;
  if (schema.enum && schema.enum.length <= 5) return true;
  return false;
}

/** Check if field should be visible for a given dict key (x-visible-keys / x-hidden-keys) */
export function isVisibleForKey(schema: JSONSchemaObject, dictKey: string | undefined): boolean {
  if (!dictKey) return true;
  const visibleKeys = schema["x-visible-keys"];
  if (visibleKeys) return visibleKeys.includes(dictKey);
  const hiddenKeys = schema["x-hidden-keys"];
  if (hiddenKeys) return !hiddenKeys.includes(dictKey);
  return true;
}

/** Check if dict has frozen keys (x-frozen-keys) - no add/remove, only edit values */
export function isFrozenKeys(schema: DictJSONSchema): boolean {
  return schema["x-frozen-keys"] === true;
}

// ==================== Resolution Helpers ====================

/** Get the effective type, resolving anyOf with null (Pydantic Optional pattern) */
export function getEffectiveType(schema: JSONSchemaObject): JSONSchemaObject {
  if (schema.type && schema.type !== "null") return schema;
  const anyOf = schema.anyOf;
  if (anyOf) {
    const nonNull = anyOf.find((s) => s.type !== "null");
    if (nonNull) return nonNull;
  }
  return schema;
}
