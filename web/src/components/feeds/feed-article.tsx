import { Anchor, Badge, Box, Checkbox, Group, Menu, Stack, Text } from "@mantine/core";
import {
  IconArchive,
  IconArchiveOff,
  IconChevronDown,
  IconChevronRight,
  IconDots,
  IconExternalLink,
  IconMail,
  IconMailOpened,
  IconRefresh,
  IconTrash,
} from "@tabler/icons-react";
import { Link } from "@tanstack/react-router";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import type { FeedItemResponse, FeedResponse } from "@/client/types.gen";
import { HintedActionIcon } from "@/components/common/hinted-action-icon";
import { useNarrowViewport } from "@/hooks/use-narrow-viewport";
import { feedDisplayName } from "@/lib/feeds/groups";
import { feedHtmlPlainText } from "@/lib/feeds/html";
import classes from "./feed-article.module.css";
import { FeedHtml } from "./feed-html";

function itemNumber(item: FeedItemResponse): string | null {
  if (item.number == null || item.number === "") {
    return null;
  }
  return item.number;
}

export function FeedArticle({
  item,
  feed,
  expanded,
  selected,
  duplicateCount,
  showFeedName,
  busy,
  onToggleExpand,
  onToggleSelect,
  onScrape,
  onIgnore,
  onUnignore,
  onMarkRead,
  onMarkUnread,
  onDelete,
  onOpenFeed,
}: {
  item: FeedItemResponse;
  feed: FeedResponse | undefined;
  expanded: boolean;
  selected: boolean;
  duplicateCount: number;
  showFeedName: boolean;
  busy: boolean;
  onToggleExpand: () => void;
  onToggleSelect: () => void;
  onScrape: () => void;
  onIgnore: () => void;
  onUnignore: () => void;
  onMarkRead: () => void;
  onMarkUnread: () => void;
  onDelete: () => void;
  onOpenFeed: (feed: FeedResponse) => void;
}) {
  const { t } = useTranslation(["feeds", "common"]);
  const narrowViewport = useNarrowViewport("md");
  const number = itemNumber(item);
  const preview = useMemo(() => feedHtmlPlainText(item.description ?? ""), [item.description]);
  const inLibrary = item.metadata_id != null;
  const unread = item.read_at == null;
  const stamp = item.published_at ?? item.created_at;
  // 窄屏的时间戳省掉年份与秒: 长日期会把元信息行挤成两行.
  const stampText = narrowViewport
    ? new Date(stamp).toLocaleString(undefined, {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      })
    : new Date(stamp).toLocaleString();

  // 窄屏把四个图标收进一个菜单: 图标并排会把标题挤到只剩一百来像素.
  const actions = narrowViewport ? (
    <Menu position="bottom-end" withinPortal>
      <Menu.Target>
        {/* 头部是展开/收起的热区, 菜单目标必须拦住冒泡, 否则点击会连带展开条目. */}
        <HintedActionIcon
          variant="subtle"
          disabled={busy}
          label={t("reader.itemActions")}
          onClick={(event) => event.stopPropagation()}
        >
          <IconDots size={16} />
        </HintedActionIcon>
      </Menu.Target>
      <Menu.Dropdown>
        {number != null && (
          <Menu.Item leftSection={<IconRefresh size={14} />} onClick={onScrape}>
            {t("actions.rescrape")}
          </Menu.Item>
        )}
        <Menu.Item
          leftSection={unread ? <IconMailOpened size={14} /> : <IconMail size={14} />}
          onClick={unread ? onMarkRead : onMarkUnread}
        >
          {unread ? t("actions.markRead") : t("actions.markUnread")}
        </Menu.Item>
        <Menu.Item
          leftSection={
            item.ignored_at == null ? <IconArchive size={14} /> : <IconArchiveOff size={14} />
          }
          onClick={item.ignored_at == null ? onIgnore : onUnignore}
        >
          {item.ignored_at == null ? t("actions.ignore") : t("actions.unignore")}
        </Menu.Item>
        <Menu.Item color="red" leftSection={<IconTrash size={14} />} onClick={onDelete}>
          {t("common:actions.delete")}
        </Menu.Item>
      </Menu.Dropdown>
    </Menu>
  ) : (
    <Group
      gap={2}
      wrap="nowrap"
      className={classes.actions}
      onClick={(event) => event.stopPropagation()}
    >
      {number != null && (
        <HintedActionIcon
          variant="subtle"
          disabled={busy}
          label={t("actions.rescrape")}
          onClick={onScrape}
        >
          <IconRefresh size={16} />
        </HintedActionIcon>
      )}
      <HintedActionIcon
        variant="subtle"
        disabled={busy}
        label={unread ? t("actions.markRead") : t("actions.markUnread")}
        onClick={unread ? onMarkRead : onMarkUnread}
      >
        {unread ? <IconMailOpened size={16} /> : <IconMail size={16} />}
      </HintedActionIcon>
      <HintedActionIcon
        variant="subtle"
        disabled={busy}
        label={item.ignored_at == null ? t("actions.ignore") : t("actions.unignore")}
        onClick={item.ignored_at == null ? onIgnore : onUnignore}
      >
        {item.ignored_at == null ? <IconArchive size={16} /> : <IconArchiveOff size={16} />}
      </HintedActionIcon>
      <HintedActionIcon
        variant="subtle"
        color="red"
        disabled={busy}
        label={t("common:actions.delete")}
        onClick={onDelete}
      >
        <IconTrash size={16} />
      </HintedActionIcon>
    </Group>
  );

  return (
    <Box
      className={classes.row}
      data-unread={unread ? "true" : undefined}
      data-selected={selected ? "true" : undefined}
      data-expanded={expanded ? "true" : undefined}
    >
      <Stack gap={6} p="sm" className={classes.card}>
        <Group
          wrap="nowrap"
          align="flex-start"
          gap="sm"
          className={classes.head}
          style={{ cursor: "pointer" }}
          onClick={onToggleExpand}
        >
          <Checkbox
            mt={4}
            checked={selected}
            disabled={busy}
            onClick={(event) => event.stopPropagation()}
            onChange={onToggleSelect}
          />
          <Box mt={2} c="dimmed" style={{ pointerEvents: "none", display: "flex" }}>
            {expanded ? <IconChevronDown size={16} /> : <IconChevronRight size={16} />}
          </Box>
          <Stack gap={4} style={{ flex: 1, minWidth: 0 }}>
            <Text
              fw={unread ? 700 : 500}
              size="sm"
              lineClamp={expanded ? undefined : 2}
              c={unread ? undefined : "dimmed"}
            >
              {item.title || item.item_key}
            </Text>
            <Group gap="xs" wrap="wrap">
              {showFeedName && feed != null && (
                <Anchor
                  component="button"
                  type="button"
                  size="xs"
                  c="dimmed"
                  onClick={(event) => {
                    event.stopPropagation();
                    onOpenFeed(feed);
                  }}
                >
                  {feedDisplayName(feed)}
                </Anchor>
              )}
              {/* 窄屏省略"无番号"占位: 该占位只用于标签对齐. */}
              {number == null ? (
                narrowViewport ? null : (
                  <Text size="xs" c="dimmed">
                    {t("labels.noNumber")}
                  </Text>
                )
              ) : inLibrary && item.metadata_id != null ? (
                <Link
                  to="/meta/$metadataId"
                  params={{ metadataId: String(item.metadata_id) }}
                  style={{ textDecoration: "none" }}
                  onClick={(event) => event.stopPropagation()}
                >
                  <Group gap={6} wrap="nowrap">
                    <Text size="xs" ff="monospace" c="blue">
                      {number}
                    </Text>
                    <Badge size="xs" variant="light" color="teal">
                      {t("labels.inLibrary")}
                    </Badge>
                  </Group>
                </Link>
              ) : (
                <Group gap={6} wrap="nowrap">
                  <Text size="xs" ff="monospace">
                    {number}
                  </Text>
                  <Badge size="xs" variant="light" color="gray">
                    {t("labels.notInLibrary")}
                  </Badge>
                </Group>
              )}
              {item.ignored_at != null && (
                <Badge size="xs" variant="light" color="gray">
                  {t("labels.ignored")}
                </Badge>
              )}
              {duplicateCount > 0 && (
                <Badge size="xs" variant="light">
                  {t("reader.duplicates", { count: duplicateCount })}
                </Badge>
              )}
              <Text size="xs" c="dimmed">
                {stampText}
              </Text>
              {item.link != null && item.link !== "" && (
                <Anchor
                  href={item.link}
                  target="_blank"
                  rel="noreferrer"
                  size="xs"
                  onClick={(event) => event.stopPropagation()}
                  aria-label={narrowViewport ? t("reader.openLink") : undefined}
                >
                  <Group gap={4} wrap="nowrap">
                    <IconExternalLink size={12} />
                    {/* 窄屏只留图标: 文案会把元信息行撑到第二行. */}
                    {narrowViewport ? null : t("reader.openLink")}
                  </Group>
                </Anchor>
              )}
            </Group>
            {!expanded && preview !== "" && (
              <Text size="sm" c="dimmed" lineClamp={2}>
                {preview}
              </Text>
            )}
          </Stack>
          {actions}
        </Group>
        {expanded ? (
          <div className={classes.body}>
            <FeedHtml html={item.description} />
          </div>
        ) : null}
      </Stack>
    </Box>
  );
}
