import { Box, Button, Group, SimpleGrid, Stack } from "@mantine/core";
import { useForm } from "@tanstack/react-form";
import { useCallback, useId, useMemo, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { UnsavedChangesBar } from "@/components/common/unsaved-changes-bar";
import { isRecord } from "@/lib/utils";
import { encodeFormBody } from "./encode";
import { FieldRouter } from "./fields";
import type { JSONSchemaObject } from "./schema";
import {
  createSchemaValidator,
  isArray,
  isBool,
  isDict,
  isEnum,
  isHidden,
  isLibrary,
  isNumeric,
  isObject,
  isPath,
  isText,
} from "./schema";

/** Deep equality check for config values (handles primitives, arrays, objects). */
function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (a == null || b == null) return a === b;
  if (typeof a !== typeof b) return false;
  if (Array.isArray(a)) {
    if (!Array.isArray(b) || a.length !== b.length) return false;
    return a.every((v, i) => deepEqual(v, b[i]));
  }
  if (isRecord(a) && isRecord(b)) {
    const keysA = Object.keys(a);
    const keysB = Object.keys(b);
    if (keysA.length !== keysB.length) return false;
    return keysA.every((k) => deepEqual(a[k], b[k]));
  }
  return false;
}

/** Keys that stay full-width even when `fieldLayout="grid"`. */
const WIDE_TEXT_KEYS = new Set([
  "title",
  "plot",
  "overview",
  "description",
  "body",
  "notes",
  "comment",
  "summary",
]);

function isCompactFormField(key: string, schema: JSONSchemaObject): boolean {
  if (isHidden(schema)) return false;
  if (
    isArray(schema) ||
    isDict(schema) ||
    isObject(schema) ||
    isPath(schema) ||
    isLibrary(schema)
  ) {
    return false;
  }
  if (isText(schema) && (schema["x-multiline"] === true || schema["x-long"] === true)) {
    return false;
  }
  if (WIDE_TEXT_KEYS.has(key)) return false;
  return isText(schema) || isNumeric(schema) || isEnum(schema) || isBool(schema);
}

interface FieldNode {
  key: string;
  schema: JSONSchemaObject;
}

/** Consecutive compact scalars become 2-col rows; everything else is a full-width row. */
function layoutFieldRows(
  properties: NonNullable<JSONSchemaObject["properties"]>,
  grid: boolean,
): FieldNode[][] {
  const nodes: FieldNode[] = [];
  for (const [key, schema] of Object.entries(properties)) {
    if (typeof schema === "boolean") continue;
    if (isHidden(schema)) continue;
    nodes.push({ key, schema });
  }
  if (!grid) return nodes.map((n) => [n]);

  const rows: FieldNode[][] = [];
  let run: FieldNode[] = [];
  const flush = () => {
    if (run.length === 0) return;
    if (run.length === 1) {
      rows.push(run);
    } else {
      for (let i = 0; i < run.length; i += 2) {
        rows.push(run.slice(i, i + 2));
      }
    }
    run = [];
  };
  for (const node of nodes) {
    if (isCompactFormField(node.key, node.schema)) {
      run.push(node);
    } else {
      flush();
      rows.push([node]);
    }
  }
  flush();
  return rows;
}

interface SchemaFormProps {
  /**
   * The schema describing this form's fields. All $ref should be resolved.
   * Each entry in `schema.properties` is rendered as a field via FieldRouter.
   */
  schema: JSONSchemaObject;
  /**
   * Path prefix prepended to field names (e.g., "scraping").
   * Field names become `${prefix}.${fieldKey}`, matching the nested defaultValues structure.
   */
  prefix: string;
  /** Initial values scoped to this form's fields (flat key→value for each property in schema). */
  values: Record<string, unknown>;
  /** i18n lookup prefix: "namespace:pathPrefix" (e.g., "settings:fields"). */
  i18nPrefix: string;
  onSave: (patch: Record<string, unknown>) => void;
  saving: boolean;
  /**
   * Where dirty save/reset actions appear.
   * - `affix`: fixed viewport-bottom bar (settings page and metadata/actor edit modals).
   * - `inline`: sticky to the nearest scrollport (embedded / create forms).
   * @default "inline"
   */
  actionsPlacement?: "affix" | "inline";
  /**
   * - `patch` (default): dirty-gated save bar; `onSave` receives only changed fields.
   * - `create`: always-visible submit bar; `onSave` receives the full form values.
   */
  mode?: "patch" | "create";
  /** Override the primary action label (defaults: save / submit by mode). */
  submitLabel?: string;
  /** Extra disable gate for the primary action (e.g. parent envelope fields incomplete). */
  submitDisabled?: boolean;
  /**
   * Field arrangement.
   * - `stack` (default): one field per row.
   * - `grid`: consecutive short scalars share a 2-col row on `sm+`.
   */
  fieldLayout?: "stack" | "grid";
}

export function SchemaForm({
  schema,
  prefix,
  i18nPrefix,
  values,
  onSave,
  saving,
  actionsPlacement = "inline",
  mode = "patch",
  submitLabel,
  submitDisabled = false,
  fieldLayout = "stack",
}: SchemaFormProps) {
  const { t } = useTranslation("common");
  const formId = useId();
  const properties = useMemo(() => schema.properties ?? {}, [schema.properties]);
  const isCreate = mode === "create";
  const rows = useMemo(
    () => layoutFieldRows(properties, fieldLayout === "grid"),
    [properties, fieldLayout],
  );

  // Build defaultValues as { [prefix]: { field1: val1, field2: val2, ... } }
  // TanStack Form resolves dot-path names like "prefix.field1" to this nested object.
  const defaultValues = useMemo(() => {
    const fields: Record<string, unknown> = {};
    for (const fieldKey of Object.keys(properties)) {
      const fieldSchema = properties[fieldKey];
      if (Object.prototype.hasOwnProperty.call(values, fieldKey)) {
        fields[fieldKey] = values[fieldKey];
      } else if (typeof fieldSchema !== "boolean" && "default" in fieldSchema) {
        fields[fieldKey] = fieldSchema.default;
      } else {
        fields[fieldKey] = null;
      }
    }
    return { [prefix]: fields };
  }, [prefix, properties, values]);

  // JSON Schema 约束校验器 - 投影逐字段错误到 TanStack Form 的 field.meta.errors.
  // i18next 的 t 只接受字面量 key, 校验消息 key 是动态拼接的, 故经 as never 适配
  // (与 use-schema-i18n.ts 的既有约定一致).
  const validate = useMemo(
    () =>
      createSchemaValidator(prefix, properties, (key, opts) =>
        String(t(key as never, opts as never)),
      ),
    [prefix, properties, t],
  );

  const form = useForm({
    defaultValues,
    validators: { onChange: validate },
    onSubmit: ({ value }) => {
      const sectionVal = isRecord(value) && isRecord(value[prefix]) ? value[prefix] : {};
      // create: 提交完整表单值; patch: 只提交相对初始值有变化的字段
      if (isCreate) {
        onSave(encodeFormBody(schema, { ...sectionVal }));
        return;
      }
      const encoded = encodeFormBody(schema, { ...sectionVal });
      const baseline = encodeFormBody(schema, { ...values });
      const patch: Record<string, unknown> = {};
      for (const [field, val] of Object.entries(encoded)) {
        if (!deepEqual(val, baseline[field])) {
          patch[field] = val;
        }
      }
      if (Object.keys(patch).length > 0) {
        onSave(patch);
      }
    },
  });

  const handleReset = useCallback(() => {
    form.reset(defaultValues);
  }, [form, defaultValues]);

  const primaryLabel =
    submitLabel ??
    (isCreate ? t("actions.submit") : saving ? t("actions.saving") : t("actions.save"));

  const actions = (
    <form.Subscribe
      selector={(s) => {
        const current = isRecord(s.values) && isRecord(s.values[prefix]) ? s.values[prefix] : {};
        return {
          dirty: !deepEqual(
            encodeFormBody(schema, { ...current }),
            encodeFormBody(schema, { ...values }),
          ),
          isValid: s.isValid,
        };
      }}
    >
      {({ dirty, isValid }) => {
        if (isCreate) {
          return (
            <Box mt="md">
              <Group justify="flex-end">
                <Button
                  type="submit"
                  form={formId}
                  disabled={saving || !isValid || submitDisabled}
                  loading={saving}
                >
                  {primaryLabel}
                </Button>
              </Group>
            </Box>
          );
        }

        return (
          <UnsavedChangesBar
            dirty={dirty}
            saving={saving}
            saveDisabled={!isValid || submitDisabled}
            formId={formId}
            onDiscard={handleReset}
            submitLabel={submitLabel}
            placement={actionsPlacement === "affix" ? "affix" : "sticky"}
          />
        );
      }}
    </form.Subscribe>
  );

  const fieldBlocks: ReactNode[] = rows.map((row, rowIdx) => {
    const fields = row.map(({ key, schema: fieldSchema }) => (
      <FieldRouter
        key={key}
        name={`${prefix}.${key}`}
        schema={fieldSchema}
        form={form}
        i18nPrefix={i18nPrefix}
      />
    ));
    const rowStyle =
      rowIdx > 0 ? { borderTop: "1px solid var(--mantine-color-default-border)" } : undefined;
    if (row.length === 1) {
      return (
        <Box key={row[0].key} style={rowStyle}>
          {fields[0]}
        </Box>
      );
    }
    return (
      <SimpleGrid
        key={`row-${rowIdx}-${row[0].key}`}
        cols={{ base: 1, sm: 2 }}
        spacing="md"
        style={rowStyle}
      >
        {fields}
      </SimpleGrid>
    );
  });

  return (
    <Stack
      component="form"
      id={formId}
      gap="md"
      onSubmit={(e) => {
        e.preventDefault();
        form.handleSubmit();
      }}
    >
      <Stack gap={0}>{fieldBlocks}</Stack>
      {actions}
    </Stack>
  );
}
