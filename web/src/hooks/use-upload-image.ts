import { uploadResourceMutation } from "@/client/@tanstack/react-query.gen";
import { useAppMutation } from "@/hooks/use-app-mutation";

/**
 * 上传一张本地图片, 返回内部资源 URL.
 *
 * 一次只提交一个文件: 调用方按文件循环, 失败提示由 `useAppMutation` 统一给出. 上传只写 Resource,
 * 不触碰任何字段 —— 返回的 URL 由调用方决定写进哪个列表, 与粘贴外链同处一列. 没有成功提示:
 * 多张图连发时会连成一片.
 */
export function useUploadImage() {
  return useAppMutation({
    mutationOptions: uploadResourceMutation(),
  });
}
