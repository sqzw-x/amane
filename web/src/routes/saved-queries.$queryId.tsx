import {
  Badge,
  Box,
  Button,
  Code,
  Collapse,
  Group,
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
  IconTrash,
} from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, stripSearchParams } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { z } from "zod";
import {
  deleteSavedQueryMutation,
  getSavedQueryOptions,
  getSavedQueryResultOptions,
  updateSavedQueryMutation,
} from "@/client/@tanstack/react-query.gen";
import { getSavedQueryResult } from "@/client/sdk.gen";
import { ListPagination } from "@/components/common/list-pagination";
import { PageSizeSelect } from "@/components/common/page-size-select";
import { confirm } from "@/lib/confirm";
import {
  savedQueryBrowseHref,
  SAVED_QUERY_BADGE_COLOR,
  SAVED_QUERY_ENTITY_LABEL_KEY,
} from "@/lib/agent/saved-query";
import { useUIStore } from "@/stores/ui";

const savedQuerySearchSchema = z.object({
  page: z.coerce.number().int().min(1).catch(1).default(1),
});

export const Route = createFileRoute("/saved-queries/$queryId")({
  validateSearch: savedQuerySearchSchema,
  search: { middlewares: [stripSearchParams({ page: 1 })] },
  component: SavedQueryDataPage,
});

async function downloadResult(queryId: number) {
  const { data, error } = await getSavedQueryResult({
    path: { query_id: queryId },
    query: { offset: 0, limit: 5000 },
  });
  if (error || !data) {
    throw new Error(String(error));
  }
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `saved-query-${queryId}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

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
  const { t } = useTranslation(["agent", "common"]);
  const [sqlOpen, setSqlOpen] = useState(false);
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

  const persistMutation = useMutation({
    ...updateSavedQueryMutation(),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["getSavedQuery"] });
    },
  });
  const deleteMutation = useMutation({
    ...deleteSavedQueryMutation(),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["listSavedQueries"] });
      await navigate({ to: "/" });
    },
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
      title: t("deletePreset"),
      message: t("confirmDeletePreset", { name: query?.name ?? `#${id}` }),
      confirmLabel: t("common:actions.delete"),
    });
    if (!ok) return;
    deleteMutation.mutate({ path: { query_id: id } });
  }

  if (metaQuery.isLoading) {
    return <Text c="dimmed">{t("loadingHistory")}</Text>;
  }
  if (metaQuery.isError || query == null) {
    return (
      <Stack gap="sm">
        <Title order={2}>#{id}</Title>
        <Text c="dimmed">{t("dataPageNotFound")}</Text>
      </Stack>
    );
  }

  return (
    <Stack gap="md" style={{ minWidth: 0 }}>
      <Group justify="space-between" align="flex-start" wrap="wrap">
        <Stack gap={6}>
          <Title order={2}>{query.name}</Title>
          <Group gap={8}>
            <Badge size="sm" variant="light" color={SAVED_QUERY_BADGE_COLOR[query.entity]}>
              {t(`${SAVED_QUERY_ENTITY_LABEL_KEY[query.entity]}`)}
            </Badge>
            {query.persisted && (
              <Badge size="sm" variant="outline" color="teal">
                {t("persistedBadge")}
              </Badge>
            )}
            <Text size="xs" c="dimmed" ff="monospace">
              #{id}
            </Text>
          </Group>
        </Stack>
        <Group gap="xs" wrap="wrap">
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
              onClick={() =>
                persistMutation.mutate({ path: { query_id: id }, body: { persisted: true } })
              }
            >
              {t("persist")}
            </Button>
          )}
          <Button
            size="xs"
            variant="default"
            leftSection={<IconDownload size={14} />}
            onClick={() => {
              downloadResult(id).catch((err: unknown) => {
                console.error(err);
              });
            }}
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
            {t("deletePreset")}
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
        <Text c="dimmed">{t("loadingHistory")}</Text>
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
    </Stack>
  );
}
