import {
  ActionIcon,
  Badge,
  Box,
  Button,
  Checkbox,
  Group,
  Menu,
  Modal,
  MultiSelect,
  Stack,
  Table,
  Text,
  type MantineBreakpoint,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconArrowMerge, IconDots, IconRefresh, IconTag, IconTrash } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  batchDeleteMetadataMutation,
  batchMetadataUserTagsMutation,
  batchScrapeMetadataMutation,
  deleteMetadataMutation,
  getMetadataQueryKey,
  listFacetsOptions,
  listMetadataQueryKey,
  mergeMetadataEntriesMutation,
  submitTaskMutation,
} from "@/client/@tanstack/react-query.gen";
import type { CacheKind, MetadataListItem, MetadataSortField, SortOrder } from "@/client/types.gen";
import { HintedActionIcon } from "@/components/common/hinted-action-icon";
import { ListToolbar } from "@/components/common/list-toolbar";
import { SortableTh } from "@/components/common/sortable-th";
import { SelectionBar } from "@/components/common/selection-bar";
import { MergeNotice } from "@/components/media/merge-notice";
import { useIdSelection } from "@/hooks/use-id-selection";
import { useResizableColumns } from "@/hooks/use-resizable-columns";
import { extractErrorMessage } from "@/lib/api-error";
import { confirm, confirmWithCheckbox } from "@/lib/confirm";
import { USER_TAG_FACET_LIST } from "@/lib/facets";
import { type MetaTableColumnKey, useUIStore } from "@/stores/ui";
import { VrBadge } from "./vr-badge";
import classes from "./meta-table.module.css";

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

/**
 * 次要列的隐藏断点.
 * lg 以下固定列与动作列的默认宽度之和为 760, 超过容器宽度时自适应列 title 归零.
 * 该映射同时决定表头与单元格的显隐, 任一处缺失即列错位.
 */
const COLUMN_VISIBLE_FROM: Partial<Record<MetaTableColumnKey, MantineBreakpoint>> = {
  studio: "lg",
  release: "lg",
  updated_at: "lg",
  file_count: "lg",
  score: "lg",
};

const CELL_OVERFLOW = { overflow: "hidden", maxWidth: 0 } as const;

export interface MetaTableProps {
  items: MetadataListItem[];
  isLoading: boolean;
  total: number;
  page: number;
  sortBy: MetadataSortField | undefined;
  order: SortOrder | undefined;
  onPageChange: (page: number) => void;
  onSort: (field: MetadataSortField) => void;
}

function cellValue(item: MetadataListItem, field: SortableColumn): string {
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

const SCRAPE_CACHE_DEFAULT: CacheKind[] = ["metadata", "trans"];
const SCRAPE_CACHE_FORCE: CacheKind[] = [];

/** 列表内的单条与批量重新刮削共用这组选项, 语义与元数据详情页的刮削菜单一致. */
function ScrapeMenuItems({ onScrape }: { onScrape: (useCache: CacheKind[]) => void }) {
  const { t } = useTranslation("common");
  return (
    <>
      <Menu.Item onClick={() => onScrape(SCRAPE_CACHE_DEFAULT)}>
        <Text size="sm">{t("actions.scrapeNormal")}</Text>
        <Text size="xs" c="dimmed">
          {t("actions.scrapeNormalDesc")}
        </Text>
      </Menu.Item>
      <Menu.Item onClick={() => onScrape(SCRAPE_CACHE_FORCE)}>
        <Text size="sm">{t("actions.scrapeForce")}</Text>
        <Text size="xs" c="dimmed">
          {t("actions.scrapeForceDesc")}
        </Text>
      </Menu.Item>
    </>
  );
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
  const [tagIds, setTagIds] = useState<string[]>([]);

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
    onSuccess: () => {
      notifications.show({
        message: t("common:toast.metadataDeleted"),
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
        message: t("common:toast.userTagBatchUpdated", { count: res.changed }),
        color: "blue",
      });
      setTagModalOpen(false);
      setTagIds([]);
      invalidate();
    },
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      }),
  });

  const mergeEntries = useMutation({
    ...mergeMetadataEntriesMutation(),
    onSuccess: (res, variables) => {
      notifications.show({
        message: variables.body.rescrape
          ? t("common:toast.scrapeStarted")
          : t("common:toast.metadataUpdated"),
        color: "blue",
      });
      clear();
      void queryClient.invalidateQueries({ queryKey: listMetadataQueryKey() });
      void queryClient.invalidateQueries({
        queryKey: getMetadataQueryKey({ path: { metadata_id: res.id } }),
      });
    },
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      }),
  });

  const busy =
    batchScrape.isPending || batchDelete.isPending || batchTags.isPending || mergeEntries.isPending;

  async function handleMergeEntries(target: MetadataListItem) {
    const sourceIds = selectedIds.filter((id) => id !== target.id);
    if (sourceIds.length === 0) {
      notifications.show({
        message: t("manage.mergeNeedSources"),
        color: "yellow",
      });
      return;
    }
    const result = await confirmWithCheckbox({
      title: t("manage.mergeConfirm"),
      message: (
        <MergeNotice
          lead={t("manage.mergeEntriesLead", { count: sourceIds.length, number: target.number })}
          items={[
            t("manage.mergeEntriesKeep"),
            t("manage.mergeEntriesRaw"),
            t("manage.mergeEntriesLinks"),
            t("manage.mergeEntriesDelete"),
          ]}
        />
      ),
      // 勾选后改走重新刮削: 按当前优先级从 raw 聚合, 目标未锁定字段不再保持不变.
      checkbox: { label: t("manage.mergeEntriesRescrape") },
      confirmLabel: t("common:actions.confirm"),
      danger: false,
      confirmColor: "orange",
    });
    if (!result.ok) return;
    mergeEntries.mutate({
      body: { target_id: target.id, source_ids: sourceIds, rescrape: result.checked },
    });
  }

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
          <SelectionBar count={selected.size} hint={t("manage.mergeGuide")}>
            <Menu shadow="md" position="bottom-start">
              <Menu.Target>
                <Button
                  size="xs"
                  variant="light"
                  leftSection={<IconRefresh size={14} />}
                  loading={busy}
                  disabled={selected.size === 0}
                >
                  {t("actions.batchScrape")}
                </Button>
              </Menu.Target>
              <Menu.Dropdown>
                <ScrapeMenuItems
                  onScrape={(useCache) =>
                    batchScrape.mutate({ body: { ids: selectedIds, use_cache: useCache } })
                  }
                />
              </Menu.Dropdown>
            </Menu>
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
          className={classes.table}
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
                  visibleFrom={COLUMN_VISIBLE_FROM[field]}
                  resizeHandle={getResizeHandleProps(field)}
                />
              ))}
              {/* 评分列不入 SORTABLE_COLUMNS: 该数组同时生成表头与单元格, 而评分单元格是徽标而非文本. */}
              <SortableTh
                field="score"
                label={t("columns.score")}
                sortBy={effectiveSortBy}
                order={effectiveOrder}
                onSort={onSort}
                w={columnWidth("score")}
                visibleFrom={COLUMN_VISIBLE_FROM.score}
                resizeHandle={getResizeHandleProps("score")}
              />
              <Table.Th ta="right" className={classes.actionsColumn}>
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
                    <Table.Td
                      key={field}
                      style={CELL_OVERFLOW}
                      visibleFrom={COLUMN_VISIBLE_FROM[field]}
                    >
                      {field === "number" ? (
                        /* flex 容器不用 CELL_OVERFLOW 的 maxWidth: 0, 否则 VR 标识也被裁掉. */
                        <Group gap={4} wrap="nowrap" style={{ overflow: "hidden" }}>
                          <Link
                            to="/meta/$metadataId"
                            params={{ metadataId: String(item.id) }}
                            style={{
                              textDecoration: "none",
                              minWidth: 0,
                              flex: "0 1 auto",
                              overflow: "hidden",
                            }}
                          >
                            <Text component="span" size="sm" ff="monospace" truncate title={value}>
                              {value}
                            </Text>
                          </Link>
                          {item.vr && <VrBadge />}
                        </Group>
                      ) : (
                        <Text size="sm" truncate title={value || undefined}>
                          {value}
                        </Text>
                      )}
                    </Table.Td>
                  );
                })}
                <Table.Td style={CELL_OVERFLOW} visibleFrom={COLUMN_VISIBLE_FROM.score}>
                  {item.score != null && (
                    <Badge variant="light" color="yellow">
                      {item.score.toFixed(1)}
                    </Badge>
                  )}
                </Table.Td>
                <Table.Td>
                  {/* 窄屏动作列只容得下一个按钮, 全部动作移入菜单. */}
                  <Group gap={4} justify="flex-end" wrap="nowrap" visibleFrom="sm">
                    <Menu shadow="md" position="bottom-start">
                      <Menu.Target>
                        <HintedActionIcon variant="subtle" label={t("actions.scrape")}>
                          <IconRefresh size={16} />
                        </HintedActionIcon>
                      </Menu.Target>
                      <Menu.Dropdown>
                        <ScrapeMenuItems
                          onScrape={(useCache) =>
                            scrapeOne.mutate({
                              body: { type: "scrape", number: item.number, use_cache: useCache },
                            })
                          }
                        />
                      </Menu.Dropdown>
                    </Menu>
                    <HintedActionIcon
                      variant="subtle"
                      label={t("manage.merge")}
                      disabled={mergeEntries.isPending}
                      onClick={() => void handleMergeEntries(item)}
                    >
                      <IconArrowMerge size={16} />
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
                        <Menu.Sub>
                          <Menu.Sub.Target>
                            <Menu.Sub.Item leftSection={<IconRefresh size={14} />}>
                              {t("actions.scrape")}
                            </Menu.Sub.Item>
                          </Menu.Sub.Target>
                          <Menu.Sub.Dropdown>
                            <ScrapeMenuItems
                              onScrape={(useCache) =>
                                scrapeOne.mutate({
                                  body: {
                                    type: "scrape",
                                    number: item.number,
                                    use_cache: useCache,
                                  },
                                })
                              }
                            />
                          </Menu.Sub.Dropdown>
                        </Menu.Sub>
                        <Menu.Item
                          leftSection={<IconArrowMerge size={14} />}
                          disabled={mergeEntries.isPending}
                          onClick={() => void handleMergeEntries(item)}
                        >
                          {t("manage.merge")}
                        </Menu.Item>
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
          <MultiSelect
            data={tagOptions}
            value={tagIds}
            onChange={setTagIds}
            searchable
            placeholder={t("detail.selectUserTag")}
          />
          <Group justify="flex-end">
            <Button
              variant="light"
              disabled={tagIds.length === 0}
              loading={batchTags.isPending}
              onClick={() =>
                tagIds.length > 0 &&
                batchTags.mutate({
                  body: {
                    ids: selectedIds,
                    user_tag_ids: tagIds.map(Number),
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
              disabled={tagIds.length === 0}
              loading={batchTags.isPending}
              onClick={() =>
                tagIds.length > 0 &&
                batchTags.mutate({
                  body: {
                    ids: selectedIds,
                    user_tag_ids: tagIds.map(Number),
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
