import { ActionIcon, Group, ScrollArea, Textarea, TextInput } from "@mantine/core";
import { IconPlus } from "@tabler/icons-react";
import type { AnyFieldApi } from "@tanstack/react-form";
import { useState } from "react";
import type { ArrayFieldProps, JSONSchemaObject } from "../schema";
import { isOrdered } from "../schema";
import { useFieldDomId } from "./dict-entry-form";
import { DraggableChips } from "./draggable-chips";
import { FieldChrome } from "./field-chrome";

export function SimpleArrayField({
  name,
  label,
  description,
  schema,
  form,
  variant,
}: ArrayFieldProps<JSONSchemaObject>) {
  const id = useFieldDomId(name);
  const ordered = isOrdered(schema);
  const long = schema["x-long"] === true;

  return (
    <form.Field name={name}>
      {(field: AnyFieldApi) => {
        const value = Array.isArray(field.state.value) ? (field.state.value as string[]) : [];

        const control = ordered ? (
          <OrderedArrayBody value={value} onChange={field.handleChange} long={long} />
        ) : long ? (
          // x-long, unordered: taller textarea, one value per line
          <Textarea
            id={id}
            value={value.join("\n")}
            onChange={(e) => {
              const parts = e.target.value
                .split("\n")
                .map((s: string) => s.trim())
                .filter(Boolean);
              field.handleChange(parts);
            }}
            placeholder="One value per line"
            rows={8}
          />
        ) : (
          // Default: comma-separated input
          <TextInput
            id={id}
            value={value.join(", ")}
            onChange={(e) => {
              const parts = e.target.value
                .split(",")
                .map((s: string) => s.trim())
                .filter(Boolean);
              field.handleChange(parts);
            }}
            placeholder="Comma-separated values"
          />
        );

        return (
          <FieldChrome variant={variant} htmlFor={id} label={label} description={description}>
            {control}
          </FieldChrome>
        );
      }}
    </form.Field>
  );
}

interface OrderedArrayBodyProps {
  value: string[];
  onChange: (v: string[]) => void;
  /** x-long: render the chip list inside a fixed-height scrollable container. */
  long?: boolean;
}

/** x-ordered mode body: draggable chips + add input. Chrome handled by parent. */
function OrderedArrayBody({ value, onChange, long }: OrderedArrayBodyProps) {
  const [newItem, setNewItem] = useState("");

  const handleAdd = () => {
    const trimmed = newItem.trim();
    if (trimmed && !value.includes(trimmed)) {
      onChange([...value, trimmed]);
      setNewItem("");
    }
  };

  const chips = (
    <DraggableChips
      items={value}
      getKey={(item) => item}
      getLabel={(item) => item}
      onChange={onChange}
      onDelete={(item) => onChange(value.filter((v) => v !== item))}
    />
  );

  return (
    <>
      {long ? (
        <ScrollArea.Autosize mah={200} type="auto">
          {chips}
        </ScrollArea.Autosize>
      ) : (
        chips
      )}
      <Group gap={6} mt={4} wrap="nowrap">
        <TextInput
          size="xs"
          value={newItem}
          onChange={(e) => setNewItem(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              handleAdd();
            }
          }}
          placeholder="Add item..."
          style={{ flex: 1 }}
        />
        <ActionIcon
          variant="default"
          size="sm"
          onClick={handleAdd}
          disabled={!newItem.trim()}
          aria-label="Add item"
        >
          <IconPlus size={14} />
        </ActionIcon>
      </Group>
    </>
  );
}
