import {
  ActionIcon,
  Badge,
  Box,
  Button,
  Checkbox,
  Group,
  Menu,
  Stack,
  Table,
  Text,
  type MantineBreakpoint,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconDots, IconFolderDown, IconForms, IconRefresh, IconTrash } from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";
import { listMediaQueryKey } from "@/client/@tanstack/react-query.gen";
import { deleteMedia, submitTask } from "@/client/sdk.gen";
import type {
  ContentType,
  MediaFileResponse,
  MediaFileStatus,
  MediaSortField,
  SortOrder,
} from "@/client/types.gen";
import { HintedActionIcon } from "@/components/common/hinted-action-icon";
import { FilePhaseBadges } from "@/components/media/file-phase-badges";
import { ListToolbar } from "@/components/common/list-toolbar";
import { SortableTh } from "@/components/common/sortable-th";
import { SelectionBar } from "@/components/common/selection-bar";
import { ScrapeOverrideDialog } from "./scrape-override-dialog";
import { useIdSelection } from "@/hooks/use-id-selection";
import { extractErrorMessage } from "@/lib/api-error";
import { confirm } from "@/lib/confirm";
import { assertNever } from "@/lib/exhaustive";
import { formatFileSize } from "@/lib/utils";
import { useUIStore } from "@/stores/ui";
import classes from "./library-media-table.module.css";

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

/**
 * 各列的隐藏断点: 可见列宽之和超出容器时表格横向滚动, 路径列随之移出首屏.
 * 该映射同时决定表头与单元格的显隐, 任一处缺失即列错位.
 */
const COLUMN_VISIBLE_FROM: Partial<Record<SortableColumn, MantineBreakpoint>> = {
  size: "sm",
  updated_at: "sm",
};

/** 元数据列不可排序, 与 size / updated 同在 sm 以下隐藏. */
const METADATA_VISIBLE_FROM: MantineBreakpoint = "sm";

export interface LibraryMediaTableProps {
  libraryId: number;
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
  libraryId,
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
  const [batchOrganizing, setBatchOrganizing] = useState(false);
  const [batchDeleting, setBatchDeleting] = useState(false);
  const [overrideTarget, setOverrideTarget] = useState<MediaFileResponse | null>(null);
  const [overrideSaving, setOverrideSaving] = useState(false);

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

  async function handleBatchOrganize() {
    const ids = selectedIds;
    setBatchOrganizing(true);
    try {
      await submitTask({
        body: { type: "organize", library_id: libraryId, media_file_ids: ids },
        throwOnError: true,
      });
      notifications.show({
        message: t("common:toast.organizeStarted"),
        color: "blue",
      });
      clear();
    } catch (err) {
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      });
    } finally {
      setBatchOrganizing(false);
    }
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

  async function handleOverrideScrape(number: string, contentType: ContentType | undefined) {
    if (overrideTarget == null) return;
    setOverrideSaving(true);
    try {
      await submitTask({
        body: {
          type: "scrape",
          media_id: overrideTarget.id,
          number,
          ...(contentType != null ? { content_type: contentType } : {}),
        },
        throwOnError: true,
      });
      notifications.show({
        message: t("common:toast.scrapeStarted"),
        color: "blue",
      });
      setOverrideTarget(null);
    } catch (err) {
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      });
    } finally {
      setOverrideSaving(false);
    }
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
  const busy = batchScraping || batchOrganizing || batchDeleting;

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
          {/* 窄屏三个批量按钮分成多行, 占用表体高度: sm 以下改由菜单提供, 选中数仍由 Badge 常驻呈现. */}
          <Group gap="xs" wrap="wrap" visibleFrom="sm">
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
              leftSection={<IconFolderDown size={14} />}
              loading={busy}
              disabled={selected.size === 0}
              onClick={() => void handleBatchOrganize()}
            >
              {t("actions.batchOrganize")}
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
          </Group>
          <Box hiddenFrom="sm">
            <Menu position="bottom-start" withinPortal>
              <Menu.Target>
                <Button size="xs" variant="light" loading={busy} disabled={selected.size === 0}>
                  {t("columns.actions")}
                </Button>
              </Menu.Target>
              <Menu.Dropdown>
                <Menu.Item
                  leftSection={<IconRefresh size={14} />}
                  disabled={busy}
                  onClick={() => void handleBatchScrape()}
                >
                  {t("actions.batchScrape")}
                </Menu.Item>
                <Menu.Item
                  leftSection={<IconFolderDown size={14} />}
                  disabled={busy}
                  onClick={() => void handleBatchOrganize()}
                >
                  {t("actions.batchOrganize")}
                </Menu.Item>
                <Menu.Divider />
                <Menu.Item
                  color="red"
                  leftSection={<IconTrash size={14} />}
                  disabled={busy}
                  onClick={() => void handleBatchDelete()}
                >
                  {t("common:actions.delete")}
                </Menu.Item>
              </Menu.Dropdown>
            </Menu>
          </Box>
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
        className={classes.table}
      >
        <Table.Thead>
          <Table.Tr>
            <Table.Th w={40}>
              <Checkbox checked={allSelected} onChange={() => toggleAll(pageIds)} />
            </Table.Th>
            <Table.Th w={120} visibleFrom={METADATA_VISIBLE_FROM}>
              {t("columns.metadata")}
            </Table.Th>
            {SORTABLE_COLUMNS.map((field) => (
              <SortableTh
                key={field}
                field={field}
                label={t(`columns.${COLUMN_I18N_KEY[field]}`)}
                sortBy={effectiveSortBy}
                order={effectiveOrder}
                onSort={onSort}
                w={COLUMN_WIDTH[field]}
                visibleFrom={COLUMN_VISIBLE_FROM[field]}
              />
            ))}
            <Table.Th ta="right" className={classes.actionsColumn}>
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
                <Table.Td style={CELL_OVERFLOW} visibleFrom={METADATA_VISIBLE_FROM}>
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
                <Table.Td style={CELL_OVERFLOW} visibleFrom={COLUMN_VISIBLE_FROM.size}>
                  <Text size="sm" truncate>
                    {formatFileSize(item.size)}
                  </Text>
                </Table.Td>
                <Table.Td style={CELL_OVERFLOW} visibleFrom={COLUMN_VISIBLE_FROM.updated_at}>
                  <Text size="sm" truncate>
                    {item.updated_at ? item.updated_at.slice(0, 10) : "—"}
                  </Text>
                </Table.Td>
                <Table.Td>
                  {/* 窄屏动作列只容得下一个按钮, 三个动作移入菜单. */}
                  <Group gap={4} justify="flex-end" wrap="nowrap" visibleFrom="sm">
                    <HintedActionIcon
                      variant="subtle"
                      label={t("actions.scrape")}
                      onClick={() =>
                        submitTask({ body: { type: "scrape", media_id: item.id } }).then(() =>
                          notifications.show({
                            message: t("common:toast.scrapeStarted"),
                            color: "blue",
                          }),
                        )
                      }
                    >
                      <IconRefresh size={16} />
                    </HintedActionIcon>
                    <HintedActionIcon
                      variant="subtle"
                      label={t("actions.scrapeWithNumber")}
                      onClick={() => setOverrideTarget(item)}
                    >
                      <IconForms size={16} />
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
                  {/* Menu 不接受 visibleFrom / hiddenFrom, 显隐由外层 Box 承担. */}
                  <Box hiddenFrom="sm" style={{ display: "flex", justifyContent: "flex-end" }}>
                    <Menu position="bottom-end" withinPortal>
                      <Menu.Target>
                        <ActionIcon size="sm" variant="subtle" aria-label={t("columns.actions")}>
                          <IconDots size={14} />
                        </ActionIcon>
                      </Menu.Target>
                      <Menu.Dropdown>
                        <Menu.Item
                          leftSection={<IconRefresh size={14} />}
                          onClick={() =>
                            submitTask({ body: { type: "scrape", media_id: item.id } }).then(() =>
                              notifications.show({
                                message: t("common:toast.scrapeStarted"),
                                color: "blue",
                              }),
                            )
                          }
                        >
                          {t("actions.scrape")}
                        </Menu.Item>
                        <Menu.Item
                          leftSection={<IconForms size={14} />}
                          onClick={() => setOverrideTarget(item)}
                        >
                          {t("actions.scrapeWithNumber")}
                        </Menu.Item>
                        <Menu.Divider />
                        <Menu.Item
                          color="red"
                          leftSection={<IconTrash size={14} />}
                          onClick={() => void handleDeleteOne(item.id)}
                        >
                          {t("common:actions.delete")}
                        </Menu.Item>
                      </Menu.Dropdown>
                    </Menu>
                  </Box>
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
      <ScrapeOverrideDialog
        target={overrideTarget}
        saving={overrideSaving}
        onClose={() => {
          if (!overrideSaving) setOverrideTarget(null);
        }}
        onSubmit={(number, contentType) => void handleOverrideScrape(number, contentType)}
      />
    </ListToolbar>
  );
}
