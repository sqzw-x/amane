import { lazy } from "react";

/**
 * 播放器与它的 media-chrome、hls.js 依赖一并动态导入: 这三者只在真正播放时才需要, 不得进入主包. 声明只留
 * 这一份, 供点播面板与详情页预告片共用, 两处引用的是同一个模块.
 *
 * 导入在点击之后发出, 开始播放与进入全屏都排在它后面; 进入全屏用的是点击的瞬时激活 (Chromium 约 5 秒), 链接
 * 慢到超过这个窗口时这次点击只得到内联播放. 详情页的点播面板通常已经加载过它, 这段等待只在没有可播流时出现.
 */
export const LazyPlaybackPlayer = lazy(() =>
  import("@/components/media/playback-player").then((module) => ({
    default: module.PlaybackPlayer,
  })),
);
