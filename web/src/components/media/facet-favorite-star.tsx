/**
 * 分类收藏控件: 单击把目标取值作为单个动作提交给 `POST /api/facets/{kind}/batch`.
 *
 * 两个动作都是整体赋值 (幂等), 界面因此不做二次确认, 误触再点一次即可还原. 提交期间按钮进入
 * loading, 不预置乐观值 — 取值只来自查询结果, 服务端返回什么就显示什么. 分类已不存在时端点仍
 * 返回成功并把该 id 计入 missing, 控件据此提示. 提交后失效列表与详情两族查询: 详情页读单个分类,
 * 只失效列表会让自身不刷新.
 */

import { notifications } from "@mantine/notifications";
import { IconStar } from "@tabler/icons-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { batchFacetsMutation, getFacetQueryKey } from "@/client/@tanstack/react-query.gen";
import type { FacetKind } from "@/client/types.gen";
import { HintedActionIcon } from "@/components/common/hinted-action-icon";
import { extractErrorMessage } from "@/lib/api-error";

export interface FacetFavoriteStarProps {
  kind: FacetKind;
  facetId: number;
  isFavorite: boolean;
}

export function FacetFavoriteStar({ kind, facetId, isFavorite }: FacetFavoriteStarProps) {
  const { t } = useTranslation(["metadata", "common"]);
  const queryClient = useQueryClient();

  const mutation = useMutation({
    ...batchFacetsMutation(),
    onSuccess: (res) => {
      if (res.missing > 0) {
        notifications.show({ message: t("favorite.missing"), color: "yellow" });
      }
      // 前缀失效覆盖该 kind 的任意筛选组合
      void queryClient.invalidateQueries({ queryKey: [{ _id: "listFacets" }] });
      void queryClient.invalidateQueries({
        queryKey: getFacetQueryKey({ path: { kind, facet_id: facetId } }),
      });
    },
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      }),
  });

  return (
    <HintedActionIcon
      variant="subtle"
      color={isFavorite ? "yellow" : "gray"}
      loading={mutation.isPending}
      aria-pressed={isFavorite}
      label={isFavorite ? t("favorite.remove") : t("favorite.add")}
      onClick={() =>
        mutation.mutate({
          path: { kind },
          body: { facet_ids: [facetId], action: isFavorite ? "unfavorite" : "favorite" },
        })
      }
    >
      {isFavorite ? <IconStar size={16} fill="currentColor" /> : <IconStar size={16} />}
    </HintedActionIcon>
  );
}

/** 词云徽章与索引页徽章内的只读收藏标记; 那两处没有动作位, 收藏在列表视图切换. */
export function FacetFavoriteMark() {
  return <IconStar size={12} fill="currentColor" aria-hidden />;
}
