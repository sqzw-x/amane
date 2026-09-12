import { Alert, Group, Select, Stack, Text } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { listPlaybackSourcesOptions } from "@/client/@tanstack/react-query.gen";
import type { PlaybackSourceItem, PlaybackSubtitleItem } from "@/client/types.gen";
import { useLatestRef } from "@/hooks/use-latest-ref";
import { extractErrorMessage } from "@/lib/api-error";
import { apiFetch } from "@/lib/api-token";

const EMPTY_SOURCES: PlaybackSourceItem[] = [];
const HLS_TYPE = "application/vnd.apple.mpegurl";

function sourceKey(item: PlaybackSourceItem): string {
  return `${item.source_id}:${item.media_file_id ?? ""}`;
}

function mediaKind(contentType: string): "video" | "hls" | "other" {
  const type = contentType.split(";", 1)[0]?.trim().toLowerCase() ?? "";
  if (type === HLS_TYPE || type === "application/x-mpegurl" || type.includes("mpegurl")) {
    return "hls";
  }
  if (type.startsWith("video/")) {
    return "video";
  }
  return "other";
}

function nativeHlsSupported(): boolean {
  if (typeof document === "undefined") {
    return false;
  }
  return document.createElement("video").canPlayType(HLS_TYPE) !== "";
}

async function readPlaybackDetail(
  href: string,
  fallback: string,
  options: { ranged: boolean },
): Promise<string> {
  try {
    const response = await apiFetch(
      href,
      options.ranged ? { headers: { Range: "bytes=0-0" } } : undefined,
    );
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

function SubtitleTracks({ tracks }: { tracks: PlaybackSubtitleItem[] }) {
  return tracks.map((track, index) => (
    <track
      key={track.id}
      kind="subtitles"
      src={track.href}
      label={track.label}
      srcLang={track.language ?? undefined}
      default={index === 0}
    />
  ));
}

function PlaybackVideo({
  href,
  kind,
  tracks,
  onFailed,
}: {
  href: string;
  kind: "video" | "hls";
  tracks: PlaybackSubtitleItem[];
  onFailed: () => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const onFailedRef = useLatestRef(onFailed);
  const useNativeSrc = kind === "video" || nativeHlsSupported();

  useEffect(() => {
    if (useNativeSrc) {
      return;
    }
    const video = videoRef.current;
    if (video == null) {
      return;
    }
    let cancelled = false;
    let destroy: (() => void) | undefined;
    void import("hls.js").then((module) => {
      if (cancelled || videoRef.current == null) {
        return;
      }
      const Hls = module.default;
      if (!Hls.isSupported()) {
        onFailedRef.current();
        return;
      }
      const hls = new Hls({
        xhrSetup(xhr) {
          xhr.withCredentials = true;
        },
      });
      if (cancelled) {
        hls.destroy();
        return;
      }
      hls.loadSource(href);
      hls.attachMedia(video);
      hls.on(Hls.Events.ERROR, (_event, data) => {
        if (data.fatal) {
          onFailedRef.current();
        }
      });
      destroy = () => {
        hls.destroy();
      };
    });
    return () => {
      cancelled = true;
      destroy?.();
    };
  }, [href, useNativeSrc, onFailedRef]);

  return (
    <video
      ref={videoRef}
      src={useNativeSrc ? href : undefined}
      controls
      playsInline
      preload="metadata"
      crossOrigin="use-credentials"
      style={{ width: "100%", maxHeight: 480, background: "#000", borderRadius: 8 }}
      onError={onFailed}
    >
      <SubtitleTracks tracks={tracks} />
    </video>
  );
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

  const kind = mediaKind(selected.content_type);
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
      {kind === "other" ? (
        <Text size="sm" c="dimmed">
          {t("detail.playbackUnsupported")}
        </Text>
      ) : (
        <PlaybackVideo
          key={selected.href}
          href={selected.href}
          kind={kind}
          tracks={selected.subtitles ?? []}
          onFailed={() => {
            void readPlaybackDetail(selected.href, t("detail.playbackFailed"), {
              ranged: kind === "video",
            }).then((message) => setError({ href: selected.href, message }));
          }}
        />
      )}
    </Stack>
  );
}
