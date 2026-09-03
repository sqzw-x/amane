import { Select } from "@mantine/core";
import type { AnyFieldApi } from "@tanstack/react-form";
import { useEnumI18n } from "../hooks";
import type { EnumSchema, FieldProps } from "../schema";
import { isSimpleEnum } from "../schema";
import { useFieldDomId } from "./dict-entry-form";
import { EnumToggleField } from "./enum-toggle-field";
import { FieldChrome } from "./field-chrome";
import { fieldError } from "./field-error";

export function EnumField({
  name,
  label,
  description,
  schema,
  form,
  i18nPath,
  i18nPrefix,
  variant,
}: FieldProps<EnumSchema>) {
  const id = useFieldDomId(name);
  const enumValues = schema.enum;
  const getOptionLabel = useEnumI18n(i18nPath, i18nPrefix);
  const simple = isSimpleEnum(schema);

  // Toggle vs Select is schema-driven, not variant-driven - bare mode keeps
  // toggle for ≤5 simple enums (looks better in dict KV rows / table cells).
  if (simple && enumValues.length <= 5) {
    return (
      <EnumToggleField
        name={name}
        label={label}
        description={description}
        schema={schema}
        form={form}
        i18nPath={i18nPath}
        i18nPrefix={i18nPrefix}
        variant={variant}
      />
    );
  }

  return (
    <form.Field name={name}>
      {(field: AnyFieldApi) => {
        const data = enumValues.map((opt, i) => ({
          value: String(opt),
          label: getOptionLabel(opt, i, schema),
        }));
        return (
          <FieldChrome
            variant={variant}
            htmlFor={id}
            label={label}
            description={description}
            error={fieldError(field)}
          >
            <Select
              id={id}
              data={data}
              value={field.state.value == null ? null : String(field.state.value)}
              onChange={(val) => field.handleChange(val)}
              placeholder={`Select ${label}`}
              comboboxProps={{ withinPortal: true }}
            />
          </FieldChrome>
        );
      }}
    </form.Field>
  );
}
