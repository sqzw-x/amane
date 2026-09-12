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

// 探测超时、上游失败等故障由非空 detail 说明; 为空表示条目没有内容或来源已停用, 不属于故障.
function failureReason(item: PlaybackSourceItem): string | null {
  const detail = item.detail;
  return detail == null || detail === "" ? null : detail;
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

  // 列表含不可用项: 无显式选择时选中首个可用项; 全部不可用时选中首个给出故障原因的项, 用于展示不可用原因.
  const failed = items.find((item) => failureReason(item) != null);
  const selected =
    items.find((item) => sourceKey(item) === selectedKey) ??
    items.find((item) => item.available) ??
    failed ??
    items[0];
  // 全部不可用且没有故障原因, 表示条目没有内容, 此时不渲染区块, 与没有可播源时一致.
  if (selected == null || (!items.some((item) => item.available) && failed == null)) {
    return null;
  }

  const kind = mediaKind(selected.content_type);
  const shownError = error?.href === selected.href ? error.message : null;
  // 不可用项不渲染播放器, 其 href 上的失败记录来自该源此前仍可用的状态, 故探测原因优先.
  const notice = selected.available
    ? shownError
    : (failureReason(selected) ?? t("detail.playbackUnavailable"));

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
            data={items.map((item) => ({
              value: sourceKey(item),
              label: item.available
                ? item.name
                : t("detail.playbackUnavailableOption", { name: item.name }),
              disabled: !item.available,
            }))}
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
      {notice ? (
        <Alert color="red" variant="light">
          {notice}
        </Alert>
      ) : null}
      {selected.available ? (
        kind === "other" ? (
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
        )
      ) : null}
    </Stack>
  );
}
