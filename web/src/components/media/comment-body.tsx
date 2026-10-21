import { Anchor, Tooltip } from "@mantine/core";
import type { Element } from "hast";
import { memo, useMemo } from "react";
import { useTranslation } from "react-i18next";
import type { Components, Options } from "react-markdown";

import { MarkdownContent } from "@/components/markdown/markdown-content";
import { rehypeCommentTimestamps } from "@/lib/media/comment-markdown";

import classes from "./comment-body.module.css";

const REHYPE_PLUGINS: Options["rehypePlugins"] = [rehypeCommentTimestamps];

type CommentBodyProps = {
  body: string;
  canSeek: boolean;
  onSeek: (seconds: number) => void;
  /** 预览中只保留时间戳的样式, 不响应点击, 也不提示播放状态. */
  interactive?: boolean;
  /** 预览区自带内边距, 去掉正文上方的额外间距. */
  compact?: boolean;
};

type SeekInfo = { seconds: number; time: string };

function readSeek(node: Readonly<Element> | undefined): SeekInfo | null {
  const seconds = node?.properties["data-seek"];
  const time = node?.properties["data-time"];
  if (typeof seconds !== "number" || typeof time !== "string") return null;
  if (!Number.isSafeInteger(seconds) || seconds < 0) return null;
  return { seconds, time };
}

/**
 * 评论正文: 按 GFM 渲染, 时间戳渲染为跳转入口, 链接在新标签页打开.
 *
 * 原始 HTML 由 `react-markdown` 转义; 图片不自动加载, 避免绕过图片代理与撑破卡片. 当前没有可跳转的
 * 播放时置灰, 悬浮说明给出原因 (与可见文案不同, 因此允许包 Tooltip).
 */
export const CommentBody = memo(function CommentBody({
  body,
  canSeek,
  onSeek,
  interactive = true,
  compact = false,
}: CommentBodyProps) {
  const { t } = useTranslation("metadata");
  const components = useMemo<Components>(
    () => ({
      span: ({ node, children }) => {
        const seek = readSeek(node);
        if (seek == null) {
          return <span>{children}</span>;
        }
        if (!interactive) {
          return <span className={classes.timestampStatic}>{children}</span>;
        }
        return canSeek ? (
          <Tooltip label={t("detail.commentSeek", { time: seek.time })}>
            <button
              type="button"
              className={classes.timestamp}
              onClick={() => onSeek(seek.seconds)}
            >
              {children}
            </button>
          </Tooltip>
        ) : (
          <Tooltip label={t("detail.playbackUnavailable")}>
            <span className={classes.disabledTimestamp}>{children}</span>
          </Tooltip>
        );
      },
      a: ({ href, title, children }) =>
        href ? (
          <Anchor
            className={classes.link}
            href={href}
            title={title}
            target="_blank"
            rel="noreferrer"
          >
            {children}
          </Anchor>
        ) : (
          <>{children}</>
        ),
      img: ({ src, alt }) =>
        src ? (
          <Anchor className={classes.link} href={src} target="_blank" rel="noreferrer">
            {alt || src}
          </Anchor>
        ) : null,
    }),
    [canSeek, interactive, onSeek, t],
  );

  return (
    <MarkdownContent
      text={body}
      className={compact ? `${classes.body} ${classes.bodyCompact}` : classes.body}
      components={components}
      rehypePlugins={REHYPE_PLUGINS}
    />
  );
});
