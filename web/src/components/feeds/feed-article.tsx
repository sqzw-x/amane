import { Anchor, Badge, Box, Checkbox, Group, Stack, Text } from "@mantine/core";
import {
  IconArchive,
  IconArchiveOff,
  IconChevronDown,
  IconChevronRight,
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
  const number = itemNumber(item);
  const preview = useMemo(() => feedHtmlPlainText(item.description ?? ""), [item.description]);
  const inLibrary = item.metadata_id != null;
  const unread = item.read_at == null;

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
              {number == null ? (
                <Text size="xs" c="dimmed">
                  {t("labels.noNumber")}
                </Text>
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
                {new Date(item.published_at ?? item.created_at).toLocaleString()}
              </Text>
              {item.link != null && item.link !== "" && (
                <Anchor
                  href={item.link}
                  target="_blank"
                  rel="noreferrer"
                  size="xs"
                  onClick={(event) => event.stopPropagation()}
                >
                  <Group gap={4} wrap="nowrap">
                    <IconExternalLink size={12} />
                    {t("reader.openLink")}
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
          <Group gap={2} wrap="nowrap" onClick={(event) => event.stopPropagation()}>
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
        </Group>
        {expanded ? (
          <div style={{ paddingLeft: 52 }}>
            <FeedHtml html={item.description} />
          </div>
        ) : null}
      </Stack>
    </Box>
  );
}
