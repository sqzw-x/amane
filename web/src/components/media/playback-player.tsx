import { Slider } from "@mantine/core";
import type { ErrorData } from "hls.js";
import "media-chrome/lang/zh-CN.js";
import type { MediaController as MediaControllerElement } from "media-chrome";
import type {
  MediaChromeMenu as MediaChromeMenuElement,
  MediaPlaybackRateMenuButton as MediaPlaybackRateMenuButtonElement,
} from "media-chrome/menu";
import {
  MediaControlBar,
  MediaController,
  MediaFullscreenButton,
  MediaLoadingIndicator,
  MediaMuteButton,
  MediaPlayButton,
  MediaTimeDisplay,
  MediaTimeRange,
} from "media-chrome/react";
import {
  MediaCaptionsMenu,
  MediaCaptionsMenuButton,
  MediaChromeMenu,
  MediaChromeMenuItem,
  MediaPlaybackRateMenuButton,
} from "media-chrome/react/menu";
import { setLanguage } from "media-chrome/utils/i18n.js";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FocusEvent,
  type KeyboardEvent,
  type RefObject,
} from "react";
import { useTranslation } from "react-i18next";

import type { PlaybackSubtitleItem } from "@/client/types.gen";
import { useLatestRef } from "@/hooks/use-latest-ref";
import i18n from "@/i18n";

import classes from "./playback-player.module.css";

const HLS_TYPE = "application/vnd.apple.mpegurl";

/** 左右方向键单次跳转的秒数; 空格暂停、j/l 跳 10 秒由 media-chrome 处理. */
const SEEK_STEP_SECONDS = 5;
/** 上下方向键单次调整音量的幅度, 与音量条的步长一致. */
const VOLUME_STEP = 0.05;
/** 音量提示在最后一次调整后保留的毫秒数. */
const VOLUME_INDICATOR_MS = 900;
/** 按住方向键多久之后进入加速 (毫秒). */
const HOLD_SEEK_DELAY_MS = 400;
/** 长按期间使用的播放倍速. */
const HOLD_SEEK_RATE = 2;
/**
 * 倍速档位, 由高到低.
 * 菜单按这里的顺序渲染, 因此最快的一档在最上面; media-chrome 自带的倍速菜单固定升序, 所以这里自己列条目.
 */
const PLAYBACK_RATES = [2, 1.5, 1.25, 1, 0.75, 0.5];
/** 指针离开倍速按钮后延迟收起菜单的毫秒数, 让指针有时间移到菜单上. */
const HOVER_CLOSE_DELAY_MS = 160;
/** 倍速菜单的 id: 菜单按钮经 `invoketarget` 找它, 不再依赖 media-chrome 自带的倍速菜单. */
const RATE_MENU_ID = "amane-playback-rate-menu";
/**
 * 关掉 media-chrome 自带的四个方向键.
 * 它在松开按键时才执行且只执行一次: 左右键因此与「按下即跳 5 秒, 按住加速」冲突, 上下键则无法按住连续调节音量.
 */
const ARROW_HOTKEYS_OFF = "noarrowleft noarrowright noarrowup noarrowdown";

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
 * 四个方向键.
 *
 * 左右: 按下即跳 `SEEK_STEP_SECONDS` 秒; 按住超过 `HOLD_SEEK_DELAY_MS` 之后把播放倍速提到
 * `HOLD_SEEK_RATE`, 倍速按钮会同步显示该值. 松开按键、窗口失焦、切换标签页时恢复用户
 * 设定的倍速 — 后两种时机用于兜住丢失的 keyup.
 *
 * 上下: 每次 keydown 调整一次音量, 按住时浏览器持续派发 keydown, 因此可以连续调节.
 *
 * 不可寻址的流 (列表给出 `seekable` 为假) 不接管左右键, 避免出现能按但跳不动的操作.
 */
function usePlayerKeys(
  videoRef: RefObject<HTMLVideoElement | null>,
  seekable: boolean,
  onVolumeStep: (delta: number) => void,
) {
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
      if (event.key === "ArrowUp" || event.key === "ArrowDown") {
        event.preventDefault();
        onVolumeStep(event.key === "ArrowUp" ? VOLUME_STEP : -VOLUME_STEP);
        return;
      }
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
    [onVolumeStep, seekable, videoRef],
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

/**
 * 拖动进度条或音量条期间关掉媒体手势层.
 *
 * 指针若松在画面上, 浏览器会把随后的 click 目标定为控制器 —— 按下与松开的最近公共祖先; 手势层按
 * 「目标在允许列表内且坐标落在播放器里」判定, 于是把这次拖动当成点击, 切换播放/暂停. 拖动期间设
 * `gesturesdisabled`, 手势层被隐藏、自身盒子为 0, 命中判定落空.
 *
 * 撤销必须晚于那次 click: 在捕获阶段就撤销的话, 手势层又变回有盒子, 判定照样成立. 先挂一次性的
 * 捕获监听等这次 click 过去, 再推到下一个任务里恢复; 指针在窗口外松开时不会有 click, 用兜底定时器收尾.
 */
const DRAG_GUARD_MAX_MS = 400;

function useDragGestureGuard(
  controllerRef: RefObject<MediaControllerElement | null>,
  onDragEnd: () => void,
) {
  const draggingRef = useRef(false);
  const restoreTimerRef = useRef<number | null>(null);
  const clickListenerRef = useRef<(() => void) | null>(null);

  const restore = useCallback(() => {
    if (clickListenerRef.current != null) {
      document.removeEventListener("click", clickListenerRef.current, true);
      clickListenerRef.current = null;
    }
    if (restoreTimerRef.current != null) {
      window.clearTimeout(restoreTimerRef.current);
      restoreTimerRef.current = null;
    }
    controllerRef.current?.removeAttribute("gesturesdisabled");
  }, [controllerRef]);

  const finishDrag = useCallback(() => {
    if (!draggingRef.current) {
      return;
    }
    draggingRef.current = false;
    onDragEnd();
    const onNextTask = () => {
      window.setTimeout(restore, 0);
    };
    clickListenerRef.current = onNextTask;
    document.addEventListener("click", onNextTask, true);
    restoreTimerRef.current = window.setTimeout(restore, DRAG_GUARD_MAX_MS);
  }, [onDragEnd, restore]);

  useEffect(() => {
    document.addEventListener("pointerup", finishDrag, true);
    document.addEventListener("pointercancel", finishDrag, true);
    return () => {
      document.removeEventListener("pointerup", finishDrag, true);
      document.removeEventListener("pointercancel", finishDrag, true);
      restore();
    };
  }, [finishDrag, restore]);

  const onDragStart = useCallback(() => {
    draggingRef.current = true;
    controllerRef.current?.setAttribute("gesturesdisabled", "");
  }, [controllerRef]);

  return { onDragStart };
}

export type PlaybackPlayerProps = {
  href: string;
  kind: "video" | "hls";
  seekable: boolean;
  tracks: PlaybackSubtitleItem[];
  /** 评论时间戳的跳转请求; `nonce` 让同一秒数的重复点击也能重新触发. */
  seekRequest: SeekRequest | null;
  /** 已经按请求定位之后回调, 由调用方清掉请求. */
  onSeekHandled: () => void;
  onFailed: (failure: HlsFailure | null) => void;
};

export type SeekRequest = {
  seconds: number;
  nonce: number;
};

export function PlaybackPlayer({
  href,
  kind,
  seekable,
  tracks,
  seekRequest,
  onSeekHandled,
  onFailed,
}: PlaybackPlayerProps) {
  const { t } = useTranslation("metadata");
  const videoRef = useRef<HTMLVideoElement>(null);
  const onFailedRef = useLatestRef(onFailed);
  const useNativeSrc = kind === "video" || nativeHlsSupported();
  const controllerRef = useRef<MediaControllerElement>(null);
  const rateButtonRef = useRef<MediaPlaybackRateMenuButtonElement>(null);
  const rateMenuRef = useRef<MediaChromeMenuElement>(null);
  const rateCloseTimerRef = useRef<number | null>(null);
  // 菜单里的勾选态只在展开时需要, 因此在展开时读取一次, 不订阅 ratechange.
  const [playbackRate, setPlaybackRate] = useState(1);

  const cancelRateClose = useCallback(() => {
    if (rateCloseTimerRef.current != null) {
      window.clearTimeout(rateCloseTimerRef.current);
      rateCloseTimerRef.current = null;
    }
  }, []);

  const closeRateMenu = useCallback(() => {
    const menu = rateMenuRef.current;
    if (menu != null && !menu.hidden) {
      rateButtonRef.current?.handleClick();
      // 菜单展开时会把焦点拿到自己身上, 收起后交还播放器, 否则方向键等快捷键不再生效.
      controllerRef.current?.focus();
    }
  }, []);

  // media-chrome 的菜单只响应点击, 这里在指针进入按钮时展开.
  const openRateMenu = useCallback(() => {
    cancelRateClose();
    const video = videoRef.current;
    if (video != null) {
      setPlaybackRate(video.playbackRate);
    }
    const menu = rateMenuRef.current;
    if (menu != null && menu.hidden) {
      rateButtonRef.current?.handleClick();
    }
  }, [cancelRateClose, videoRef]);

  const scheduleRateClose = useCallback(() => {
    cancelRateClose();
    rateCloseTimerRef.current = window.setTimeout(() => {
      rateCloseTimerRef.current = null;
      closeRateMenu();
    }, HOVER_CLOSE_DELAY_MS);
  }, [cancelRateClose, closeRateMenu]);

  useEffect(() => cancelRateClose, [cancelRateClose]);

  // 评论时间戳的跳转: 定位后立即播放, 与主流播放器点击时间戳的行为一致.
  // 同一秒数可能被连点, 因此按 nonce 判重, 不按内容判重.
  const handledSeekRef = useRef(0);
  useEffect(() => {
    if (seekRequest == null || seekRequest.nonce === handledSeekRef.current) {
      return;
    }
    const video = videoRef.current;
    if (video == null) {
      return;
    }
    handledSeekRef.current = seekRequest.nonce;
    video.currentTime = Math.max(seekRequest.seconds, 0);
    // 自动播放可能被浏览器拒绝; 那时位置已经跳过去了, 不额外提示.
    void video.play().catch(() => undefined);
    onSeekHandled();
  }, [onSeekHandled, seekRequest]);

  // 音量提示由 React 状态控制显隐, 放在播放器盒子内、控制器之外, 因此不受控件自动隐藏的影响.
  const [volumeIndicator, setVolumeIndicator] = useState<{ level: number; muted: boolean } | null>(
    null,
  );
  const volumeIndicatorTimerRef = useRef<number | null>(null);

  const showVolumeIndicator = useCallback(() => {
    const video = videoRef.current;
    if (video == null) {
      return;
    }
    setVolumeIndicator({ level: video.volume, muted: video.muted });
    if (volumeIndicatorTimerRef.current != null) {
      window.clearTimeout(volumeIndicatorTimerRef.current);
    }
    volumeIndicatorTimerRef.current = window.setTimeout(() => {
      volumeIndicatorTimerRef.current = null;
      setVolumeIndicator(null);
    }, VOLUME_INDICATOR_MS);
  }, [videoRef]);

  useEffect(
    () => () => {
      if (volumeIndicatorTimerRef.current != null) {
        window.clearTimeout(volumeIndicatorTimerRef.current);
      }
    },
    [],
  );

  const stepVolume = useCallback(
    (delta: number) => {
      const video = videoRef.current;
      if (video == null) {
        return;
      }
      const next = Math.min(Math.max(video.volume + delta, 0), 1);
      video.volume = next;
      // 从静音往上调即恢复声音, 与主流播放器一致.
      if (next > 0 && video.muted) {
        video.muted = false;
      }
    },
    [videoRef],
  );

  const { onKeyDown, onKeyUp } = usePlayerKeys(videoRef, seekable, stepVolume);

  const applyPlaybackRate = useCallback(
    (rate: number) => {
      const video = videoRef.current;
      if (video != null) {
        video.playbackRate = rate;
      }
      setPlaybackRate(rate);
    },
    [videoRef],
  );

  // 音量条的焦点落在 shadow DOM 里, `:focus-within` 在 WebKit 下不匹配宿主, 因此自己跟踪指针与焦点.
  const [volumePanelOpen, setVolumePanelOpen] = useState(false);
  // 拖动期间即使指针移出浮层也保持展开, 否则滑杆会拖到一半消失.
  const [volumeDragging, setVolumeDragging] = useState(false);

  // 音量条自己画: media-chrome 的 range 只按 clientX 取值, 旋转成竖向会拖不准.
  // 取值与 media-chrome 的 MEDIA_VOLUME_REQUEST 走同一条路径 (都写媒体元素), 键盘调音量不受影响.
  // 初始值不能假定为 1: media-chrome 把音量与静音偏好写进 localStorage, 装载时就可能不是 1.
  const [volume, setVolume] = useState(1);

  const endVolumeDrag = useCallback(() => setVolumeDragging(false), []);
  const dragGuard = useDragGestureGuard(controllerRef, endVolumeDrag);
  const handleDragStart = dragGuard.onDragStart;

  const startVolumeDrag = useCallback(() => {
    setVolumeDragging(true);
    handleDragStart();
  }, [handleDragStart]);

  const openVolumePanel = useCallback(() => {
    const video = videoRef.current;
    if (video != null) {
      setVolume(video.volume);
    }
    setVolumePanelOpen(true);
  }, [videoRef]);

  // 只认键盘焦点: 程序化交还的焦点 (例如倍速菜单收起时把焦点还给音量条) 不该把浮层带出来.
  const handleVolumeFocus = useCallback(
    (event: FocusEvent<HTMLDivElement>) => {
      const target = event.target;
      if (target instanceof HTMLElement && !target.matches(":focus-visible")) {
        return;
      }
      openVolumePanel();
    },
    [openVolumePanel],
  );

  const handleVolumeBlur = useCallback((event: FocusEvent<HTMLDivElement>) => {
    const next = event.relatedTarget;
    if (next instanceof Node && event.currentTarget.contains(next)) {
      return;
    }
    setVolumePanelOpen(false);
  }, []);

  // 音量或静音变化都走这里: 滑杆取值、音量提示、持久化的偏好都由它对齐.
  useEffect(() => {
    const video = videoRef.current;
    if (video == null) {
      return;
    }
    const sync = () => {
      setVolume(video.volume);
      showVolumeIndicator();
    };
    video.addEventListener("volumechange", sync);
    return () => video.removeEventListener("volumechange", sync);
  }, [showVolumeIndicator, videoRef]);

  const applyVolume = useCallback(
    (next: number) => {
      const video = videoRef.current;
      if (video != null) {
        video.volume = next;
        // 从静音往上拖即恢复声音, 与主流播放器一致.
        if (next > 0 && video.muted) {
          video.muted = false;
        }
      }
      setVolume(next);
    },
    [videoRef],
  );

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
    <>
      <MediaController
        ref={controllerRef}
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
        <div className={classes.scrim} aria-hidden="true" />
        <MediaControlBar>
          <MediaPlayButton />
          <MediaTimeDisplay showDuration />
          <div className={classes.barSpacer} aria-hidden="true" />
          {/* 音量收在按钮上方的浮层里, 指针进入或其中获得焦点时展开. */}
          <div
            className={classes.popupHost}
            data-open={volumePanelOpen || volumeDragging ? "true" : undefined}
            onPointerEnter={openVolumePanel}
            onPointerLeave={() => setVolumePanelOpen(false)}
            onFocus={handleVolumeFocus}
            onBlur={handleVolumeBlur}
          >
            <MediaMuteButton />
            <div className={classes.volumePanel} onPointerDown={startVolumeDrag}>
              <Slider
                orientation="vertical"
                size="sm"
                w={16}
                h={96}
                thumbSize={14}
                min={0}
                max={1}
                step={0.05}
                value={volume}
                onChange={applyVolume}
                label={(value) => `${Math.round(value * 100)}%`}
                aria-label={t("detail.playbackVolume")}
                color="brand"
                styles={{ track: { backgroundColor: "rgb(255 255 255 / 0.32)" } }}
              />
            </div>
          </div>
          <div
            className={classes.popupHost}
            onPointerEnter={openRateMenu}
            onPointerLeave={scheduleRateClose}
          >
            <MediaPlaybackRateMenuButton ref={rateButtonRef} invokeTarget={RATE_MENU_ID} />
            <MediaChromeMenu id={RATE_MENU_ID} ref={rateMenuRef} hidden>
              {PLAYBACK_RATES.map((rate) => (
                <MediaChromeMenuItem
                  key={rate}
                  value={String(rate)}
                  type="radio"
                  checked={rate === playbackRate}
                  onClick={() => applyPlaybackRate(rate)}
                >
                  {`${rate}x`}
                </MediaChromeMenuItem>
              ))}
            </MediaChromeMenu>
          </div>
          {tracks.length > 0 ? <MediaCaptionsMenuButton /> : null}
          <MediaFullscreenButton />
          {tracks.length > 0 ? <MediaCaptionsMenu hidden /> : null}
        </MediaControlBar>
        {seekable ? <MediaTimeRange onPointerDown={handleDragStart} /> : null}
      </MediaController>
      {/* 音量提示: 键盘调音量与静音时显示, 放在控制器之外因此不参与控件的自动隐藏. */}
      {volumeIndicator != null ? (
        <div className={classes.volumeIndicatorLayer}>
          <div className={classes.volumeIndicator} role="status" aria-live="polite">
            <div className={classes.volumeIndicatorBar}>
              <div
                className={classes.volumeIndicatorFill}
                style={{
                  width: `${volumeIndicator.muted ? 0 : Math.round(volumeIndicator.level * 100)}%`,
                }}
              />
            </div>
            <span>
              {volumeIndicator.muted
                ? t("detail.playbackMuted")
                : `${Math.round(volumeIndicator.level * 100)}%`}
            </span>
          </div>
        </div>
      ) : null}
    </>
  );
}
