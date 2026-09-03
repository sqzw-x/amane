import { Button, Group, Input } from "@mantine/core";
import type { AnyFieldApi } from "@tanstack/react-form";
import type { FieldProps } from "../schema";
import { isNullable } from "../schema";
import type { TextJSONSchema } from "../schema/types";
import { useFieldDomId } from "./dict-entry-form";
import { FieldChrome } from "./field-chrome";
import { fieldError } from "./field-error";

/**
 * Generic text input field. Also serves as the fallback for unrecognized schema types.
 * Accepts the broad SchemaFieldProps since it may receive composed or untyped schemas.
 *
 * `x-long` renders a taller textarea even when `x-multiline` isn't set - for
 * long-form text (notes, logs, body text) that benefits from more vertical space.
 */
export function TextField({
  name,
  label,
  description,
  schema,
  form,
  variant,
}: FieldProps<TextJSONSchema>) {
  const id = useFieldDomId(name);
  const nullable = isNullable(schema);
  const long = schema["x-long"] === true;
  const multiline = schema["x-multiline"] === true || long;

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
          <Group gap="xs" wrap="nowrap" align={multiline ? "flex-start" : "center"}>
            {multiline ? (
              <Input
                id={id}
                component="textarea"
                rows={long ? 8 : 3}
                value={(field.state.value as string) ?? ""}
                onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) =>
                  field.handleChange(e.target.value || (nullable ? null : ""))
                }
                placeholder={nullable ? "(not set)" : undefined}
                style={{ flex: 1 }}
              />
            ) : (
              <Input
                id={id}
                value={(field.state.value as string) ?? ""}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                  field.handleChange(e.target.value || (nullable ? null : ""))
                }
                placeholder={nullable ? "(not set)" : undefined}
                style={{ flex: 1 }}
              />
            )}
            {nullable && field.state.value != null && (
              <Button
                type="button"
                variant="outline"
                size="compact-sm"
                onClick={() => field.handleChange(null)}
              >
                Clear
              </Button>
            )}
          </Group>
        </FieldChrome>
      )}
    </form.Field>
  );
}
