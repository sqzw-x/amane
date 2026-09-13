import type { ErrorData } from "hls.js";
import "media-chrome/lang/zh-CN.js";
import {
  MediaControlBar,
  MediaController,
  MediaFullscreenButton,
  MediaLoadingIndicator,
  MediaMuteButton,
  MediaPlayButton,
  MediaTimeDisplay,
  MediaTimeRange,
  MediaVolumeRange,
} from "media-chrome/react";
import {
  MediaCaptionsMenu,
  MediaCaptionsMenuButton,
  MediaPlaybackRateMenu,
  MediaPlaybackRateMenuButton,
} from "media-chrome/react/menu";
import { setLanguage } from "media-chrome/utils/i18n.js";
import { useCallback, useEffect, useRef, type KeyboardEvent, type RefObject } from "react";

import type { PlaybackSubtitleItem } from "@/client/types.gen";
import { useLatestRef } from "@/hooks/use-latest-ref";
import i18n from "@/i18n";

import classes from "./playback-player.module.css";

const HLS_TYPE = "application/vnd.apple.mpegurl";

/** 左右方向键单次跳转的秒数; 上下方向键调音量、空格暂停、j/l 跳 10 秒由 media-chrome 处理. */
const SEEK_STEP_SECONDS = 5;
/** 按住方向键多久之后进入加速 (毫秒). */
const HOLD_SEEK_DELAY_MS = 400;
/** 长按期间使用的播放倍速. */
const HOLD_SEEK_RATE = 2;
/** 倍速菜单的档位. */
const PLAYBACK_RATES = [0.5, 0.75, 1, 1.25, 1.5, 2];
/**
 * 关掉 media-chrome 自带的左右方向键.
 * 它在松开按键时才跳转且固定 10 秒, 与这里的「按下即跳 5 秒, 按住加速」冲突.
 */
const ARROW_HOTKEYS_OFF = "noarrowleft noarrowright";

// media-chrome 的控制条文案来自其自带语言包, 语言与 i18next 保持一致.
// 该模块随播放器一起懒加载, 因此界面语言切换后需要重新挂载播放器才会生效.
setLanguage(i18n.resolvedLanguage ?? i18n.language);

/** hls.js 致命错误中构成提示文案的字段, 后端没有给出 detail 时使用. */
export type HlsFailure = {
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

function nativeHlsSupported(): boolean {
  if (typeof document === "undefined") {
    return false;
  }
  return document.createElement("video").canPlayType(HLS_TYPE) !== "";
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

/**
 * 方向键跳转与长按加速.
 *
 * 按下即跳 `SEEK_STEP_SECONDS` 秒; 按住超过 `HOLD_SEEK_DELAY_MS` 之后把播放倍速提到
 * `HOLD_SEEK_RATE`, 倍速按钮会同步显示该值. 松开按键、窗口失焦、切换标签页时恢复用户
 * 设定的倍速 — 后两种时机用于兜住丢失的 keyup.
 *
 * 不可寻址的流 (列表给出 `seekable` 为假) 不接管方向键, 避免出现能按但跳不动的操作.
 */
function useArrowSeek(videoRef: RefObject<HTMLVideoElement | null>, seekable: boolean) {
  const holdTimerRef = useRef<number | null>(null);
  const savedRateRef = useRef<number | null>(null);

  const releaseHold = useCallback(() => {
    if (holdTimerRef.current != null) {
      window.clearTimeout(holdTimerRef.current);
      holdTimerRef.current = null;
    }
    const video = videoRef.current;
    if (video != null && savedRateRef.current != null) {
      video.playbackRate = savedRateRef.current;
      savedRateRef.current = null;
    }
  }, [videoRef]);

  useEffect(() => {
    window.addEventListener("blur", releaseHold);
    document.addEventListener("visibilitychange", releaseHold);
    return () => {
      window.removeEventListener("blur", releaseHold);
      document.removeEventListener("visibilitychange", releaseHold);
      releaseHold();
    };
  }, [releaseHold]);

  const onKeyDown = useCallback(
    (event: KeyboardEvent<HTMLElement>) => {
      if (!seekable || (event.key !== "ArrowLeft" && event.key !== "ArrowRight")) {
        return;
      }
      event.preventDefault();
      if (event.repeat) {
        return;
      }
      const video = videoRef.current;
      if (video == null) {
        return;
      }
      const offset = event.key === "ArrowRight" ? SEEK_STEP_SECONDS : -SEEK_STEP_SECONDS;
      video.currentTime = Math.max(video.currentTime + offset, 0);
      holdTimerRef.current = window.setTimeout(() => {
        holdTimerRef.current = null;
        const held = videoRef.current;
        if (held == null) {
          return;
        }
        savedRateRef.current = held.playbackRate;
        held.playbackRate = Math.max(held.playbackRate, HOLD_SEEK_RATE);
      }, HOLD_SEEK_DELAY_MS);
    },
    [seekable, videoRef],
  );

  const onKeyUp = useCallback(
    (event: KeyboardEvent<HTMLElement>) => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") {
        return;
      }
      releaseHold();
    },
    [releaseHold],
  );

  return { onKeyDown, onKeyUp };
}

export type PlaybackPlayerProps = {
  href: string;
  kind: "video" | "hls";
  seekable: boolean;
  tracks: PlaybackSubtitleItem[];
  onFailed: (failure: HlsFailure | null) => void;
};

export function PlaybackPlayer({ href, kind, seekable, tracks, onFailed }: PlaybackPlayerProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const onFailedRef = useLatestRef(onFailed);
  const useNativeSrc = kind === "video" || nativeHlsSupported();
  const { onKeyDown, onKeyUp } = useArrowSeek(videoRef, seekable);

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
    <MediaController
      className={classes.controller}
      hotkeys={ARROW_HOTKEYS_OFF}
      onKeyDown={onKeyDown}
      onKeyUp={onKeyUp}
    >
      <video
        slot="media"
        ref={videoRef}
        src={useNativeSrc ? href : undefined}
        playsInline
        preload="metadata"
        // 原生播放路径没有 hls.js 错误数据, 失败原因由探测结果说明.
        onError={() => onFailedRef.current(null)}
      >
        <SubtitleTracks tracks={tracks} />
      </video>
      <MediaLoadingIndicator slot="centered-chrome" />
      <MediaControlBar>
        <MediaPlayButton />
        {seekable ? <MediaTimeRange /> : null}
        <MediaTimeDisplay showDuration />
        <MediaMuteButton />
        <MediaVolumeRange />
        <MediaPlaybackRateMenuButton />
        {tracks.length > 0 ? <MediaCaptionsMenuButton /> : null}
        <MediaFullscreenButton />
        {/* 菜单必须放在控制条内: 控制条把带 role="menu" 的子元素绝对定位, 菜单才不会顶起控制条. */}
        <MediaPlaybackRateMenu rates={PLAYBACK_RATES} hidden />
        {tracks.length > 0 ? <MediaCaptionsMenu hidden /> : null}
      </MediaControlBar>
    </MediaController>
  );
}
