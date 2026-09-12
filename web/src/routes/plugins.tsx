import {
  Alert,
  Anchor,
  Button,
  Center,
  FileButton,
  Group,
  Loader,
  Paper,
  Stack,
  Text,
  Title,
} from "@mantine/core";
import { IconAlertCircle, IconUpload } from "@tabler/icons-react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { HintedActionIcon } from "@/components/common/hinted-action-icon";
import { PathPicker } from "@/components/path-picker";
import { PluginCard } from "@/components/plugins/plugin-card";
import { useInstallPlugin, usePlugins, useReloadPlugins } from "@/hooks/use-plugins";

export const Route = createFileRoute("/plugins")({
  component: PluginsPage,
});

function PluginsPage() {
  const { t } = useTranslation("plugins");
  const query = usePlugins();
  const plugins = query.data?.items ?? [];
  const failures = query.data?.failures ?? [];
  const scrapePlugins = plugins.filter((plugin) => {
    const capabilities = plugin.descriptor.capabilities ?? ["film_metadata"];
    return capabilities.includes("film_metadata");
  });
  // 播放源分区按 playback 能力筛选: 同时声明影片元数据能力的插件在两个分区都列出.
  const playbackPlugins = plugins.filter((plugin) => {
    const capabilities = plugin.descriptor.capabilities ?? [];
    return capabilities.includes("playback");
  });

  return (
    <Stack gap="md">
      <div>
        <Title order={2}>{t("title")}</Title>
      </div>

      <PluginCatalogActions />

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
        <>
          <div>
            <Title order={4}>{t("scrapeSection")}</Title>
            <Text size="sm" c="dimmed" mt={4}>
              {t("routeHint")}{" "}
              <Link to="/settings" search={{ section: "scraping" }}>
                <Anchor component="span" size="sm">
                  {t("routeLink")}
                </Anchor>
              </Link>
            </Text>
          </div>
          {scrapePlugins.length === 0 ? <Text c="dimmed">{t("emptyScrape")}</Text> : null}
          {scrapePlugins.map((plugin) => (
            <PluginCard key={plugin.descriptor.id} plugin={plugin} />
          ))}
          <div>
            <Title order={4}>{t("playbackSection")}</Title>
            <Text size="sm" c="dimmed" mt={4}>
              {t("playbackHint")}
            </Text>
          </div>
          {playbackPlugins.length === 0 ? <Text c="dimmed">{t("emptyPlayback")}</Text> : null}
          {playbackPlugins.map((plugin) => (
            <PluginCard key={plugin.descriptor.id} plugin={plugin} />
          ))}
        </>
      ) : null}
    </Stack>
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
