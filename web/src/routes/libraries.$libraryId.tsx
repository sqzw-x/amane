import {
  ActionIcon,
  Anchor,
  Badge,
  Button,
  Collapse,
  Group,
  Modal,
  SegmentedControl,
  Skeleton,
  Stack,
  Text,
  TextInput,
  Title,
  Tooltip,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconArrowLeft, IconFilter, IconSearch, IconX } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Link, stripSearchParams, useNavigate } from "@tanstack/react-router";
import { useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { z } from "zod";
import {
  listLibrariesOptions,
  listLibrariesQueryKey,
  listMediaOptions,
  updateLibraryMutation,
} from "@/client/@tanstack/react-query.gen";
import type { MediaFileStatus, MediaSortField } from "@/client/types.gen";
import { BrowsePageShell } from "@/components/common/browse-page-shell";
import { PageSizeSelect } from "@/components/common/page-size-select";
import { LibraryActionButtons } from "@/components/library/library-actions";
import {
  LIBRARY_FORM_MODAL_SIZE,
  emptyLibraryForm,
  LibraryFormFields,
  libraryFormFromResponse,
  libraryFormToUpdateBody,
  type LibraryFormState,
} from "@/components/library/library-form";
import { LibraryMediaTable } from "@/components/library/library-media-table";
import { useResettingState } from "@/hooks/use-resetting-state";
import { extractErrorMessage } from "@/lib/api-error";
import { isOneOf } from "@/lib/exhaustive";
import { MEDIA_FILE_STATUSES, MEDIA_SORT_FIELDS, SORT_ORDERS } from "@/lib/exhaustive-maps";
import { useUIStore } from "@/stores/ui";

const libraryDetailSearchSchema = z.object({
  q: z.string().optional(),
  status: z.enum(MEDIA_FILE_STATUSES).optional(),
  sort_by: z.enum(MEDIA_SORT_FIELDS).optional(),
  order: z.enum(SORT_ORDERS).optional(),
  page: z.coerce.number().int().min(1).catch(1).default(1),
});

export const Route = createFileRoute("/libraries/$libraryId")({
  validateSearch: libraryDetailSearchSchema,
  search: { middlewares: [stripSearchParams({ page: 1, sort_by: "updated_at", order: "desc" })] },
  component: LibraryDetailPage,
});

function BackLink() {
  const { t } = useTranslation("library");
  return (
    <Link to="/libraries" style={{ textDecoration: "none" }}>
      <Anchor
        component="span"
        size="sm"
        c="dimmed"
        underline="hover"
        style={{ display: "inline-flex", alignItems: "center", gap: 4 }}
      >
        <IconArrowLeft size={14} />
        {t("backToList")}
      </Anchor>
    </Link>
  );
}

/** Select / URL 回传原始字符串; 校验后窄化回 MediaFileStatus, 空串/未知值视为"全部". */
function parseStatusFilter(value: string | null): MediaFileStatus | undefined {
  if (!value) return undefined;
  return isOneOf(MEDIA_FILE_STATUSES, value) ? value : undefined;
}

function LibraryDetailPage() {
  const { libraryId } = Route.useParams();
  const search = Route.useSearch();
  const routeNavigate = Route.useNavigate();
  const { t } = useTranslation(["library", "common"]);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const listLimit = useUIStore((s) => s.pageSizes.libraryMedia);

  const id = Number(libraryId);
  const validId = Number.isInteger(id) && id > 0;

  const { data, isLoading } = useQuery({ ...listLibrariesOptions(), enabled: validId });
  const library = data?.items.find((lib) => lib.id === id);

  const [editOpen, setEditOpen] = useState(false);
  const [editForm, setEditForm] = useState<LibraryFormState>(emptyLibraryForm());
  const [searchInput, setSearchInput] = useResettingState(() => search.q ?? "", search.q);
  const [advancedOpen, setAdvancedOpen] = useState(search.status != null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const offset = (search.page - 1) * listLimit;
  const mediaQuery = useQuery({
    ...listMediaOptions({
      query: {
        library_id: id,
        search: search.q || undefined,
        status: search.status,
        offset,
        limit: listLimit,
        sort_by: search.sort_by ?? "updated_at",
        order: search.order ?? "desc",
      },
    }),
    enabled: validId && library != null,
  });

  const updateMutation = useMutation({
    ...updateLibraryMutation(),
    onSuccess: () => {
      notifications.show({ message: t("common:toast.watchPathUpdated"), color: "blue" });
      setEditOpen(false);
      void queryClient.invalidateQueries({ queryKey: listLibrariesQueryKey() });
    },
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      }),
  });

  const total = mediaQuery.data?.total ?? 0;
  const hasStatusFilter = search.status != null;
  const statusOptions = useMemo(
    () => [
      { value: "", label: t("filters.all") },
      ...MEDIA_FILE_STATUSES.map((s) => ({ value: s, label: t(`filters.${s}`) })),
    ],
    [t],
  );

  function handleSearchChange(value: string) {
    setSearchInput(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      void routeNavigate({ search: (prev) => ({ ...prev, q: value || undefined, page: 1 }) });
    }, 300);
  }

  function handleSort(field: MediaSortField) {
    void routeNavigate({
      search: (prev) => {
        if ((prev.sort_by ?? "updated_at") === field) {
          const nextOrder = (prev.order ?? "desc") === "asc" ? "desc" : "asc";
          return { ...prev, sort_by: field, order: nextOrder, page: 1 };
        }
        return {
          ...prev,
          sort_by: field,
          order:
            field === "size" || field === "created_at" || field === "updated_at" ? "desc" : "asc",
          page: 1,
        };
      },
    });
  }

  function setStatusFilter(value: string | null) {
    void routeNavigate({
      search: (prev) => ({ ...prev, status: parseStatusFilter(value), page: 1 }),
    });
  }

  if (!validId) {
    return (
      <Stack align="center" justify="center" mih="40vh" gap="xs">
        <Text fw={600}>{t("invalidId")}</Text>
        <BackLink />
      </Stack>
    );
  }

  if (!isLoading && !library) {
    return (
      <Stack align="center" justify="center" mih="40vh" gap="xs">
        <Text fw={600}>{t("notFound")}</Text>
        <BackLink />
      </Stack>
    );
  }

  if (!library) {
    return (
      <Stack gap="md">
        <Skeleton height={80} radius="md" />
        <Skeleton height={320} radius="md" />
      </Stack>
    );
  }

  function openEdit() {
    if (!library) return;
    setEditForm(libraryFormFromResponse(library));
    setEditOpen(true);
  }

  function submitEdit() {
    updateMutation.mutate({
      path: { library_id: id },
      body: libraryFormToUpdateBody(editForm),
    });
  }

  return (
    <>
      <BrowsePageShell
        fill
        title={
          <Stack gap={4}>
            <BackLink />
            <Title order={2}>{library.name}</Title>
            <Text size="xs" c="dimmed" ff="monospace" truncate="end">
              {library.path}
            </Text>
          </Stack>
        }
        summary={
          <Text size="sm" c="dimmed">
            {t("common:pagination.totalItems", { count: total })}
          </Text>
        }
        search={
          <TextInput
            value={searchInput}
            onChange={(e) => handleSearchChange(e.currentTarget.value)}
            placeholder={t("searchPlaceholder")}
            leftSection={<IconSearch size={16} />}
            w="100%"
          />
        }
        extras={
          <Tooltip label={t("filters.title")}>
            <ActionIcon
              variant={advancedOpen || hasStatusFilter ? "filled" : "default"}
              size={36}
              onClick={() => setAdvancedOpen((v) => !v)}
              aria-label={t("filters.title")}
            >
              <IconFilter size={16} />
            </ActionIcon>
          </Tooltip>
        }
        pageSize={
          <PageSizeSelect
            sizeKey="libraryMedia"
            onChanged={() => void routeNavigate({ search: (prev) => ({ ...prev, page: 1 }) })}
          />
        }
      >
        <Collapse expanded={advancedOpen}>
          <SegmentedControl
            size="sm"
            value={search.status ?? ""}
            onChange={setStatusFilter}
            data={statusOptions}
          />
        </Collapse>

        {search.status != null && (
          <Group gap="xs">
            <Group gap={4} wrap="nowrap">
              <Badge variant="outline">{t(`filters.${search.status}`)}</Badge>
              <ActionIcon
                size="sm"
                variant="subtle"
                color="gray"
                onClick={() => setStatusFilter("")}
                aria-label={t("common:actions.clear")}
              >
                <IconX size={14} />
              </ActionIcon>
            </Group>
          </Group>
        )}

        <LibraryMediaTable
          key={listLimit}
          libraryId={library.id}
          libraryPath={library.path}
          items={mediaQuery.data?.items ?? []}
          isLoading={mediaQuery.isLoading}
          total={total}
          page={search.page}
          sortBy={search.sort_by}
          order={search.order}
          onPageChange={(page) => void routeNavigate({ search: (prev) => ({ ...prev, page }) })}
          onSort={handleSort}
          trailing={
            <LibraryActionButtons
              library={library}
              onConfigure={openEdit}
              onDeleted={() => void navigate({ to: "/libraries" })}
            />
          }
        />
      </BrowsePageShell>

      <Modal
        opened={editOpen}
        onClose={() => setEditOpen(false)}
        title={t("configureLibrary")}
        size={LIBRARY_FORM_MODAL_SIZE}
        centered
      >
        <Stack gap="md">
          <LibraryFormFields value={editForm} onChange={setEditForm} showCreateOnly={false} />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setEditOpen(false)}>
              {t("common:actions.cancel")}
            </Button>
            <Button
              loading={updateMutation.isPending}
              disabled={!editForm.video_template.trim()}
              onClick={submitEdit}
            >
              {updateMutation.isPending ? t("common:actions.saving") : t("common:actions.save")}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </>
  );
}
