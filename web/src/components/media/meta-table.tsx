import { Badge, Button, Checkbox, Group, Modal, Select, Stack, Table, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconRefresh, IconTag, IconTrash } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  batchDeleteMetadataMutation,
  batchMetadataUserTagsMutation,
  batchScrapeMetadataMutation,
  deleteMetadataMutation,
  listFacetsOptions,
  listMetadataQueryKey,
  submitTaskMutation,
} from "@/client/@tanstack/react-query.gen";
import type { MetadataResponse, MetadataSortField, SortOrder } from "@/client/types.gen";
import { HintedActionIcon } from "@/components/common/hinted-action-icon";
import { ListToolbar } from "@/components/common/list-toolbar";
import { ResizableTh, SortableTh } from "@/components/common/sortable-th";
import { SelectionBar } from "@/components/common/selection-bar";
import { useIdSelection } from "@/hooks/use-id-selection";
import { useResizableColumns } from "@/hooks/use-resizable-columns";
import { extractErrorMessage } from "@/lib/api-error";
import { confirm } from "@/lib/confirm";
import { USER_TAG_FACET_LIST } from "@/lib/facets";
import { type MetaTableColumnKey, useUIStore } from "@/stores/ui";

const SORTABLE_COLUMNS = [
  "number",
  "title",
  "studio",
  "release",
  "updated_at",
  "file_count",
] as const satisfies readonly MetadataSortField[];

type SortableColumn = (typeof SORTABLE_COLUMNS)[number];

const COLUMN_I18N_KEY = {
  number: "number",
  title: "title",
  studio: "studio",
  release: "release",
  updated_at: "updated",
  file_count: "fileCount",
} as const satisfies Record<SortableColumn, string>;

/** 默认列宽 (px). title 默认不设置宽以吃掉剩余空间 (自适应); 拖拽后写入覆盖. */
const DEFAULT_COLUMN_WIDTHS = {
  number: 120,
  title: 360,
  studio: 140,
  release: 110,
  updated_at: 110,
  file_count: 80,
  score: 72,
} as const satisfies Record<MetaTableColumnKey, number>;

/** title 默认自适应; 其它列用默认 px. */
const FLEX_COLUMNS = new Set<MetaTableColumnKey>(["title"]);

const CELL_OVERFLOW = { overflow: "hidden", maxWidth: 0 } as const;

export interface MetaTableProps {
  items: MetadataResponse[];
  isLoading: boolean;
  total: number;
  page: number;
  sortBy: MetadataSortField | undefined;
  order: SortOrder | undefined;
  onPageChange: (page: number) => void;
  onSort: (field: MetadataSortField) => void;
}

function cellValue(item: MetadataResponse, field: SortableColumn): string {
  switch (field) {
    case "number":
      return item.number;
    case "title":
      return item.title ?? "";
    case "studio":
      return item.studio ?? "";
    case "release":
      return item.release ?? "";
    case "updated_at":
      return item.updated_at ? item.updated_at.slice(0, 10) : "";
    case "file_count":
      return String(item.file_count ?? 0);
  }
}

export function MetaTable({
  items,
  isLoading,
  total,
  page,
  sortBy,
  order,
  onPageChange,
  onSort,
}: MetaTableProps) {
  const { t } = useTranslation(["metadata", "common", "library"]);
  const queryClient = useQueryClient();
  const limit = useUIStore((s) => s.pageSizes.metaList);
  const storedWidths = useUIStore((s) => s.metaColumnWidths);
  const setMetaColumnWidths = useUIStore((s) => s.setMetaColumnWidths);
  const { effectiveWidth, hasCustomWidth, getResizeHandleProps } = useResizableColumns({
    defaults: DEFAULT_COLUMN_WIDTHS,
    stored: storedWidths,
    onChange: setMetaColumnWidths,
  });

  function columnWidth(key: MetaTableColumnKey): number | undefined {
    if (hasCustomWidth(key)) return effectiveWidth(key);
    if (FLEX_COLUMNS.has(key)) return undefined;
    return effectiveWidth(key);
  }
  const pageIds = items.map((i) => i.id);
  const { selected, selectedIds, toggleOne, toggleAll, isAllSelected, clear } = useIdSelection();
  const [tagModalOpen, setTagModalOpen] = useState(false);
  const [tagId, setTagId] = useState<string | null>(null);

  const { data: userTags } = useQuery(listFacetsOptions(USER_TAG_FACET_LIST));

  const invalidate = () => void queryClient.invalidateQueries({ queryKey: listMetadataQueryKey() });

  const scrapeOne = useMutation(submitTaskMutation());
  const deleteOne = useMutation({
    ...deleteMetadataMutation(),
    onSuccess: () => {
      notifications.show({ message: t("common:toast.metadataDeleted"), color: "blue" });
      invalidate();
    },
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      }),
  });

  const batchScrape = useMutation({
    ...batchScrapeMetadataMutation(),
    onSuccess: (res) => {
      notifications.show({
        message: t("common:toast.batchScrapeStarted", {
          defaultValue: `已提交 ${res.submitted} 个刮削任务`,
          count: res.submitted,
        }),
        color: "blue",
      });
    },
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.batchScrapeFailed")),
        color: "red",
      }),
  });

  const batchDelete = useMutation({
    ...batchDeleteMetadataMutation(),
    onSuccess: (res) => {
      notifications.show({
        message: t("common:toast.metadataDeleted", {
          defaultValue: `已删除 ${res.deleted} 条`,
        }),
        color: "blue",
      });
      clear();
      invalidate();
    },
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      }),
  });

  async function handleBatchDelete() {
    const ok = await confirm({
      title: t("actions.confirmBatchDeleteTitle"),
      message: t("actions.confirmBatchDeleteDesc", { count: selectedIds.length }),
      confirmLabel: t("common:actions.delete"),
    });
    if (!ok) return;
    batchDelete.mutate({ body: { ids: selectedIds } });
  }

  async function handleDeleteOne(metadataId: number) {
    const ok = await confirm({
      title: t("common:actions.delete"),
      message: t("confirmDelete", { id: metadataId }),
      confirmLabel: t("common:actions.delete"),
    });
    if (!ok) return;
    deleteOne.mutate({ path: { metadata_id: metadataId } });
  }

  const batchTags = useMutation({
    ...batchMetadataUserTagsMutation(),
    onSuccess: (res) => {
      notifications.show({
        message: t("common:toast.metadataUpdated", {
          defaultValue: `已更新 ${res.affected} 条`,
        }),
        color: "blue",
      });
      setTagModalOpen(false);
      setTagId(null);
      invalidate();
    },
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      }),
  });

  const busy = batchScrape.isPending || batchDelete.isPending || batchTags.isPending;

  const tagOptions = useMemo(
    () => (userTags?.items ?? []).map((tag) => ({ value: String(tag.id), label: tag.name })),
    [userTags],
  );

  const totalPages = Math.max(1, Math.ceil(total / limit));
  const allSelected = isAllSelected(pageIds);
  const effectiveSortBy = sortBy ?? "updated_at";
  const effectiveOrder = order ?? "desc";

  function handlePageChange(p: number) {
    clear();
    onPageChange(p);
  }

  return (
    <>
      <ListToolbar
        totalPages={totalPages}
        page={page}
        onChange={handlePageChange}
        header={
          <SelectionBar count={selected.size}>
            <Button
              size="xs"
              variant="light"
              leftSection={<IconRefresh size={14} />}
              loading={busy}
              disabled={selected.size === 0}
              onClick={() => batchScrape.mutate({ body: { ids: selectedIds } })}
            >
              {t("actions.batchScrape")}
            </Button>
            <Button
              size="xs"
              variant="light"
              leftSection={<IconTag size={14} />}
              loading={busy}
              disabled={selected.size === 0}
              onClick={() => setTagModalOpen(true)}
            >
              {t("detail.userTags")}
            </Button>
            <Button
              size="xs"
              variant="light"
              color="red"
              leftSection={<IconTrash size={14} />}
              loading={busy}
              disabled={selected.size === 0}
              onClick={() => void handleBatchDelete()}
            >
              {t("actions.batchDelete")}
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
          style={{ minWidth: 720 }}
        >
          <Table.Thead>
            <Table.Tr>
              <Table.Th w={40}>
                <Checkbox checked={allSelected} onChange={() => toggleAll(pageIds)} />
              </Table.Th>
              {SORTABLE_COLUMNS.map((field) => (
                <SortableTh
                  key={field}
                  field={field}
                  label={t(`columns.${COLUMN_I18N_KEY[field]}`)}
                  sortBy={effectiveSortBy}
                  order={effectiveOrder}
                  onSort={onSort}
                  w={columnWidth(field)}
                  resizeHandle={getResizeHandleProps(field)}
                />
              ))}
              <ResizableTh w={columnWidth("score")} resizeHandle={getResizeHandleProps("score")}>
                {t("columns.score")}
              </ResizableTh>
              <Table.Th w={88} ta="right">
                {t("columns.actions")}
              </Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {items.map((item) => (
              <Table.Tr
                key={item.id}
                bg={selected.has(item.id) ? "var(--mantine-color-blue-light)" : undefined}
              >
                <Table.Td>
                  <Checkbox checked={selected.has(item.id)} onChange={() => toggleOne(item.id)} />
                </Table.Td>
                {SORTABLE_COLUMNS.map((field) => {
                  const value = cellValue(item, field);
                  return (
                    <Table.Td key={field} style={CELL_OVERFLOW}>
                      {field === "number" ? (
                        <Link
                          to="/meta/$metadataId"
                          params={{ metadataId: String(item.id) }}
                          style={{ textDecoration: "none", display: "block", overflow: "hidden" }}
                        >
                          <Text component="span" size="sm" ff="monospace" truncate title={value}>
                            {value}
                          </Text>
                        </Link>
                      ) : (
                        <Text size="sm" truncate title={value || undefined}>
                          {value}
                        </Text>
                      )}
                    </Table.Td>
                  );
                })}
                <Table.Td style={CELL_OVERFLOW}>
                  {item.score != null && (
                    <Badge variant="light" color="yellow">
                      {item.score.toFixed(1)}
                    </Badge>
                  )}
                </Table.Td>
                <Table.Td>
                  <Group gap={4} justify="flex-end" wrap="nowrap">
                    <HintedActionIcon
                      variant="subtle"
                      label={t("actions.scrape")}
                      onClick={() =>
                        scrapeOne.mutate({ body: { type: "scrape", number: item.number } })
                      }
                    >
                      <IconRefresh size={16} />
                    </HintedActionIcon>
                    <HintedActionIcon
                      variant="subtle"
                      color="red"
                      label={t("common:actions.delete")}
                      onClick={() => void handleDeleteOne(item.id)}
                    >
                      <IconTrash size={16} />
                    </HintedActionIcon>
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>

        {!isLoading && items.length === 0 && (
          <Text c="dimmed" size="sm" ta="center" py="xl">
            {t("empty")}
          </Text>
        )}
      </ListToolbar>

      <Modal
        opened={tagModalOpen}
        onClose={() => setTagModalOpen(false)}
        title={t("detail.userTags")}
        centered
      >
        <Stack gap="md">
          <Select
            data={tagOptions}
            value={tagId}
            onChange={setTagId}
            searchable
            placeholder={t("detail.selectUserTag")}
          />
          <Group justify="flex-end">
            <Button
              variant="light"
              disabled={!tagId}
              loading={batchTags.isPending}
              onClick={() =>
                tagId &&
                batchTags.mutate({
                  body: {
                    ids: selectedIds,
                    user_tag_id: Number(tagId),
                    action: "attach",
                  },
                })
              }
            >
              {t("common:actions.add")}
            </Button>
            <Button
              variant="light"
              color="red"
              disabled={!tagId}
              loading={batchTags.isPending}
              onClick={() =>
                tagId &&
                batchTags.mutate({
                  body: {
                    ids: selectedIds,
                    user_tag_id: Number(tagId),
                    action: "detach",
                  },
                })
              }
            >
              {t("common:actions.remove")}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </>
  );
}
