import type { AnyFieldApi } from "@tanstack/react-form";
import { LibraryPicker } from "@/components/library-picker";
import type { FieldProps, LibraryJSONSchema } from "../schema";
import { isNullable } from "../schema";
import { useFieldDomId } from "./dict-entry-form";
import { FieldChrome } from "./field-chrome";
import { fieldError } from "./field-error";

export function LibraryField({
  name,
  label,
  description,
  schema,
  form,
  variant,
}: FieldProps<LibraryJSONSchema>) {
  const id = useFieldDomId(name);
  const nullable = isNullable(schema);

  return (
    <form.Field name={name}>
      {(field: AnyFieldApi) => (
        <FieldChrome
          variant={variant}
          htmlFor={id}
          label={label}
          description={description}
          error={fieldError(field)}
        >
          <LibraryPicker
            value={typeof field.state.value === "number" ? field.state.value : null}
            onChange={(v) => field.handleChange(v)}
            clearable={nullable}
          />
        </FieldChrome>
      )}
    </form.Field>
  );
}
