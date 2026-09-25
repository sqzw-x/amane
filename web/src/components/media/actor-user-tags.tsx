import { Group } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  batchActorUserTagsMutation,
  createUserTagsMutation,
  getActorQueryKey,
  listFacetsOptions,
  listFacetsQueryKey,
} from "@/client/@tanstack/react-query.gen";
import { FacetBadge } from "@/components/media/facet-badge";
import { UserTagActions } from "@/components/media/user-tag-add";
import { extractErrorMessage } from "@/lib/api-error";
import { USER_TAG_FACET_LIST } from "@/lib/facets";

/**
 * 演员详情的用户标签挂载.
 *
 * 新建与挂载各一次请求: 名称先经批量创建端点换成 id, 再与既有 id 一起提交给挂载端点.
 */
export function ActorUserTags({
  actorId,
  attached,
}: {
  actorId: number;
  attached: ReadonlyArray<{ id: number; name: string }>;
}) {
  const { t } = useTranslation(["metadata", "common"]);
  const queryClient = useQueryClient();
  const { data: userTagOptions } = useQuery(listFacetsOptions(USER_TAG_FACET_LIST));
  const ensureTagsMutation = useMutation(createUserTagsMutation());
  const applyTagsMutation = useMutation(batchActorUserTagsMutation());

  function invalidate() {
    void queryClient.invalidateQueries({
      queryKey: getActorQueryKey({ path: { actor_id: actorId } }),
    });
  }

  async function handleAddTags(selection: { tagIds: number[]; createNames: string[] }) {
    const names = [
      ...new Set(
        selection.createNames.map((name) => name.trim()).filter((name) => name.length > 0),
      ),
    ];
    if (selection.tagIds.length === 0 && names.length === 0) return;
    try {
      let createdIds: number[] = [];
      let created = 0;
      if (names.length > 0) {
        const ensured = await ensureTagsMutation.mutateAsync({ body: { names } });
        createdIds = ensured.items.map((tag) => tag.id);
        created = ensured.created;
        // 标签此刻已落库; created 为 0 也可能是别人刚建的同名行落在候选之外, 故一律重取
        void queryClient.invalidateQueries({ queryKey: listFacetsQueryKey(USER_TAG_FACET_LIST) });
      }
      await applyTagsMutation.mutateAsync({
        body: {
          ids: [actorId],
          user_tag_ids: [...selection.tagIds, ...createdIds],
          action: "attach",
        },
      });
      notifications.show({
        message: created > 0 ? t("common:toast.userTagCreated") : t("common:toast.userTagAttached"),
        color: "blue",
      });
      invalidate();
    } catch (err) {
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      });
    }
  }

  async function handleDetachTags(tagIds: number[]) {
    if (tagIds.length === 0) return;
    try {
      await applyTagsMutation.mutateAsync({
        body: { ids: [actorId], user_tag_ids: tagIds, action: "detach" },
      });
      notifications.show({ message: t("common:toast.userTagDetached"), color: "blue" });
      invalidate();
    } catch (err) {
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      });
    }
  }

  return (
    <Group gap={6} align="center" wrap="wrap">
      {attached.map((tag) => (
        <FacetBadge key={tag.id} kind="user_tag" id={tag.id} name={tag.name} />
      ))}
      <UserTagActions
        attached={attached}
        candidates={(userTagOptions?.items ?? []).filter(
          (tag) => !attached.some((linked) => linked.id === tag.id),
        )}
        onChoose={(selection) => void handleAddTags(selection)}
        onDetach={(ids) => void handleDetachTags(ids)}
        disabled={ensureTagsMutation.isPending || applyTagsMutation.isPending}
      />
    </Group>
  );
}
