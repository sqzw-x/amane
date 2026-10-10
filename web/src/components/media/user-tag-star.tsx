/**
 * 详情页的收藏星标: 单击挂载 / 移除固定名称的收藏标签.
 *
 * 状态按名称从条目自身的标签判定; 挂载端点只认标签 id, 已收藏时该 id 就在条目里, 未收藏时取自
 * 标签目录 (`useFavoriteTag`), 目录里还没有这枚标签则按需创建. 目录未就绪前禁用点击, 避免在
 * 「未收藏」与「还不知道」之间做出错误提交. 单击即提交, 不做二次确认: 误触再点一次即可还原.
 */

import { ActionIcon } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconStar, IconStarOff } from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { extractErrorMessage } from "@/lib/api-error";
import { favoriteTagId, useFavoriteTag } from "@/lib/media/user-tags";

interface StarToggleProps {
  favorited: boolean;
  disabled: boolean;
  /** 挂载或移除收藏标签; 失败时抛出, 由星标回滚乐观状态. */
  onChange: (favorited: boolean) => Promise<void>;
}

function StarToggle({ favorited, disabled, onChange }: StarToggleProps) {
  const { t } = useTranslation(["metadata", "common"]);
  const [optimistic, setOptimistic] = useState<boolean | null>(null);
  const [pending, setPending] = useState(false);
  const shown = optimistic ?? favorited;
  const label = shown ? t("detail.unfavorite") : t("detail.favorite");

  async function toggle() {
    const next = !shown;
    setOptimistic(next);
    setPending(true);
    try {
      await onChange(next);
    } catch (err) {
      setOptimistic(null);
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      });
    } finally {
      setPending(false);
    }
  }

  return (
    <ActionIcon
      variant="subtle"
      color={shown ? "yellow" : "gray"}
      size={28}
      disabled={disabled || pending}
      aria-label={label}
      aria-pressed={shown}
      onClick={() => void toggle()}
    >
      {shown ? <IconStar size={18} fill="currentColor" /> : <IconStarOff size={18} />}
    </ActionIcon>
  );
}

/**
 * 条目标题行的收藏星标.
 *
 * `tags` 是条目自身的用户标签 (详情响应), `apply` 把标签 id 挂到该条目上 —— 影片与演员的挂载端点
 * 不同, 由调用方给出; `queryKey` 是详情查询的 key, 提交后据此重取.
 */
export function FavoriteStarToggle({
  tags,
  apply,
  queryKey,
}: {
  tags: ReadonlyArray<{ id: number; name: string }> | null | undefined;
  apply: (favorited: boolean, tagId: number) => Promise<unknown>;
  queryKey: readonly unknown[];
}) {
  const queryClient = useQueryClient();
  const { loading, resolve } = useFavoriteTag();

  const attachedId = favoriteTagId(tags);
  const attached = attachedId != null;

  async function onChange(favorited: boolean) {
    if (!favorited) {
      if (attachedId == null) return;
      await apply(false, attachedId);
    } else {
      const target = await resolve();
      if (target == null) return;
      await apply(true, target);
    }
    await queryClient.invalidateQueries({ queryKey });
  }

  // 未收藏时由 `resolve` 兜底创建标签, 因此只有目录尚未加载时才禁用.
  return <StarToggle favorited={attached} disabled={loading} onChange={onChange} />;
}
