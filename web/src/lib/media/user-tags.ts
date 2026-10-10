/**
 * 收藏 = 一枚固定名称的用户标签, 影片与演员共用同一枚.
 *
 * 后端只认标签 id, 而该 id 由界面首次收藏时创建, 不是常量; 详情页与列表页因此都按名称判定, 再按
 * 目录里拿到的 id 挂载或筛选. 目录由 `lib/facets.ts` 的 `USER_TAG_FACET_LIST` 一次拉满.
 *
 * 标签名用界面上的显示名, 因此它在用户标签面板里自解释; 代价是用户自建的同类标签会被一并复用
 * (同名即同一枚), 改名或删除会让两侧同时失去收藏.
 */

import { useMutation, useQuery } from "@tanstack/react-query";
import {
  createUserTagsMutation,
  listFacetsOptions,
  listFacetsQueryKey,
} from "@/client/@tanstack/react-query.gen";
import { useQueryClient } from "@tanstack/react-query";
import type { UserTagResponse } from "@/client/types.gen";
import { USER_TAG_FACET_LIST } from "@/lib/facets";

export const FAVORITE_TAG_NAME = "收藏夹";

type NamedTag = Pick<UserTagResponse, "id" | "name">;

export function favoriteTagId(
  tags: ReadonlyArray<NamedTag> | null | undefined,
): number | undefined {
  return (tags ?? []).find((tag) => tag.name === FAVORITE_TAG_NAME)?.id;
}

/** 收藏标签 id; `resolve` 在目录里还没有该标签时按需创建 (后端 `ensure_user_tags` 幂等). */
export function useFavoriteTag(): {
  tagId: number | undefined;
  loading: boolean;
  resolve: () => Promise<number | null>;
} {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery(listFacetsOptions(USER_TAG_FACET_LIST));
  const createTags = useMutation(createUserTagsMutation());
  const tagId = favoriteTagId(data?.items);

  async function resolve(): Promise<number | null> {
    if (tagId != null) return tagId;
    const ensured = await createTags.mutateAsync({ body: { names: [FAVORITE_TAG_NAME] } });
    const created = ensured.items[0]?.id ?? null;
    if (created != null) {
      void queryClient.invalidateQueries({ queryKey: listFacetsQueryKey(USER_TAG_FACET_LIST) });
    }
    return created;
  }

  return { tagId, loading: isLoading, resolve };
}
