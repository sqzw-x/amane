import { Badge, Card, Group, Stack, Switch, Text } from "@mantine/core";
import { IconSettings, IconTrash } from "@tabler/icons-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { PluginResponse } from "@/client/types.gen";
import { HintedActionIcon } from "@/components/common/hinted-action-icon";
import { PluginSettingsDialog } from "@/components/plugins/plugin-settings-dialog";
import { hasConfigurableFields, resolvePluginSchema } from "@/components/plugins/plugin-schema";
import { useUninstallPlugin, useUpdatePlugin } from "@/hooks/use-plugins";
import { confirm } from "@/lib/confirm";

/**
 * 一个插件一张卡片, 能力以标签铺开. 只有声明了可渲染字段的插件才出现设置入口 —
 * 空配置弹窗没有可操作内容.
 */
export function PluginCard({ plugin }: { plugin: PluginResponse }) {
  const { t } = useTranslation(["plugins", "common"]);
  const mutation = useUpdatePlugin();
  const uninstall = useUninstallPlugin();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const schema = resolvePluginSchema(plugin.config_schema);
  const config = plugin.config.config ?? {};
  const enabled = plugin.config.enabled ?? true;
  const pending = mutation.isPending || uninstall.isPending;
  const configurable = hasConfigurableFields(schema);

  const update = (
    nextConfig: Record<string, unknown>,
    options?: { enabled?: boolean; onSuccess?: () => void },
  ) => {
    mutation.mutate(
      {
        path: { plugin_id: plugin.descriptor.id },
        body: { enabled: options?.enabled ?? enabled, config: nextConfig },
      },
      { onSuccess: options?.onSuccess },
    );
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
    <Card withBorder radius="md" padding="md">
      <Stack gap="xs" h="100%">
        <Group justify="space-between" align="flex-start" gap="xs" wrap="nowrap">
          <Stack gap={2} style={{ minWidth: 0 }}>
            <Text fw={600} lineClamp={2}>
              {plugin.descriptor.name}
              {plugin.descriptor.version ? (
                <Text span size="xs" c="dimmed" ml={6}>
                  v{plugin.descriptor.version}
                </Text>
              ) : null}
            </Text>
            <Text size="xs" c="dimmed" ff="monospace" lineClamp={1}>
              {plugin.descriptor.id}
            </Text>
          </Stack>
          <Switch
            size="sm"
            checked={enabled}
            onChange={(event) => update(config, { enabled: event.currentTarget.checked })}
            disabled={pending}
            aria-label={t("enabled")}
          />
        </Group>

        <Group gap={4}>
          {(plugin.descriptor.capabilities ?? []).map((capability) => (
            <Badge key={capability} size="sm" variant="light" color="blue">
              {t(`capabilities.${capability}`, { defaultValue: capability })}
            </Badge>
          ))}
        </Group>

        <Group gap={4} justify="flex-end" mt="auto">
          {configurable ? (
            <HintedActionIcon
              variant="subtle"
              size="sm"
              onClick={() => setSettingsOpen(true)}
              label={t("settings")}
            >
              <IconSettings size={16} />
            </HintedActionIcon>
          ) : null}
          <HintedActionIcon
            variant="subtle"
            color="red"
            size="sm"
            onClick={() => void handleUninstall()}
            label={t("uninstall")}
            disabled={pending}
          >
            <IconTrash size={16} />
          </HintedActionIcon>
        </Group>
      </Stack>

      {/* 关闭时卸载: 表单状态与初值随列表数据一并重建, 再次打开看到的是保存后的配置. */}
      {settingsOpen ? (
        <PluginSettingsDialog
          plugin={plugin}
          schema={schema}
          opened={settingsOpen}
          onClose={() => setSettingsOpen(false)}
          onSave={(nextConfig) => update(nextConfig, { onSuccess: () => setSettingsOpen(false) })}
          saving={pending}
        />
      ) : null}
    </Card>
  );
}
