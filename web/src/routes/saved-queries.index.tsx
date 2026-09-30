import {
  Badge,
  Button,
  Checkbox,
  Group,
  Loader,
  Modal,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconPlus, IconSearch, IconTrash } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  batchDeleteSavedQueriesMutation,
  createSavedQueryMutation,
  listSavedQueriesOptions,
  updateSavedQueryMutation,
} from "@/client/@tanstack/react-query.gen";
import type { SavedQueryEntity, SavedQueryResponse } from "@/client/types.gen";
import { EnumToggle } from "@/components/common/enum-toggle";
import { SavedQueryCard } from "@/components/saved-query/saved-query-card";
import {
  emptySavedQueryForm,
  isSavedQueryFormDirty,
  savedQueryFormFromResponse,
  savedQueryFormToCreateBody,
  savedQueryFormToUpdateBody,
  SavedQueryFormFields,
  SAVED_QUERY_FORM_MODAL_SIZE,
  type SavedQueryFormState,
} from "@/components/saved-query/saved-query-form";
import { useIdSelection } from "@/hooks/use-id-selection";
import { extractErrorMessage } from "@/lib/api-error";
import { confirm } from "@/lib/confirm";
import { SAVED_QUERY_ENTITIES } from "@/lib/exhaustive-maps";
import { SAVED_QUERY_ENTITY_LABEL_KEY } from "@/lib/saved-query/display";
import { downloadSavedQueryResult } from "@/lib/saved-query/download";

/** 信息量小于片库类页面, 宽屏下限制内容宽度并居中; 与插件页 / 网络检测页同宽. */
const PAGE_MAX_WIDTH = 1120;

type EntityFilter = SavedQueryEntity | "all";

const ENTITY_FILTERS: readonly EntityFilter[] = ["all", ...SAVED_QUERY_ENTITIES];

export const Route = createFileRoute("/saved-queries/")({ component: SavedQueriesPage });

function SavedQueriesPage() {
  const { t } = useTranslation(["savedQueries", "common"]);
  const queryClient = useQueryClient();
  const navigate = Route.useNavigate();
  const selection = useIdSelection();

  const [search, setSearch] = useState("");
  const [entityFilter, setEntityFilter] = useState<EntityFilter>("all");
  const [createOpen, setCreateOpen] = useState(false);
  const [createForm, setCreateForm] = useState<SavedQueryFormState>(emptySavedQueryForm);
  const [editing, setEditing] = useState<SavedQueryResponse | null>(null);
  const [editForm, setEditForm] = useState<SavedQueryFormState>(emptySavedQueryForm);

  // 本页只管理已保留的预设; 会话内的临时预设留在会话页查看.
  const listQuery = useQuery(listSavedQueriesOptions({ query: { persisted_only: true } }));
  const items = listQuery.data?.items ?? [];

  const needle = search.trim().toLowerCase();
  const visible = items.filter((query) => {
    if (entityFilter !== "all" && query.entity !== entityFilter) return false;
    if (needle === "") return true;
    return (
      query.name.toLowerCase().includes(needle) || query.description.toLowerCase().includes(needle)
    );
  });
  const visibleIds = visible.map((query) => query.id);
  const allVisibleSelected = selection.isAllSelected(visibleIds);

  function invalidateLists() {
    void queryClient.invalidateQueries({ queryKey: [{ _id: "listSavedQueries" }] });
  }

  function showError(err: unknown) {
    notifications.show({
      color: "red",
      message: extractErrorMessage(err, t("common:toast.operationFailed")),
    });
  }

  const createMutation = useMutation({
    ...createSavedQueryMutation(),
    onSuccess: (created) => {
      notifications.show({ color: "blue", message: t("createdToast") });
      setCreateOpen(false);
      setCreateForm(emptySavedQueryForm());
      invalidateLists();
      void navigate({
        to: "/saved-queries/$queryId",
        params: { queryId: String(created.id) },
      });
    },
    onError: showError,
  });
  const updateMutation = useMutation({
    ...updateSavedQueryMutation(),
    onSuccess: () => {
      notifications.show({ color: "blue", message: t("updatedToast") });
      setEditing(null);
      invalidateLists();
      void queryClient.invalidateQueries({ queryKey: [{ _id: "getSavedQuery" }] });
      void queryClient.invalidateQueries({ queryKey: [{ _id: "getSavedQueryResult" }] });
    },
    onError: showError,
  });
  const deleteMutation = useMutation({
    ...batchDeleteSavedQueriesMutation(),
    onSuccess: (result) => {
      if (result.deleted === 0) {
        notifications.show({
          color: "yellow",
          message: t("deleteNothingToast", { missing: result.missing }),
        });
      } else if (result.missing > 0) {
        notifications.show({
          color: "blue",
          message: t("deletedPartialToast", { deleted: result.deleted, missing: result.missing }),
        });
      } else {
        notifications.show({
          color: "blue",
          message: t("deletedToast", { count: result.deleted }),
        });
      }
      selection.clear();
      invalidateLists();
    },
    onError: showError,
  });

  function submitCreate() {
    createMutation.mutate({ body: savedQueryFormToCreateBody(createForm) });
  }

  function submitEdit() {
    if (editing == null) return;
    updateMutation.mutate({
      path: { query_id: editing.id },
      body: savedQueryFormToUpdateBody(editForm, editing),
    });
  }

  async function handleDelete(query: SavedQueryResponse) {
    const ok = await confirm({
      title: t("delete"),
      message: t("confirmDelete", { name: query.name }),
      confirmLabel: t("common:actions.delete"),
    });
    if (!ok) return;
    deleteMutation.mutate({ body: { ids: [query.id] } });
  }

  async function handleBatchDelete() {
    const ids = selection.selectedIds;
    const ok = await confirm({
      title: t("delete"),
      message: t("confirmBatchDelete", { count: ids.length }),
      confirmLabel: t("common:actions.delete"),
    });
    if (!ok) return;
    deleteMutation.mutate({ body: { ids } });
  }

  const editDirty = editing != null && isSavedQueryFormDirty(editForm, editing);
  const createDisabled = createForm.name.trim() === "" || createForm.sql.trim() === "";
  const editDisabled = !editDirty || editForm.name.trim() === "" || editForm.sql.trim() === "";

  return (
    <Stack gap="md" maw={PAGE_MAX_WIDTH} mx="auto" w="100%">
      <Group justify="space-between" align="center" wrap="wrap">
        <Title order={2}>{t("title")}</Title>
        <Button leftSection={<IconPlus size={16} />} onClick={() => setCreateOpen(true)}>
          {t("newPreset")}
        </Button>
      </Group>

      <TextInput
        value={search}
        onChange={(e) => setSearch(e.currentTarget.value)}
        placeholder={t("searchPlaceholder")}
        leftSection={<IconSearch size={16} />}
      />

      <Group gap="xs" wrap="wrap">
        <EnumToggle
          options={ENTITY_FILTERS}
          value={entityFilter}
          onChange={setEntityFilter}
          getLabel={(entity) =>
            entity === "all" ? t("filterAll") : t(SAVED_QUERY_ENTITY_LABEL_KEY[entity])
          }
        />
      </Group>

      <Group gap="sm" wrap="wrap" align="center">
        <Checkbox
          size="sm"
          checked={allVisibleSelected}
          indeterminate={!allVisibleSelected && selection.selectedIds.length > 0}
          disabled={visibleIds.length === 0}
          onChange={() => selection.toggleAll(visibleIds)}
          label={t("selectAll")}
        />
        {selection.selectedIds.length > 0 && (
          <>
            <Button
              size="compact-sm"
              color="red"
              variant="light"
              leftSection={<IconTrash size={14} />}
              loading={deleteMutation.isPending}
              onClick={() => void handleBatchDelete()}
            >
              {t("batchDelete")}
            </Button>
            <Badge size="sm" variant="light" tt="none">
              {t("common:batch.selected", { count: selection.selectedIds.length })}
            </Badge>
          </>
        )}
      </Group>

      {listQuery.isPending && (
        <Group justify="center" py="xl">
          <Loader size="sm" />
        </Group>
      )}

      {listQuery.isError && (
        <Text c="red" size="sm">
          {t("loadFailed")}
        </Text>
      )}

      {!listQuery.isPending && !listQuery.isError && items.length === 0 && (
        <Text c="dimmed" size="sm" ta="center" py="xl">
          {t("empty")}
        </Text>
      )}

      {!listQuery.isPending && !listQuery.isError && items.length > 0 && visible.length === 0 && (
        <Text c="dimmed" size="sm" ta="center" py="xl">
          {t("emptyFiltered")}
        </Text>
      )}

      {visible.length > 0 && (
        <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }} spacing="md">
          {visible.map((query) => (
            <SavedQueryCard
              key={query.id}
              query={query}
              selected={selection.selected.has(query.id)}
              onToggleSelect={() => selection.toggleOne(query.id)}
              onDownload={() =>
                void downloadSavedQueryResult(query.id, t("common:toast.operationFailed"))
              }
              onEdit={() => {
                setEditing(query);
                setEditForm(savedQueryFormFromResponse(query));
              }}
              onDelete={() => void handleDelete(query)}
            />
          ))}
        </SimpleGrid>
      )}

      <Modal
        opened={createOpen}
        onClose={() => setCreateOpen(false)}
        title={t("newPreset")}
        size={SAVED_QUERY_FORM_MODAL_SIZE}
        centered
      >
        <Stack gap="md">
          <SavedQueryFormFields value={createForm} onChange={setCreateForm} entityEditable />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setCreateOpen(false)}>
              {t("common:actions.cancel")}
            </Button>
            <Button
              loading={createMutation.isPending}
              disabled={createDisabled}
              onClick={submitCreate}
            >
              {t("common:actions.save")}
            </Button>
          </Group>
        </Stack>
      </Modal>

      <Modal
        opened={editing != null}
        onClose={() => setEditing(null)}
        title={t("editPreset")}
        size={SAVED_QUERY_FORM_MODAL_SIZE}
        centered
      >
        <Stack gap="md">
          <SavedQueryFormFields value={editForm} onChange={setEditForm} entityEditable={false} />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setEditing(null)}>
              {t("common:actions.cancel")}
            </Button>
            <Button loading={updateMutation.isPending} disabled={editDisabled} onClick={submitEdit}>
              {t("common:actions.save")}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
