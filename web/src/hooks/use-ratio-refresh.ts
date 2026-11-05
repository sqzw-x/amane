import { useCallback, useEffect, useRef, useState } from "react";

/**
 * 图片比例到达后请求重渲染.
 *
 * 按帧合并: 一次滚动或一屏首图会同时回填多张比例, 逐张重渲染会让整页连续抖动. 返回的函数可
 * 直接作为卡片的重渲染回调, 卸载时撤销尚未执行的那一帧.
 */
export function useRatioRefresh(): () => void {
  const [, forceRender] = useState(0);
  const frame = useRef<number | null>(null);

  const refresh = useCallback(() => {
    if (frame.current != null) return;
    frame.current = requestAnimationFrame(() => {
      frame.current = null;
      forceRender((tick) => tick + 1);
    });
  }, []);

  useEffect(
    () => () => {
      if (frame.current != null) cancelAnimationFrame(frame.current);
    },
    [],
  );

  return refresh;
}
