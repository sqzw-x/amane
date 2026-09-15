import { Badge, Button, Card, Group, Stack, Text, Textarea, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconPencil, IconSend, IconTrash } from "@tabler/icons-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";

import {
  createCommentMutation,
  deleteCommentMutation,
  getMetadataQueryKey,
  updateCommentMutation,
} from "@/client/@tanstack/react-query.gen";
import type { CommentResponse } from "@/client/types.gen";
import { EnumToggle } from "@/components/common/enum-toggle";
import { HintedActionIcon } from "@/components/common/hinted-action-icon";
import { extractErrorMessage } from "@/lib/api-error";
import { confirm } from "@/lib/confirm";
import { formatRelativeTime } from "@/lib/format-relative-time";

import { CommentBody } from "./comment-body";
import classes from "./comment-section.module.css";

/** 与后端 `CommentBody` 的上限一致. */
const MAX_BODY_LENGTH = 10000;

const SORTS = ["newest", "oldest"] as const;
type CommentSort = (typeof SORTS)[number];

type CommentSectionProps = {
  metadataId: number;
  comments: CommentResponse[];
  canSeek: boolean;
  onSeek: (seconds: number) => void;
};

type CommentRow = {
  comment: CommentResponse;
  /** 楼号按创建顺序固定, 不随当前排序变化. */
  floor: number;
  /** 排序键: 编辑时间, 未编辑时等于创建时间. */
  editedAt: number;
};

function buildRows(comments: CommentResponse[], sort: CommentSort): CommentRow[] {
  const byCreated = comments.toSorted(
    (a, b) => Date.parse(a.created_at) - Date.parse(b.created_at) || a.id - b.id,
  );
  const rows: CommentRow[] = byCreated.map((comment, index) => ({
    comment,
    floor: index + 1,
    editedAt: Date.parse(comment.updated_at),
  }));
  const direction = sort === "newest" ? -1 : 1;
  rows.sort(
    (a, b) => direction * (a.editedAt - b.editedAt) || direction * (a.comment.id - b.comment.id),
  );
  return rows;
}

function isEdited(comment: CommentResponse): boolean {
  return Date.parse(comment.updated_at) > Date.parse(comment.created_at);
}

/** Ctrl/Cmd + Enter 提交, 其余按键交回输入框. */
function submitOnShortcut(event: KeyboardEvent<HTMLTextAreaElement>, submit: () => void) {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
    event.preventDefault();
    submit();
  }
}

/**
 * 评论区: 列表 + 发表与编辑.
 *
 * 数据来自影片详情响应, 改动后失效该详情查询; 排序与编辑态只存在组件内, 不写入地址栏.
 */
export function CommentSection({ metadataId, comments, canSeek, onSeek }: CommentSectionProps) {
  const { t, i18n } = useTranslation(["metadata", "common"]);
  const queryClient = useQueryClient();
  const [sort, setSort] = useState<CommentSort>("newest");
  const [body, setBody] = useState("");
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editBody, setEditBody] = useState("");
  const rows = useMemo(() => buildRows(comments, sort), [comments, sort]);
  const locale = i18n.language;

  function invalidateDetail() {
    void queryClient.invalidateQueries({
      queryKey: getMetadataQueryKey({ path: { metadata_id: metadataId } }),
    });
  }

  function notifyError(error: unknown) {
    notifications.show({
      message: extractErrorMessage(error, t("common:toast.operationFailed")),
      color: "red",
    });
  }

  const createComment = useMutation({
    ...createCommentMutation(),
    onSuccess: () => {
      notifications.show({ message: t("common:toast.commentCreated"), color: "blue" });
      setBody("");
      invalidateDetail();
    },
    onError: notifyError,
  });

  const updateComment = useMutation({
    ...updateCommentMutation(),
    onSuccess: () => {
      notifications.show({ message: t("common:toast.commentUpdated"), color: "blue" });
      setEditingId(null);
      invalidateDetail();
    },
    onError: notifyError,
  });

  const deleteComment = useMutation({
    ...deleteCommentMutation(),
    onSuccess: () => {
      notifications.show({ message: t("common:toast.commentDeleted"), color: "blue" });
      setEditingId(null);
      invalidateDetail();
    },
    onError: notifyError,
  });

  function submitComment() {
    const trimmed = body.trim();
    if (trimmed.length === 0) return;
    createComment.mutate({ path: { metadata_id: metadataId }, body: { body: trimmed } });
  }

  function saveEdit(comment: CommentResponse) {
    const trimmed = editBody.trim();
    if (trimmed.length === 0 || trimmed === comment.body) return;
    updateComment.mutate({ path: { comment_id: comment.id }, body: { body: trimmed } });
  }

  async function removeComment(comment: CommentResponse) {
    const ok = await confirm({
      title: t("detail.deleteCommentTitle"),
      message: t("detail.deleteCommentMessage"),
      confirmLabel: t("common:actions.delete"),
    });
    if (!ok) return;
    deleteComment.mutate({ path: { comment_id: comment.id } });
  }

  return (
    <Card withBorder radius="md" p="md" className={classes.card}>
      <Group justify="space-between" align="center" wrap="nowrap" gap="sm">
        <Group gap="xs" align="center" wrap="nowrap">
          <Title order={5}>{t("detail.comments")}</Title>
          {comments.length > 0 && (
            <Badge size="sm" variant="light" color="gray" radius="sm">
              {comments.length}
            </Badge>
          )}
        </Group>
        {comments.length > 1 && (
          <Group gap="xs" align="center" wrap="nowrap">
            <Text size="xs" c="dimmed">
              {t("detail.commentSortLabel")}
            </Text>
            <EnumToggle
              options={SORTS}
              value={sort}
              onChange={setSort}
              getLabel={(option) =>
                option === "newest" ? t("detail.commentSortNewest") : t("detail.commentSortOldest")
              }
            />
          </Group>
        )}
      </Group>

      {rows.length > 0 && (
        <Stack gap={0} mt="xs">
          {rows.map(({ comment, floor }) => (
            <div key={comment.id} className={classes.item}>
              {/* 相对时间与「已编辑」在窄屏折行, 否则英文文案会把行内操作顶出卡片. */}
              <Group gap={8} align="center" wrap="wrap">
                <span className={classes.floor}>#{floor}</span>
                <Text size="xs" c="dimmed">
                  {formatRelativeTime(comment.created_at, locale, t("detail.commentJustNow"))}
                  {isEdited(comment) &&
                    ` ${t("detail.commentEditedAt", {
                      time: formatRelativeTime(
                        comment.updated_at,
                        locale,
                        t("detail.commentJustNow"),
                      ),
                    })}`}
                </Text>
                <Group gap={2} align="center" wrap="nowrap" className={classes.actions}>
                  <HintedActionIcon
                    size="sm"
                    variant="subtle"
                    color="gray"
                    label={t("detail.editComment")}
                    onClick={() => {
                      setEditingId(comment.id);
                      setEditBody(comment.body);
                    }}
                  >
                    <IconPencil size={14} />
                  </HintedActionIcon>
                  <HintedActionIcon
                    size="sm"
                    variant="subtle"
                    color="red"
                    label={t("common:actions.delete")}
                    onClick={() => void removeComment(comment)}
                  >
                    <IconTrash size={14} />
                  </HintedActionIcon>
                </Group>
              </Group>

              {editingId === comment.id ? (
                <Stack gap={6} mt={8}>
                  <Textarea
                    value={editBody}
                    onChange={(event) => setEditBody(event.currentTarget.value)}
                    aria-label={t("detail.editComment")}
                    autosize
                    minRows={2}
                    maxRows={10}
                    maxLength={MAX_BODY_LENGTH}
                    autoFocus
                    onKeyDown={(event) => {
                      if (event.key === "Escape") {
                        setEditingId(null);
                        return;
                      }
                      submitOnShortcut(event, () => saveEdit(comment));
                    }}
                  />
                  <Group justify="space-between" align="center" gap="sm">
                    <Group gap="xs" align="center">
                      <Button
                        size="compact-sm"
                        variant="default"
                        onClick={() => setEditingId(null)}
                      >
                        {t("common:actions.cancel")}
                      </Button>
                      <Button
                        size="compact-sm"
                        loading={updateComment.isPending}
                        disabled={editBody.trim().length === 0 || editBody.trim() === comment.body}
                        onClick={() => saveEdit(comment)}
                      >
                        {t("common:actions.save")}
                      </Button>
                    </Group>
                    <Text size="xs" c="dimmed">
                      {editBody.length} / {MAX_BODY_LENGTH}
                    </Text>
                  </Group>
                </Stack>
              ) : (
                <CommentBody body={comment.body} canSeek={canSeek} onSeek={onSeek} />
              )}
            </div>
          ))}
        </Stack>
      )}

      <Stack gap={6} pt="md">
        <Textarea
          value={body}
          onChange={(event) => setBody(event.currentTarget.value)}
          placeholder={t("detail.commentPlaceholder")}
          autosize
          minRows={2}
          maxRows={8}
          maxLength={MAX_BODY_LENGTH}
          onKeyDown={(event) => submitOnShortcut(event, submitComment)}
        />
        <Group justify="space-between" align="center" gap="sm">
          <Button
            size="compact-sm"
            leftSection={<IconSend size={14} />}
            loading={createComment.isPending}
            disabled={body.trim().length === 0}
            onClick={submitComment}
          >
            {t("detail.addComment")}
          </Button>
          <Text size="xs" c="dimmed">
            {body.length} / {MAX_BODY_LENGTH}
          </Text>
        </Group>
      </Stack>
    </Card>
  );
}
