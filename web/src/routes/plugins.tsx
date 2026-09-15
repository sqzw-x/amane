import {
  Alert,
  Anchor,
  Button,
  Center,
  FileButton,
  Group,
  Loader,
  Paper,
  SimpleGrid,
  Stack,
  Text,
  Title,
} from "@mantine/core";
import { IconAlertCircle, IconUpload } from "@tabler/icons-react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { useCallback, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { PluginResponse } from "@/client/types.gen";
import { HintedActionIcon } from "@/components/common/hinted-action-icon";
import { PathPicker } from "@/components/path-picker";
import { PluginCard } from "@/components/plugins/plugin-card";
import { DraggableChips } from "@/components/schema-form/fields/draggable-chips";
import { useInstallPlugin, usePlugins, useReloadPlugins } from "@/hooks/use-plugins";
import { useNarrowViewport } from "@/hooks/use-narrow-viewport";
import { orderPlaybackSources } from "@/lib/media/source-order";
import { useUIStore } from "@/stores/ui";

export const Route = createFileRoute("/plugins")({
  component: PluginsPage,
});

/** 列数随容器宽度调整; 不按能力分区 — 同一插件可同时提供多项能力. */
const PLUGIN_GRID_COLS = { base: 1, xs: 2, md: 3, lg: 4 } as const;

/** 插件页信息量小于片库类页面, 宽屏下限制内容宽度并居中. */
const PAGE_MAX_WIDTH = 1120;

function PluginsPage() {
  const { t } = useTranslation("plugins");
  const query = usePlugins();
  const plugins = query.data?.items ?? [];
  const failures = query.data?.failures ?? [];

  return (
    <Stack gap="md" maw={PAGE_MAX_WIDTH} mx="auto" w="100%">
      <div>
        <Title order={2}>{t("title")}</Title>
        <Text size="sm" c="dimmed" mt={4}>
          {t("routeHint")}{" "}
          <Link to="/settings" search={{ section: "scraping" }}>
            <Anchor component="span" size="sm">
              {t("routeLink")}
            </Anchor>
          </Link>
        </Text>
      </div>

      <PluginCatalogActions />

      <PlaybackOrderSection plugins={plugins} />

      {query.isLoading ? (
        <Center py="xl">
          <Loader size="sm" />
        </Center>
      ) : null}
      {query.error ? (
        <Alert icon={<IconAlertCircle size={18} />} color="red" variant="light">
          {t("loadError")}
        </Alert>
      ) : null}
      {failures.map((failure) => (
        <Alert key={`${failure.name}:${failure.value}`} color="red" variant="light">
          <Text size="sm" fw={600}>
            {failure.name}
          </Text>
          <Text size="sm">{failure.error}</Text>
        </Alert>
      ))}
      {!query.isLoading && !query.error && plugins.length === 0 ? (
        <Text c="dimmed">{t("empty")}</Text>
      ) : null}
      {!query.isLoading && !query.error && plugins.length > 0 ? (
        <SimpleGrid cols={PLUGIN_GRID_COLS} spacing="md">
          {plugins.map((plugin) => (
            <PluginCard key={plugin.descriptor.id} plugin={plugin} />
          ))}
        </SimpleGrid>
      ) : null}
    </Stack>
  );
}

/**
 * 播放源的用户顺序, 决定详情页面板列出与默认探测的先后.
 *
 * 只列已启用的播放源, 与后端 `PlaybackFactory.playback_source_ids` 同判据, 因此这里排的正是详情页
 * 会列出的那些. 拖动结果写入 UI store (仅本浏览器); 刮削源与混合能力插件的刮削面都不参与, 卡片
 * 位置也不随这里的顺序变化.
 */
function PlaybackOrderSection({ plugins }: { plugins: PluginResponse[] }) {
  const { t } = useTranslation("plugins");
  const order = useUIStore((state) => state.playbackSourceOrder);
  const setOrder = useUIStore((state) => state.setPlaybackSourceOrder);
  // 触屏设备无法执行 HTML5 拖拽, 窄屏改由上移 / 下移按钮调整来源顺序.
  const narrow = useNarrowViewport();
  const playbackPlugins = plugins.filter(
    (plugin) =>
      (plugin.config.enabled ?? true) &&
      (plugin.descriptor.capabilities ?? []).includes("playback"),
  );

  const handleChange = useCallback(
    (reordered: PluginResponse[]) => {
      setOrder(reordered.map((plugin) => plugin.descriptor.id));
    },
    [setOrder],
  );

  if (playbackPlugins.length < 2) {
    return null;
  }

  return (
    <Paper withBorder p="md">
      <Stack gap="xs">
        <Text fw={600} size="sm">
          {t("playbackOrder")}
        </Text>
        <DraggableChips
          items={orderPlaybackSources(playbackPlugins, order, (plugin) => plugin.descriptor.id)}
          getKey={(plugin) => plugin.descriptor.id}
          getLabel={(plugin) => plugin.descriptor.name}
          onChange={handleChange}
          onMove={narrow ? handleChange : undefined}
        />
      </Stack>
    </Paper>
  );
}

function PluginCatalogActions() {
  const { t } = useTranslation("plugins");
  const resetRef = useRef<() => void>(null);
  const [path, setPath] = useState("");
  const install = useInstallPlugin();
  const reload = useReloadPlugins();
  const pending = install.isPending || reload.isPending;

  const submitPath = () => {
    const spec = path.trim();
    if (!spec) return;
    install.mutate(
      { body: { path: spec } },
      {
        onSuccess: () => setPath(""),
      },
    );
  };

  const submitZip = (file: File | null) => {
    resetRef.current?.();
    if (file == null) return;
    install.mutate({ body: { file } });
  };

  return (
    <Paper withBorder p="md">
      <Stack gap="sm">
        <PathPicker
          label={t("install")}
          value={path}
          onChange={setPath}
          pathType="mixed"
          placeholder={t("installPlaceholder")}
          disabled={pending}
        />
        <Text size="xs" c="dimmed">
          {t("installHint")}
        </Text>
        <Group gap="xs">
          <Button
            size="sm"
            onClick={submitPath}
            loading={install.isPending}
            disabled={pending || path.trim() === ""}
          >
            {t("install")}
          </Button>
          <FileButton resetRef={resetRef} onChange={submitZip} accept=".zip,application/zip">
            {(props) => (
              <HintedActionIcon
                {...props}
                variant="default"
                size="lg"
                label={t("pickZip")}
                disabled={pending}
              >
                <IconUpload size={16} />
              </HintedActionIcon>
            )}
          </FileButton>
          <Button
            size="sm"
            variant="default"
            onClick={() => reload.mutate({})}
            loading={reload.isPending}
            disabled={pending}
          >
            {t("reload")}
          </Button>
        </Group>
      </Stack>
    </Paper>
  );
}
