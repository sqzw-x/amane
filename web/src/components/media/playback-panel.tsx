import {
  Alert,
  AspectRatio,
  Box,
  Button,
  Card,
  Center,
  CheckIcon,
  Group,
  Menu,
  Skeleton,
  Stack,
  Text,
} from "@mantine/core";
import { IconChevronDown } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import type { TFunction } from "i18next";
import { lazy, Suspense, useEffect, useRef, useState, type ReactNode, type Ref } from "react";
import { useTranslation } from "react-i18next";

import {
  listPlaybackSourcesOptions,
  listPlaybackStreamsOptions,
} from "@/client/@tanstack/react-query.gen";
import type { PlaybackSourceOption, PlaybackStreamItem } from "@/client/types.gen";
import { EnumToggle } from "@/components/common/enum-toggle";
import { APP_SHELL_HEADER_HEIGHT } from "@/components/layout/app-shell-metrics";
import type { HlsFailure, SeekRequest } from "@/components/media/playback-player";
import { extractErrorMessage } from "@/lib/api-error";
import { apiFetch } from "@/lib/api-token";
import { orderPlaybackSources } from "@/lib/media/source-order";
import { useUIStore } from "@/stores/ui";

// 播放器只在选中可播的流之后才需要, 单独成块懒加载, 与 hls.js 一样不进入主包.
const PlaybackPlayer = lazy(() =>
  import("@/components/media/playback-player").then((module) => ({
    default: module.PlaybackPlayer,
  })),
);

const EMPTY_SOURCES: PlaybackSourceOption[] = [];
const EMPTY_STREAMS: PlaybackStreamItem[] = [];
/**
 * 视频宽度上限.
 * 只限制高度会让替换元素的盒子比例宽于素材比例, `object-fit: contain` 于是在左右留黑边;
 * 这里改为限制宽度, 让盒子比例由 16:9 决定, 并把播放器压在一屏之内.
 */
const PLAYER_MAX_WIDTH = "min(100%, calc(var(--amane-vh) * 0.72 * 16 / 9))";
/** 短枚举平铺展示; 超过该数量时换行难以阅读, 回退为下拉菜单. */
const MAX_TOGGLE_ITEMS = 4;

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

function mediaKind(contentType: string): "video" | "hls" | "other" {
  const type = contentType.split(";", 1)[0]?.trim().toLowerCase() ?? "";
  if (type.includes("mpegurl")) {
    return "hls";
  }
  if (type.startsWith("video/")) {
    return "video";
  }
  return "other";
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

type PickerOption = {
  value: string;
  label: string;
};

// 选项过多时用下拉菜单: 标签是主机拼好的完整名称, 菜单给长名称留出宽度.
function PickerMenu({
  options,
  value,
  onChange,
}: {
  options: PickerOption[];
  value: string;
  onChange: (value: string) => void;
}) {
  const current = options.find((option) => option.value === value);
  return (
    <Menu shadow="md" position="bottom-end" withinPortal>
      <Menu.Target>
        <Button
          size="compact-sm"
          variant="default"
          maw={260}
          rightSection={<IconChevronDown size={14} />}
        >
          <Text size="sm" truncate="end" title={current?.label}>
            {current?.label ?? ""}
          </Text>
        </Button>
      </Menu.Target>
      <Menu.Dropdown maw={360}>
        {options.map((option) => (
          <Menu.Item
            key={option.value}
            leftSection={option.value === value ? <CheckIcon size={14} /> : <Box w={14} />}
            onClick={() => {
              if (option.value !== value) {
                onChange(option.value);
              }
            }}
          >
            <Text size="sm">{option.label}</Text>
          </Menu.Item>
        ))}
      </Menu.Dropdown>
    </Menu>
  );
}

/**
 * 播放器的固定比例外框, 也是出错时的播放窗口.
 * 切换来源时流列表要按新的 key 重新探测, 期间用同一外框占位: 否则播放器一收一放会让页面高度骤变,
 * 已经滚下去的位置会被浏览器夹回顶部. 底色与播放器一致, 提示直接落在窗口里.
 * 顶端固定头部会盖住对齐到视口上沿的元素, 因此外框带走头部高度的滚动边距.
 */
function PlayerFrame({
  children,
  frameRef,
}: {
  children: ReactNode;
  frameRef?: Ref<HTMLDivElement>;
}) {
  return (
    <Box
      ref={frameRef}
      w="100%"
      maw={PLAYER_MAX_WIDTH}
      mx="auto"
      bg="#000"
      style={{ scrollMarginTop: APP_SHELL_HEADER_HEIGHT }}
    >
      <AspectRatio ratio={16 / 9}>{children}</AspectRatio>
    </Box>
  );
}

/** 播放窗口内的提示: 探测失败、播放失败与不支持的媒体类型都在窗口里居中显示. */
function PlayerMessage({
  tone = "error",
  children,
}: {
  tone?: "error" | "neutral";
  children: ReactNode;
}) {
  return (
    <Center h="100%" w="100%" p="md">
      <Alert color={tone === "error" ? "red" : "gray"} variant="light" maw={520}>
        {children}
      </Alert>
    </Center>
  );
}

/** 一级选择: 少量选项平铺 (EnumToggle), 过多则回退为下拉菜单. */ function LevelPicker({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: PickerOption[];
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <Box role="group" aria-label={label}>
      {options.length > MAX_TOGGLE_ITEMS ? (
        <PickerMenu options={options} value={value} onChange={onChange} />
      ) : (
        <EnumToggle
          options={options.map((option) => option.value)}
          getLabel={(option) => options.find((item) => item.value === option)?.label ?? option}
          value={value}
          onChange={onChange}
        />
      )}
    </Box>
  );
}

export function PlaybackPanel({
  metadataId,
  seekRequest,
  onSeekHandled,
  onCanSeekChange,
}: {
  metadataId: number;
  /** 评论时间戳的跳转请求; 为空表示没有待处理的跳转. */
  seekRequest: SeekRequest | null;
  /** 播放器已经按请求定位之后回调, 由调用方清掉请求. */
  onSeekHandled: () => void;
  /** 当前能否跳转, 供评论里的时间戳决定是否可点. */
  onCanSeekChange: (canSeek: boolean) => void;
}) {
  const { t } = useTranslation("metadata");
  // 来源列表不调用插件, 因此打开面板就能渲染; 探测推迟到用户切到某个来源时.
  const sourcesQuery = useQuery({
    ...listPlaybackSourcesOptions(),
    enabled: metadataId > 0,
  });
  // 后端按来源 ID 返回; 用户顺序在插件页维护, 位置 0 即本面板默认探测的来源.
  const sourceOrder = useUIStore((state) => state.playbackSourceOrder);
  const sources = orderPlaybackSources(
    sourcesQuery.data?.items ?? EMPTY_SOURCES,
    sourceOrder,
    (item) => item.source_id,
  );
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

  // 评论里的时间戳能否跳转, 与这里能否渲染播放器是同一个条件.
  const canSeek =
    notice == null &&
    selected != null &&
    selected.available &&
    selected.seekable &&
    kind !== "other";

  const playerFrameRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    onCanSeekChange(canSeek);
  }, [canSeek, onCanSeekChange]);

  // 评论就在播放窗口下方, 点时间戳通常不必移动页面: 窗口还有一部分在视野里就不滚动, 完全滑出视野时才
  // 把它带回屏幕, 并对齐上沿而不是居中 —— 居中会把刚点的那条评论一起挪出视野.
  // 依赖流的选择: 流列表到位之前播放窗口尚未渲染, 带 `t` 的地址要在这里补一次.
  useEffect(() => {
    if (seekRequest == null || selected == null) {
      return;
    }
    const frame = playerFrameRef.current;
    if (frame == null) {
      return;
    }
    const rect = frame.getBoundingClientRect();
    if (rect.bottom > 0 && rect.top < window.innerHeight) {
      return;
    }
    frame.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [seekRequest, selected]);

  const title = (
    <Text size="sm" fw={600}>
      {t("detail.playback")}
    </Text>
  );
  if (sourceError != null) {
    return (
      <Card withBorder radius="md" p="md">
        <Stack gap="xs">
          {title}
          <Alert color="red" variant="light">
            {sourceError}
          </Alert>
        </Stack>
      </Card>
    );
  }
  // 来源列表为空表示没有已启用的播放源, 此时无从选择, 整块不渲染.
  if (source == null) {
    return null;
  }

  // 当前来源的流正在探测: 用与控件等宽的占位, 避免控件出现时行高跳动.
  const streamPicker = streamsQuery.isPending ? (
    <Skeleton height={30} width={200} radius="sm" />
  ) : streams.length > 1 && selected != null ? (
    <LevelPicker
      label={t("detail.playbackStreamLabel")}
      options={streams.map((item) => ({
        value: streamKey(item),
        label: item.available
          ? item.name
          : t("detail.playbackUnavailableOption", { name: item.name }),
      }))}
      value={streamKey(selected)}
      onChange={(key) => setPickedStream({ sourceId: source.source_id, key })}
    />
  ) : null;

  return (
    <Card withBorder radius="md" p="md">
      <Stack gap="xs">
        <Group justify="space-between" align="flex-end" wrap="wrap">
          {title}
          <Group gap="xs" wrap="wrap" align="center">
            {sources.length > 1 ? (
              <LevelPicker
                label={t("detail.playbackSourceLabel")}
                options={sources.map((item) => ({ value: item.source_id, label: item.name }))}
                value={source.source_id}
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
        {/* 播放窗口常驻: 探测中、出错、类型不支持都在同一外框内呈现, 页面高度不随状态突变. */}
        <PlayerFrame frameRef={playerFrameRef}>
          {streamsQuery.isPending ? (
            <Skeleton height="100%" />
          ) : notice != null ? (
            <PlayerMessage>{notice}</PlayerMessage>
          ) : selected == null ? (
            <PlayerMessage>{t("detail.playbackUnavailable")}</PlayerMessage>
          ) : kind === "other" ? (
            <PlayerMessage tone="neutral">{t("detail.playbackUnsupported")}</PlayerMessage>
          ) : (
            <Suspense fallback={<Skeleton height="100%" />}>
              <PlaybackPlayer
                key={selected.href}
                href={selected.href}
                kind={kind}
                seekable={selected.seekable}
                tracks={selected.subtitles ?? []}
                seekRequest={seekRequest}
                onSeekHandled={onSeekHandled}
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
            </Suspense>
          )}
        </PlayerFrame>
      </Stack>
    </Card>
  );
}
