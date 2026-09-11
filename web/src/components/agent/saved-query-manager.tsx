import {
  Badge,
  Box,
  Button,
  Code,
  Collapse,
  Group,
  Loader,
  Menu,
  ScrollArea,
  SegmentedControl,
  Stack,
  Text,
  UnstyledButton,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import {
  IconBookmark,
  IconChevronDown,
  IconChevronRight,
  IconDownload,
  IconExternalLink,
  IconTable,
  IconTrash,
} from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  deleteSavedQueryMutation,
  listSavedQueriesOptions,
  listSavedQueriesQueryKey,
} from "@/client/@tanstack/react-query.gen";
import { getSavedQueryResult } from "@/client/sdk.gen";
import type { SavedQueryResponse } from "@/client/types.gen";
import { HintedActionIcon } from "@/components/common/hinted-action-icon";
import { confirm } from "@/lib/confirm";
import {
  savedQueryBrowseHref,
  SAVED_QUERY_BADGE_COLOR,
  SAVED_QUERY_ENTITY_LABEL_KEY,
  SAVED_QUERY_OPEN_LABEL_KEY,
} from "@/lib/agent/saved-query";

type Scope = "persisted" | "session";

async function downloadSavedQueryResult(queryId: number) {
  const { data, error } = await getSavedQueryResult({
    path: { query_id: queryId },
    query: { offset: 0, limit: 5000 },
  });
  if (error || !data) {
    notifications.show({ color: "red", message: String(error) });
    return;
  }
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `saved-query-${queryId}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

function SavedQueryRow({ item, onDeleted }: { item: SavedQueryResponse; onDeleted: () => void }) {
  const { t } = useTranslation(["agent", "common"]);
  const [open, setOpen] = useState(false);
  const deleteMutation = useMutation({
    ...deleteSavedQueryMutation(),
    onSuccess: () => {
      onDeleted();
    },
    onError: (err) => {
      notifications.show({
        color: "red",
        message: err instanceof Error ? err.message : String(err),
      });
    },
  });

  const entityLabel = t(`${SAVED_QUERY_ENTITY_LABEL_KEY[item.entity]}`);
  const openHref = savedQueryBrowseHref({ id: item.id, entity: item.entity });

  async function handleDelete() {
    const ok = await confirm({
      title: t("deletePreset"),
      message: t("confirmDeletePreset", { name: item.name }),
      confirmLabel: t("common:actions.delete"),
    });
    if (!ok) return;
    deleteMutation.mutate({ path: { query_id: item.id } });
  }

  return (
    <Box
      p="md"
      style={{
        borderRadius: "var(--mantine-radius-md)",
        border: "1px solid var(--mantine-color-default-border)",
        background: "var(--mantine-color-body)",
      }}
    >
      <Stack gap="sm">
        <UnstyledButton onClick={() => setOpen((v) => !v)} style={{ textAlign: "left" }}>
          <Group gap="sm" wrap="nowrap" align="flex-start">
            <Box c="dimmed" pt={2} style={{ display: "flex", lineHeight: 0 }}>
              {open ? <IconChevronDown size={16} /> : <IconChevronRight size={16} />}
            </Box>
            <Stack gap={6} style={{ minWidth: 0, flex: 1 }}>
              <Text size="sm" fw={600} lineClamp={2} style={{ lineHeight: 1.35 }}>
                {item.name}
              </Text>
              <Group gap={8}>
                <Badge size="sm" variant="light" color={SAVED_QUERY_BADGE_COLOR[item.entity]}>
                  {entityLabel}
                </Badge>
                {item.persisted && (
                  <Badge size="sm" variant="outline" color="teal">
                    {t("persistedBadge")}
                  </Badge>
                )}
                <Text size="xs" c="dimmed" ff="monospace">
                  #{item.id}
                </Text>
              </Group>
            </Stack>
          </Group>
        </UnstyledButton>

        <Collapse expanded={open}>
          <Code
            block
            style={{
              maxHeight: 160,
              overflow: "auto",
              fontSize: 12,
              borderRadius: "var(--mantine-radius-sm)",
            }}
          >
            {item.sql}
          </Code>
        </Collapse>

        <Group gap="xs" wrap="wrap">
          {openHref != null && (
            <Button
              component="a"
              href={openHref}
              target="_blank"
              rel="noreferrer"
              size="compact-sm"
              variant="light"
              leftSection={<IconExternalLink size={14} />}
            >
              {t(`${SAVED_QUERY_OPEN_LABEL_KEY[item.entity]}`)}
            </Button>
          )}
          <Button
            component="a"
            href={`/saved-queries/${item.id}`}
            target="_blank"
            rel="noreferrer"
            size="compact-sm"
            variant="light"
            leftSection={<IconTable size={14} />}
          >
            {t("openData")}
          </Button>
          <Button
            size="compact-sm"
            variant="default"
            leftSection={<IconDownload size={14} />}
            onClick={() => void downloadSavedQueryResult(item.id)}
          >
            {t("download")}
          </Button>
          <Button
            size="compact-sm"
            variant="subtle"
            color="red"
            leftSection={<IconTrash size={14} />}
            loading={deleteMutation.isPending}
            onClick={() => void handleDelete()}
          >
            {t("deletePreset")}
          </Button>
        </Group>
      </Stack>
    </Box>
  );
}

export function SavedQueryManager({ sessionId }: { sessionId: number | null }) {
  const { t } = useTranslation(["agent", "common"]);
  const qc = useQueryClient();
  const [opened, setOpened] = useState(false);
  const [scope, setScope] = useState<Scope>("persisted");

  const effectiveScope: Scope = scope === "session" && sessionId == null ? "persisted" : scope;

  const listOpts =
    effectiveScope === "session" && sessionId != null
      ? listSavedQueriesOptions({ query: { session_id: sessionId } })
      : listSavedQueriesOptions({ query: { persisted_only: true } });

  const listQuery = useQuery({
    ...listOpts,
    enabled: opened,
  });

  function invalidateLists() {
    void qc.invalidateQueries({
      queryKey: listSavedQueriesQueryKey({ query: { persisted_only: true } }),
    });
    if (sessionId != null) {
      void qc.invalidateQueries({
        queryKey: listSavedQueriesQueryKey({ query: { session_id: sessionId } }),
      });
    }
  }

  const items = listQuery.data?.items ?? [];
  const errorMessage =
    listQuery.error instanceof Error ? listQuery.error.message : t("common:status.error");

  return (
    <Menu
      opened={opened}
      onChange={setOpened}
      position="bottom-start"
      withinPortal
      width={420}
      shadow="lg"
      radius="md"
      closeOnItemClick={false}
    >
      <Menu.Target>
        <HintedActionIcon variant="light" size="md" label={t("presets")}>
          <IconBookmark size={16} />
        </HintedActionIcon>
      </Menu.Target>
      <Menu.Dropdown p="md">
        <Stack gap="md">
          <Stack gap={4}>
            <Text size="md" fw={700} style={{ letterSpacing: "-0.02em" }}>
              {t("presets")}
            </Text>
            <Text size="xs" c="dimmed">
              {effectiveScope === "session" ? t("presetsSessionHint") : t("presetsPersistedHint")}
            </Text>
          </Stack>

          <SegmentedControl
            size="sm"
            fullWidth
            value={effectiveScope}
            onChange={(v) => setScope(v as Scope)}
            data={[
              { label: t("presetsPersisted"), value: "persisted" },
              {
                label: t("presetsSession"),
                value: "session",
                disabled: sessionId == null,
              },
            ]}
          />

          {listQuery.isPending && (
            <Group justify="center" py="xl">
              <Loader size="sm" />
            </Group>
          )}

          {listQuery.isError && (
            <Text c="red" size="sm">
              {errorMessage}
            </Text>
          )}

          {!listQuery.isPending && !listQuery.isError && items.length === 0 && (
            <Box py="xl" px="md">
              <Text c="dimmed" size="sm" ta="center" style={{ lineHeight: 1.6 }}>
                {effectiveScope === "session"
                  ? t("presetsSessionEmpty")
                  : t("presetsPersistedEmpty")}
              </Text>
            </Box>
          )}

          {!listQuery.isPending && items.length > 0 && (
            <ScrollArea.Autosize mah={420} offsetScrollbars type="auto">
              <Stack gap="sm" pr={4}>
                {items.map((item) => (
                  <SavedQueryRow key={item.id} item={item} onDeleted={invalidateLists} />
                ))}
              </Stack>
            </ScrollArea.Autosize>
          )}
        </Stack>
      </Menu.Dropdown>
    </Menu>
  );
}
