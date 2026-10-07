import { notifications } from "@mantine/notifications";
import { useMutation, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  deleteFacetMutation,
  deleteFacetRuleMutation,
  listFacetRulesQueryKey,
  mergeFacetsMutation,
  renameFacetMutation,
} from "@/client/@tanstack/react-query.gen";
import type { FacetKind } from "@/client/types.gen";
import { extractErrorMessage } from "@/lib/api-error";
import { confirm } from "@/lib/confirm";

export type FacetNamedTarget = { id: number; name: string };

export interface UseFacetIdentityActionsOptions {
  kind: FacetKind;
  /** 列表缓存 key (actors → listActors; catalog → listFacets). */
  listQueryKey: QueryKey;
  /**
   * true (默认): openMerge 先经居中确认再提交.
   * false: openMerge 有来源时直接提交 (演员表).
   */
  confirmMerge?: boolean;
  /** 合并成功后清空多选. */
  onMerged?: () => void;
}

/**
 * 分类实体身份写操作 - rename / merge / delete (+ 可选删规则).
 * 简单确认经 `confirm()`; rename 仍由调用方渲染带输入的 Modal.
 */
export function useFacetIdentityActions({
  kind,
  listQueryKey,
  confirmMerge = true,
  onMerged,
}: UseFacetIdentityActionsOptions) {
  const { t } = useTranslation(["metadata", "common"]);
  const queryClient = useQueryClient();
  const isUserTag = kind === "user_tag";

  const [renameTarget, setRenameTarget] = useState<FacetNamedTarget | null>(null);
  const [renameValue, setRenameValue] = useState("");

  const invalidate = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: listQueryKey });
    if (!isUserTag) {
      void queryClient.invalidateQueries({ queryKey: listFacetRulesQueryKey({ path: { kind } }) });
    }
  }, [queryClient, listQueryKey, isUserTag, kind]);

  const fail = useCallback(
    (err: unknown) => {
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      });
    },
    [t],
  );

  const renameMutation = useMutation({
    ...renameFacetMutation(),
    onSuccess: () => {
      notifications.show({ message: t("common:toast.metadataUpdated"), color: "blue" });
      setRenameTarget(null);
      invalidate();
    },
    onError: fail,
  });

  const mergeMutation = useMutation({
    ...mergeFacetsMutation(),
    onSuccess: () => {
      notifications.show({ message: t("common:toast.metadataUpdated"), color: "blue" });
      onMerged?.();
      invalidate();
    },
    onError: fail,
  });

  const deleteMutation = useMutation({
    ...deleteFacetMutation(),
    onSuccess: () => {
      notifications.show({
        message: isUserTag ? t("common:toast.userTagDeleted") : t("common:toast.facetDeleted"),
        color: "blue",
      });
      invalidate();
    },
    onError: fail,
  });

  const deleteRuleMutation = useMutation({
    ...deleteFacetRuleMutation(),
    onSuccess: () => {
      notifications.show({ message: t("common:toast.facetRuleDeleted"), color: "blue" });
      invalidate();
    },
    onError: fail,
  });

  const openRename = useCallback((target: FacetNamedTarget) => {
    setRenameTarget(target);
    setRenameValue(target.name);
  }, []);

  const closeRename = useCallback(() => setRenameTarget(null), []);

  const submitRename = useCallback(() => {
    if (!renameTarget) return;
    const name = renameValue.trim();
    if (!name) return;
    renameMutation.mutate({
      path: { kind, facet_id: renameTarget.id },
      body: { name },
    });
  }, [renameTarget, renameValue, renameMutation, kind]);

  const openDelete = useCallback(
    async (target: FacetNamedTarget) => {
      const message = isUserTag
        ? t("manage.deleteUserTagBody", { name: target.name })
        : t("manage.deleteFacetBody", { name: target.name });
      const ok = await confirm({
        title: t("common:actions.delete"),
        message,
        confirmLabel: t("common:actions.delete"),
      });
      if (!ok) return;
      deleteMutation.mutate({ path: { kind, facet_id: target.id } });
    },
    [isUserTag, t, deleteMutation, kind],
  );

  const openMerge = useCallback(
    async (targetId: number, selected: Set<number>) => {
      const sources = [...selected].filter((id) => id !== targetId);
      if (sources.length === 0) {
        notifications.show({
          message: t("manage.mergeNeedSources"),
          color: "yellow",
        });
        return;
      }
      if (confirmMerge) {
        const ok = await confirm({
          title: t("manage.mergeConfirm"),
          message: t("manage.mergeConfirmBody", { count: sources.length }),
          confirmLabel: t("common:actions.confirm"),
          danger: false,
          confirmColor: "orange",
        });
        if (!ok) return;
      }
      mergeMutation.mutate({
        path: { kind },
        body: { target_id: targetId, source_ids: sources },
      });
    },
    [confirmMerge, mergeMutation, kind, t],
  );

  const busy = renameMutation.isPending || mergeMutation.isPending || deleteMutation.isPending;

  return {
    isUserTag,
    invalidate,
    renameTarget,
    renameValue,
    setRenameValue,
    openRename,
    closeRename,
    submitRename,
    renamePending: renameMutation.isPending,
    openDelete,
    deletePending: deleteMutation.isPending,
    openMerge,
    mergePending: mergeMutation.isPending,
    deleteRule: (ruleId: number) => deleteRuleMutation.mutate({ path: { kind, rule_id: ruleId } }),
    deleteRulePending: deleteRuleMutation.isPending,
    busy,
  };
}
