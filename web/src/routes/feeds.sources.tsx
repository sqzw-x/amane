import { ActionIcon, Badge, Button, Group, Modal, Text, TextInput, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconFilter, IconPlus, IconSearch, IconX } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, stripSearchParams } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { z } from "zod";
import {
  createFeedMutation,
  listAllFeedItemsQueryKey,
  listFeedsOptions,
  listFeedsQueryKey,
  updateFeedMutation,
} from "@/client/@tanstack/react-query.gen";
import type { FeedResponse } from "@/client/types.gen";
import { BrowsePageShell } from "@/components/common/browse-page-shell";
import { HintedActionIcon } from "@/components/common/hinted-action-icon";
import { PageSizeSelect } from "@/components/common/page-size-select";
import {
  emptyFeedForm,
  FeedFormFields,
  feedFormCanSubmit,
  feedFormFromResponse,
  feedFormToBody,
  type FeedFormState,
} from "@/components/feeds/feed-form";
import { FeedSourceFilterControls } from "@/components/feeds/feed-source-filters";
import { FeedSourcesTable } from "@/components/feeds/feed-sources-table";
import { OpmlImportButton } from "@/components/feeds/opml-import";
import { useResettingState } from "@/hooks/use-resetting-state";
import { extractErrorMessage } from "@/lib/api-error";
import { SORT_ORDERS } from "@/lib/exhaustive-maps";
import {
  FEED_SOURCE_SORT_FIELDS,
  FEED_SOURCE_TRI_FILTERS,
  type FeedSourceFilters,
  type FeedSourceSortField,
  filterFeedSources,
  hasActiveFeedSourceFilters,
  sortFeedSources,
  uniqueFeedGroups,
} from "@/lib/feeds/groups";
import { useUIStore } from "@/stores/ui";

const EMPTY_FEEDS: FeedResponse[] = [];

const sourcesSearchSchema = z.object({
  q: z.string().optional(),
  feed: z.coerce.number().int().positive().optional(),
  enabled: z.enum(FEED_SOURCE_TRI_FILTERS).optional(),
  auto_enqueue: z.enum(FEED_SOURCE_TRI_FILTERS).optional(),
  sort_by: z.enum(FEED_SOURCE_SORT_FIELDS).catch("group").default("group"),
  order: z.enum(SORT_ORDERS).catch("asc").default("asc"),
  page: z.coerce.number().int().min(1).catch(1).default(1),
});

export const Route = createFileRoute("/feeds/sources")({
  validateSearch: sourcesSearchSchema,
  search: { middlewares: [stripSearchParams({ page: 1, sort_by: "group", order: "asc" })] },
  component: FeedSourcesPage,
});

function ActiveFilterChip({ label, onClear }: { label: string; onClear: () => void }) {
  const { t } = useTranslation("common");
  return (
    <Group gap={4} wrap="nowrap">
      <Badge variant="outline">{label}</Badge>
      <ActionIcon
        size="sm"
        variant="subtle"
        color="gray"
        onClick={onClear}
        aria-label={t("actions.clear")}
      >
        <IconX size={14} />
      </ActionIcon>
    </Group>
  );
}

function FeedSourcesPage() {
  const { t } = useTranslation(["feeds", "common"]);
  const queryClient = useQueryClient();
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  const { data, isLoading } = useQuery(listFeedsOptions());
  const feeds = data?.items ?? EMPTY_FEEDS;
  const groupOptions = useMemo(() => uniqueFeedGroups(feeds), [feeds]);
  const limit = useUIStore((s) => s.pageSizes.feedSources);
  const [searchInput, setSearchInput] = useResettingState(() => search.q ?? "", search.q);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const locatedKeyRef = useRef("");
  const [createOpen, setCreateOpen] = useState(false);
  const [createForm, setCreateForm] = useState<FeedFormState>(emptyFeedForm);
  const [editing, setEditing] = useState<FeedResponse | null>(null);
  const [editForm, setEditForm] = useState<FeedFormState>(emptyFeedForm);

  const filters: FeedSourceFilters = {
    enabled: search.enabled,
    auto_enqueue: search.auto_enqueue,
  };
  const hasFilters = hasActiveFeedSourceFilters(filters);
  const [advancedOpen, setAdvancedOpen] = useState(hasFilters);

  const filtered = useMemo(() => {
    const matched = filterFeedSources(feeds, search.q ?? "", {
      enabled: search.enabled,
      auto_enqueue: search.auto_enqueue,
    });
    return sortFeedSources(matched, search.sort_by, search.order);
  }, [feeds, search.auto_enqueue, search.enabled, search.order, search.q, search.sort_by]);
  const total = filtered.length;
  const offset = (search.page - 1) * limit;
  const pageItems = filtered.slice(offset, offset + limit);
  const locateKey =
    search.feed == null
      ? ""
      : [
          search.feed,
          search.q ?? "",
          search.enabled ?? "",
          search.auto_enqueue ?? "",
          search.sort_by,
          search.order,
          String(limit),
        ].join(":");

  useEffect(() => {
    if (search.feed == null || isLoading) {
      if (search.feed == null) {
        locatedKeyRef.current = "";
      }
      return;
    }
    if (locatedKeyRef.current === locateKey) {
      return;
    }
    locatedKeyRef.current = locateKey;
    const index = filtered.findIndex((feed) => feed.id === search.feed);
    if (index < 0) {
      return;
    }
    const targetPage = Math.floor(index / limit) + 1;
    if (targetPage !== search.page) {
      void navigate({ search: (prev) => ({ ...prev, page: targetPage }) });
    }
  }, [filtered, isLoading, limit, locateKey, navigate, search.feed, search.page]);

  function invalidate() {
    void queryClient.invalidateQueries({ queryKey: listFeedsQueryKey() });
    void queryClient.invalidateQueries({ queryKey: listAllFeedItemsQueryKey() });
  }

  function patchSearch(
    patch: Partial<{
      q: string | undefined;
      feed: number | undefined;
      enabled: FeedSourceFilters["enabled"];
      auto_enqueue: FeedSourceFilters["auto_enqueue"];
      sort_by: FeedSourceSortField;
      order: "asc" | "desc";
      page: number;
    }>,
  ) {
    void navigate({ search: (prev) => ({ ...prev, ...patch }) });
  }

  function handleSearchChange(value: string) {
    setSearchInput(value);
    if (debounceRef.current) {
      clearTimeout(debounceRef.current);
    }
    debounceRef.current = setTimeout(() => {
      patchSearch({
        q: value.trim() === "" ? undefined : value,
        feed: undefined,
        page: 1,
      });
    }, 300);
  }

  const createMutation = useMutation({
    ...createFeedMutation(),
    onSuccess: () => {
      notifications.show({ message: t("common:toast.feedCreated"), color: "blue" });
      setCreateOpen(false);
      setCreateForm(emptyFeedForm());
      invalidate();
    },
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      }),
  });
  const updateMutation = useMutation({
    ...updateFeedMutation(),
    onSuccess: () => {
      notifications.show({ message: t("common:toast.feedUpdated"), color: "blue" });
      setEditing(null);
      invalidate();
    },
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      }),
  });

  return (
    <>
      <BrowsePageShell
        fill
        title={<Title order={2}>{t("manageTitle")}</Title>}
        actions={
          <Group gap="sm">
            <OpmlImportButton
              existingUrls={new Set(feeds.map((feed) => feed.url))}
              groupOptions={groupOptions}
              onImported={invalidate}
            />
            <Button
              leftSection={<IconPlus size={16} />}
              onClick={() => {
                setCreateForm(emptyFeedForm());
                setCreateOpen(true);
              }}
            >
              {t("newFeed")}
            </Button>
          </Group>
        }
        summary={
          <Text size="sm" c="dimmed">
            {t("common:pagination.totalItems", { count: total })}
          </Text>
        }
        search={
          <TextInput
            value={searchInput}
            onChange={(event) => handleSearchChange(event.currentTarget.value)}
            placeholder={t("searchSources")}
            leftSection={<IconSearch size={16} />}
            w="100%"
          />
        }
        extras={
          <HintedActionIcon
            variant={advancedOpen || hasFilters ? "filled" : "default"}
            size={36}
            onClick={() => setAdvancedOpen((open) => !open)}
            label={t("filter.title")}
          >
            <IconFilter size={16} />
          </HintedActionIcon>
        }
        pageSize={
          <PageSizeSelect sizeKey="feedSources" onChanged={() => patchSearch({ page: 1 })} />
        }
      >
        <FeedSourceFilterControls
          opened={advancedOpen}
          values={filters}
          onChange={(next) =>
            patchSearch({ enabled: next.enabled, auto_enqueue: next.auto_enqueue, page: 1 })
          }
        />

        {hasFilters && (
          <Group gap="xs">
            {filters.enabled != null && (
              <ActiveFilterChip
                label={`${t("fields.enabled")}: ${t(filters.enabled === "true" ? "filter.enabled" : "filter.disabled")}`}
                onClear={() => patchSearch({ enabled: undefined, page: 1 })}
              />
            )}
            {filters.auto_enqueue != null && (
              <ActiveFilterChip
                label={`${t("fields.autoEnqueue")}: ${t(filters.auto_enqueue === "true" ? "filter.autoEnqueue" : "filter.discoverOnly")}`}
                onClear={() => patchSearch({ auto_enqueue: undefined, page: 1 })}
              />
            )}
          </Group>
        )}

        {!isLoading && total === 0 ? (
          <Text c="dimmed" size="sm" ta="center" py="xl">
            {feeds.length === 0 ? t("empty") : t("sidebar.noMatches")}
          </Text>
        ) : (
          <FeedSourcesTable
            items={pageItems}
            isLoading={isLoading}
            total={total}
            page={search.page}
            sortBy={search.sort_by}
            order={search.order}
            onPageChange={(page) => patchSearch({ page })}
            onSort={(field) => {
              if (search.sort_by === field) {
                patchSearch({ order: search.order === "asc" ? "desc" : "asc", page: 1 });
                return;
              }
              patchSearch({
                sort_by: field,
                order: field === "last_fetch" || field === "enabled" ? "desc" : "asc",
                page: 1,
              });
            }}
            highlightId={search.feed}
            onEdit={(feed) => {
              setEditing(feed);
              setEditForm(feedFormFromResponse(feed));
            }}
          />
        )}
      </BrowsePageShell>

      <Modal
        opened={createOpen}
        onClose={() => setCreateOpen(false)}
        title={t("newFeed")}
        size="lg"
        centered
      >
        <FeedFormFields form={createForm} onChange={setCreateForm} groupOptions={groupOptions} />
        <Group justify="flex-end" mt="md">
          <Button variant="default" onClick={() => setCreateOpen(false)}>
            {t("common:actions.cancel")}
          </Button>
          <Button
            loading={createMutation.isPending}
            disabled={!feedFormCanSubmit(createForm)}
            onClick={() => createMutation.mutate({ body: feedFormToBody(createForm) })}
          >
            {t("common:actions.save")}
          </Button>
        </Group>
      </Modal>

      <Modal
        opened={editing != null}
        onClose={() => setEditing(null)}
        title={t("editFeed")}
        size="lg"
        centered
      >
        <FeedFormFields form={editForm} onChange={setEditForm} groupOptions={groupOptions} />
        <Group justify="flex-end" mt="md">
          <Button variant="default" onClick={() => setEditing(null)}>
            {t("common:actions.cancel")}
          </Button>
          <Button
            loading={updateMutation.isPending}
            disabled={editing == null || !feedFormCanSubmit(editForm)}
            onClick={() => {
              if (editing == null) {
                return;
              }
              updateMutation.mutate({
                path: { feed_id: editing.id },
                body: feedFormToBody(editForm),
              });
            }}
          >
            {t("common:actions.save")}
          </Button>
        </Group>
      </Modal>
    </>
  );
}
