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
  Stack,
  Text,
  Tooltip,
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
  batchDeleteSavedQueriesMutation,
  listSavedQueriesOptions,
} from "@/client/@tanstack/react-query.gen";
import type { SavedQueryResponse } from "@/client/types.gen";
import { HintedActionIcon } from "@/components/common/hinted-action-icon";
import { extractErrorMessage } from "@/lib/api-error";
import { confirm } from "@/lib/confirm";
import {
  savedQueryBrowseHref,
  SAVED_QUERY_BADGE_COLOR,
  SAVED_QUERY_ENTITY_LABEL_KEY,
  SAVED_QUERY_OPEN_LABEL_KEY,
} from "@/lib/saved-query/display";
import { downloadSavedQueryResult } from "@/lib/saved-query/download";

function SavedQueryRow({ item, onDeleted }: { item: SavedQueryResponse; onDeleted: () => void }) {
  const { t } = useTranslation(["agent", "savedQueries", "common"]);
  const [open, setOpen] = useState(false);
  const deleteMutation = useMutation({
    ...batchDeleteSavedQueriesMutation(),
    onSuccess: onDeleted,
    onError: (err) =>
      notifications.show({
        color: "red",
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
      }),
  });

  const entityLabel = t(SAVED_QUERY_ENTITY_LABEL_KEY[item.entity], { ns: "savedQueries" });
  const openHref = savedQueryBrowseHref({ id: item.id, entity: item.entity });

  async function handleDelete() {
    const ok = await confirm({
      title: t("delete", { ns: "savedQueries" }),
      message: t("confirmDelete", { ns: "savedQueries", name: item.name }),
      confirmLabel: t("common:actions.delete"),
    });
    if (!ok) return;
    deleteMutation.mutate({ body: { ids: [item.id] } });
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
                {item.persisted ? (
                  <Badge size="sm" variant="outline" color="teal">
                    {t("persistedBadge", { ns: "savedQueries" })}
                  </Badge>
                ) : (
                  <Tooltip label={t("sessionTooltip", { ns: "savedQueries" })}>
                    <Badge size="sm" variant="light" color="gray">
                      {t("sessionBadge", { ns: "savedQueries" })}
                    </Badge>
                  </Tooltip>
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
              {t(SAVED_QUERY_OPEN_LABEL_KEY[item.entity], { ns: "savedQueries" })}
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
            {t(SAVED_QUERY_OPEN_LABEL_KEY.data, { ns: "savedQueries" })}
          </Button>
          <Button
            size="compact-sm"
            variant="default"
            leftSection={<IconDownload size={14} />}
            onClick={() =>
              void downloadSavedQueryResult(item.id, t("common:toast.operationFailed"))
            }
          >
            {t("download", { ns: "savedQueries" })}
          </Button>
          <Button
            size="compact-sm"
            variant="subtle"
            color="red"
            leftSection={<IconTrash size={14} />}
            loading={deleteMutation.isPending}
            onClick={() => void handleDelete()}
          >
            {t("delete", { ns: "savedQueries" })}
          </Button>
        </Group>
      </Stack>
    </Box>
  );
}

/** 合并两次受限查询并按 updated_at 倒序, 与列表接口排序一致; id 去重防重. */
function mergePresets(
  persisted: SavedQueryResponse[] | undefined,
  session: SavedQueryResponse[] | undefined,
): SavedQueryResponse[] {
  const byId = new Map<number, SavedQueryResponse>();
  for (const item of [...(persisted ?? []), ...(session ?? [])]) byId.set(item.id, item);
  return [...byId.values()].toSorted((a, b) => b.updated_at.localeCompare(a.updated_at));
}

/** 会话页的预设入口: 展示已保留的全部预设与当前会话的临时预设, 靠标签区分. */
export function SavedQueryManager({ sessionId }: { sessionId: number | null }) {
  const { t } = useTranslation(["agent", "savedQueries", "common"]);
  const qc = useQueryClient();
  const [opened, setOpened] = useState(false);

  // 两次受限请求: 已保留预设 + 当前会话临时预设, 不拉取其它会话的临时预设.
  const persistedQuery = useQuery({
    ...listSavedQueriesOptions({ query: { persisted_only: true } }),
    enabled: opened,
  });
  const sessionQuery = useQuery({
    ...listSavedQueriesOptions({ query: { session_id: sessionId } }),
    enabled: opened && sessionId != null,
  });

  function invalidateLists() {
    void qc.invalidateQueries({ queryKey: [{ _id: "listSavedQueries" }] });
  }

  const items = mergePresets(persistedQuery.data?.items, sessionQuery.data?.items);
  const isLoading = persistedQuery.isPending || (sessionId != null && sessionQuery.isPending);
  const isError = persistedQuery.isError || (sessionId != null && sessionQuery.isError);
  const queryError = persistedQuery.error ?? sessionQuery.error;
  const errorMessage = queryError instanceof Error ? queryError.message : t("common:status.error");

  return (
    <Menu
      opened={opened}
      onChange={setOpened}
      position="bottom-start"
      withinPortal
      // 面板宽度受视口限制: 固定 420px 在窄屏会越出右缘, 行内操作无法点击.
      width="min(420px, calc(100vw - 32px))"
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
          <Text size="md" fw={700} style={{ letterSpacing: "-0.02em" }}>
            {t("presets")}
          </Text>

          {isLoading && (
            <Group justify="center" py="xl">
              <Loader size="sm" />
            </Group>
          )}

          {isError && (
            <Text c="red" size="sm">
              {errorMessage}
            </Text>
          )}

          {!isLoading && !isError && items.length === 0 && (
            <Box py="xl" px="md">
              <Text c="dimmed" size="sm" ta="center" style={{ lineHeight: 1.6 }}>
                {t("presetsEmpty")}
              </Text>
            </Box>
          )}

          {!isLoading && items.length > 0 && (
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
