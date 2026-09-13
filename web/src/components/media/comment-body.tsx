import classes from "./comment-body.module.css";
import { Text, Tooltip } from "@mantine/core";
import { Fragment } from "react";
import { useTranslation } from "react-i18next";

import { splitCommentTimestamps } from "@/lib/media/comment-timestamps";

/**
 * 评论正文: 其中的时间戳渲染为可点击的跳转.
 *
 * 当前没有可跳转的播放时置灰, 悬浮说明给出原因 (与可见文案不同, 因此允许包 Tooltip).
 */
export function CommentBody({
  body,
  canSeek,
  onSeek,
}: {
  body: string;
  canSeek: boolean;
  onSeek: (seconds: number) => void;
}) {
  const { t } = useTranslation("metadata");
  return (
    <Text size="sm" className={classes.body}>
      {splitCommentTimestamps(body).map((segment, index) =>
        segment.kind === "text" ? (
          <Fragment key={index}>{segment.text}</Fragment>
        ) : canSeek ? (
          <Tooltip key={index} label={t("detail.commentSeek", { time: segment.text })}>
            <button
              type="button"
              className={classes.timestamp}
              onClick={() => onSeek(segment.seconds)}
            >
              {segment.text}
            </button>
          </Tooltip>
        ) : (
          <Tooltip key={index} label={t("detail.playbackUnavailable")}>
            <span className={classes.disabledTimestamp}>{segment.text}</span>
          </Tooltip>
        ),
      )}
    </Text>
  );
}
