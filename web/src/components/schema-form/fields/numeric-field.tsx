import { NumberInput } from "@mantine/core";
import type { AnyFieldApi } from "@tanstack/react-form";
import type { FieldProps, NumericJSONSchema } from "../schema";
import { getEffectiveType, isNullable } from "../schema";
import { useFieldDomId } from "./dict-entry-form";
import { FieldChrome } from "./field-chrome";
import { fieldError } from "./field-error";

/**
 * 数值输入框不传 min / max / step: 控件既不钳位也不提供微调箭头, 越界值原样保留,
 * 由表单校验在字段下方提示, 保存随之禁用.
 */
export function NumericField({
  name,
  label,
  description,
  schema,
  form,
  variant,
}: FieldProps<NumericJSONSchema>) {
  const id = useFieldDomId(name);
  const nullable = isNullable(schema);
  const effectiveSchema = getEffectiveType(schema);
  const isInteger = typeof effectiveSchema === "object" && effectiveSchema.type === "integer";

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
          <NumberInput
            id={id}
            hideControls
            allowDecimal={!isInteger}
            value={typeof field.state.value === "number" ? field.state.value : ""}
            onChange={(value) => {
              if (value === "") {
                field.handleChange(nullable ? null : 0);
                return;
              }
              field.handleChange(
                typeof value === "string"
                  ? isInteger
                    ? Number.parseInt(value, 10)
                    : Number.parseFloat(value)
                  : value,
              );
            }}
          />
        </FieldChrome>
      )}
    </form.Field>
  );
}
