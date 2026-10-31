import { uploadResourceMutation } from "@/client/@tanstack/react-query.gen";
import { useAppMutation } from "@/hooks/use-app-mutation";

/**
 * 上传一张本地图片, 返回内部资源 URL.
 *
 * 上传只写 Resource, 不触碰任何字段 —— 返回的 URL 由调用方决定写入哪个字段. 失败提示由
 * `useAppMutation` 统一给出; 没有成功提示, 否则连续更换图片时会连成一片.
 */
export function useUploadImage() {
  return useAppMutation({
    mutationOptions: uploadResourceMutation(),
  });
}
