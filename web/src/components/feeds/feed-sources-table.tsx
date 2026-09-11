import {
  ActionIcon,
  Badge,
  Button,
  Checkbox,
  Group,
  Modal,
  Skeleton,
  Stack,
  Switch,
  Table,
  TagsInput,
  Text,
  Tooltip,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconClockPlay, IconFilter, IconPencil, IconTrash } from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { listAllFeedItemsQueryKey, listFeedsQueryKey } from "@/client/@tanstack/react-query.gen";
import { deleteFeed, pollFeed, updateFeed } from "@/client/sdk.gen";
import type { FeedResponse, SortOrder } from "@/client/types.gen";
import { ListToolbar } from "@/components/common/list-toolbar";
import { SelectionBar } from "@/components/common/selection-bar";
import { SortableTh } from "@/components/common/sortable-th";
import { intervalLabelCount, intervalLabelKey } from "@/components/feeds/feed-form";
import { useIdSelection } from "@/hooks/use-id-selection";
import { extractErrorMessage } from "@/lib/api-error";
import { confirm } from "@/lib/confirm";
import { feedDisplayName, feedGroup, type FeedSourceSortField } from "@/lib/feeds/groups";
import { useUIStore } from "@/stores/ui";

const CELL_OVERFLOW = { overflow: "hidden", maxWidth: 0 } as const;

type BatchKind = "poll" | "enable" | "disable" | "enqueue" | "discover" | "keywords" | "delete";

function unionIgnoreKeywords(current: readonly string[], extra: readonly string[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const raw of [...current, ...extra]) {
    const keyword = raw.trim();
    if (keyword === "") {
      continue;
    }
    const key = keyword.toLocaleLowerCase();
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    result.push(keyword);
  }
  return result;
}

async function pollOne(id: number) {
  await pollFeed({ path: { feed_id: id }, throwOnError: true });
}

async function runBatch(
  ids: readonly number[],
  each: (id: number) => Promise<void>,
): Promise<{ ok: number; failed: number }> {
  let ok = 0;
  let failed = 0;
  for (const id of ids) {
    try {
      await each(id);
      ok += 1;
    } catch {
      failed += 1;
    }
  }
  return { ok, failed };
}

export interface FeedSourcesTableProps {
  items: FeedResponse[];
  isLoading: boolean;
  total: number;
  page: number;
  sortBy: FeedSourceSortField;
  order: SortOrder;
  onPageChange: (page: number) => void;
  onSort: (field: FeedSourceSortField) => void;
  onEdit: (feed: FeedResponse) => void;
  highlightId?: number;
}

export function FeedSourcesTable({
  items,
  isLoading,
  total,
  page,
  sortBy,
  order,
  onPageChange,
  onSort,
  onEdit,
  highlightId,
}: FeedSourcesTableProps) {
  const { t } = useTranslation(["feeds", "common"]);
  const queryClient = useQueryClient();
  const limit = useUIStore((s) => s.pageSizes.feedSources);
  const totalPages = Math.max(1, Math.ceil(total / limit));
  const pageIds = items.map((feed) => feed.id);
  const { selected, selectedIds, toggleOne, toggleAll, isAllSelected, clear } = useIdSelection();
  const allSelected = isAllSelected(pageIds);
  const [batchKind, setBatchKind] = useState<BatchKind | null>(null);
  const [togglingId, setTogglingId] = useState<number | null>(null);
  const [keywordOpen, setKeywordOpen] = useState(false);
  const [keywordDraft, setKeywordDraft] = useState<string[]>([]);
  const [keywordReplace, setKeywordReplace] = useState(false);
  const busy = batchKind != null || togglingId != null;

  useEffect(() => {
    if (highlightId == null) {
      return;
    }
    if (!items.some((feed) => feed.id === highlightId)) {
      return;
    }
    const rowId = `feed-source-${highlightId}`;
    const timer = window.setTimeout(() => {
      document.getElementById(rowId)?.scrollIntoView({ block: "nearest" });
    }, 0);
    return () => window.clearTimeout(timer);
  }, [highlightId, items]);

  function invalidate() {
    void queryClient.invalidateQueries({ queryKey: listFeedsQueryKey() });
    void queryClient.invalidateQueries({ queryKey: listAllFeedItemsQueryKey() });
  }

  function showBatchResult(result: { ok: number; failed: number }) {
    notifications.show({
      message:
        result.failed > 0
          ? t("batchSourceResultWithFailed", result)
          : t("batchSourceResult", result),
      color: result.failed > 0 ? "red" : "blue",
    });
  }

  function handlePageChange(next: number) {
    clear();
    onPageChange(next);
  }

  async function handlePoll(ids: readonly number[]) {
    if (ids.length === 0) {
      return;
    }
    setBatchKind("poll");
    try {
      showBatchResult(await runBatch(ids, pollOne));
      invalidate();
    } finally {
      setBatchKind(null);
    }
  }

  async function handlePatchSelected(
    ids: readonly number[],
    kind: Exclude<BatchKind, "poll" | "delete" | "keywords">,
    body: { enabled: boolean } | { auto_enqueue: boolean },
  ) {
    if (ids.length === 0) {
      return;
    }
    setBatchKind(kind);
    try {
      showBatchResult(
        await runBatch(ids, async (id) => {
          await updateFeed({ path: { feed_id: id }, body, throwOnError: true });
        }),
      );
      clear();
      invalidate();
    } finally {
      setBatchKind(null);
    }
  }

  async function handleIgnoreKeywords() {
    const ids = selectedIds;
    if (ids.length === 0) {
      return;
    }
    const extra = keywordDraft.map((item) => item.trim()).filter((item) => item !== "");
    setBatchKind("keywords");
    try {
      showBatchResult(
        await runBatch(ids, async (id) => {
          const feed = items.find((row) => row.id === id);
          const current = feed?.ignore_keywords ?? [];
          const ignore_keywords = keywordReplace ? extra : unionIgnoreKeywords(current, extra);
          await updateFeed({
            path: { feed_id: id },
            body: { ignore_keywords },
            throwOnError: true,
          });
        }),
      );
      setKeywordOpen(false);
      clear();
      invalidate();
    } finally {
      setBatchKind(null);
    }
  }

  async function handleDelete(ids: readonly number[]) {
    if (ids.length === 0) {
      return;
    }
    const named = ids.length === 1 ? items.find((feed) => feed.id === ids[0]) : undefined;
    const ok = await confirm({
      title: ids.length === 1 ? t("confirm.deleteTitle") : t("confirm.deleteManyTitle"),
      message:
        named != null
          ? t("confirm.deleteMessage", { name: feedDisplayName(named) })
          : t("confirm.deleteManyMessage", { count: ids.length }),
      confirmLabel: t("common:actions.delete"),
    });
    if (!ok) {
      return;
    }
    setBatchKind("delete");
    try {
      showBatchResult(
        await runBatch(ids, async (id) => {
          await deleteFeed({ path: { feed_id: id }, throwOnError: true });
        }),
      );
      clear();
      invalidate();
    } finally {
      setBatchKind(null);
    }
  }

  async function handleToggleEnabled(feed: FeedResponse) {
    setTogglingId(feed.id);
    try {
      await updateFeed({
        path: { feed_id: feed.id },
        body: { enabled: !feed.enabled },
        throwOnError: true,
      });
      invalidate();
    } catch (err) {
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      });
    } finally {
      setTogglingId(null);
    }
  }

  return (
    <>
      <Modal
        opened={keywordOpen}
        onClose={() => setKeywordOpen(false)}
        title={t("keywordDialog.title")}
        centered
      >
        <Stack gap="md">
          <TagsInput
            label={t("fields.ignoreKeywords")}
            description={t("fields.ignoreKeywordsHint")}
            placeholder={t("fields.ignoreKeywordsPlaceholder")}
            value={keywordDraft}
            onChange={setKeywordDraft}
            splitChars={[",", "，", "\n"]}
            clearable
          />
          <Switch
            label={t("keywordDialog.replace")}
            description={t("keywordDialog.replaceHint")}
            checked={keywordReplace}
            onChange={(event) => setKeywordReplace(event.currentTarget.checked)}
          />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setKeywordOpen(false)}>
              {t("common:actions.cancel")}
            </Button>
            <Button loading={batchKind === "keywords"} onClick={() => void handleIgnoreKeywords()}>
              {t("keywordDialog.apply")}
            </Button>
          </Group>
        </Stack>
      </Modal>
      <ListToolbar
        totalPages={totalPages}
        page={page}
        onChange={handlePageChange}
        header={
          <SelectionBar count={selectedIds.length}>
            <Button
              size="xs"
              variant="light"
              leftSection={<IconClockPlay size={14} />}
              loading={batchKind === "poll"}
              disabled={selectedIds.length === 0 || busy}
              onClick={() => void handlePoll(selectedIds)}
            >
              {t("actions.pollSelected")}
            </Button>
            <Button
              size="xs"
              variant="light"
              loading={batchKind === "enable"}
              disabled={selectedIds.length === 0 || busy}
              onClick={() => void handlePatchSelected(selectedIds, "enable", { enabled: true })}
            >
              {t("actions.enableSelected")}
            </Button>
            <Button
              size="xs"
              variant="light"
              loading={batchKind === "disable"}
              disabled={selectedIds.length === 0 || busy}
              onClick={() => void handlePatchSelected(selectedIds, "disable", { enabled: false })}
            >
              {t("actions.disableSelected")}
            </Button>
            <Button
              size="xs"
              variant="light"
              loading={batchKind === "enqueue"}
              disabled={selectedIds.length === 0 || busy}
              onClick={() =>
                void handlePatchSelected(selectedIds, "enqueue", { auto_enqueue: true })
              }
            >
              {t("actions.enableAutoEnqueueSelected")}
            </Button>
            <Button
              size="xs"
              variant="light"
              loading={batchKind === "discover"}
              disabled={selectedIds.length === 0 || busy}
              onClick={() =>
                void handlePatchSelected(selectedIds, "discover", { auto_enqueue: false })
              }
            >
              {t("actions.disableAutoEnqueueSelected")}
            </Button>
            <Button
              size="xs"
              variant="light"
              leftSection={<IconFilter size={14} />}
              loading={batchKind === "keywords"}
              disabled={selectedIds.length === 0 || busy}
              onClick={() => {
                setKeywordDraft([]);
                setKeywordReplace(false);
                setKeywordOpen(true);
              }}
            >
              {t("actions.setIgnoreKeywords")}
            </Button>
            <Button
              size="xs"
              variant="light"
              color="red"
              leftSection={<IconTrash size={14} />}
              loading={batchKind === "delete"}
              disabled={selectedIds.length === 0 || busy}
              onClick={() => void handleDelete(selectedIds)}
            >
              {t("common:actions.delete")}
            </Button>
          </SelectionBar>
        }
      >
        <Table
          stickyHeader
          highlightOnHover
          verticalSpacing="sm"
          layout="fixed"
          w="100%"
          style={{ minWidth: 960 }}
        >
          <Table.Thead>
            <Table.Tr>
              <Table.Th w={36}>
                <Checkbox
                  checked={allSelected}
                  disabled={pageIds.length === 0 || busy}
                  onChange={() => toggleAll(pageIds)}
                  aria-label={t("reader.selectPage")}
                />
              </Table.Th>
              <SortableTh
                field="name"
                label={t("columns.name")}
                sortBy={sortBy}
                order={order}
                onSort={onSort}
              />
              <SortableTh
                field="group"
                label={t("columns.group")}
                sortBy={sortBy}
                order={order}
                onSort={onSort}
                w={140}
              />
              <SortableTh
                field="url"
                label={t("columns.url")}
                sortBy={sortBy}
                order={order}
                onSort={onSort}
              />
              <SortableTh
                field="interval"
                label={t("labels.interval")}
                sortBy={sortBy}
                order={order}
                onSort={onSort}
                w={110}
              />
              <SortableTh
                field="last_fetch"
                label={t("labels.lastFetch")}
                sortBy={sortBy}
                order={order}
                onSort={onSort}
                w={180}
              />
              <SortableTh
                field="enabled"
                label={t("fields.enabled")}
                sortBy={sortBy}
                order={order}
                onSort={onSort}
                w={88}
              />
              <Table.Th ta="right" w={120}>
                {t("columns.actions")}
              </Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {items.map((feed) => (
              <Table.Tr
                id={`feed-source-${feed.id}`}
                key={feed.id}
                bg={
                  highlightId === feed.id
                    ? "var(--mantine-color-brand-light)"
                    : selected.has(feed.id)
                      ? "var(--mantine-color-blue-light)"
                      : undefined
                }
              >
                <Table.Td>
                  <Checkbox
                    checked={selected.has(feed.id)}
                    disabled={busy}
                    onChange={() => toggleOne(feed.id)}
                  />
                </Table.Td>
                <Table.Td style={CELL_OVERFLOW}>
                  <Group gap="xs" wrap="nowrap">
                    <Link
                      to="/feeds"
                      search={{ feed: feed.id }}
                      style={{ textDecoration: "none", minWidth: 0 }}
                    >
                      <Text span c="brand" size="sm" truncate title={feedDisplayName(feed)}>
                        {feedDisplayName(feed)}
                      </Text>
                    </Link>
                    {feed.content_type != null && (
                      <Badge size="xs" variant="light">
                        {t(`contentTypes.${feed.content_type}`)}
                      </Badge>
                    )}
                    {!feed.auto_enqueue && (
                      <Badge size="xs" variant="light" color="gray">
                        {t("labels.discoverOnly")}
                      </Badge>
                    )}
                  </Group>
                </Table.Td>
                <Table.Td style={CELL_OVERFLOW}>
                  <Text
                    size="sm"
                    truncate
                    c={feedGroup(feed) === "" ? "dimmed" : undefined}
                    title={feedGroup(feed) === "" ? undefined : feedGroup(feed)}
                  >
                    {feedGroup(feed) === "" ? t("sidebar.ungrouped") : feedGroup(feed)}
                  </Text>
                </Table.Td>
                <Table.Td style={CELL_OVERFLOW}>
                  <Text size="xs" ff="monospace" truncate title={feed.url}>
                    {feed.url}
                  </Text>
                </Table.Td>
                <Table.Td>
                  <Text size="sm">
                    {t(intervalLabelKey(feed.interval_seconds), {
                      count: intervalLabelCount(feed.interval_seconds),
                    })}
                  </Text>
                </Table.Td>
                <Table.Td style={CELL_OVERFLOW}>
                  <Stack gap={2}>
                    <Text size="sm" truncate>
                      {feed.last_fetched_at
                        ? new Date(feed.last_fetched_at).toLocaleString()
                        : t("labels.never")}
                    </Text>
                    {feed.last_error != null && feed.last_error !== "" && (
                      <Text size="xs" c="red" truncate title={feed.last_error}>
                        {feed.last_error}
                      </Text>
                    )}
                  </Stack>
                </Table.Td>
                <Table.Td>
                  <Switch
                    checked={feed.enabled}
                    disabled={busy}
                    onChange={() => void handleToggleEnabled(feed)}
                  />
                </Table.Td>
                <Table.Td>
                  <Group gap={4} justify="flex-end" wrap="nowrap">
                    <Tooltip label={t("actions.poll")}>
                      <ActionIcon
                        variant="subtle"
                        disabled={busy}
                        onClick={() => void handlePoll([feed.id])}
                      >
                        <IconClockPlay size={16} />
                      </ActionIcon>
                    </Tooltip>
                    <Tooltip label={t("common:actions.edit")}>
                      <ActionIcon variant="subtle" disabled={busy} onClick={() => onEdit(feed)}>
                        <IconPencil size={16} />
                      </ActionIcon>
                    </Tooltip>
                    <Tooltip label={t("common:actions.delete")}>
                      <ActionIcon
                        variant="subtle"
                        color="red"
                        disabled={busy}
                        onClick={() => void handleDelete([feed.id])}
                      >
                        <IconTrash size={16} />
                      </ActionIcon>
                    </Tooltip>
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))}
            {isLoading &&
              items.length === 0 &&
              Array.from({ length: 8 }, (_, i) => (
                <Table.Tr key={`sk-${i}`}>
                  <Table.Td>
                    <Skeleton h={16} w={16} />
                  </Table.Td>
                  <Table.Td>
                    <Skeleton h={14} w="70%" />
                  </Table.Td>
                  <Table.Td>
                    <Skeleton h={14} w={80} />
                  </Table.Td>
                  <Table.Td>
                    <Skeleton h={14} w="90%" />
                  </Table.Td>
                  <Table.Td>
                    <Skeleton h={14} w={64} />
                  </Table.Td>
                  <Table.Td>
                    <Skeleton h={14} w={120} />
                  </Table.Td>
                  <Table.Td>
                    <Skeleton h={18} w={36} />
                  </Table.Td>
                  <Table.Td>
                    <Skeleton h={14} w={80} ml="auto" />
                  </Table.Td>
                </Table.Tr>
              ))}
          </Table.Tbody>
        </Table>
      </ListToolbar>
    </>
  );
}
