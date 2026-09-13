import { Alert, CheckIcon, Group, Loader, Select, Stack, Text } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import type { ErrorData } from "hls.js";
import type { TFunction } from "i18next";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  listPlaybackSourcesOptions,
  listPlaybackStreamsOptions,
} from "@/client/@tanstack/react-query.gen";
import type {
  PlaybackSourceOption,
  PlaybackStreamItem,
  PlaybackSubtitleItem,
} from "@/client/types.gen";
import { useLatestRef } from "@/hooks/use-latest-ref";
import { extractErrorMessage } from "@/lib/api-error";
import { apiFetch } from "@/lib/api-token";

const EMPTY_SOURCES: PlaybackSourceOption[] = [];
const EMPTY_STREAMS: PlaybackStreamItem[] = [];
const HLS_TYPE = "application/vnd.apple.mpegurl";

// 流的 key 在来源内唯一; 来源整个不可用的那一行没有 key, 用空串占位, 此时选择器只有这一行, 不渲染.
function streamKey(item: PlaybackStreamItem): string {
  return item.key ?? "";
}

// 探测超时、上游失败等故障由非空 detail 说明; 为空表示条目没有内容或来源已停用, 不属于故障.
function failureReason(item: PlaybackStreamItem): string | null {
  const detail = item.detail;
  return detail == null || detail === "" ? null : detail;
}

// 无显式选择时选中第一条可用流; 全部不可用时选中第一条带原因的流, 用于展示不可用原因.
// 不可用项同样可以选中: 选中后不渲染播放器, 未在标签里说明的原因显示在下方的提示里.
function pickStream(
  items: PlaybackStreamItem[],
  pickedKey: string | null,
): PlaybackStreamItem | undefined {
  const explicit = pickedKey == null ? undefined : items.find((item) => item.key === pickedKey);
  return (
    explicit ??
    items.find((item) => item.available) ??
    items.find((item) => failureReason(item) != null) ??
    items[0]
  );
}

// 选项名可能很长 (来源名 · 文件名), 区分不同流的那一段恰在末尾: 截断处用原生提示补全名.
// 自定义选项内容会替掉默认渲染, 选中项的勾必须自己画回来.
function NameSelect({
  value,
  data,
  width,
  onChange,
}: {
  value: string;
  data: Array<{ value: string; label: string }>;
  width: number;
  onChange: (value: string) => void;
}) {
  return (
    <Select
      size="xs"
      w={width}
      value={value}
      data={data}
      onChange={(next) => {
        if (next != null) {
          onChange(next);
        }
      }}
      allowDeselect={false}
      renderOption={({ option, checked }) => (
        <Group gap={6} wrap="nowrap">
          {checked ? <CheckIcon size={12} /> : null}
          <span title={option.label}>{option.label}</span>
        </Group>
      )}
    />
  );
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

// hls.js 致命错误中构成提示文案的字段, 后端没有给出 detail 时使用.
type HlsFailure = {
  details: string;
  status: number | null;
  target: string;
};

// 失败地址取自分片或错误数据本身; 两者都缺失时使用播放源地址, 该地址仍属于当前来源.
function hlsFailure(data: ErrorData, href: string): HlsFailure {
  return {
    details: data.details,
    status: data.response?.code ?? null,
    target: data.frag?.url ?? data.url ?? href,
  };
}

function hlsFailureMessage(failure: HlsFailure, t: TFunction<"metadata">): string {
  return failure.status == null
    ? t("detail.playbackFailedHls", { details: failure.details, url: failure.target })
    : t("detail.playbackFailedHlsWithStatus", {
        details: failure.details,
        status: failure.status,
        url: failure.target,
      });
}

// 后端 detail 缺失时的提示来源: hls.js 错误原因优先, 其次为探测响应的 HTTP 状态码, 最后为基础文案.
type PlaybackFallback = {
  plain: string;
  withStatus: (status: number) => string;
  hlsReason: string | null;
};

function fallbackMessage(fallback: PlaybackFallback, status: number | null): string {
  if (fallback.hlsReason != null) {
    return fallback.hlsReason;
  }
  return status == null ? fallback.plain : fallback.withStatus(status);
}

// 提示的取值顺序: 后端 detail 优先, 后端没有给出 detail 时采用 fallback 说明的原因或状态码.
async function readPlaybackDetail(
  href: string,
  fallback: PlaybackFallback,
  options: { ranged: boolean },
): Promise<string> {
  try {
    const response = await apiFetch(
      href,
      options.ranged ? { headers: { Range: "bytes=0-0" } } : undefined,
    );
    // 2xx 表示探测本身成功, 该响应的状态码不含失败信息.
    if (response.ok || response.status === 206) {
      return fallbackMessage(fallback, null);
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
    return fallbackMessage(fallback, response.status);
  } catch (error) {
    return fallback.hlsReason ?? extractErrorMessage(error, fallback.plain);
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
  onFailed: (failure: HlsFailure | null) => void;
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
        onFailedRef.current(null);
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
          onFailedRef.current(hlsFailure(data, href));
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
      // 原生播放路径没有 hls.js 错误数据, 失败原因由探测结果说明.
      onError={() => onFailed(null)}
    >
      <SubtitleTracks tracks={tracks} />
    </video>
  );
}

export function PlaybackPanel({ metadataId }: { metadataId: number }) {
  const { t } = useTranslation("metadata");
  // 来源列表不调用插件, 因此打开面板就能渲染; 探测推迟到用户切到某个来源时.
  const sourcesQuery = useQuery({
    ...listPlaybackSourcesOptions(),
    enabled: metadataId > 0,
  });
  const sources = sourcesQuery.data?.items ?? EMPTY_SOURCES;
  const [pickedSourceId, setPickedSourceId] = useState<string | null>(null);
  const [pickedStream, setPickedStream] = useState<{ sourceId: string; key: string } | null>(null);
  const [error, setError] = useState<{ href: string; message: string } | null>(null);

  // 来源列表变化后原选择可能不存在 (来源被停用或卸载), 回落到第一个来源.
  const source = sources.find((item) => item.source_id === pickedSourceId) ?? sources[0];
  // 当前来源的流在挂载时与切换来源时加载; 没有来源时查询不启用, 不发请求.
  const streamsQuery = useQuery({
    ...listPlaybackStreamsOptions({
      path: { source_id: source?.source_id ?? "", metadata_id: metadataId },
    }),
    enabled: metadataId > 0 && source != null,
  });
  const streams = streamsQuery.data?.items ?? EMPTY_STREAMS;
  // 流的选择连同所属来源一起记: 切换来源后 key 不属于新来源, 选择回到该来源的默认值, 不会指向另一个来源的流.
  const pickedKey =
    pickedStream != null && pickedStream.sourceId === source?.source_id ? pickedStream.key : null;
  const selected = pickStream(streams, pickedKey);

  const sourceError = sourcesQuery.isError
    ? extractErrorMessage(sourcesQuery.error, t("detail.playbackSourcesFailed"))
    : null;
  const title = (
    <Text size="sm" fw={600}>
      {t("detail.playback")}
    </Text>
  );
  if (sourceError != null) {
    return (
      <Stack gap="xs">
        {title}
        <Alert color="red" variant="light">
          {sourceError}
        </Alert>
      </Stack>
    );
  }
  // 来源列表为空表示没有已启用的播放源, 此时无从选择, 整块不渲染.
  if (source == null) {
    return null;
  }

  const streamError = streamsQuery.isError
    ? extractErrorMessage(streamsQuery.error, t("detail.playbackStreamsFailed"))
    : null;
  const kind = selected == null ? "other" : mediaKind(selected.content_type);
  const shownError = selected != null && error?.href === selected.href ? error.message : null;
  // 不可用项不渲染播放器, 其 href 上的失败记录来自该源此前仍可用的状态, 故探测原因优先.
  const notice =
    streamError ??
    (selected == null
      ? null
      : selected.available
        ? shownError
        : (failureReason(selected) ?? t("detail.playbackUnavailable")));

  // 当前来源的流正在探测: 用加载态占位, 不新增文案.
  const streamPicker = streamsQuery.isPending ? (
    <Loader size="xs" />
  ) : streams.length > 1 && selected != null ? (
    <NameSelect
      value={streamKey(selected)}
      data={streams.map((item) => ({
        value: streamKey(item),
        label: item.available
          ? item.name
          : t("detail.playbackUnavailableOption", { name: item.name }),
      }))}
      width={240}
      onChange={(key) => setPickedStream({ sourceId: source.source_id, key })}
    />
  ) : null;

  return (
    <Stack gap="xs">
      <Group justify="space-between" align="flex-end" wrap="wrap">
        {title}
        <Group gap="xs" wrap="wrap" align="center">
          {sources.length > 1 ? (
            <NameSelect
              value={source.source_id}
              data={sources.map((item) => ({ value: item.source_id, label: item.name }))}
              width={160}
              onChange={setPickedSourceId}
            />
          ) : (
            <Text size="xs" c="dimmed">
              {source.name}
            </Text>
          )}
          {streamPicker}
        </Group>
      </Group>
      {notice ? (
        <Alert color="red" variant="light">
          {notice}
        </Alert>
      ) : null}
      {selected != null && selected.available ? (
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
            onFailed={(failure) => {
              void readPlaybackDetail(
                selected.href,
                {
                  plain: t("detail.playbackFailed"),
                  withStatus: (status) => t("detail.playbackFailedWithStatus", { status }),
                  hlsReason: failure == null ? null : hlsFailureMessage(failure, t),
                },
                { ranged: kind === "video" },
              ).then((message) => setError({ href: selected.href, message }));
            }}
          />
        )
      ) : null}
    </Stack>
  );
}
