import { Switch } from "@mantine/core";
import type { AnyFieldApi } from "@tanstack/react-form";
import type { BooleanJSONSchema, FieldProps } from "../schema";
import { useFieldDomId } from "./dict-entry-form";
import { FieldChrome } from "./field-chrome";

export function BoolField({
  name,
  label,
  description,
  form,
  variant,
}: FieldProps<BooleanJSONSchema>) {
  const id = useFieldDomId(name);
  return (
    <form.Field name={name}>
      {(field: AnyFieldApi) => (
        <FieldChrome
          variant={variant}
          layout="horizontal"
          htmlFor={id}
          label={label}
          description={description}
        >
          <Switch
            id={id}
            checked={field.state.value === true}
            onChange={(e) => field.handleChange(e.currentTarget.checked)}
            aria-label={label}
          />
        </FieldChrome>
      )}
    </form.Field>
  );
}
