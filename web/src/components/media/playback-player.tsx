import { Slider } from "@mantine/core";
import { useMediaQuery } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import {
  IconClock,
  IconLink,
  IconPlayerPauseFilled,
  IconPlayerPlayFilled,
  IconSun,
  IconVolume,
  IconVolumeOff,
} from "@tabler/icons-react";
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
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
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
/** 按住右键多久之后进入加速 (毫秒). 短于该时长的按键在松开时跳转. */
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
/** 右键菜单的尺寸下限估算, 只用于把菜单夹在播放窗口内; 实际宽度由条目文案决定. */
const CONTEXT_MENU_MIN_WIDTH = 200;
const CONTEXT_MENU_MAX_HEIGHT = 96;
/**
 * 关掉 media-chrome 自带的四个方向键.
 * 它在松开按键时才执行且只执行一次: 左右键因此与「短按跳 5 秒, 按住加速」冲突, 上下键则无法按住连续调节音量.
 */
const ARROW_HOTKEYS_OFF = "noarrowleft noarrowright noarrowup noarrowdown";
/**
 * 音量偏好的存储键.
 *
 * media-chrome 自带一份音量与静音偏好 (`media-chrome-pref-volume` / `-muted`), 两者互相独立,
 * 与本播放器的音量模型冲突, 因此由控制器上的 `novolumepref` / `nomutedpref` 关掉, 只留这一份.
 * 静音就是音量 0, 不单独记录.
 */
const VOLUME_PREF_KEY = "amane-playback-volume";

/** 读取存储的音量; 未存储、损坏或越界时回到满音量. */
function readStoredVolume(): number {
  try {
    // 必须先判空: `Number(null)` 为 0, 直接转换会把「没有存储」当成静音.
    const stored = window.localStorage.getItem(VOLUME_PREF_KEY);
    if (stored == null) {
      return 1;
    }
    const parsed = Number(stored);
    return Number.isFinite(parsed) && parsed >= 0 && parsed <= 1 ? parsed : 1;
  } catch {
    return 1;
  }
}

function writeStoredVolume(volume: number): void {
  try {
    window.localStorage.setItem(VOLUME_PREF_KEY, String(volume));
  } catch {
    // 无痕模式等场景下存储不可用, 音量只在本次会话内生效.
  }
}

/**
 * 秒数写成时间戳, 与评论里可点击的时间戳同形 (`mm:ss`, 满一小时才带小时).
 * 向下取整: 复制或跳转回到当前正在播放的这一秒.
 */
function formatClock(seconds: number): string {
  const total = Math.max(Math.floor(seconds), 0);
  const mm = String(Math.floor(total / 60) % 60).padStart(2, "0");
  const ss = String(total % 60).padStart(2, "0");
  const hours = Math.floor(total / 3600);
  return hours > 0 ? `${hours}:${mm}:${ss}` : `${mm}:${ss}`;
}

/**
 * 播放位置的分享地址: 在现有地址上换掉 `t` 参数, 其余参数 (来源、流等) 原样保留.
 * 位置不足一秒时不写 `t` —— `0` 与缺省等价, 写上去只是噪声.
 */
function shareableTimeUrl(seconds: number): string {
  const current = new URL(window.location.href);
  if (Math.floor(seconds) <= 0) {
    current.searchParams.delete("t");
  } else {
    current.searchParams.set("t", String(Math.floor(seconds)));
  }
  return current.toString();
}

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
 * 画面的右键菜单.
 *
 * 监听挂在控制器上而不是走 React 的 `onContextMenu`: 该事件不会派发到自定义元素上的 React 监听
 * (普通元素正常), 控制器收不到回调. 判定与画面的单击、双击一致 —— 只有视频与控制器自身算数,
 * 控制条与菜单保留浏览器原生右键菜单.
 */
function useVideoContextMenu(
  controllerRef: RefObject<MediaControllerElement | null>,
  videoRef: RefObject<HTMLVideoElement | null>,
  onOpen: (anchor: ContextMenuAnchor) => void,
) {
  const openRef = useLatestRef(onOpen);
  useEffect(() => {
    const controller = controllerRef.current;
    if (controller == null) {
      return;
    }
    const handle = (event: MouseEvent) => {
      const video = videoRef.current;
      const target = event.composedPath()[0];
      if (video == null || (target !== video && target !== controller)) {
        return;
      }
      event.preventDefault();
      openRef.current(contextMenuAnchor(controller, event));
    };
    controller.addEventListener("contextmenu", handle);
    return () => controller.removeEventListener("contextmenu", handle);
  }, [controllerRef, openRef, videoRef]);
}

/** 右键菜单在播放窗口内的落点: 相对控制器的坐标, 以及展开的方向. */
type ContextMenuAnchor = {
  left: number;
  top: number;
  /** 下方放不下时向上展开: 锚点改取底边 (右键处的指针). */
  upward: boolean;
};

/** 右键菜单在指针处展开, 越出播放窗口的部分夹回窗口内. */
function contextMenuAnchor(
  controller: MediaControllerElement,
  event: MouseEvent,
): ContextMenuAnchor {
  const rect = controller.getBoundingClientRect();
  const left = Math.min(
    event.clientX - rect.left,
    Math.max(rect.width - CONTEXT_MENU_MIN_WIDTH, 0),
  );
  const bottom = event.clientY - rect.top;
  const upward = bottom > rect.height - CONTEXT_MENU_MAX_HEIGHT;
  return {
    left: Math.max(left, 0),
    top: upward ? bottom : Math.min(bottom, Math.max(rect.height - CONTEXT_MENU_MAX_HEIGHT, 0)),
    upward,
  };
}

/**
 * 画面上的右键菜单: 复制当前时间戳与带时间参数的分享地址.
 *
 * 菜单必须留在控制器内部: 控制器的容器带 `overflow: hidden`, 挂到控制器外会被裁掉; 全屏时也只有控制器
 * 的子树可见 (Mantine 的 Portal 挂到 `document.body`, 全屏下不可见).
 *
 * 复制的秒数在点按条目时读取, 不是展开菜单时的那一秒 —— 菜单展开期间画面仍在播放.
 *
 * 展开期间在菜单下面盖一层透明遮罩, 菜单之外的点击都由它接住: 这次点击的 target 不是视频,
 * 因此画面不会切换播放/暂停 (画面点击只认视频与控制器自身), 控制条也不会被误触. 收起判断
 * 因此只看"点在不在菜单里", 不依赖事件传播次序.
 *
 * 不可寻址的流没有可复制的跳转目标, 两项都禁用.
 */
function ContextMenu({
  anchor,
  seekable,
  readSeconds,
  onClose,
}: {
  anchor: ContextMenuAnchor;
  seekable: boolean;
  /** 读取当前播放位置, 在点按条目时调用. */
  readSeconds: () => number;
  /** 收起菜单; 参数为真时把焦点交还播放器 (复制之后焦点留在菜单项上). */
  onClose: (restoreFocus: boolean) => void;
}) {
  const { t } = useTranslation("metadata");

  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClick = (event: MouseEvent) => {
      const target = event.target;
      // 菜单自己的点击由条目收起; 其余任何点击 (含画面、控制条、页面别处) 都收起. 这次点击照常
      // 生效, 因此点画面同时会切换播放/暂停.
      if (target instanceof Node && menuRef.current?.contains(target)) {
        return;
      }
      onClose(false);
    };
    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose(false);
      }
    };
    // 捕获阶段之外另加冒泡阶段: 页面其它层的阻止传播不会让菜单留在画面上.
    document.addEventListener("click", handleClick, true);
    document.addEventListener("click", handleClick);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("click", handleClick, true);
      document.removeEventListener("click", handleClick);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [onClose]);

  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      notifications.show({ message: t("detail.playbackCopied"), color: "blue" });
    } catch {
      notifications.show({ message: t("detail.playbackCopyFailed"), color: "red" });
    }
    onClose(true);
  };

  return (
    <>
      {/* 遮罩占满播放器: 菜单之外的点击落在它身上, 不会落到画面或控制条上; 点击只用于收起菜单. */}
      <div
        className={classes.contextMenuBackdrop}
        aria-hidden="true"
        onClick={() => onClose(false)}
        onContextMenu={(event) => event.preventDefault()}
      />
      <div
        ref={menuRef}
        className={classes.contextMenu}
        role="menu"
        data-upward={anchor.upward ? "true" : undefined}
        style={{ left: anchor.left, top: anchor.top }}
        // 菜单上的右键不改写落点, 也不允许浏览器原生菜单叠上来. 这是普通元素, 合成事件正常触发.
        onContextMenu={(event) => event.preventDefault()}
      >
        <button
          type="button"
          role="menuitem"
          className={classes.contextMenuItem}
          disabled={!seekable}
          onClick={() => void copy(formatClock(readSeconds()))}
        >
          <IconClock size={16} aria-hidden />
          {t("detail.playbackCopyTimestamp")}
        </button>
        <button
          type="button"
          role="menuitem"
          className={classes.contextMenuItem}
          disabled={!seekable}
          onClick={() => void copy(shareableTimeUrl(readSeconds()))}
        >
          <IconLink size={16} aria-hidden />
          {t("detail.playbackCopyLink")}
        </button>
      </div>
    </>
  );
}

/**
 * 四个方向键.
 *
 * 左右: 跳转在松开按键时执行, 因此短按跳 `SEEK_STEP_SECONDS` 秒, 长按不跳转. 按住右键超过
 * `HOLD_SEEK_DELAY_MS` 之后把播放倍速提到 `HOLD_SEEK_RATE`, 倍速按钮会同步显示该值. 松开按键、
 * 窗口失焦、切换标签页时恢复用户设定的倍速 — 后两种时机用于兜住丢失的 keyup.
 *
 * 加速只属于右键: 倒放无法用倍速实现 (`HTMLMediaElement.playbackRate` 不接受非正值), 左键长按
 * 因此没有动作. 加速归属按下时记下的键, 松开另一个键不打断长按.
 *
 * 上下: 每次 keydown 调整一次音量, 按住时浏览器持续派发 keydown, 因此可以连续调节.
 *
 * 不可寻址的流 (列表给出 `seekable` 为假) 不接管左右键, 避免出现能按但跳不动的操作.
 */
function usePlayerKeys(
  controllerRef: RefObject<MediaControllerElement | null>,
  videoRef: RefObject<HTMLVideoElement | null>,
  seekable: boolean,
  onVolumeStep: (delta: number) => void,
  onSpeedHold: (holding: boolean) => void,
) {
  const holdTimerRef = useRef<number | null>(null);
  const savedRateRef = useRef<number | null>(null);
  // 长按按下的方向键; 为空表示这次按住还没到加速的时机, 松开时按短按处理.
  const heldKeyRef = useRef<"ArrowLeft" | "ArrowRight" | null>(null);

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
    heldKeyRef.current = null;
    // 按住加速的标记与触屏长按同源: 键盘松开也要撤掉.
    onSpeedHold(false);
  }, [onSpeedHold, videoRef]);

  useEffect(() => {
    const controller = controllerRef.current;
    if (controller == null) {
      return;
    }
    window.addEventListener("blur", releaseHold);
    document.addEventListener("visibilitychange", releaseHold);
    // 焦点离开播放器后 keyup 不会再回到控制器, 长按状态必须在这里复位, 否则下一次按方向键
    // 会被当成「已有键按住」而忽略, 其松开时又按短按跳转.
    controller.addEventListener("blur", releaseHold);
    return () => {
      window.removeEventListener("blur", releaseHold);
      document.removeEventListener("visibilitychange", releaseHold);
      controller.removeEventListener("blur", releaseHold);
      releaseHold();
    };
  }, [controllerRef, releaseHold]);

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
      // 按住期间浏览器持续派发 keydown; 已经有按下的键时不重新计时, 也不改归属.
      if (event.repeat || heldKeyRef.current != null) {
        return;
      }
      const key = event.key;
      heldKeyRef.current = key;
      holdTimerRef.current = window.setTimeout(() => {
        holdTimerRef.current = null;
        // 只有右键有长按动作.
        if (heldKeyRef.current !== "ArrowRight") {
          return;
        }
        const held = videoRef.current;
        if (held == null) {
          return;
        }
        savedRateRef.current = held.playbackRate;
        held.playbackRate = Math.max(held.playbackRate, HOLD_SEEK_RATE);
        onSpeedHold(true);
      }, HOLD_SEEK_DELAY_MS);
    },
    [onSpeedHold, onVolumeStep, seekable, videoRef],
  );

  const onKeyUp = useCallback(
    (event: KeyboardEvent<HTMLElement>) => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") {
        return;
      }
      // 两个方向键同时按住时, 松开另一个键不改变长按归属.
      if (heldKeyRef.current !== event.key) {
        return;
      }
      const video = videoRef.current;
      // 已经进入加速的长按不跳转; 只有短按才跳.
      const shortPress = holdTimerRef.current != null;
      releaseHold();
      if (!shortPress || video == null) {
        return;
      }
      const offset = event.key === "ArrowRight" ? SEEK_STEP_SECONDS : -SEEK_STEP_SECONDS;
      video.currentTime = Math.max(video.currentTime + offset, 0);
    },
    [releaseHold, videoRef],
  );

  return { onKeyDown, onKeyUp };
}

/** 拖动进度条或音量条期间置位的属性: 手势层与画面点击都不再响应. */
const GESTURES_DISABLED_ATTRIBUTE = "gesturesdisabled";
/**
 * 触屏手势进行中置位的属性: 控制条与进度条不参与 (见样式表).
 * 触摸按下即置位, 松手撤掉 — 撤掉的那一瞬在浏览器派发 click 之前, 点按切换控件显隐照常.
 */
const GESTURE_ATTRIBUTE = "data-amane-gesture";
/** 双击的判定窗口 (毫秒). 单击的动作须等过整个窗口, 才能确定没有第二次点击. */
const DOUBLE_CLICK_MS = 250;
/** 触屏长按多久算"按住加速" (毫秒); 走的是键盘 `HOLD_SEEK_DELAY_MS` 那条动作. */
const TOUCH_LONG_PRESS_MS = 450;
/** 触屏手势的起手位移阈值 (像素): 超过它就不再是点按. */
const TOUCH_MOVE_THRESHOLD_PX = 10;
/** 横向滑过整个播放窗口对应的跳转秒数, 以及单次手势的跳转上限. */
const TOUCH_SEEK_SPAN_SECONDS = 120;
const TOUCH_SEEK_MAX_SECONDS = 90;
/** 竖直滑动走完音量 (右半屏) 或亮度 (左半屏) 全量程所需的像素数, 向上为增. */
const TOUCH_LEVEL_FULL_SPAN_PX = 220;
/** 亮度下限: 全黑既没有意义也回不来. */
const MIN_BRIGHTNESS = 0.2;
/** 调整提示的类型: 音量与亮度共用同一套提示. */
type HudKind = "volume" | "brightness";

/** 播放/暂停切换: 手势层的双击、画面单击与居中大按钮共用, 三处的判据必须一致. */
function togglePlayback(video: HTMLVideoElement | null): void {
  if (video == null) {
    return;
  }
  if (video.paused) {
    void video.play().catch(() => undefined);
  } else {
    video.pause();
  }
}

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
 * 全屏播放转横屏.
 *
 * 手机上竖屏全屏会让画面挤在中间一条, 主流播放器都转成横屏. 壳里由原生层锁方向 (见 docs/dev/android.md),
 * 浏览器只能由页面自己锁: Screen Orientation API 只在全屏期间可锁, 退出时交还系统. 由其它元素发起的
 * 全屏不介入; 没有该 API 的浏览器 (iOS Safari、桌面浏览器) 与锁失败都静默忽略, 系统旋转仍然生效.
 */
function useFullscreenLandscape(controllerRef: RefObject<MediaControllerElement | null>) {
  useEffect(() => {
    const controller = controllerRef.current;
    if (controller == null) {
      return;
    }

    const handleFullscreenChange = () => {
      const orientation: ScreenOrientation | undefined = screen.orientation;
      if (orientation == null || typeof orientation.lock !== "function") {
        return;
      }
      if (document.fullscreenElement !== controller) {
        if (typeof orientation.unlock === "function") {
          orientation.unlock();
        }
        return;
      }
      void orientation.lock("landscape").catch(() => undefined);
    };

    document.addEventListener("fullscreenchange", handleFullscreenChange);
    return () => document.removeEventListener("fullscreenchange", handleFullscreenChange);
  }, [controllerRef]);
}

/**
 * 把焦点交还播放器, 但不让浏览器为此滚动页面.
 *
 * 播放器在视口里只露出一部分时, 聚焦会让浏览器把它整个滚进来 —— 收起菜单或倍速菜单之后页面
 * 会突然跳到播放器顶端. 焦点只影响快捷键, 不需要改变滚动位置.
 */
function focusPlayer(controllerRef: RefObject<MediaControllerElement | null>): void {
  controllerRef.current?.focus({ preventScroll: true });
}

/**
 * 触屏手势, 只认 `pointerType === "touch"`: 鼠标的单击 / 双击 / 右键保持原样.
 *
 * 长按加速; 横滑拖进度; 双击播放 / 暂停; 竖直滑动调亮度或音量 — 按起手的半屏分派, 左半屏亮度, 右半屏音量.
 * 视频区域因此不再把纵向手势留给页面滚动, 音量取媒体元素自己的音量 (不是系统音量), 亮度是画面滤镜.
 *
 * 长按在 Chromium 里同时会派发 `contextmenu`, 这里在捕获阶段拦掉那一次, 不打开右键菜单.
 *
 * 三个回调与 `readLevel` 必须都是稳定引用: 引用变化会重建监听, 正在进行的手势会丢.
 */
function useTouchGestures(
  controllerRef: RefObject<MediaControllerElement | null>,
  videoRef: RefObject<HTMLVideoElement | null>,
  seekable: boolean,
  onSpeedHold: (holding: boolean) => void,
  onSeekPreview: (preview: { target: number; total: number | null } | null) => void,
  onLevelChange: (kind: HudKind, value: number) => void,
  readLevel: (kind: HudKind) => number,
) {
  useEffect(() => {
    const controller = controllerRef.current;
    if (controller == null) {
      return;
    }

    let pointerId: number | null = null;
    let startX = 0;
    let startY = 0;
    let baseSeconds = 0;
    let deltaSeconds = 0;
    let rateBeforeHold = 1;
    let mode: "idle" | "seek" | "speed" | "level" = "idle";
    let levelKind: HudKind = "volume";
    let baseLevel = 0;
    let longPressTimer: number | null = null;
    let lastTapAt = 0;
    let lastPointerType = "mouse";

    const clearLongPress = () => {
      if (longPressTimer != null) {
        window.clearTimeout(longPressTimer);
        longPressTimer = null;
      }
    };

    /**
     * 拖动进度条 / 音量条期间媒体手势让位 — 那条拖动由控件自己处理.
     *
     * 判定不能只在 `pointerdown` 做一次: 拖动标记是控件的 `pointerdown` 里置位的, 而那个处理在冒泡阶段,
     * 落在捕获阶段的这里之后; 起手落在进度条上时, 手势会先接上, 再在移动或松手时把控件那一次拖动改掉
     * (拖动中还会多出一条横滑提示).
     */
    const gesturesDisabled = () => controller.hasAttribute(GESTURES_DISABLED_ATTRIBUTE);

    const handlePointerDown = (event: PointerEvent) => {
      lastPointerType = event.pointerType;
      if (event.pointerType !== "touch" || controller.hasAttribute(GESTURES_DISABLED_ATTRIBUTE)) {
        return;
      }
      const video = videoRef.current;
      if (video == null) {
        return;
      }
      // 手势期间不弹控制条: 提示由悬浮层给, 进度条只在拖动它自己时才需要.
      controller.setAttribute(GESTURE_ATTRIBUTE, "");
      pointerId = event.pointerId;
      startX = event.clientX;
      startY = event.clientY;
      baseSeconds = video.currentTime;
      deltaSeconds = 0;
      mode = "idle";
      clearLongPress();
      longPressTimer = window.setTimeout(() => {
        longPressTimer = null;
        // 手指还停在原处, 但这次起手可能已被控件认成拖动 (进度条), 此时不再算按住加速.
        if (gesturesDisabled()) {
          return;
        }
        const current = videoRef.current;
        if (current == null) {
          return;
        }
        mode = "speed";
        rateBeforeHold = current.playbackRate;
        current.playbackRate = HOLD_SEEK_RATE;
        onSpeedHold(true);
      }, TOUCH_LONG_PRESS_MS);
    };

    const handlePointerMove = (event: PointerEvent) => {
      if (event.pointerId !== pointerId || mode === "speed") {
        return;
      }
      if (gesturesDisabled()) {
        clearLongPress();
        pointerId = null;
        mode = "idle";
        return;
      }
      const dx = event.clientX - startX;
      const dy = event.clientY - startY;
      if (mode === "idle") {
        if (Math.hypot(dx, dy) < TOUCH_MOVE_THRESHOLD_PX) {
          return;
        }
        clearLongPress();
        if (Math.abs(dy) > Math.abs(dx)) {
          // 竖直: 左半屏调亮度, 右半屏调音量. 基值在手势起点读一次, 之后不再跟着状态走.
          const rect = controller.getBoundingClientRect();
          levelKind = event.clientX < rect.left + rect.width / 2 ? "brightness" : "volume";
          baseLevel = readLevel(levelKind);
          mode = "level";
        } else if (seekable) {
          mode = "seek";
        } else {
          // 不可定位的流 (直播 / 上游不声明) 不做进度, 这次手势作废.
          pointerId = null;
          return;
        }
      }
      if (mode === "level") {
        const delta = -dy / TOUCH_LEVEL_FULL_SPAN_PX;
        onLevelChange(levelKind, Math.min(Math.max(baseLevel + delta, 0), 1));
        return;
      }
      const span = Math.max(controller.clientWidth, 1);
      const raw = (dx / span) * TOUCH_SEEK_SPAN_SECONDS;
      deltaSeconds = Math.min(Math.max(raw, -TOUCH_SEEK_MAX_SECONDS), TOUCH_SEEK_MAX_SECONDS);
      // 总长度在这里读: 它随媒体加载出现, 订阅它会让整个播放器跟着重渲染.
      const live = videoRef.current;
      const total =
        live != null && Number.isFinite(live.duration) && live.duration > 0 ? live.duration : null;
      const target = Math.max(baseSeconds + deltaSeconds, 0);
      // 提示里的目标时间与落点一致: 超过总长时按总长显示.
      onSeekPreview({ target: total == null ? target : Math.min(target, total), total });
    };

    const handlePointerEnd = (event: PointerEvent) => {
      if (event.pointerId !== pointerId) {
        return;
      }
      clearLongPress();
      controller.removeAttribute(GESTURE_ATTRIBUTE);
      const video = videoRef.current;
      const ended = mode;
      mode = "idle";
      pointerId = null;
      // 系统撤销这次触摸 (通知栏下拉, 系统手势, 窗口失焦): 跳转不落地, 也不能被记成一次点按 —
      // 否则紧随其后的一次真点按会凑成双击, 意外切换播放状态.
      if (event.type === "pointercancel") {
        if (ended === "speed" && video != null) {
          video.playbackRate = rateBeforeHold;
        }
        lastTapAt = 0;
        onSpeedHold(false);
        onSeekPreview(null);
        return;
      }
      if (gesturesDisabled()) {
        // 控件接管了这次触摸 (拖动进度条 / 音量条): 已生效的加速要还回去, 跳转与提示都不做.
        if (ended === "speed" && video != null) {
          video.playbackRate = rateBeforeHold;
        }
        onSpeedHold(false);
        onSeekPreview(null);
        return;
      }
      if (ended === "level") {
        // 亮度与音量都是实时生效的, 松开不再做事.
        return;
      }
      if (video == null) {
        onSpeedHold(false);
        onSeekPreview(null);
        return;
      }
      if (ended === "speed") {
        video.playbackRate = rateBeforeHold;
        onSpeedHold(false);
        return;
      }
      if (ended === "seek") {
        const duration = Number.isFinite(video.duration)
          ? video.duration
          : Number.POSITIVE_INFINITY;
        video.currentTime = Math.min(Math.max(baseSeconds + deltaSeconds, 0), duration);
        onSeekPreview(null);
        return;
      }
      // 点按: 两次落在双击窗口内就切换播放状态; 单击仍由 media-chrome 切换控件显隐.
      const now = event.timeStamp;
      if (now - lastTapAt < DOUBLE_CLICK_MS) {
        lastTapAt = 0;
        togglePlayback(video);
        return;
      }
      lastTapAt = now;
    };

    const handleContextMenu = (event: MouseEvent) => {
      if (lastPointerType !== "touch") {
        return;
      }
      // 捕获阶段拦下: 事件因此不会到达画面, 视频右键菜单的监听不会展开.
      event.preventDefault();
      event.stopPropagation();
    };

    controller.addEventListener("pointerdown", handlePointerDown, true);
    controller.addEventListener("pointermove", handlePointerMove, true);
    controller.addEventListener("pointerup", handlePointerEnd, true);
    controller.addEventListener("pointercancel", handlePointerEnd, true);
    controller.addEventListener("contextmenu", handleContextMenu, true);
    return () => {
      clearLongPress();
      controller.removeAttribute(GESTURE_ATTRIBUTE);
      controller.removeEventListener("pointerdown", handlePointerDown, true);
      controller.removeEventListener("pointermove", handlePointerMove, true);
      controller.removeEventListener("pointerup", handlePointerEnd, true);
      controller.removeEventListener("pointercancel", handlePointerEnd, true);
      controller.removeEventListener("contextmenu", handleContextMenu, true);
    };
  }, [controllerRef, onLevelChange, onSeekPreview, onSpeedHold, readLevel, seekable, videoRef]);
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
 *
 * 返回的函数用于撤掉已排队的那次单击 (右键菜单收起时调用).
 */
function useClickGestures(
  controllerRef: RefObject<MediaControllerElement | null>,
  videoRef: RefObject<HTMLVideoElement | null>,
  fullscreenButtonRef: RefObject<MediaFullscreenButtonElement | null>,
) {
  // 已排队但还没执行的那次单击; 菜单展开时撤掉它, 那次点击只用于收起菜单.
  const cancelPendingClickRef = useRef<() => void>(() => undefined);

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
        togglePlayback(videoRef.current);
      }, DOUBLE_CLICK_MS);
    };

    cancelPendingClickRef.current = () => {
      if (pendingClick != null) {
        window.clearTimeout(pendingClick);
        pendingClick = null;
      }
    };

    controller.addEventListener("pointerdown", handlePointerDown, true);
    controller.addEventListener("click", handleClick, true);
    return () => {
      cancelPendingClickRef.current = () => undefined;
      controller.removeEventListener("pointerdown", handlePointerDown, true);
      controller.removeEventListener("click", handleClick, true);
      if (pendingClick != null) {
        window.clearTimeout(pendingClick);
      }
    };
  }, [controllerRef, fullscreenButtonRef, videoRef]);

  return cancelPendingClickRef;
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
  /** 评论时间戳的跳转请求; 同一秒数的重复点击靠新请求重新触发. */
  seekRequest: SeekRequest | null;
  /** 已经按请求定位之后回调, 由调用方清掉请求. */
  onSeekHandled: () => void;
  onFailed: (failure: HlsFailure | null) => void;
};

/**
 * 跳转请求.
 * `nonce` 必须由调用方严格递增: 播放器按它与已处理的请求判重, 复用同一个值会被当成已处理而忽略,
 * 请求被清空后重新从 1 计数就会命中这种情况.
 */
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

  /**
   * 粗指针 (触屏) 上倍速改成右侧滑出面板: 悬浮菜单靠指针进入展开, 触屏既没有悬停也没有离开 —
   * 全屏播放是横屏, 视口很宽, 靠宽度判断会退回鼠标形态, 因此按指针类型判定.
   */
  const coarsePointer = useMediaQuery("(pointer: coarse)");
  const [rateSheetOpen, setRateSheetOpen] = useState(false);
  const rateSheetVisible = coarsePointer && rateSheetOpen;

  // 右键菜单: 只记落点. 复制的秒数在点按条目时读取, 因此不订阅 timeupdate (订阅会让整个播放器
  // 每秒重渲染).
  const [contextMenu, setContextMenu] = useState<ContextMenuAnchor | null>(null);

  // 画面的单击、双击与它们的排队状态: 菜单开合都要能撤掉排队中的那次单击.
  const cancelPendingClickRef = useClickGestures(controllerRef, videoRef, fullscreenButtonRef);

  // 触屏手势的提示: 长按加速与横滑拖进度, 与音量提示同形 (渲染位置见下方提示层).
  const [speedHold, setSpeedHold] = useState(false);
  const [seekPreview, setSeekPreview] = useState<{ target: number; total: number | null } | null>(
    null,
  );

  const openContextMenu = useCallback(
    (anchor: ContextMenuAnchor) => {
      // 右键之前若已经有一次左键单击在排队 (先左键后右键的连击), 它不该在菜单展开后切播放状态.
      cancelPendingClickRef.current();
      setContextMenu(anchor);
    },
    [cancelPendingClickRef],
  );
  useVideoContextMenu(controllerRef, videoRef, openContextMenu);

  const readSeconds = useCallback(() => videoRef.current?.currentTime ?? 0, [videoRef]);

  // 收起菜单的那一次点击只用于收起: 撤掉它排队中的单击, 否则画面会跟着切换播放/暂停.
  // 交还焦点只发生在点菜单条目之后 (那时焦点在菜单项上), 点击别处收起时不夺回焦点.
  const closeContextMenu = useCallback(
    (restoreFocus: boolean) => {
      cancelPendingClickRef.current();
      setContextMenu(null);
      if (restoreFocus) {
        focusPlayer(controllerRef);
      }
    },
    [cancelPendingClickRef, controllerRef],
  );

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
      focusPlayer(controllerRef);
    }
  }, []);

  // media-chrome 的菜单只响应点击, 这里在指针进入按钮时展开; 触屏没有悬停, 那次进入要忽略.
  const openRateMenu = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      if (event.pointerType !== "mouse") {
        return;
      }
      cancelRateClose();
      // 鼠标形态下不会渲染滑出菜单, 顺手清掉它可能留下的展开态.
      setRateSheetOpen(false);
      const video = videoRef.current;
      if (video != null) {
        setPlaybackRate(video.playbackRate);
      }
      const menu = rateMenuRef.current;
      if (menu != null && menu.hidden) {
        rateButtonRef.current?.handleClick();
      }
    },
    [cancelRateClose, videoRef],
  );

  const openRateSheet = useCallback(() => {
    const video = videoRef.current;
    if (video != null) {
      setPlaybackRate(video.playbackRate);
    }
    setRateSheetOpen(true);
  }, [videoRef]);

  const closeRateSheet = useCallback(() => setRateSheetOpen(false), []);

  // 粗指针下倍速按钮的点击改开滑出菜单: 在捕获阶段截下, media-chrome 的菜单不会展开.
  const interceptRateButtonClick = useCallback(
    (event: ReactMouseEvent<HTMLDivElement>) => {
      if (!coarsePointer) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      openRateSheet();
    },
    [coarsePointer, openRateSheet],
  );

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
  //
  // 元素尚未拿到媒体资源 (readyState 为 HAVE_NOTHING) 时写 currentTime 只记下默认播放起点, 不产生
  // seeking 事件; hls.js 的起点只来自 seeking 事件与自身配置, 于是它从片头开始加载, 这一次跳转不生效.
  // 目标因此先记下, 等元素能定位 (loadedmetadata / canplay) 时再写入.
  const handledSeekRef = useRef(0);
  const pendingSeekRef = useRef<number | null>(null);

  const applyPendingSeek = useCallback(() => {
    const video = videoRef.current;
    const seconds = pendingSeekRef.current;
    if (video == null || seconds == null || video.readyState === HTMLMediaElement.HAVE_NOTHING) {
      return;
    }
    pendingSeekRef.current = null;
    video.currentTime = Math.max(seconds, 0);
    // 自动播放可能被浏览器拒绝; 那时位置已经跳过去了, 不额外提示.
    void video.play().catch(() => undefined);
  }, [videoRef]);

  useEffect(() => {
    const video = videoRef.current;
    if (video == null) {
      return;
    }
    video.addEventListener("loadedmetadata", applyPendingSeek);
    video.addEventListener("canplay", applyPendingSeek);
    return () => {
      video.removeEventListener("loadedmetadata", applyPendingSeek);
      video.removeEventListener("canplay", applyPendingSeek);
    };
  }, [applyPendingSeek, videoRef]);

  useEffect(() => {
    if (seekRequest == null || seekRequest.nonce === handledSeekRef.current) {
      return;
    }
    handledSeekRef.current = seekRequest.nonce;
    pendingSeekRef.current = seekRequest.seconds;
    applyPendingSeek();
    onSeekHandled();
  }, [applyPendingSeek, onSeekHandled, seekRequest]);

  // 调整提示 (音量 / 亮度) 由 React 状态控制显隐, 不随控件自动隐藏, 见渲染处的提示层.
  const [hud, setHud] = useState<{ kind: HudKind; level: number } | null>(null);
  const hudTimerRef = useRef<number | null>(null);

  const showHud = useCallback((subject: HudKind, level: number) => {
    setHud({ kind: subject, level });
    if (hudTimerRef.current != null) {
      window.clearTimeout(hudTimerRef.current);
    }
    hudTimerRef.current = window.setTimeout(() => {
      hudTimerRef.current = null;
      setHud(null);
    }, VOLUME_INDICATOR_MS);
  }, []);

  useEffect(
    () => () => {
      if (hudTimerRef.current != null) {
        window.clearTimeout(hudTimerRef.current);
      }
    },
    [],
  );

  // 音量是唯一的音量状态: 静音即音量 0, 媒体元素的 `muted` 始终为假.
  // 值随存储初始化, 因此装载时滑杆与图标直接落在上次的音量上; 写入存储只由用户操作触发.
  const [volume, setVolume] = useState(readStoredVolume);
  // 亮度是画面叠加层的透明度 (网页拿不到系统亮度): 0.2 ~ 1 相对原片, 只在本次会话内有效.
  const [brightness, setBrightness] = useState(1);
  // 手势的基值来自这两个 ref: 手势回调必须是稳定引用, 不能跟着 state 变.
  const volumeRef = useRef(volume);
  const brightnessRef = useRef(brightness);

  useEffect(() => {
    volumeRef.current = volume;
    brightnessRef.current = brightness;
  }, [brightness, volume]);
  // 取消静音要回到的音量. 存储里只有当前音量, 0 之后无从取回, 因此这份记忆只保留在本次会话内.
  const lastNonZeroVolumeRef = useRef(volume === 0 ? 1 : volume);

  const applyVolume = useCallback(
    (next: number) => {
      const video = videoRef.current;
      if (video != null) {
        video.volume = next;
      }
      if (next > 0) {
        lastNonZeroVolumeRef.current = next;
      }
      setVolume(next);
    },
    [videoRef],
  );

  // 音量变化只由用户操作发起 (滑杆、方向键、静音按钮), 因此提示与存储都在操作处处理, 不订阅 volumechange:
  // 订阅会把装载时的恢复也当成一次调整.
  const setVolumeFromUser = useCallback(
    (next: number) => {
      applyVolume(next);
      showHud("volume", next);
      writeStoredVolume(next);
    },
    [applyVolume, showHud],
  );

  // 恢复存储的音量. 滑杆与提示的取值来自 React 状态, 这里只写媒体元素; 存在存储里的音量为 0 时即为静音.
  useEffect(() => {
    const video = videoRef.current;
    if (video != null) {
      video.volume = readStoredVolume();
    }
  }, []);

  // 从 0 起调与从任何音量起调同形: 逐级加减一档, 不做「恢复上次音量」的特例.
  const stepVolume = useCallback(
    (delta: number) => {
      const next = Math.min(Math.max(Math.round((volume + delta) * 100) / 100, 0), 1);
      setVolumeFromUser(next);
    },
    [setVolumeFromUser, volume],
  );

  // 静音按钮与 `m` 快捷键都请求静音位; 本播放器把静音实现为音量 0, 因此在控制器上就地截下这两条请求,
  // 换成音量请求再重新派发. 必须用 stopImmediatePropagation: 控制器自己的监听挂在同一元素上.
  const toggleMute = useCallback(() => {
    setVolumeFromUser(volume === 0 ? lastNonZeroVolumeRef.current : 0);
  }, [setVolumeFromUser, volume]);

  useEffect(() => {
    const controller = controllerRef.current;
    if (controller == null) {
      return;
    }
    const handle = (event: Event) => {
      event.stopImmediatePropagation();
      toggleMute();
    };
    controller.addEventListener("mediamuterequest", handle, true);
    controller.addEventListener("mediaunmuterequest", handle, true);
    return () => {
      controller.removeEventListener("mediamuterequest", handle, true);
      controller.removeEventListener("mediaunmuterequest", handle, true);
    };
  }, [toggleMute]);

  const { onKeyDown, onKeyUp } = usePlayerKeys(
    controllerRef,
    videoRef,
    seekable,
    stepVolume,
    setSpeedHold,
  );

  // 手势的基值读取与写入都走稳定引用: 它们变化会重建手势监听, 正在进行的那次手势会丢.
  const readLevel = useCallback(
    (level: HudKind) => (level === "volume" ? volumeRef.current : brightnessRef.current),
    [],
  );
  const handleLevelChange = useCallback(
    (level: HudKind, value: number) => {
      if (level === "volume") {
        // 与滑杆同一条路径: 提示与存储都在里面处理.
        setVolumeFromUser(value);
        return;
      }
      // 亮度留 20% 的地板: 全黑没有意义, 也回不来.
      const next = Math.max(value, MIN_BRIGHTNESS);
      setBrightness(next);
      showHud("brightness", next);
    },
    [setVolumeFromUser, showHud],
  );

  useTouchGestures(
    controllerRef,
    videoRef,
    seekable,
    setSpeedHold,
    setSeekPreview,
    handleLevelChange,
    readLevel,
  );

  useFullscreenScrollRestore(controllerRef);
  useFullscreenLandscape(controllerRef);

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

  const endVolumeDrag = useCallback(() => setVolumeDragging(false), []);
  const dragGuard = useDragGestureGuard(controllerRef, endVolumeDrag);
  const handleDragStart = dragGuard.onDragStart;

  const startVolumeDrag = useCallback(() => {
    setVolumeDragging(true);
    handleDragStart();
  }, [handleDragStart]);

  const openVolumePanel = useCallback(() => setVolumePanelOpen(true), []);

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
        // 音量与静音偏好只留本播放器自己的一份, 见 VOLUME_PREF_KEY.
        noVolumePref
        noMutedPref
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
        {/* 亮度是叠加层, 由竖直滑动写入. */}
        <div className={classes.dim} style={{ opacity: 1 - brightness }} aria-hidden="true" />
        <div className={classes.scrim} aria-hidden="true" />
        {/* 触屏上的居中大按钮: 与控制条同进同出 (media-chrome 把控制器内的非媒体子节点一起淡出). */}
        {coarsePointer ? (
          <div className={classes.centerPlayLayer}>
            <button
              type="button"
              className={classes.centerPlay}
              aria-label={t("detail.playbackToggle")}
              onClick={() => togglePlayback(videoRef.current)}
            >
              <span className={classes.centerPlayIcon}>
                <IconPlayerPlayFilled size={40} />
              </span>
              <span className={classes.centerPauseIcon}>
                <IconPlayerPauseFilled size={40} />
              </span>
            </button>
          </div>
        ) : null}
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
            {/* 触屏上不铺拖动的滑杆: 音量走竖直滑动 (见 useTouchGestures), 按钮只管静音.
                判据是指针类型而不是宽度 — 鼠标在窄窗口里既没有滑杆也没有触屏手势, 用宽度判定会让他两头落空. */}
            {coarsePointer ? null : (
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
                  onChange={setVolumeFromUser}
                  label={(value) => `${Math.round(value * 100)}%`}
                  aria-label={t("detail.playbackVolume")}
                  color="brand"
                  styles={{ track: { backgroundColor: "rgb(255 255 255 / 0.32)" } }}
                />
              </div>
            )}
          </div>
          <div
            className={classes.popupHost}
            onClickCapture={interceptRateButtonClick}
            onPointerEnter={openRateMenu}
            onPointerLeave={scheduleRateClose}
          >
            <MediaPlaybackRateMenuButton ref={rateButtonRef} invokeTarget={RATE_MENU_ID} />
            {coarsePointer ? null : (
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
            )}
          </div>
          {tracks.length > 0 ? <MediaCaptionsMenuButton /> : null}
          <MediaFullscreenButton ref={fullscreenButtonRef} />
          {tracks.length > 0 ? <MediaCaptionsMenu hidden /> : null}
        </MediaControlBar>
        {seekable ? <MediaTimeRange onPointerDown={handleDragStart} /> : null}
        {/* 触屏倍速: 右侧滑出面板. 放在控制器内, 因此全屏下同样可见 (全屏只渲染控制器子树). */}
        {rateSheetVisible ? (
          <>
            <button
              type="button"
              className={classes.sheetBackdrop}
              onClick={closeRateSheet}
              aria-label={t("detail.playbackClose")}
            />
            <div className={classes.rateSheet} role="menu" aria-label={t("detail.playbackRate")}>
              <div className={classes.rateSheetTitle}>{t("detail.playbackRate")}</div>
              {PLAYBACK_RATES.map((rate) => (
                <button
                  key={rate}
                  type="button"
                  role="menuitemradio"
                  aria-checked={rate === playbackRate}
                  className={classes.rateSheetItem}
                  data-active={rate === playbackRate ? "true" : undefined}
                  onClick={() => {
                    applyPlaybackRate(rate);
                    closeRateSheet();
                  }}
                >
                  {`${rate}x`}
                </button>
              ))}
            </div>
          </>
        ) : null}
        {contextMenu != null ? (
          <ContextMenu
            anchor={contextMenu}
            seekable={seekable}
            readSeconds={readSeconds}
            onClose={closeContextMenu}
          />
        ) : null}
        {/* 调整提示: 键盘调音量 / 静音与触屏竖直滑动 (音量或亮度) 时显示.
          三个提示层都留在控制器内: 全屏只渲染控制器子树, 放在外面会随全屏消失; 它们不是 media-chrome
          控件, 控件自动隐藏与它们无关. */}
        {hud != null ? (
          <div className={classes.volumeIndicatorLayer}>
            <div className={classes.volumeIndicator} role="status" aria-live="polite">
              {hud.kind === "volume" ? (
                hud.level === 0 ? (
                  <IconVolumeOff size={16} />
                ) : (
                  <IconVolume size={16} />
                )
              ) : (
                <IconSun size={16} />
              )}
              <div className={classes.volumeIndicatorBar}>
                <div
                  className={classes.volumeIndicatorFill}
                  style={{ width: `${Math.round(hud.level * 100)}%` }}
                />
              </div>
              <span>
                {hud.kind === "volume" && hud.level === 0
                  ? t("detail.playbackMuted")
                  : `${Math.round(hud.level * 100)}%`}
              </span>
            </div>
          </div>
        ) : null}
        {/* 按住加速的标记: 触屏长按与键盘按住都会置位, 从菜单里选的倍速不显示. */}
        {speedHold ? (
          <div className={classes.speedBadgeLayer}>
            <div className={classes.speedBadge} role="status" aria-live="polite">
              {`${HOLD_SEEK_RATE}×`}
            </div>
          </div>
        ) : null}
        {/* 横滑拖进度: 目标时间 / 总长度. */}
        {seekPreview != null ? (
          <div className={classes.volumeIndicatorLayer}>
            <div
              className={`${classes.volumeIndicator} ${classes.gestureIndicator}`}
              role="status"
              aria-live="polite"
            >
              {seekPreview.total == null
                ? formatClock(seekPreview.target)
                : `${formatClock(seekPreview.target)} / ${formatClock(seekPreview.total)}`}
            </div>
          </div>
        ) : null}
      </MediaController>
    </>
  );
}
