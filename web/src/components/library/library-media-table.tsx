import { ActionIcon, Badge, Button, Checkbox, Group, Stack, Table, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconRefresh, IconTrash } from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";
import { listMediaQueryKey } from "@/client/@tanstack/react-query.gen";
import { deleteMedia, submitTask } from "@/client/sdk.gen";
import type {
  MediaFileResponse,
  MediaFileStatus,
  MediaSortField,
  SortOrder,
} from "@/client/types.gen";
import { FilePhaseBadges } from "@/components/media/file-phase-badges";
import { ListToolbar } from "@/components/common/list-toolbar";
import { SortableTh } from "@/components/common/sortable-th";
import { SelectionBar } from "@/components/common/selection-bar";
import { useIdSelection } from "@/hooks/use-id-selection";
import { extractErrorMessage } from "@/lib/api-error";
import { confirm } from "@/lib/confirm";
import { assertNever } from "@/lib/exhaustive";
import { formatFileSize } from "@/lib/utils";
import { useUIStore } from "@/stores/ui";

function statusColor(status: MediaFileStatus): string {
  switch (status) {
    case "scraped":
      return "teal";
    case "failed":
      return "red";
    case "skip":
      return "gray";
    case "pending":
      return "yellow";
    default:
      return assertNever(status, "MediaFileStatus");
  }
}

/** library.path 前缀去除, 得到库内相对路径; 非该库路径时原样返回. */
function relativePath(libraryPath: string, path: string): string {
  if (path.startsWith(libraryPath)) {
    return path.slice(libraryPath.length).replace(/^\//, "");
  }
  return path;
}

const SORTABLE_COLUMNS = [
  "path",
  "status",
  "size",
  "updated_at",
] as const satisfies readonly MediaSortField[];

type SortableColumn = (typeof SORTABLE_COLUMNS)[number];

const COLUMN_I18N_KEY = {
  path: "path",
  status: "status",
  size: "size",
  updated_at: "updated",
} as const satisfies Record<SortableColumn, string>;

const COLUMN_WIDTH: Record<SortableColumn, number | undefined> = {
  path: undefined,
  status: 110,
  size: 90,
  updated_at: 110,
};

const CELL_OVERFLOW = { overflow: "hidden", maxWidth: 0 } as const;

export interface LibraryMediaTableProps {
  libraryPath: string;
  items: MediaFileResponse[];
  isLoading: boolean;
  total: number;
  page: number;
  sortBy: MediaSortField | undefined;
  order: SortOrder | undefined;
  onPageChange: (page: number) => void;
  onSort: (field: MediaSortField) => void;
  trailing?: ReactNode;
}

export function LibraryMediaTable({
  libraryPath,
  items,
  isLoading,
  total,
  page,
  sortBy,
  order,
  onPageChange,
  onSort,
  trailing,
}: LibraryMediaTableProps) {
  const { t } = useTranslation(["library", "common"]);
  const queryClient = useQueryClient();
  const limit = useUIStore((s) => s.pageSizes.libraryMedia);
  const { selected, selectedIds, toggleOne, toggleAll, isAllSelected, clear } = useIdSelection();
  const [batchScraping, setBatchScraping] = useState(false);
  const [batchDeleting, setBatchDeleting] = useState(false);

  const invalidate = () => void queryClient.invalidateQueries({ queryKey: listMediaQueryKey() });

  async function handleBatchScrape() {
    const ids = selectedIds;
    setBatchScraping(true);
    const results = await Promise.allSettled(
      ids.map((id) => submitTask({ body: { type: "scrape", media_id: id }, throwOnError: true })),
    );
    setBatchScraping(false);
    const failed = results.filter((r) => r.status === "rejected").length;
    const ok = ids.length - failed;
    if (ok > 0) {
      notifications.show({
        message: t("common:toast.batchScrapeStarted", { count: ok }),
        color: "blue",
      });
    }
    if (failed > 0) {
      notifications.show({
        message: t("common:toast.batchScrapeFailed", { count: failed }),
        color: "red",
      });
    }
    clear();
  }

  async function handleBatchDelete() {
    const ok = await confirm({
      title: t("batch.confirmDeleteTitle"),
      message: t("batch.confirmDeleteDesc", { count: selected.size }),
      confirmLabel: t("common:actions.delete"),
    });
    if (!ok) return;
    const ids = selectedIds;
    setBatchDeleting(true);
    const results = await Promise.allSettled(
      ids.map((id) => deleteMedia({ path: { media_id: id }, throwOnError: true })),
    );
    setBatchDeleting(false);
    const failed = results.filter((r) => r.status === "rejected").length;
    const deleted = ids.length - failed;
    if (deleted > 0) notifications.show({ message: t("common:toast.mediaDeleted"), color: "blue" });
    if (failed > 0) {
      notifications.show({
        message: extractErrorMessage(null, t("common:toast.operationFailed")),
        color: "red",
      });
    }
    clear();
    invalidate();
  }

  async function handleDeleteOne(mediaId: number) {
    const ok = await confirm({
      title: t("detail.confirmDeleteTitle"),
      message: t("detail.confirmDeleteDesc"),
      confirmLabel: t("common:actions.delete"),
    });
    if (!ok) return;
    await deleteMedia({ path: { media_id: mediaId } });
    notifications.show({ message: t("common:toast.mediaDeleted"), color: "blue" });
    invalidate();
  }

  const totalPages = Math.max(1, Math.ceil(total / limit));
  const pageIds = items.map((i) => i.id);
  const allSelected = isAllSelected(pageIds);
  const effectiveSortBy = sortBy ?? "updated_at";
  const effectiveOrder = order ?? "desc";
  const busy = batchScraping || batchDeleting;

  function handlePageChange(p: number) {
    clear();
    onPageChange(p);
  }

  return (
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
            onClick={() => void handleBatchScrape()}
          >
            {t("actions.batchScrape")}
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
            {t("common:actions.delete")}
          </Button>
        </SelectionBar>
      }
      trailing={trailing}
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
            <Table.Th w={120}>{t("columns.metadata")}</Table.Th>
            {SORTABLE_COLUMNS.map((field) => (
              <SortableTh
                key={field}
                field={field}
                label={t(`columns.${COLUMN_I18N_KEY[field]}`)}
                sortBy={effectiveSortBy}
                order={effectiveOrder}
                onSort={onSort}
                w={COLUMN_WIDTH[field]}
              />
            ))}
            <Table.Th w={88} ta="right">
              {t("columns.actions")}
            </Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {items.map((item) => {
            const rel = relativePath(libraryPath, item.path);
            return (
              <Table.Tr
                key={item.id}
                bg={selected.has(item.id) ? "var(--mantine-color-blue-light)" : undefined}
              >
                <Table.Td>
                  <Checkbox checked={selected.has(item.id)} onChange={() => toggleOne(item.id)} />
                </Table.Td>
                <Table.Td style={CELL_OVERFLOW}>
                  {item.metadata_id != null ? (
                    <Link
                      to="/meta/$metadataId"
                      params={{ metadataId: String(item.metadata_id) }}
                      style={{ textDecoration: "none", display: "block", overflow: "hidden" }}
                    >
                      <Text
                        component="span"
                        size="sm"
                        ff="monospace"
                        c="brand"
                        truncate
                        title={`#${item.metadata_id}`}
                      >
                        #{item.metadata_id}
                      </Text>
                    </Link>
                  ) : (
                    <Text size="sm" c="dimmed">
                      —
                    </Text>
                  )}
                </Table.Td>
                <Table.Td style={CELL_OVERFLOW}>
                  <Stack gap={4}>
                    <Text size="sm" ff="monospace" truncate title={rel}>
                      {rel}
                    </Text>
                    <FilePhaseBadges phase={item} />
                  </Stack>
                </Table.Td>
                <Table.Td style={CELL_OVERFLOW}>
                  <Badge size="sm" variant="light" color={statusColor(item.status)}>
                    {t(`filters.${item.status}`)}
                  </Badge>
                </Table.Td>
                <Table.Td style={CELL_OVERFLOW}>
                  <Text size="sm" truncate>
                    {formatFileSize(item.size)}
                  </Text>
                </Table.Td>
                <Table.Td style={CELL_OVERFLOW}>
                  <Text size="sm" truncate>
                    {item.updated_at ? item.updated_at.slice(0, 10) : "—"}
                  </Text>
                </Table.Td>
                <Table.Td>
                  <Group gap={4} justify="flex-end" wrap="nowrap">
                    <ActionIcon
                      variant="subtle"
                      onClick={() =>
                        submitTask({ body: { type: "scrape", media_id: item.id } }).then(() =>
                          notifications.show({
                            message: t("common:toast.scrapeStarted"),
                            color: "blue",
                          }),
                        )
                      }
                      title={t("actions.scrape")}
                    >
                      <IconRefresh size={16} />
                    </ActionIcon>
                    <ActionIcon
                      variant="subtle"
                      color="red"
                      onClick={() => void handleDeleteOne(item.id)}
                      title={t("common:actions.delete")}
                    >
                      <IconTrash size={16} />
                    </ActionIcon>
                  </Group>
                </Table.Td>
              </Table.Tr>
            );
          })}
        </Table.Tbody>
      </Table>

      {!isLoading && items.length === 0 && (
        <Text c="dimmed" size="sm" ta="center" py="xl">
          {t("empty")}
        </Text>
      )}
    </ListToolbar>
  );
}
