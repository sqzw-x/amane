import type { FeedResponse } from "@/client/types.gen";

/** URL 里表示未分组; 不能出现在规范化后的 group 路径段中. */
export const UNGROUPED_GROUP = ".";

export function feedGroup(feed: Pick<FeedResponse, "group">): string {
  return feed.group?.trim() ?? "";
}

export function feedDisplayName(feed: Pick<FeedResponse, "name" | "url">): string {
  const name = feed.name.trim();
  return name === "" ? feed.url : name;
}

export function normalizeFeedGroup(value: string): string {
  const parts = value
    .trim()
    .replaceAll("\\", "/")
    .split("/")
    .map((part) => part.trim())
    .filter((part) => part.length > 0);
  if (
    parts.some(
      (part) => part === "." || part === ".." || [...part].some((ch) => ch.charCodeAt(0) < 32),
    )
  ) {
    throw new Error("invalid_group");
  }
  return parts.join("/");
}

export function tryNormalizeFeedGroup(value: string): string | null {
  try {
    return normalizeFeedGroup(value);
  } catch {
    return null;
  }
}

/** 把公共前缀拼到已有分组前. 两边都会经规范化; 任一侧非法则抛. */
export function joinFeedGroup(prefix: string, group: string): string {
  const left = normalizeFeedGroup(prefix);
  const right = normalizeFeedGroup(group);
  if (left === "") {
    return right;
  }
  if (right === "") {
    return left;
  }
  return `${left}/${right}`;
}

export function tryJoinFeedGroup(prefix: string, group: string): string | null {
  try {
    return joinFeedGroup(prefix, group);
  } catch {
    return null;
  }
}

export type FeedFolderNode = {
  kind: "folder";
  path: string;
  name: string;
  children: FeedTreeNode[];
  feedCount: number;
  unreadCount: number;
};

export type FeedLeafNode = {
  kind: "feed";
  feed: FeedResponse;
};

export type FeedTreeNode = FeedFolderNode | FeedLeafNode;

export type FeedTree = {
  ungrouped: FeedResponse[];
  folders: FeedFolderNode[];
};

type MutableFolder = {
  path: string;
  name: string;
  folders: Map<string, MutableFolder>;
  feeds: FeedResponse[];
};

function freezeFolder(node: MutableFolder): FeedFolderNode {
  const folderChildren = [...node.folders.values()]
    .toSorted((a, b) => a.name.localeCompare(b.name))
    .map(freezeFolder);
  const feedChildren: FeedLeafNode[] = node.feeds
    .toSorted((a, b) =>
      feedDisplayName(a).localeCompare(feedDisplayName(b), undefined, { sensitivity: "base" }),
    )
    .map((feed) => ({ kind: "feed", feed }));
  return {
    kind: "folder",
    path: node.path,
    name: node.name,
    children: [...folderChildren, ...feedChildren],
    feedCount: node.feeds.length + folderChildren.reduce((sum, child) => sum + child.feedCount, 0),
    unreadCount:
      node.feeds.reduce((sum, feed) => sum + (feed.unread_count ?? 0), 0) +
      folderChildren.reduce((sum, child) => sum + child.unreadCount, 0),
  };
}

export function buildFeedTree(feeds: readonly FeedResponse[]): FeedTree {
  const root: MutableFolder = { path: "", name: "", folders: new Map(), feeds: [] };
  for (const feed of feeds) {
    const group = feedGroup(feed);
    let node = root;
    if (group !== "") {
      let acc = "";
      for (const part of group.split("/")) {
        acc = acc === "" ? part : `${acc}/${part}`;
        let child = node.folders.get(part);
        if (child == null) {
          child = { path: acc, name: part, folders: new Map(), feeds: [] };
          node.folders.set(part, child);
        }
        node = child;
      }
    }
    node.feeds.push(feed);
  }
  const frozen = freezeFolder(root);
  return {
    ungrouped: frozen.children
      .filter((child): child is FeedLeafNode => child.kind === "feed")
      .map((child) => child.feed),
    folders: frozen.children.filter((child): child is FeedFolderNode => child.kind === "folder"),
  };
}

export function filterFeedsByQuery(feeds: readonly FeedResponse[], query: string): FeedResponse[] {
  const needle = query.trim().toLowerCase();
  if (needle === "") {
    return [...feeds];
  }
  return feeds.filter((feed) => {
    const group = feedGroup(feed).toLowerCase();
    return (
      feedDisplayName(feed).toLowerCase().includes(needle) ||
      feed.url.toLowerCase().includes(needle) ||
      group.includes(needle)
    );
  });
}

/** URL 三态筛选: 缺省为不限. */
export const FEED_SOURCE_TRI_FILTERS = ["true", "false"] as const;
export type FeedSourceTriFilter = (typeof FEED_SOURCE_TRI_FILTERS)[number];

export type FeedSourceFilters = {
  enabled?: FeedSourceTriFilter;
  auto_enqueue?: FeedSourceTriFilter;
};

export function hasActiveFeedSourceFilters(filters: FeedSourceFilters): boolean {
  return filters.enabled != null || filters.auto_enqueue != null;
}

function matchesTriFilter(value: boolean, filter: FeedSourceTriFilter | undefined): boolean {
  if (filter == null) {
    return true;
  }
  return value === (filter === "true");
}

export function filterFeedSources(
  feeds: readonly FeedResponse[],
  query: string,
  filters: FeedSourceFilters,
): FeedResponse[] {
  return filterFeedsByQuery(feeds, query).filter(
    (feed) =>
      matchesTriFilter(feed.enabled, filters.enabled) &&
      matchesTriFilter(feed.auto_enqueue, filters.auto_enqueue),
  );
}

export const FEED_SOURCE_SORT_FIELDS = [
  "name",
  "group",
  "url",
  "interval",
  "last_fetch",
  "enabled",
] as const;

export type FeedSourceSortField = (typeof FEED_SOURCE_SORT_FIELDS)[number];

function compareFeedSources(a: FeedResponse, b: FeedResponse, sortBy: FeedSourceSortField): number {
  switch (sortBy) {
    case "name":
      return feedDisplayName(a).localeCompare(feedDisplayName(b));
    case "group":
      return (
        feedGroup(a).localeCompare(feedGroup(b)) ||
        feedDisplayName(a).localeCompare(feedDisplayName(b))
      );
    case "url":
      return a.url.localeCompare(b.url);
    case "interval":
      return a.interval_seconds - b.interval_seconds;
    case "last_fetch":
      return (a.last_fetched_at ?? "").localeCompare(b.last_fetched_at ?? "");
    case "enabled":
      return Number(a.enabled) - Number(b.enabled);
  }
}

export function sortFeedSources(
  feeds: readonly FeedResponse[],
  sortBy: FeedSourceSortField,
  order: "asc" | "desc",
): FeedResponse[] {
  const dir = order === "asc" ? 1 : -1;
  return [...feeds].toSorted((a, b) => dir * compareFeedSources(a, b, sortBy));
}

export function sumFeedUnread(feeds: readonly FeedResponse[]): number {
  return feeds.reduce((sum, feed) => sum + (feed.unread_count ?? 0), 0);
}

export function uniqueFeedGroups(feeds: readonly FeedResponse[]): string[] {
  const groups = new Set<string>();
  for (const feed of feeds) {
    const group = feedGroup(feed);
    if (group === "") {
      continue;
    }
    groups.add(group);
    const parts = group.split("/");
    let acc = "";
    for (const part of parts.slice(0, -1)) {
      acc = acc === "" ? part : `${acc}/${part}`;
      groups.add(acc);
    }
  }
  return [...groups].toSorted((a, b) => a.localeCompare(b));
}

export function ancestorGroupPaths(path: string): string[] {
  if (path === "" || path === UNGROUPED_GROUP) {
    return [];
  }
  const parts = path.split("/");
  const result: string[] = [];
  let acc = "";
  for (const part of parts) {
    acc = acc === "" ? part : `${acc}/${part}`;
    result.push(acc);
  }
  return result;
}
