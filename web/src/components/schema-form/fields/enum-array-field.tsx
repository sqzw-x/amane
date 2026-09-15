import { Button, Checkbox, Group, Stack } from "@mantine/core";
import { IconPlus } from "@tabler/icons-react";
import type { AnyFieldApi } from "@tanstack/react-form";
import { useNarrowViewport } from "@/hooks/use-narrow-viewport";
import { useEnumI18n } from "../hooks";
import type { ArrayFieldProps, EnumSchema } from "../schema";
import { isOrdered } from "../schema";
import { DraggableChips } from "./draggable-chips";
import { FieldChrome } from "./field-chrome";

export function EnumArrayField({
  name,
  label,
  description,
  schema,
  itemSchema,
  form,
  i18nPath,
  i18nPrefix,
  variant,
}: ArrayFieldProps<EnumSchema>) {
  const enumValues: (string | number)[] = itemSchema.enum;
  const ordered = isOrdered(schema);
  const getOptionLabel = useEnumI18n(i18nPath, i18nPrefix);
  // 触屏设备无法执行 HTML5 拖拽, 窄屏改由上移 / 下移按钮调整顺序.
  const narrow = useNarrowViewport();

  return (
    <form.Field name={name}>
      {(field: AnyFieldApi) => {
        const selected = (field.state.value as (string | number)[]) ?? [];
        const selectedSet = new Set(selected);

        const control = ordered ? (
          // x-ordered mode: DraggableChips for selected items + add buttons for unselected
          <Stack gap={6}>
            <DraggableChips
              items={selected}
              getKey={(item) => String(item)}
              getLabel={(item) => {
                const idx = enumValues.indexOf(item);
                return getOptionLabel(item, idx, itemSchema);
              }}
              onChange={(newItems) => field.handleChange(newItems)}
              onDelete={(item) => field.handleChange(selected.filter((v) => v !== item))}
              onMove={narrow ? (newItems) => field.handleChange(newItems) : undefined}
            />
            {enumValues.some((v) => !selectedSet.has(v)) && (
              <Group gap={6} wrap="wrap">
                {enumValues
                  .filter((v) => !selectedSet.has(v))
                  .map((opt) => {
                    const idx = enumValues.indexOf(opt);
                    return (
                      <Button
                        key={opt}
                        type="button"
                        size="compact-xs"
                        variant="outline"
                        color="gray"
                        leftSection={<IconPlus size={12} />}
                        style={{ borderStyle: "dashed" }}
                        onClick={() => field.handleChange([...selected, opt])}
                      >
                        {getOptionLabel(opt, idx, itemSchema)}
                      </Button>
                    );
                  })}
              </Group>
            )}
          </Stack>
        ) : (
          // Default mode: checkbox list
          <Group gap="sm" wrap="wrap">
            {enumValues.map((opt, i) => {
              const isChecked = selectedSet.has(opt);
              return (
                <Checkbox
                  key={opt}
                  label={getOptionLabel(opt, i, itemSchema)}
                  checked={isChecked}
                  onChange={(e) => {
                    const checked = e.currentTarget.checked;
                    const next = new Set(selectedSet);
                    if (checked) next.add(opt);
                    else next.delete(opt);
                    field.handleChange(Array.from(next));
                  }}
                />
              );
            })}
          </Group>
        );

        return (
          <FieldChrome variant={variant} label={label} description={description}>
            {control}
          </FieldChrome>
        );
      }}
    </form.Field>
  );
}
