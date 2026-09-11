import {
  ActionIcon,
  Badge,
  Box,
  Group,
  NavLink,
  ScrollArea,
  Text,
  TextInput,
  Tooltip,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import {
  IconChevronDown,
  IconChevronRight,
  IconFolder,
  IconRefresh,
  IconRss,
  IconSearch,
} from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useMemo, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { listAllFeedItemsQueryKey, listFeedsQueryKey } from "@/client/@tanstack/react-query.gen";
import { pollFeed } from "@/client/sdk.gen";
import type { FeedResponse } from "@/client/types.gen";
import { extractErrorMessage } from "@/lib/api-error";
import {
  ancestorGroupPaths,
  buildFeedTree,
  feedDisplayName,
  feedGroup,
  filterFeedsByQuery,
  sumFeedUnread,
  type FeedFolderNode,
  type FeedTreeNode,
  UNGROUPED_GROUP,
} from "@/lib/feeds/groups";

export type FeedSelection =
  | { kind: "all" }
  | { kind: "ungrouped" }
  | { kind: "group"; path: string }
  | { kind: "feed"; id: number };

function selectionOf(feedId: number | undefined, group: string | undefined): FeedSelection {
  if (feedId != null) {
    return { kind: "feed", id: feedId };
  }
  if (group === UNGROUPED_GROUP) {
    return { kind: "ungrouped" };
  }
  if (group != null && group !== "") {
    return { kind: "group", path: group };
  }
  return { kind: "all" };
}

function isSelected(selection: FeedSelection, candidate: FeedSelection): boolean {
  if (selection.kind !== candidate.kind) {
    return false;
  }
  if (selection.kind === "feed" && candidate.kind === "feed") {
    return selection.id === candidate.id;
  }
  if (selection.kind === "group" && candidate.kind === "group") {
    return selection.path === candidate.path;
  }
  return true;
}

function pathsToReveal(kind: FeedSelection["kind"], selectedPath: string): readonly string[] {
  const ancestors = ancestorGroupPaths(selectedPath);
  return kind === "feed" ? ancestors : ancestors.slice(0, -1);
}

function ManageIconButton({
  label,
  onClick,
  children,
}: {
  label: string;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <Tooltip label={label} withArrow>
      <ActionIcon
        variant="subtle"
        size="sm"
        color="gray"
        aria-label={label}
        onClick={(event) => {
          event.preventDefault();
          event.stopPropagation();
          onClick();
        }}
      >
        {children}
      </ActionIcon>
    </Tooltip>
  );
}

function UnreadCountBadge({ count }: { count: number }) {
  if (count <= 0) {
    return null;
  }
  return (
    <Badge size="xs" variant="filled" color="violet">
      {count}
    </Badge>
  );
}

function FeedLeafNav({
  feed,
  depth,
  selection,
  polling,
  onSelectFeed,
  onManage,
  onPoll,
}: {
  feed: FeedResponse;
  depth: number;
  selection: FeedSelection;
  polling: boolean;
  onSelectFeed: (feed: FeedResponse) => void;
  onManage: (feed: FeedResponse) => void;
  onPoll: (feed: FeedResponse) => void;
}) {
  const { t } = useTranslation("feeds");
  const unread = feed.unread_count ?? 0;
  return (
    <NavLink
      component="button"
      label={
        <Text span size="sm" fw={unread > 0 ? 700 : 500} lineClamp={1}>
          {feedDisplayName(feed)}
        </Text>
      }
      leftSection={
        <ManageIconButton label={t("actions.openInSources")} onClick={() => onManage(feed)}>
          <IconRss size={16} />
        </ManageIconButton>
      }
      rightSection={
        <Group gap={6} wrap="nowrap">
          <UnreadCountBadge count={unread} />
          <Tooltip label={t("actions.poll")} withArrow>
            <ActionIcon
              variant="subtle"
              size="sm"
              color="gray"
              loading={polling}
              aria-label={t("actions.poll")}
              onClick={(event) => {
                event.preventDefault();
                event.stopPropagation();
                onPoll(feed);
              }}
            >
              <IconRefresh size={14} />
            </ActionIcon>
          </Tooltip>
        </Group>
      }
      disableRightSectionRotation
      active={isSelected(selection, { kind: "feed", id: feed.id })}
      onClick={() => onSelectFeed(feed)}
      style={{ paddingLeft: 8 + depth * 8 }}
      color={feed.enabled ? undefined : "gray"}
    />
  );
}

function FolderNav({
  node,
  depth,
  selection,
  opened,
  pollingIds,
  onToggle,
  onSelectGroup,
  onSelectFeed,
  onManageGroup,
  onManageFeed,
  onPoll,
}: {
  node: FeedFolderNode;
  depth: number;
  selection: FeedSelection;
  opened: ReadonlySet<string>;
  pollingIds: ReadonlySet<number>;
  onToggle: (path: string) => void;
  onSelectGroup: (path: string) => void;
  onSelectFeed: (feed: FeedResponse) => void;
  onManageGroup: (path: string) => void;
  onManageFeed: (feed: FeedResponse) => void;
  onPoll: (feed: FeedResponse) => void;
}) {
  const { t } = useTranslation("feeds");
  const expanded = opened.has(node.path);
  const selected = isSelected(selection, { kind: "group", path: node.path });
  return (
    <>
      <NavLink
        component="button"
        label={node.name}
        leftSection={
          <Group gap={4} wrap="nowrap">
            <ActionIcon
              variant="subtle"
              size="sm"
              color="gray"
              onClick={(event) => {
                event.preventDefault();
                event.stopPropagation();
                onToggle(node.path);
              }}
            >
              {expanded ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
            </ActionIcon>
            <ManageIconButton
              label={t("actions.openGroupInSources")}
              onClick={() => onManageGroup(node.path)}
            >
              <IconFolder size={16} />
            </ManageIconButton>
          </Group>
        }
        rightSection={<UnreadCountBadge count={node.unreadCount} />}
        disableRightSectionRotation
        active={selected}
        onClick={() => {
          if (!selected) {
            onSelectGroup(node.path);
            if (!expanded) {
              onToggle(node.path);
            }
            return;
          }
          onToggle(node.path);
        }}
        style={{ paddingLeft: 8 + depth * 8 }}
      />
      {expanded
        ? node.children.map((child) => (
            <TreeNode
              key={child.kind === "folder" ? child.path : `feed-${child.feed.id}`}
              node={child}
              depth={depth + 1}
              selection={selection}
              opened={opened}
              pollingIds={pollingIds}
              onToggle={onToggle}
              onSelectGroup={onSelectGroup}
              onSelectFeed={onSelectFeed}
              onManageGroup={onManageGroup}
              onManageFeed={onManageFeed}
              onPoll={onPoll}
            />
          ))
        : null}
    </>
  );
}

function TreeNode({
  node,
  depth,
  selection,
  opened,
  pollingIds,
  onToggle,
  onSelectGroup,
  onSelectFeed,
  onManageGroup,
  onManageFeed,
  onPoll,
}: {
  node: FeedTreeNode;
  depth: number;
  selection: FeedSelection;
  opened: ReadonlySet<string>;
  pollingIds: ReadonlySet<number>;
  onToggle: (path: string) => void;
  onSelectGroup: (path: string) => void;
  onSelectFeed: (feed: FeedResponse) => void;
  onManageGroup: (path: string) => void;
  onManageFeed: (feed: FeedResponse) => void;
  onPoll: (feed: FeedResponse) => void;
}) {
  if (node.kind === "folder") {
    return (
      <FolderNav
        node={node}
        depth={depth}
        selection={selection}
        opened={opened}
        pollingIds={pollingIds}
        onToggle={onToggle}
        onSelectGroup={onSelectGroup}
        onSelectFeed={onSelectFeed}
        onManageGroup={onManageGroup}
        onManageFeed={onManageFeed}
        onPoll={onPoll}
      />
    );
  }
  return (
    <FeedLeafNav
      feed={node.feed}
      depth={depth}
      selection={selection}
      polling={pollingIds.has(node.feed.id)}
      onSelectFeed={onSelectFeed}
      onManage={onManageFeed}
      onPoll={onPoll}
    />
  );
}

export function FeedSidebar({
  feeds,
  feedId,
  group,
  onSelectAll,
  onSelectUngrouped,
  onSelectGroup,
  onSelectFeed,
}: {
  feeds: readonly FeedResponse[];
  feedId: number | undefined;
  group: string | undefined;
  onSelectAll: () => void;
  onSelectUngrouped: () => void;
  onSelectGroup: (path: string) => void;
  onSelectFeed: (feed: FeedResponse) => void;
}) {
  const { t } = useTranslation(["feeds", "common"]);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [pollingIds, setPollingIds] = useState<Set<number>>(() => new Set());
  const selection = selectionOf(feedId, group);
  const filtered = useMemo(() => filterFeedsByQuery(feeds, query), [feeds, query]);
  const tree = useMemo(() => buildFeedTree(filtered), [filtered]);

  const selectedPath =
    selection.kind === "group"
      ? selection.path
      : selection.kind === "feed"
        ? feedGroup(feeds.find((feed) => feed.id === selection.id) ?? { group: "" })
        : "";

  const [opened, setOpened] = useState<Set<string>>(
    () => new Set(pathsToReveal(selection.kind, selectedPath)),
  );
  const revealKey = `${selection.kind}:${selectedPath}`;
  const [prevRevealKey, setPrevRevealKey] = useState(revealKey);
  if (revealKey !== prevRevealKey) {
    setPrevRevealKey(revealKey);
    const reveal = pathsToReveal(selection.kind, selectedPath);
    if (!reveal.every((path) => opened.has(path))) {
      const next = new Set(opened);
      for (const path of reveal) {
        next.add(path);
      }
      setOpened(next);
    }
  }

  function toggle(path: string) {
    setOpened((prev) => {
      const next = new Set(prev);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  }

  async function poll(feed: FeedResponse) {
    if (pollingIds.has(feed.id)) {
      return;
    }
    setPollingIds((prev) => {
      const next = new Set(prev);
      next.add(feed.id);
      return next;
    });
    try {
      await pollFeed({ path: { feed_id: feed.id }, throwOnError: true });
      notifications.show({ message: t("common:toast.feedPolled"), color: "blue" });
      void queryClient.invalidateQueries({ queryKey: listFeedsQueryKey() });
      void queryClient.invalidateQueries({ queryKey: listAllFeedItemsQueryKey() });
    } catch (err) {
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      });
    } finally {
      setPollingIds((prev) => {
        const next = new Set(prev);
        next.delete(feed.id);
        return next;
      });
    }
  }

  function openFeedInSources(feed: FeedResponse) {
    void navigate({ to: "/feeds/sources", search: { feed: feed.id } });
  }

  function openGroupInSources(path: string) {
    void navigate({ to: "/feeds/sources", search: { q: path } });
  }

  return (
    <Box
      style={{
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
        height: "100%",
        borderRight: "1px solid var(--mantine-color-default-border)",
      }}
    >
      <Box p="sm" pb="xs">
        <TextInput
          value={query}
          onChange={(event) => setQuery(event.currentTarget.value)}
          placeholder={t("sidebar.searchFeeds")}
          leftSection={<IconSearch size={16} />}
          size="sm"
        />
      </Box>
      <ScrollArea style={{ flex: 1 }} offsetScrollbars type="hover">
        <NavLink
          label={t("sidebar.all")}
          rightSection={<UnreadCountBadge count={sumFeedUnread(feeds)} />}
          active={selection.kind === "all"}
          onClick={onSelectAll}
        />
        {tree.ungrouped.length > 0 && (
          <NavLink
            label={t("sidebar.ungrouped")}
            leftSection={<IconFolder size={16} />}
            rightSection={<UnreadCountBadge count={sumFeedUnread(tree.ungrouped)} />}
            disableRightSectionRotation
            active={selection.kind === "ungrouped"}
            childrenOffset={12}
            defaultOpened
            onClick={onSelectUngrouped}
          >
            {tree.ungrouped.map((feed) => (
              <FeedLeafNav
                key={feed.id}
                feed={feed}
                depth={1}
                selection={selection}
                polling={pollingIds.has(feed.id)}
                onSelectFeed={onSelectFeed}
                onManage={openFeedInSources}
                onPoll={poll}
              />
            ))}
          </NavLink>
        )}
        {tree.folders.map((folder) => (
          <TreeNode
            key={folder.path}
            node={folder}
            depth={0}
            selection={selection}
            opened={opened}
            pollingIds={pollingIds}
            onToggle={toggle}
            onSelectGroup={onSelectGroup}
            onSelectFeed={onSelectFeed}
            onManageGroup={openGroupInSources}
            onManageFeed={openFeedInSources}
            onPoll={poll}
          />
        ))}
        {filtered.length === 0 && (
          <Text size="sm" c="dimmed" px="md" py="sm">
            {t("sidebar.noMatches")}
          </Text>
        )}
      </ScrollArea>
    </Box>
  );
}
