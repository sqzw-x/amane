import {
  Badge,
  Button,
  Checkbox,
  Group,
  Modal,
  Stack,
  Table,
  Text,
  TextInput,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconArrowMerge, IconPencil, IconPlus, IconTrash } from "@tabler/icons-react";
import { useMutation } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { createUserTagMutation, listFacetsQueryKey } from "@/client/@tanstack/react-query.gen";
import type { FacetKind, FacetResponse, FacetSortField, SortOrder } from "@/client/types.gen";
import { FacetRulesPanel } from "./facet-rules-panel";
import { HintedActionIcon } from "@/components/common/hinted-action-icon";
import { ListToolbar } from "@/components/common/list-toolbar";
import { SelectionBar } from "@/components/common/selection-bar";
import { SortableTh } from "@/components/common/sortable-th";
import { useFacetIdentityActions } from "@/hooks/use-facet-identity-actions";
import { useIdSelection } from "@/hooks/use-id-selection";
import { extractErrorMessage } from "@/lib/api-error";
import { useUIStore } from "@/stores/ui";

export interface CatalogFacetTableProps {
  kind: FacetKind;
  items: FacetResponse[];
  isLoading: boolean;
  total: number;
  page: number;
  sortBy: FacetSortField | undefined;
  order: SortOrder | undefined;
  onPageChange: (page: number) => void;
  onSort: (field: FacetSortField) => void;
}

export function CatalogFacetTable({
  kind,
  items,
  isLoading,
  total,
  page,
  sortBy,
  order,
  onPageChange,
  onSort,
}: CatalogFacetTableProps) {
  const { t } = useTranslation(["metadata", "common", "library"]);
  const limit = useUIStore((s) => s.pageSizes.catalogList);
  const pageIds = items.map((i) => i.id);

  const { selected, toggleOne, toggleAll, isAllSelected, clear } = useIdSelection();
  const identity = useFacetIdentityActions({
    kind,
    listQueryKey: listFacetsQueryKey({ path: { kind } }),
    confirmMerge: true,
    onMerged: clear,
  });
  const { isUserTag } = identity;

  const [newTagName, setNewTagName] = useState("");

  const createMutation = useMutation({
    ...createUserTagMutation(),
    onSuccess: () => {
      notifications.show({ message: t("common:toast.userTagCreated"), color: "blue" });
      setNewTagName("");
      identity.invalidate();
    },
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      }),
  });

  const totalPages = Math.max(1, Math.ceil(total / limit));
  const effectiveSortBy = sortBy ?? "name";
  const effectiveOrder = order ?? "asc";
  const allSelected = isAllSelected(pageIds);

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
          <Stack gap="sm">
            {isUserTag && (
              <Group gap="xs">
                <TextInput
                  value={newTagName}
                  onChange={(e) => setNewTagName(e.currentTarget.value)}
                  placeholder={t("detail.newUserTagPlaceholder")}
                  size="sm"
                  maw={280}
                />
                <Button
                  size="sm"
                  leftSection={<IconPlus size={14} />}
                  disabled={!newTagName.trim()}
                  loading={createMutation.isPending}
                  onClick={() => createMutation.mutate({ body: { name: newTagName.trim() } })}
                >
                  {t("common:actions.add")}
                </Button>
              </Group>
            )}
            <SelectionBar count={selected.size} hint={t("manage.mergeGuide")} />
          </Stack>
        }
        trailing={
          !isUserTag ? (
            <FacetRulesPanel kind={kind} onDeleteRule={identity.deleteRule} />
          ) : undefined
        }
      >
        <Table stickyHeader highlightOnHover verticalSpacing="sm">
          <Table.Thead>
            <Table.Tr>
              <Table.Th w={36}>
                <Checkbox checked={allSelected} onChange={() => toggleAll(pageIds)} />
              </Table.Th>
              <SortableTh
                field="name"
                label={t("manage.name")}
                sortBy={effectiveSortBy}
                order={effectiveOrder}
                onSort={onSort}
              />
              <SortableTh
                field="count"
                label={t("manage.count")}
                sortBy={effectiveSortBy}
                order={effectiveOrder}
                onSort={onSort}
                w={100}
              />
              <Table.Th ta="right" w={160}>
                {t("columns.actions")}
              </Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {items.map((facet) => (
              <Table.Tr key={facet.id}>
                <Table.Td>
                  <Checkbox checked={selected.has(facet.id)} onChange={() => toggleOne(facet.id)} />
                </Table.Td>
                <Table.Td>
                  <Link
                    to="/catalog/$kind/$facetId"
                    params={{ kind, facetId: String(facet.id) }}
                    style={{ textDecoration: "none", color: "inherit" }}
                  >
                    <Text span c="brand" style={{ cursor: "pointer" }}>
                      {facet.name}
                    </Text>
                  </Link>
                </Table.Td>
                <Table.Td>
                  <Badge variant="light">{facet.count}</Badge>
                </Table.Td>
                <Table.Td>
                  <Group gap={4} justify="flex-end">
                    <HintedActionIcon
                      variant="subtle"
                      label={t("common:actions.edit")}
                      onClick={() => identity.openRename({ id: facet.id, name: facet.name })}
                    >
                      <IconPencil size={16} />
                    </HintedActionIcon>
                    <HintedActionIcon
                      variant="subtle"
                      label={t("manage.merge", { defaultValue: "合并到此项" })}
                      onClick={() => void identity.openMerge(facet.id, selected)}
                    >
                      <IconArrowMerge size={16} />
                    </HintedActionIcon>
                    <HintedActionIcon
                      variant="subtle"
                      color="red"
                      label={t("common:actions.delete")}
                      onClick={() => void identity.openDelete({ id: facet.id, name: facet.name })}
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
            {t("common:status.empty")}
          </Text>
        )}
      </ListToolbar>

      <Modal
        opened={identity.renameTarget != null}
        onClose={identity.closeRename}
        title={t("common:actions.edit")}
        centered
      >
        <Stack gap="md">
          <TextInput
            value={identity.renameValue}
            onChange={(e) => identity.setRenameValue(e.currentTarget.value)}
          />
          <Group justify="flex-end">
            <Button variant="default" onClick={identity.closeRename}>
              {t("common:actions.cancel")}
            </Button>
            <Button
              loading={identity.renamePending}
              disabled={!identity.renameValue.trim()}
              onClick={identity.submitRename}
            >
              {t("common:actions.save")}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </>
  );
}
