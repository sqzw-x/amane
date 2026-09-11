import {
  Badge,
  Button,
  Checkbox,
  Group,
  Menu,
  Modal,
  rem,
  Skeleton,
  Stack,
  Table,
  Text,
  TextInput,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import {
  IconArrowMerge,
  IconEraser,
  IconPencil,
  IconRefresh,
  IconTrash,
  IconUser,
} from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";
import type { ParseKeys } from "i18next";
import { useTranslation } from "react-i18next";
import { listActorsQueryKey } from "@/client/@tanstack/react-query.gen";
import { scrapeActor, updateActor } from "@/client/sdk.gen";
import type {
  ActorGender,
  ActorResponse,
  ActorSortField,
  CacheKind,
  SortOrder,
} from "@/client/types.gen";
import { HintedActionIcon } from "@/components/common/hinted-action-icon";
import { ListToolbar } from "@/components/common/list-toolbar";
import { ResizableTh, SortableTh } from "@/components/common/sortable-th";
import { SelectionBar } from "@/components/common/selection-bar";
import { FacetRulesPanel } from "./facet-rules-panel";
import { useFacetIdentityActions } from "@/hooks/use-facet-identity-actions";
import { useIdSelection } from "@/hooks/use-id-selection";
import { useResizableColumns } from "@/hooks/use-resizable-columns";
import { CLEARED_ACTOR_PERSON_PATCH } from "@/lib/actors/person";
import { confirm } from "@/lib/confirm";
import { exhaustiveRecord } from "@/lib/exhaustive";
import { ageFromBirthday } from "@/lib/format-birthday";
import { proxyImageUrl } from "@/lib/utils";
import { ProxyImage } from "@/components/media/proxy-image";
import { type ActorTableColumnKey, useUIStore } from "@/stores/ui";

/** 表头可排序列 = ActorSortField 全集 (与 SortMenu 对齐). */
const SORTABLE_COLUMNS = [
  "name",
  "count",
  "birthday",
  "height",
  "bust",
  "waist",
  "hip",
  "cup",
  "has_image",
  "updated_at",
] as const satisfies readonly ActorSortField[];

type SortableColumn = (typeof SORTABLE_COLUMNS)[number];

const DEFAULT_COLUMN_WIDTHS = {
  name: 160,
  count: 72,
  gender: 72,
  birthday: 120,
  height: 72,
  bust: 64,
  waist: 64,
  hip: 64,
  cup: 56,
  has_image: 72,
  updated_at: 100,
} as const satisfies Record<ActorTableColumnKey, number>;

const CELL_OVERFLOW = { overflow: "hidden", maxWidth: 0 } as const;

type MetadataKey = ParseKeys<"metadata">;

const GENDER_I18N_KEY = exhaustiveRecord<ActorGender>()({
  female: "browse.person.genderFemale",
  male: "browse.person.genderMale",
  unknown: "browse.person.genderUnknown",
} as const satisfies Record<ActorGender, MetadataKey>);

const SORT_COLUMN_I18N_KEY = exhaustiveRecord<SortableColumn>()({
  name: "manage.name",
  count: "manage.count",
  birthday: "browse.person.birthday",
  height: "browse.person.height",
  bust: "browse.person.bust",
  waist: "browse.person.waist",
  hip: "browse.person.hip",
  cup: "browse.person.cup",
  has_image: "actors.hasImage",
  updated_at: "columns.updated",
} as const satisfies Record<SortableColumn, MetadataKey>);

export interface ActorTableProps {
  items: ActorResponse[];
  isLoading: boolean;
  total: number;
  page: number;
  sortBy: ActorSortField | undefined;
  order: SortOrder | undefined;
  onPageChange: (page: number) => void;
  onSort: (field: ActorSortField) => void;
}

function cellValue(actor: ActorResponse, field: SortableColumn): string {
  switch (field) {
    case "name":
      return actor.name;
    case "count":
      return String(actor.count);
    case "birthday":
      return actor.birthday?.trim() ?? "";
    case "height":
      return actor.height != null ? String(actor.height) : "";
    case "bust":
      return actor.bust != null ? String(actor.bust) : "";
    case "waist":
      return actor.waist != null ? String(actor.waist) : "";
    case "hip":
      return actor.hip != null ? String(actor.hip) : "";
    case "cup":
      return actor.cup ?? "";
    case "has_image":
      return actor.image_urls?.length ? "✓" : "";
    case "updated_at":
      return actor.updated_at ? new Date(actor.updated_at).toLocaleDateString() : "";
    default: {
      const _: never = field;
      return _;
    }
  }
}

export function ActorTable({
  items,
  isLoading,
  total,
  page,
  sortBy,
  order,
  onPageChange,
  onSort,
}: ActorTableProps) {
  const { t } = useTranslation(["metadata", "common"]);
  const queryClient = useQueryClient();
  const kind = "actor" as const;
  const pageIds = items.map((i) => i.id);
  const limit = useUIStore((s) => s.pageSizes.actorsList);
  const totalPages = Math.max(1, Math.ceil(total / limit));

  const storedWidths = useUIStore((s) => s.actorColumnWidths);
  const setActorColumnWidths = useUIStore((s) => s.setActorColumnWidths);
  const { effectiveWidth, getResizeHandleProps } = useResizableColumns({
    defaults: DEFAULT_COLUMN_WIDTHS,
    stored: storedWidths,
    onChange: setActorColumnWidths,
  });

  const { selected, selectedIds, toggleOne, toggleAll, isAllSelected, clear } = useIdSelection();
  const identity = useFacetIdentityActions({
    kind,
    listQueryKey: listActorsQueryKey(),
    confirmMerge: false,
    onMerged: clear,
  });

  const [batchScraping, setBatchScraping] = useState(false);
  const [batchGendering, setBatchGendering] = useState(false);
  const [batchClearing, setBatchClearing] = useState(false);

  async function scrapeIds(ids: number[], useCache: CacheKind[]) {
    if (ids.length === 0) return;
    setBatchScraping(true);
    const results = await Promise.allSettled(
      ids.map((id) =>
        scrapeActor({ path: { actor_id: id }, body: { use_cache: useCache }, throwOnError: true }),
      ),
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
  }

  async function handleBatchScrape(useCache: CacheKind[]) {
    await scrapeIds(selectedIds, useCache);
    clear();
  }

  async function handleScrapeMissing(useCache: CacheKind[]) {
    const ids = items
      .filter((i) => !i.birthday && !i.height && !i.image_urls?.length)
      .map((i) => i.id);
    if (ids.length === 0) {
      notifications.show({ message: t("actors.noneMissing"), color: "gray" });
      return;
    }
    await scrapeIds(ids, useCache);
  }

  async function handleBatchGender(gender: ActorGender) {
    if (selectedIds.length === 0) return;
    setBatchGendering(true);
    const results = await Promise.allSettled(
      selectedIds.map((id) =>
        updateActor({ path: { actor_id: id }, body: { gender }, throwOnError: true }),
      ),
    );
    setBatchGendering(false);
    const failed = results.filter((r) => r.status === "rejected").length;
    const ok = selectedIds.length - failed;
    if (ok > 0) {
      notifications.show({
        message: t("actors.batchGenderDone", {
          count: ok,
          gender: t(GENDER_I18N_KEY[gender]),
        }),
        color: "blue",
      });
      clear();
      void queryClient.invalidateQueries({ queryKey: listActorsQueryKey() });
    }
    if (failed > 0) {
      notifications.show({
        message: t("actors.batchGenderFailed", { count: failed }),
        color: "red",
      });
    }
  }

  async function handleClearPerson(
    ids: number[],
    opts?: { name?: string; clearSelection?: boolean },
  ) {
    if (ids.length === 0) return;
    const ok = await confirm({
      title: t("actors.clearPerson"),
      message:
        ids.length === 1 && opts?.name
          ? t("actors.clearPersonBody", { name: opts.name })
          : t("actors.batchClearPersonBody", { count: ids.length }),
      confirmLabel: t("actors.clearPerson"),
    });
    if (!ok) return;
    setBatchClearing(true);
    const results = await Promise.allSettled(
      ids.map((id) =>
        updateActor({
          path: { actor_id: id },
          body: CLEARED_ACTOR_PERSON_PATCH,
          throwOnError: true,
        }),
      ),
    );
    setBatchClearing(false);
    const failed = results.filter((r) => r.status === "rejected").length;
    const succeeded = ids.length - failed;
    if (succeeded > 0) {
      notifications.show({
        message:
          ids.length === 1
            ? t("actors.clearPersonDone")
            : t("actors.batchClearPersonDone", { count: succeeded }),
        color: "blue",
      });
      if (opts?.clearSelection) clear();
      void queryClient.invalidateQueries({ queryKey: listActorsQueryKey() });
    }
    if (failed > 0) {
      notifications.show({
        message: t("actors.batchClearPersonFailed", { count: failed }),
        color: "red",
      });
    }
  }

  const effectiveSortBy = sortBy ?? "name";
  const effectiveOrder = order ?? "asc";
  const allSelected = isAllSelected(pageIds);
  const busy = batchScraping || batchGendering || batchClearing || identity.busy;

  function handlePageChange(p: number) {
    clear();
    onPageChange(p);
  }

  function columnWidth(key: keyof typeof DEFAULT_COLUMN_WIDTHS): number {
    return effectiveWidth(key);
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
                <Button size="xs" variant="light" loading={busy}>
                  {t("actors.scrapeMissingOnPage")}
                </Button>
              </Menu.Target>
              <Menu.Dropdown>
                <Menu.Item onClick={() => void handleScrapeMissing(["metadata", "trans"])}>
                  <Text size="sm">{t("common:actions.scrapeNormal")}</Text>
                  <Text size="xs" c="dimmed">
                    {t("common:actions.scrapeNormalDesc")}
                  </Text>
                </Menu.Item>
                <Menu.Item onClick={() => void handleScrapeMissing([])}>
                  <Text size="sm">{t("common:actions.scrapeForce")}</Text>
                  <Text size="xs" c="dimmed">
                    {t("common:actions.scrapeForceDesc")}
                  </Text>
                </Menu.Item>
              </Menu.Dropdown>
            </Menu>
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
                <Menu.Item onClick={() => void handleBatchScrape(["metadata", "trans"])}>
                  <Text size="sm">{t("common:actions.scrapeNormal")}</Text>
                  <Text size="xs" c="dimmed">
                    {t("common:actions.scrapeNormalDesc")}
                  </Text>
                </Menu.Item>
                <Menu.Item onClick={() => void handleBatchScrape([])}>
                  <Text size="sm">{t("common:actions.scrapeForce")}</Text>
                  <Text size="xs" c="dimmed">
                    {t("common:actions.scrapeForceDesc")}
                  </Text>
                </Menu.Item>
              </Menu.Dropdown>
            </Menu>
            <Menu shadow="md" position="bottom-start">
              <Menu.Target>
                <Button
                  size="xs"
                  variant="light"
                  leftSection={<IconUser size={14} />}
                  loading={busy}
                  disabled={selected.size === 0}
                >
                  {t("actors.batchGender")}
                </Button>
              </Menu.Target>
              <Menu.Dropdown>
                {(["female", "male", "unknown"] as const).map((g) => (
                  <Menu.Item key={g} onClick={() => void handleBatchGender(g)}>
                    {t(GENDER_I18N_KEY[g])}
                  </Menu.Item>
                ))}
              </Menu.Dropdown>
            </Menu>
            <Button
              size="xs"
              variant="light"
              leftSection={<IconEraser size={14} />}
              loading={busy}
              disabled={selected.size === 0}
              onClick={() => void handleClearPerson(selectedIds, { clearSelection: true })}
            >
              {t("actors.batchClearPerson")}
            </Button>
          </SelectionBar>
        }
        trailing={<FacetRulesPanel kind={kind} onDeleteRule={identity.deleteRule} />}
      >
        <Table
          stickyHeader
          highlightOnHover
          verticalSpacing="sm"
          layout="fixed"
          w="100%"
          style={{ minWidth: 1100 }}
        >
          <Table.Thead>
            <Table.Tr>
              <Table.Th w={36}>
                <Checkbox checked={allSelected} onChange={() => toggleAll(pageIds)} />
              </Table.Th>
              <Table.Th w={48} />
              <SortableTh
                field="name"
                label={t(SORT_COLUMN_I18N_KEY.name)}
                sortBy={effectiveSortBy}
                order={effectiveOrder}
                onSort={onSort}
                w={columnWidth("name")}
                resizeHandle={getResizeHandleProps("name")}
              />
              <ResizableTh w={columnWidth("gender")} resizeHandle={getResizeHandleProps("gender")}>
                {t("browse.person.gender")}
              </ResizableTh>
              {SORTABLE_COLUMNS.filter((f) => f !== "name").map((field) => (
                <SortableTh
                  key={field}
                  field={field}
                  label={t(SORT_COLUMN_I18N_KEY[field])}
                  sortBy={effectiveSortBy}
                  order={effectiveOrder}
                  onSort={onSort}
                  w={columnWidth(field)}
                  resizeHandle={getResizeHandleProps(field)}
                />
              ))}
              <Table.Th ta="right" w={152}>
                {t("columns.actions")}
              </Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {items.map((actor) => (
              <Table.Tr
                key={actor.id}
                bg={selected.has(actor.id) ? "var(--mantine-color-blue-light)" : undefined}
              >
                <Table.Td>
                  <Checkbox checked={selected.has(actor.id)} onChange={() => toggleOne(actor.id)} />
                </Table.Td>
                <Table.Td>
                  {actor.image_urls?.[0] ? (
                    <ProxyImage
                      src={proxyImageUrl(actor.image_urls[0]) ?? actor.image_urls[0]}
                      loading="lazy"
                      referrerPolicy="no-referrer"
                      alt=""
                      style={{
                        display: "block",
                        width: rem(40),
                        height: rem(40),
                        objectFit: "cover",
                        borderRadius: "var(--mantine-radius-sm)",
                      }}
                      placeholder={
                        <span
                          aria-hidden
                          style={{
                            display: "block",
                            width: rem(40),
                            height: rem(40),
                            borderRadius: "var(--mantine-radius-sm)",
                          }}
                        />
                      }
                    />
                  ) : (
                    <Text size="xs" c="dimmed">
                      —
                    </Text>
                  )}
                </Table.Td>
                <Table.Td style={CELL_OVERFLOW}>
                  <Link
                    to="/actors/$actorId"
                    params={{ actorId: String(actor.id) }}
                    style={{ textDecoration: "none", color: "inherit", display: "block" }}
                  >
                    <Text span c="brand" size="sm" truncate title={actor.name}>
                      {actor.name}
                    </Text>
                  </Link>
                </Table.Td>
                <Table.Td style={CELL_OVERFLOW}>
                  <Badge
                    size="sm"
                    variant={actor.gender === "unknown" ? "outline" : "light"}
                    color={actor.gender === "unknown" ? "gray" : undefined}
                  >
                    {t(GENDER_I18N_KEY[actor.gender ?? "unknown"])}
                  </Badge>
                </Table.Td>
                {SORTABLE_COLUMNS.filter((f) => f !== "name").map((field) => {
                  const raw = cellValue(actor, field);
                  const age =
                    field === "birthday" && actor.birthday ? ageFromBirthday(actor.birthday) : null;
                  const value =
                    field === "birthday" && raw && age != null
                      ? t("actors.birthdayWithAge", { date: raw, age })
                      : raw;
                  return (
                    <Table.Td key={field} style={CELL_OVERFLOW}>
                      {field === "count" ? (
                        <Badge variant="light">{actor.count}</Badge>
                      ) : (
                        <Text size="sm" truncate title={value || undefined}>
                          {value || "—"}
                        </Text>
                      )}
                    </Table.Td>
                  );
                })}
                <Table.Td>
                  <Group gap={4} justify="flex-end" wrap="nowrap">
                    <HintedActionIcon
                      variant="subtle"
                      label={t("common:actions.edit")}
                      onClick={() => identity.openRename({ id: actor.id, name: actor.name })}
                    >
                      <IconPencil size={16} />
                    </HintedActionIcon>
                    <HintedActionIcon
                      variant="subtle"
                      label={t("manage.merge")}
                      disabled={identity.mergePending}
                      onClick={() => void identity.openMerge(actor.id, selected)}
                    >
                      <IconArrowMerge size={16} />
                    </HintedActionIcon>
                    <HintedActionIcon
                      variant="subtle"
                      label={t("actors.clearPerson")}
                      disabled={batchClearing}
                      onClick={() => void handleClearPerson([actor.id], { name: actor.name })}
                    >
                      <IconEraser size={16} />
                    </HintedActionIcon>
                    <HintedActionIcon
                      variant="subtle"
                      color="red"
                      label={t("common:actions.delete")}
                      onClick={() => void identity.openDelete({ id: actor.id, name: actor.name })}
                    >
                      <IconTrash size={16} />
                    </HintedActionIcon>
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
                    <Skeleton h={40} w={40} radius="sm" />
                  </Table.Td>
                  <Table.Td>
                    <Skeleton h={14} w="80%" />
                  </Table.Td>
                  <Table.Td>
                    <Skeleton h={14} w={48} />
                  </Table.Td>
                  {SORTABLE_COLUMNS.filter((f) => f !== "name").map((field) => (
                    <Table.Td key={field}>
                      <Skeleton h={14} w="60%" />
                    </Table.Td>
                  ))}
                  <Table.Td />
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
        <Stack>
          <TextInput
            value={identity.renameValue}
            onChange={(e) => identity.setRenameValue(e.currentTarget.value)}
          />
          <Button loading={identity.renamePending} onClick={identity.submitRename}>
            {t("common:actions.save")}
          </Button>
        </Stack>
      </Modal>
    </>
  );
}
