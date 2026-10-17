import { lazy } from "react";

/**
 * 播放器与它的 media-chrome、hls.js 依赖一并动态导入: 这三者只在真正播放时才需要加载, 不得进入主包.
 * 导入声明只保留这一份, 供点播面板与详情页预告片共用, 两处引用的是同一个模块.
 *
 * 导入在点击之后才发起, 开始播放与进入全屏都排在其后; 进入全屏依赖点击的瞬时激活 (Chromium 约 5 秒),
 * 模块加载慢于该窗口时, 这次点击只能得到内联播放. 点播面板渲染播放器时该模块已经加载; 详情页上这段等待因此只出现在面板
 * 没有可播流 (因而未渲染播放器) 的场合.
 */
export const LazyPlaybackPlayer = lazy(() =>
  import("@/components/media/playback-player").then((module) => ({
    default: module.PlaybackPlayer,
  })),
);
