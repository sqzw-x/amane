import { Badge, Button, Group, Paper, Stack, Switch, Text, Title } from "@mantine/core";
import { useTranslation } from "react-i18next";
import type { PluginResponse } from "@/client/types.gen";
import { resolveSchema } from "@/components/schema-form/schema";
import type { JSONSchemaObject } from "@/components/schema-form/schema";
import { SchemaForm } from "@/components/schema-form/schema-form";
import { useUninstallPlugin, useUpdatePlugin } from "@/hooks/use-plugins";
import { confirm } from "@/lib/confirm";
import { isRecord } from "@/lib/utils";

function isPluginSchema(value: unknown): value is JSONSchemaObject {
  if (!isRecord(value)) return false;
  return (
    value.type === "object" ||
    Object.prototype.hasOwnProperty.call(value, "properties") ||
    Object.prototype.hasOwnProperty.call(value, "additionalProperties") ||
    Object.prototype.hasOwnProperty.call(value, "$ref")
  );
}

function resolvePluginSchema(raw: Record<string, unknown>): JSONSchemaObject {
  // Plugin schemas arrive through the API and are intentionally dynamic; the
  // schema-form package is the boundary that validates their JSON Schema shape.
  const schema: JSONSchemaObject = isPluginSchema(raw) ? raw : { type: "object", properties: {} };
  return resolveSchema(schema, schema);
}

export function PluginCard({ plugin }: { plugin: PluginResponse }) {
  const { t } = useTranslation(["plugins", "common"]);
  const mutation = useUpdatePlugin();
  const uninstall = useUninstallPlugin();
  const schema = resolvePluginSchema(plugin.config_schema);
  const config = plugin.config.config ?? {};
  const enabled = plugin.config.enabled ?? true;
  const pending = mutation.isPending || uninstall.isPending;

  const update = (nextConfig: Record<string, unknown>, nextEnabled = enabled) => {
    mutation.mutate({
      path: { plugin_id: plugin.descriptor.id },
      body: { enabled: nextEnabled, config: nextConfig },
    });
  };

  const handleUninstall = async () => {
    const ok = await confirm({
      title: t("uninstall"),
      message: t("uninstallConfirm", {
        name: plugin.descriptor.name,
        id: plugin.descriptor.id,
      }),
      confirmLabel: t("common:actions.delete"),
    });
    if (!ok) return;
    uninstall.mutate({ path: { plugin_id: plugin.descriptor.id } });
  };

  return (
    <Paper withBorder p="md">
      <Stack gap="md">
        <Group justify="space-between" align="flex-start">
          <Stack gap={2}>
            <Title order={4}>{plugin.descriptor.name}</Title>
            <Text size="sm" c="dimmed">
              {t("sourceId")}: {plugin.descriptor.id}
            </Text>
            {plugin.path ? (
              <Text size="sm" c="dimmed">
                {t("path")}: {plugin.path}
              </Text>
            ) : null}
          </Stack>
          <Group gap="sm">
            <Switch
              checked={enabled}
              label={t("enabled")}
              onChange={(event) => update(config, event.currentTarget.checked)}
              disabled={pending}
            />
            <Button
              variant="light"
              color="red"
              size="xs"
              onClick={() => void handleUninstall()}
              disabled={pending}
            >
              {t("uninstall")}
            </Button>
          </Group>
        </Group>

        <Group gap="xs">
          <Badge variant="light">
            {t("version")}: {plugin.descriptor.version ?? "-"}
          </Badge>
          {plugin.descriptor.capabilities?.map((capability) => (
            <Badge key={capability} variant="light" color="blue">
              {t(`capabilities.${capability}`, { defaultValue: capability })}
            </Badge>
          ))}
        </Group>

        <SchemaForm
          schema={schema}
          prefix="pluginConfig"
          values={config}
          i18nPrefix="settings:fields"
          mode="create"
          submitLabel={t("save")}
          onSave={(nextConfig) => update(nextConfig)}
          saving={pending}
        />
      </Stack>
    </Paper>
  );
}
