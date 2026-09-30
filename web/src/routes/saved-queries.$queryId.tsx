import {
  Badge,
  Box,
  Button,
  Code,
  Collapse,
  Group,
  Modal,
  ScrollArea,
  Stack,
  Table,
  Text,
  Title,
  UnstyledButton,
} from "@mantine/core";
import {
  IconChevronDown,
  IconChevronRight,
  IconDownload,
  IconExternalLink,
  IconPencil,
  IconTrash,
} from "@tabler/icons-react";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, stripSearchParams } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { z } from "zod";
import {
  batchDeleteSavedQueriesMutation,
  batchPersistSavedQueriesMutation,
  getSavedQueryOptions,
  getSavedQueryResultOptions,
  updateSavedQueryMutation,
} from "@/client/@tanstack/react-query.gen";
import { ListPagination } from "@/components/common/list-pagination";
import { PageSizeSelect } from "@/components/common/page-size-select";
import {
  isSavedQueryFormDirty,
  isSavedQueryFormSubmittable,
  savedQueryFormFromResponse,
  savedQueryFormToUpdateBody,
  SavedQueryFormFields,
  SAVED_QUERY_FORM_MODAL_SIZE,
  type SavedQueryFormState,
} from "@/components/saved-query/saved-query-form";
import { confirm } from "@/lib/confirm";
import { extractErrorMessage } from "@/lib/api-error";
import { formatRelativeTime } from "@/lib/format-relative-time";
import {
  savedQueryBrowseHref,
  SAVED_QUERY_BADGE_COLOR,
  SAVED_QUERY_ENTITY_LABEL_KEY,
} from "@/lib/saved-query/display";
import { downloadSavedQueryResult } from "@/lib/saved-query/download";
import { useUIStore } from "@/stores/ui";

const savedQuerySearchSchema = z.object({
  page: z.coerce.number().int().min(1).catch(1).default(1),
});

export const Route = createFileRoute("/saved-queries/$queryId")({
  validateSearch: savedQuerySearchSchema,
  search: { middlewares: [stripSearchParams({ page: 1 })] },
  component: SavedQueryDataPage,
});

function formatCell(value: unknown): string {
  if (value === null) return "NULL";
  if (typeof value === "string") return value;
  return String(value);
}

function SavedQueryDataPage() {
  const { queryId } = Route.useParams();
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  const qc = useQueryClient();
  const { t, i18n } = useTranslation(["savedQueries", "common"]);
  const [sqlOpen, setSqlOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [editForm, setEditForm] = useState<SavedQueryFormState | null>(null);
  const pageSize = useUIStore((s) => s.pageSizes.savedQuery);

  const id = Number(queryId);
  const metaQuery = useQuery(getSavedQueryOptions({ path: { query_id: id } }));
  const resultQuery = useQuery({
    ...getSavedQueryResultOptions({
      path: { query_id: id },
      query: { offset: (search.page - 1) * pageSize, limit: pageSize },
    }),
    enabled: metaQuery.isSuccess,
  });

  function invalidateQueries() {
    void qc.invalidateQueries({ queryKey: [{ _id: "getSavedQuery" }] });
    void qc.invalidateQueries({ queryKey: [{ _id: "getSavedQueryResult" }] });
    void qc.invalidateQueries({ queryKey: [{ _id: "listSavedQueries" }] });
  }

  function showError(err: unknown) {
    notifications.show({
      color: "red",
      message: extractErrorMessage(err, t("common:toast.operationFailed")),
    });
  }

  const updateMutation = useMutation({
    ...updateSavedQueryMutation(),
    onSuccess: () => {
      notifications.show({ color: "blue", message: t("updatedToast") });
      setEditOpen(false);
      invalidateQueries();
    },
    onError: showError,
  });
  const persistMutation = useMutation({
    ...batchPersistSavedQueriesMutation(),
    onSuccess: () => {
      notifications.show({ color: "blue", message: t("persistedToast") });
      invalidateQueries();
    },
    onError: showError,
  });
  const deleteMutation = useMutation({
    ...batchDeleteSavedQueriesMutation(),
    onSuccess: async (result) => {
      if (result.deleted === 0) {
        notifications.show({
          color: "yellow",
          message: t("deleteNothingToast", { missing: result.missing }),
        });
      } else {
        notifications.show({
          color: "blue",
          message: t("deletedToast", { count: result.deleted }),
        });
      }
      await qc.invalidateQueries({ queryKey: [{ _id: "listSavedQueries" }] });
      await navigate({ to: "/saved-queries" });
    },
    onError: showError,
  });

  const query = metaQuery.data;
  const entity = query?.entity;
  const href = query != null ? savedQueryBrowseHref({ id, entity: query.entity }) : null;
  const total = resultQuery.data?.total ?? 0;
  const columns = resultQuery.data?.columns ?? [];
  const rows = resultQuery.data?.rows ?? [];
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const rangeStart = total === 0 ? 0 : (search.page - 1) * pageSize + 1;
  const rangeEnd = Math.min(total, search.page * pageSize);

  async function handleDelete() {
    const ok = await confirm({
      title: t("delete"),
      message: t("confirmDelete", { name: query?.name ?? `#${id}` }),
      confirmLabel: t("common:actions.delete"),
    });
    if (!ok) return;
    deleteMutation.mutate({ body: { ids: [id] } });
  }

  function openEdit() {
    if (query == null) return;
    setEditForm(savedQueryFormFromResponse(query));
    setEditOpen(true);
  }

  if (metaQuery.isLoading) {
    return <Text c="dimmed">{t("common:status.loading")}</Text>;
  }
  if (metaQuery.isError || query == null) {
    return (
      <Stack gap="sm">
        <Title order={2}>#{id}</Title>
        <Text c="dimmed">{t("dataPageNotFound")}</Text>
      </Stack>
    );
  }

  const editDirty =
    editForm != null &&
    isSavedQueryFormDirty(editForm, query) &&
    isSavedQueryFormSubmittable(editForm);

  return (
    <Stack gap="md" style={{ minWidth: 0 }}>
      <Group justify="space-between" align="flex-start" wrap="wrap">
        <Stack gap={6}>
          <Title order={2}>{query.name}</Title>
          <Group gap={8}>
            <Badge size="sm" variant="light" color={SAVED_QUERY_BADGE_COLOR[query.entity]}>
              {t(SAVED_QUERY_ENTITY_LABEL_KEY[query.entity])}
            </Badge>
            {query.persisted && (
              <Badge size="sm" variant="outline" color="teal">
                {t("persistedBadge")}
              </Badge>
            )}
            <Text size="xs" c="dimmed" ff="monospace">
              #{id}
            </Text>
            <Text size="xs" c="dimmed">
              {t("updatedAt", {
                time: formatRelativeTime(query.updated_at, i18n.language, t("justNow")),
              })}
            </Text>
          </Group>
          {query.description.trim() !== "" && (
            <Text size="sm" c="dimmed" maw={720}>
              {query.description}
            </Text>
          )}
        </Stack>
        <Group gap="xs" wrap="wrap">
          <Button
            size="xs"
            variant="default"
            leftSection={<IconPencil size={14} />}
            onClick={openEdit}
          >
            {t("common:actions.edit")}
          </Button>
          {href != null && (
            <Button
              component="a"
              href={href}
              target="_blank"
              rel="noreferrer"
              size="xs"
              variant="light"
              leftSection={<IconExternalLink size={14} />}
            >
              {entity === "actor" ? t("openActors") : t("openMeta")}
            </Button>
          )}
          {!query.persisted && (
            <Button
              size="xs"
              variant="default"
              loading={persistMutation.isPending}
              onClick={() => persistMutation.mutate({ body: { ids: [id] } })}
            >
              {t("persist")}
            </Button>
          )}
          <Button
            size="xs"
            variant="default"
            leftSection={<IconDownload size={14} />}
            onClick={() => void downloadSavedQueryResult(id, t("common:toast.operationFailed"))}
          >
            {t("download")}
          </Button>
          <Button
            size="xs"
            variant="subtle"
            color="red"
            leftSection={<IconTrash size={14} />}
            loading={deleteMutation.isPending}
            onClick={() => void handleDelete()}
          >
            {t("delete")}
          </Button>
        </Group>
      </Group>

      <UnstyledButton onClick={() => setSqlOpen((v) => !v)} style={{ textAlign: "left" }}>
        <Group gap="xs" wrap="nowrap">
          <Box c="dimmed" style={{ display: "flex", lineHeight: 0 }}>
            {sqlOpen ? <IconChevronDown size={16} /> : <IconChevronRight size={16} />}
          </Box>
          <Text size="sm" c="dimmed" ff="monospace" lineClamp={1}>
            {query.sql}
          </Text>
        </Group>
      </UnstyledButton>
      <Collapse expanded={sqlOpen}>
        <Code
          block
          style={{
            maxHeight: 240,
            overflow: "auto",
            fontSize: 12,
            borderRadius: "var(--mantine-radius-sm)",
          }}
        >
          {query.sql}
        </Code>
      </Collapse>

      <Group justify="space-between" wrap="wrap">
        <Text size="sm" c="dimmed">
          {t("common:pagination.range", { start: rangeStart, end: rangeEnd, total })}
        </Text>
        <PageSizeSelect
          sizeKey="savedQuery"
          onChanged={() => void navigate({ search: (prev) => ({ ...prev, page: 1 }) })}
        />
      </Group>

      {resultQuery.isLoading ? (
        <Text c="dimmed">{t("common:status.loading")}</Text>
      ) : resultQuery.isError ? (
        <Text c="dimmed">{t("dataPageFailed")}</Text>
      ) : total === 0 ? (
        <Text c="dimmed">{t("dataPageEmpty")}</Text>
      ) : (
        <>
          <ScrollArea type="auto" offsetScrollbars>
            <Table
              stickyHeader
              striped
              highlightOnHover
              withTableBorder
              horizontalSpacing="xs"
              verticalSpacing="xs"
              style={{ tableLayout: "fixed", minWidth: 640 }}
            >
              <Table.Thead>
                <Table.Tr>
                  {columns.map((c) => (
                    <Table.Th key={c} fz="xs" ff="monospace">
                      {c}
                    </Table.Th>
                  ))}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {rows.map((row, i) => (
                  <Table.Tr key={i}>
                    {row.map((cell, j) => (
                      <Table.Td key={j} fz="sm">
                        <Text size="sm" truncate title={formatCell(cell)}>
                          {formatCell(cell)}
                        </Text>
                      </Table.Td>
                    ))}
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </ScrollArea>
          {totalPages > 1 && (
            <Group justify="center">
              <ListPagination
                totalPages={totalPages}
                page={search.page}
                onChange={(page) => void navigate({ search: (prev) => ({ ...prev, page }) })}
              />
            </Group>
          )}
        </>
      )}

      <Modal
        opened={editOpen}
        onClose={() => setEditOpen(false)}
        title={t("editPreset")}
        size={SAVED_QUERY_FORM_MODAL_SIZE}
        centered
      >
        {editForm != null && (
          <Stack gap="md">
            <SavedQueryFormFields value={editForm} onChange={setEditForm} entityEditable={false} />
            <Group justify="flex-end">
              <Button variant="default" onClick={() => setEditOpen(false)}>
                {t("common:actions.cancel")}
              </Button>
              <Button
                loading={updateMutation.isPending}
                disabled={!editDirty}
                onClick={() =>
                  updateMutation.mutate({
                    path: { query_id: id },
                    body: savedQueryFormToUpdateBody(editForm, query),
                  })
                }
              >
                {t("common:actions.save")}
              </Button>
            </Group>
          </Stack>
        )}
      </Modal>
    </Stack>
  );
}
