import { Slider } from "@mantine/core";
import type { ErrorData } from "hls.js";
import "media-chrome/lang/zh-CN.js";
import type {
  MediaController as MediaControllerElement,
  MediaFullscreenButton as MediaFullscreenButtonElement,
} from "media-chrome";
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

/** 拖动进度条或音量条期间置位的属性: 手势层与画面点击都不再响应. */
const GESTURES_DISABLED_ATTRIBUTE = "gesturesdisabled";
/** 双击的判定窗口 (毫秒). 单击的动作须等过整个窗口, 才能确定没有第二次点击. */
const DOUBLE_CLICK_MS = 250;

/**
 * 全屏前后保持页面的滚动位置.
 *
 * 浏览器在进入全屏时会改写页面的滚动位置, 退出时未必写回 (Chromium 与 WebKit 都出现过这里的回归), 页
 * 面于是停在顶部. 因此进入前记下位置, 退出后写回: 事件里写一次, 下一帧再写一次 —— 浏览器自己的写入落
 * 在退出过程中, 只有晚于它才不会被覆盖.
 *
 * 记下的位置取自用户可见的状态: 全屏期间浏览器写入的值不能作为还原目标, 因此只在非全屏时更新; 指针与
 * 按键事件也一并更新, 覆盖「浏览器先改写滚动位置, 再进入全屏」的次序. 由其它元素发起的全屏不介入.
 */
function useFullscreenScrollRestore(controllerRef: RefObject<MediaControllerElement | null>) {
  useEffect(() => {
    const controller = controllerRef.current;
    if (controller == null) {
      return;
    }
    let visibleScroll = window.scrollY;
    // 非空表示这次全屏由本播放器发起, 退出时需要还原.
    let anchor: number | null = null;
    let restoreFrame: number | null = null;

    const rememberScroll = () => {
      if (document.fullscreenElement == null) {
        visibleScroll = window.scrollY;
      }
    };

    const handleFullscreenChange = () => {
      const fullscreen = document.fullscreenElement;
      if (fullscreen != null) {
        anchor = fullscreen === controller ? visibleScroll : null;
        return;
      }
      if (anchor == null) {
        return;
      }
      const top = anchor;
      anchor = null;
      window.scrollTo({ top, behavior: "instant" });
      restoreFrame = window.requestAnimationFrame(() => {
        restoreFrame = null;
        window.scrollTo({ top, behavior: "instant" });
      });
    };

    window.addEventListener("scroll", rememberScroll, true);
    window.addEventListener("pointerdown", rememberScroll, true);
    window.addEventListener("keydown", rememberScroll, true);
    document.addEventListener("fullscreenchange", handleFullscreenChange);
    return () => {
      window.removeEventListener("scroll", rememberScroll, true);
      window.removeEventListener("pointerdown", rememberScroll, true);
      window.removeEventListener("keydown", rememberScroll, true);
      document.removeEventListener("fullscreenchange", handleFullscreenChange);
      if (restoreFrame != null) {
        window.cancelAnimationFrame(restoreFrame);
      }
    };
  }, [controllerRef]);
}

/**
 * 画面的单击与双击.
 *
 * media-chrome 的手势层对每次 click 立即切换播放/暂停, 双击因此会先暂停再恢复, 画面停顿一次.
 * 鼠标与触控笔的点击改由这里接管: 第一次单击等过 `DOUBLE_CLICK_MS` 再执行, 窗口内出现第二次点击则
 * 改为切换全屏. 触摸的点击不接管, 仍由手势层即时切换 — 以双击切换全屏会与触摸平台的连击手势冲突.
 *
 * 判定与手势层保持一致, 否则会出现一边响应而另一边不响应的分歧: 目标须为视频或控制器自身 (画面上的
 * 控件各自处理点击; 拖动进度条后在画面上松手时, 浏览器把 click 的目标定为两者的最近公共祖先, 即控制
 * 器), 拖动期间 (`gesturesdisabled`) 不计数.
 *
 * 接管后这些点击在控制器处停止传播: 手势层挂在控制器上的监听与更外层的冒泡阶段监听都收不到. 控制条
 * 按钮、菜单与浮层的点击目标不是画面, 不进入这条路径.
 */
function useClickGestures(
  controllerRef: RefObject<MediaControllerElement | null>,
  videoRef: RefObject<HTMLVideoElement | null>,
  fullscreenButtonRef: RefObject<MediaFullscreenButtonElement | null>,
) {
  useEffect(() => {
    const controller = controllerRef.current;
    if (controller == null) {
      return;
    }
    // 指针类型取自 pointerdown: click 事件在部分浏览器里不带该信息; 缺省值与手势层相同, 都按鼠标处理.
    let pointerType = "mouse";
    let pendingClick: number | null = null;

    const handlePointerDown = (event: PointerEvent) => {
      pointerType = event.pointerType;
    };

    const handleClick = (event: MouseEvent) => {
      const video = videoRef.current;
      const target = event.composedPath()[0];
      if (video == null || (target !== video && target !== controller)) {
        return;
      }
      if (pointerType === "touch" || controller.hasAttribute(GESTURES_DISABLED_ATTRIBUTE)) {
        return;
      }
      // 手势层的 click 监听挂在控制器上; 捕获阶段拦下它, 这次点击才不会同时被它当成单击.
      event.stopPropagation();
      if (pendingClick != null) {
        window.clearTimeout(pendingClick);
        pendingClick = null;
        // 进入还是退出全屏由全屏按钮判定, 它按自身的全屏状态发出请求.
        fullscreenButtonRef.current?.handleClick(event);
        return;
      }
      pendingClick = window.setTimeout(() => {
        pendingClick = null;
        const current = videoRef.current;
        if (current == null) {
          return;
        }
        if (current.paused) {
          void current.play().catch(() => undefined);
        } else {
          current.pause();
        }
      }, DOUBLE_CLICK_MS);
    };

    controller.addEventListener("pointerdown", handlePointerDown, true);
    controller.addEventListener("click", handleClick, true);
    return () => {
      controller.removeEventListener("pointerdown", handlePointerDown, true);
      controller.removeEventListener("click", handleClick, true);
      if (pendingClick != null) {
        window.clearTimeout(pendingClick);
      }
    };
  }, [controllerRef, fullscreenButtonRef, videoRef]);
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
    controllerRef.current?.removeAttribute(GESTURES_DISABLED_ATTRIBUTE);
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
    controllerRef.current?.setAttribute(GESTURES_DISABLED_ATTRIBUTE, "");
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
  const fullscreenButtonRef = useRef<MediaFullscreenButtonElement>(null);
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

  useClickGestures(controllerRef, videoRef, fullscreenButtonRef);
  useFullscreenScrollRestore(controllerRef);

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
          <MediaFullscreenButton ref={fullscreenButtonRef} />
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
