import { Alert, Group, Select, Stack, Text } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { listPlaybackSourcesOptions } from "@/client/@tanstack/react-query.gen";
import type { PlaybackSourceItem } from "@/client/types.gen";
import { extractErrorMessage } from "@/lib/api-error";
import { apiFetch } from "@/lib/api-token";

const EMPTY_SOURCES: PlaybackSourceItem[] = [];

function sourceKey(item: PlaybackSourceItem): string {
  return `${item.source_id}:${item.media_file_id ?? ""}`;
}

function isDirectVideo(contentType: string): boolean {
  return contentType.split(";", 1)[0]?.trim().toLowerCase().startsWith("video/") ?? false;
}

async function readPlaybackDetail(href: string, fallback: string): Promise<string> {
  try {
    const response = await apiFetch(href, { headers: { Range: "bytes=0-0" } });
    if (response.ok || response.status === 206) {
      return fallback;
    }
    const body: unknown = await response.json().catch(() => null);
    if (
      typeof body === "object" &&
      body !== null &&
      "detail" in body &&
      typeof body.detail === "string" &&
      body.detail
    ) {
      return body.detail;
    }
    return fallback;
  } catch (error) {
    return extractErrorMessage(error, fallback);
  }
}

export function PlaybackPanel({ metadataId }: { metadataId: number }) {
  const { t } = useTranslation("metadata");
  const query = useQuery({
    ...listPlaybackSourcesOptions({ query: { metadata_id: metadataId } }),
    enabled: metadataId > 0,
  });
  const items = query.data?.items ?? EMPTY_SOURCES;
  const [selectedKey, setSelectedKey] = useState<string>("");
  const [error, setError] = useState<{ href: string; message: string } | null>(null);

  const selected = items.find((item) => sourceKey(item) === selectedKey) ?? items[0];
  if (items.length === 0 || selected == null) {
    return null;
  }

  const canPlay = isDirectVideo(selected.content_type);
  const shownError = error?.href === selected.href ? error.message : null;

  return (
    <Stack gap="xs">
      <Group justify="space-between" align="flex-end" wrap="wrap">
        <Text size="sm" fw={600}>
          {t("detail.playback")}
        </Text>
        {items.length > 1 ? (
          <Select
            size="xs"
            w={220}
            value={sourceKey(selected)}
            data={items.map((item) => ({ value: sourceKey(item), label: item.name }))}
            onChange={(value) => {
              if (value == null) return;
              setSelectedKey(value);
            }}
            allowDeselect={false}
          />
        ) : (
          <Text size="xs" c="dimmed">
            {selected.name}
          </Text>
        )}
      </Group>
      {shownError ? (
        <Alert color="red" variant="light">
          {shownError}
        </Alert>
      ) : null}
      {canPlay ? (
        <video
          key={selected.href}
          src={selected.href}
          controls
          playsInline
          preload="metadata"
          style={{ width: "100%", maxHeight: 480, background: "#000", borderRadius: 8 }}
          onError={() => {
            void readPlaybackDetail(selected.href, t("detail.playbackFailed")).then((message) =>
              setError({ href: selected.href, message }),
            );
          }}
        />
      ) : (
        <Text size="sm" c="dimmed">
          {t("detail.playbackUnsupported")}
        </Text>
      )}
    </Stack>
  );
}
