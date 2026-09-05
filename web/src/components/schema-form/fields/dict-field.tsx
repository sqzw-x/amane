import {
  ActionIcon,
  Group,
  Paper,
  ScrollArea,
  Select,
  Stack,
  Tabs,
  Text,
  TextInput,
} from "@mantine/core";
import { IconPlus, IconTrash } from "@tabler/icons-react";
import type { AnyFieldApi } from "@tanstack/react-form";
import { useRef, useState } from "react";
import { useDictKeyI18n } from "../hooks";
import type { DictFieldProps, JSONSchemaObject } from "../schema";
import {
  isArray,
  isEnum,
  isFrozenKeys,
  isObject,
  isSimpleScalar,
  isVisibleForKey,
} from "../schema";
import { DictEntryScope, dictEntryForm } from "./dict-entry-form";
import { FieldRouter } from "./field-router";

export function DictField({
  name,
  label,
  description,
  valueSchema,
  schema,
  form,
  i18nPath,
  i18nPrefix,
}: DictFieldProps<JSONSchemaObject>) {
  const readonly = schema["x-readonly"] ?? false;
  const frozenKeys = isFrozenKeys(schema);
  const canModifyKeys = !readonly && !frozenKeys;
  const getKeyLabel = useDictKeyI18n(i18nPath, i18nPrefix);
  // Simple scalar values (str/number/enum/bool/path) render as a compact KV
  // list with a scrollbar; complex values (object/array/dict/multiline)
  // continue to use the tabbed layout.
  const simpleMode = isSimpleScalar(valueSchema);

  const [newKey, setNewKey] = useState("");
  const [activeTab, setActiveTab] = useState("");
  // 标题栏滚轮不得切换条目 (部分浏览器把 tablist 当 radio group).
  const ignoreWheelTabChange = useRef(false);
  const ignoreWheelTabTimer = useRef(0);

  const keyEnum =
    schema.propertyNames && isEnum(schema.propertyNames) ? schema.propertyNames.enum : null;

  return (
    <form.Field name={name}>
      {(field: AnyFieldApi) => {
        const dictValue = (field.state.value as Record<string, unknown>) || {};
        const allKeys = Object.keys(dictValue);
        const entries = allKeys.map((k) => [k, dictValue[k]] as const);
        const resolvedTab = allKeys.includes(activeTab) ? activeTab : (allKeys[0] ?? "");

        const handleAdd = () => {
          if (!newKey.trim() || newKey in dictValue) return;

          const defaultValue =
            "default" in valueSchema
              ? valueSchema.default
              : valueSchema.type === "object"
                ? {}
                : valueSchema.type === "array"
                  ? []
                  : null;

          field.handleChange({ ...dictValue, [newKey]: defaultValue });
          if (!simpleMode) setActiveTab(newKey);
          setNewKey("");
        };

        const handleRemove = (key: string) => {
          const newDict = { ...dictValue };
          delete newDict[key];
          field.handleChange(newDict);
          if (!simpleMode && activeTab === key) {
            const remaining = Object.keys(newDict);
            setActiveTab(remaining.length > 0 ? remaining[0] : "");
          }
        };

        return (
          <Stack gap="xs" py="xs">
            <Group justify="space-between" align="flex-start" wrap="nowrap">
              <Stack gap={2}>
                <Text size="sm" fw={500}>
                  {label}
                </Text>
                {description && (
                  <Text size="xs" c="dimmed">
                    {description}
                  </Text>
                )}
              </Stack>
              {canModifyKeys && (
                <Group gap={6} wrap="nowrap">
                  {keyEnum ? (
                    <Select
                      size="xs"
                      w={160}
                      placeholder="Select..."
                      value={newKey || null}
                      onChange={(val) => setNewKey(val ?? "")}
                      data={keyEnum
                        .filter((k) => !(String(k) in dictValue))
                        .map((k) => ({ value: String(k), label: getKeyLabel(String(k)) }))}
                      comboboxProps={{ withinPortal: true }}
                    />
                  ) : (
                    <TextInput
                      size="xs"
                      w={160}
                      placeholder="New key..."
                      value={newKey}
                      onChange={(e) => setNewKey(e.target.value)}
                      onKeyDown={(e) => {
                        // Skip Enter while IME composition is active so the key
                        // confirms the IME selection instead of adding a dict key.
                        if (e.nativeEvent.isComposing || e.keyCode === 229) return;
                        if (e.key === "Enter") {
                          e.preventDefault();
                          handleAdd();
                        }
                      }}
                    />
                  )}
                  <ActionIcon
                    variant="default"
                    size="lg"
                    onClick={handleAdd}
                    disabled={!newKey.trim()}
                    aria-label="Add entry"
                  >
                    <IconPlus size={14} />
                  </ActionIcon>
                </Group>
              )}
            </Group>

            {entries.length === 0 ? (
              <Text size="sm" c="dimmed" ta="center" py="md">
                No entries
              </Text>
            ) : simpleMode ? (
              <Paper withBorder radius="md" style={{ overflow: "hidden" }}>
                <ScrollArea.Autosize mah={384} type="auto">
                  <Stack gap={0}>
                    {entries.map(([key], idx) => (
                      <Group
                        key={key}
                        justify="space-between"
                        wrap="nowrap"
                        gap="sm"
                        px="sm"
                        py={6}
                        style={
                          idx === 0
                            ? undefined
                            : { borderTop: "1px solid var(--mantine-color-default-border)" }
                        }
                      >
                        <Text
                          size="sm"
                          fw={500}
                          title={getKeyLabel(key)}
                          style={{
                            minWidth: 140,
                            maxWidth: 200,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                          }}
                        >
                          {getKeyLabel(key)}
                        </Text>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <DictEntryScope
                            parentField={field}
                            entryKey={key}
                            bindEntry
                            pruneEmpty={canModifyKeys}
                          >
                            <FieldRouter
                              name={`${name}:${key}`}
                              i18nPath={`${i18nPath}.$`}
                              i18nPrefix={i18nPrefix}
                              schema={valueSchema}
                              form={dictEntryForm}
                              variant="bare"
                            />
                          </DictEntryScope>
                        </div>
                        {canModifyKeys ? (
                          <ActionIcon
                            variant="subtle"
                            color="gray"
                            size="sm"
                            onClick={() => handleRemove(key)}
                            aria-label={`Remove ${key}`}
                          >
                            <IconTrash size={14} />
                          </ActionIcon>
                        ) : (
                          <div style={{ width: 28 }} />
                        )}
                      </Group>
                    ))}
                  </Stack>
                </ScrollArea.Autosize>
              </Paper>
            ) : (
              <Tabs
                value={resolvedTab}
                onChange={(val) => {
                  if (ignoreWheelTabChange.current) return;
                  setActiveTab(val ?? "");
                }}
                activateTabWithKeyboard={false}
              >
                <Paper withBorder radius="md" style={{ overflow: "hidden" }}>
                  <Tabs.List
                    onWheel={() => {
                      ignoreWheelTabChange.current = true;
                      const focused = document.activeElement;
                      if (
                        focused instanceof HTMLElement &&
                        focused.getAttribute("role") === "tab"
                      ) {
                        focused.blur();
                      }
                      window.clearTimeout(ignoreWheelTabTimer.current);
                      ignoreWheelTabTimer.current = window.setTimeout(() => {
                        ignoreWheelTabChange.current = false;
                      }, 120);
                    }}
                  >
                    {entries.map(([key]) => (
                      <Tabs.Tab key={key} value={key}>
                        {getKeyLabel(key)}
                      </Tabs.Tab>
                    ))}
                  </Tabs.List>

                  {entries.map(([key]) => (
                    <Tabs.Panel key={key} value={key} p="md">
                      <Group justify="space-between" mb="sm">
                        <Text size="sm" fw={500}>
                          {getKeyLabel(key)}
                        </Text>
                        {canModifyKeys && (
                          <ActionIcon
                            variant="subtle"
                            color="gray"
                            size="sm"
                            onClick={() => handleRemove(key)}
                            aria-label={`Remove ${key}`}
                          >
                            <IconTrash size={14} />
                          </ActionIcon>
                        )}
                      </Group>

                      {isObject(valueSchema) && valueSchema.properties ? (
                        <DictEntryScope
                          parentField={field}
                          entryKey={key}
                          bindEntry={false}
                          pruneEmpty={canModifyKeys}
                        >
                          <Stack gap={0} pl="xs">
                            {Object.entries(valueSchema.properties).map(
                              ([fieldName, fieldSchema]) =>
                                isVisibleForKey(fieldSchema, key) && (
                                  <FieldRouter
                                    key={fieldName}
                                    name={fieldName}
                                    // i18n lookup uses "$" wildcard so all dict entries share one translation set
                                    i18nPath={`${i18nPath}.$.${fieldName}`}
                                    i18nPrefix={i18nPrefix}
                                    schema={fieldSchema}
                                    form={dictEntryForm}
                                  />
                                ),
                            )}
                          </Stack>
                        </DictEntryScope>
                      ) : (
                        <DictEntryScope
                          parentField={field}
                          entryKey={key}
                          bindEntry
                          pruneEmpty={canModifyKeys}
                        >
                          <Stack gap={0} pl="xs">
                            <FieldRouter
                              name={`${name}:${key}`}
                              i18nPath={`${i18nPath}.$`}
                              i18nPrefix={i18nPrefix}
                              schema={valueSchema}
                              form={dictEntryForm}
                              variant={isArray(valueSchema) ? "bare" : "default"}
                            />
                          </Stack>
                        </DictEntryScope>
                      )}
                    </Tabs.Panel>
                  ))}
                </Paper>
              </Tabs>
            )}
          </Stack>
        );
      }}
    </form.Field>
  );
}
