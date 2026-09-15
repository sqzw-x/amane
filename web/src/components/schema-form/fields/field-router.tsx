import { Alert, Box, Code, Text } from "@mantine/core";
import { IconAlertTriangle } from "@tabler/icons-react";
import { devLog } from "@/lib/dev-logger";
import { useSchemaI18n } from "../hooks";
import type { FieldVariant, JSONSchemaObject, SchemaFormInstance } from "../schema";
import {
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
} from "../schema";
import { BoolField } from "./bool-field";
import { DictField } from "./dict-field";
import { EnumArrayField } from "./enum-array-field";
import { EnumField } from "./enum-field";
import classes from "./field-router.module.css";
import { LibraryField } from "./library-field";
import { NumericField } from "./numeric-field";
import { ObjectArrayField } from "./object-array-field";
import { PathField } from "./path-field";
import { SimpleArrayField } from "./simple-array-field";
import { TextField } from "./text-field";

const TAG = "FieldRouter";

interface FieldRouterProps {
  /** Form field binding path (e.g., "scraping.site_config.javdb.use_proxy") */
  name: string;
  schema: JSONSchemaObject;
  form: SchemaFormInstance;
  /**
   * Override i18n lookup path when it differs from form binding path.
   * Used by DictField to map "scraping.site_config.javdb.use_proxy" →
   * "scraping.site_config.$.use_proxy" so all dict keys share one translation.
   */
  i18nPath?: string;
  /** i18n lookup prefix: "namespace:pathPrefix" (e.g., "settings:fields", "tasks"). */
  i18nPrefix?: string;
  /**
   * Visual mode passed through to leaf field components.
   * - `default` (the default) - full chrome (label + description + wrapper).
   * - `bare` - strip chrome; container provides labeling/layout.
   *
   * Composite types (Dict / Array / Object-inline) currently ignore this
   * prop - only leaf fields apply it. Container-level variant support can
   * be added when a use case appears.
   */
  variant?: FieldVariant;
}

export function FieldRouter({
  name,
  schema,
  form,
  i18nPath,
  i18nPrefix = "settings:fields",
  variant,
}: FieldRouterProps) {
  const effectiveI18nPath = i18nPath ?? name;
  const { label, description } = useSchemaI18n(effectiveI18nPath, schema, i18nPrefix);
  const basic = {
    name,
    label,
    description,
    form,
    i18nPath: effectiveI18nPath,
    i18nPrefix,
    variant,
  };

  if (isHidden(schema)) {
    devLog.debug(TAG, `Skip hidden field: ${name}`);
    return null;
  }

  if (isText(schema)) {
    devLog.info(TAG, `→ TextField: ${name}`);
    return <TextField schema={schema} {...basic} />;
  }

  if (isPath(schema)) {
    devLog.info(TAG, `→ PathField: ${name}`);
    return <PathField schema={schema} {...basic} />;
  }

  if (isLibrary(schema)) {
    devLog.info(TAG, `→ LibraryField: ${name}`);
    return <LibraryField schema={schema} {...basic} />;
  }

  if (isBool(schema)) {
    devLog.info(TAG, `→ BoolField: ${name}`);
    return <BoolField schema={schema} {...basic} />;
  }

  if (isEnum(schema)) {
    devLog.info(TAG, `→ EnumField: ${name}`);
    return <EnumField schema={schema} {...basic} />;
  }

  if (isNumeric(schema)) {
    devLog.info(TAG, `→ NumericField: ${name}`);
    return <NumericField schema={schema} {...basic} />;
  }

  if (isDict(schema)) {
    devLog.info(TAG, `→ DictField: ${name}`);
    return <DictField schema={schema} valueSchema={schema.additionalProperties} {...basic} />;
  }

  if (isObject(schema)) {
    const properties = schema.properties ?? {};
    if (Object.keys(properties).length === 0) {
      devLog.debug(TAG, `Skip empty object: ${name}`);
      return null;
    }
    devLog.info(TAG, `→ Object (inline children): ${name}`, { keys: Object.keys(properties) });
    return (
      <Box
        className={classes.nest}
        pl="sm"
        py="xs"
        style={{ borderLeft: "2px solid var(--mantine-color-default-border)" }}
      >
        {schema.title && (
          <Text size="sm" fw={500} mb="xs">
            {label}
          </Text>
        )}
        {Object.entries(properties).map(([key, fieldSchema]) => (
          <FieldRouter
            key={key}
            name={`${name}.${key}`}
            schema={fieldSchema}
            form={form}
            i18nPath={`${effectiveI18nPath}.${key}`}
            i18nPrefix={i18nPrefix}
          />
        ))}
      </Box>
    );
  }

  if (isArray(schema)) {
    const itemSchema = schema.items;
    if (!itemSchema) {
      devLog.debug(TAG, `Skip empty array: ${name}`);
      return null;
    }
    // i18nPath 加 `.$,` 表示 array item 层级, 与 DictField 行为一致.
    const itemBasic = { ...basic, i18nPath: `${effectiveI18nPath}.$` };
    if (isEnum(itemSchema)) {
      devLog.info(TAG, `→ EnumArrayField: ${name}`);
      return <EnumArrayField schema={schema} itemSchema={itemSchema} {...itemBasic} />;
    }
    if (isObject(itemSchema) && itemSchema.properties) {
      devLog.info(TAG, `→ ObjectArrayField: ${name}`);
      return <ObjectArrayField schema={schema} itemSchema={itemSchema} {...itemBasic} />;
    }
    devLog.info(TAG, `→ SimpleArrayField: ${name}`);
    return <SimpleArrayField schema={schema} itemSchema={itemSchema} {...itemBasic} />;
  }

  // 不支持的类型 - 界面警告 + 控制台调试
  devLog.warn(TAG, `Unsupported schema type for "${name}":`, schema);
  return (
    <Alert
      color="yellow"
      variant="light"
      icon={<IconAlertTriangle size={16} />}
      title="Unsupported field"
    >
      <Text size="sm" mb={4}>
        <Code>{name}</Code>
      </Text>
      <Code block style={{ maxHeight: 96, overflow: "auto" }}>
        {JSON.stringify(schema, null, 2)}
      </Code>
    </Alert>
  );
}
