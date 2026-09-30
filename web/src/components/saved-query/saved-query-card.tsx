import {
  ActionIcon,
  Badge,
  Card,
  Checkbox,
  Group,
  Stack,
  Text,
  UnstyledButton,
} from "@mantine/core";
import { IconDownload, IconPencil, IconTrash } from "@tabler/icons-react";
import { useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import type { SavedQueryResponse } from "@/client/types.gen";
import { formatRelativeTime } from "@/lib/format-relative-time";
import { SAVED_QUERY_BADGE_COLOR, SAVED_QUERY_ENTITY_LABEL_KEY } from "@/lib/saved-query/display";

/** 卡片主体点击即进入预设的消费入口: ID 类型去对应筛选页, data 去数据页. */
export function SavedQueryCard({
  query,
  selected,
  onToggleSelect,
  onEdit,
  onDelete,
  onDownload,
}: {
  query: SavedQueryResponse;
  selected: boolean;
  onToggleSelect: () => void;
  onEdit: () => void;
  onDelete: () => void;
  onDownload: () => void;
}) {
  const { t, i18n } = useTranslation(["savedQueries", "common"]);
  const navigate = useNavigate();

  function openPrimary() {
    if (query.entity === "metadata") {
      void navigate({ to: "/meta", search: { saved_query_id: query.id } });
    } else if (query.entity === "actor") {
      void navigate({ to: "/actors", search: { saved_query_id: query.id } });
    } else {
      void navigate({ to: "/saved-queries/$queryId", params: { queryId: String(query.id) } });
    }
  }

  return (
    <Card withBorder radius="md" padding="sm">
      <Group align="flex-start" wrap="nowrap" gap="xs">
        <Checkbox
          size="xs"
          mt={3}
          checked={selected}
          onChange={onToggleSelect}
          aria-label={query.name}
        />
        <UnstyledButton onClick={openPrimary} style={{ flex: 1, minWidth: 0, textAlign: "left" }}>
          <Stack gap={6}>
            <Text fw={600} lineClamp={2} style={{ lineHeight: 1.3 }}>
              {query.name}
            </Text>
            <Group gap={6} wrap="wrap">
              <Badge size="sm" variant="light" color={SAVED_QUERY_BADGE_COLOR[query.entity]}>
                {t(SAVED_QUERY_ENTITY_LABEL_KEY[query.entity])}
              </Badge>
              <Text size="xs" c="dimmed">
                {t("updatedAt", {
                  time: formatRelativeTime(query.updated_at, i18n.language, t("justNow")),
                })}
              </Text>
            </Group>
            {query.description.trim() !== "" && (
              <Text size="sm" c="dimmed" lineClamp={2}>
                {query.description}
              </Text>
            )}
          </Stack>
        </UnstyledButton>
        <Group gap={2} wrap="nowrap">
          <ActionIcon
            variant="subtle"
            color="gray"
            size="sm"
            aria-label={t("common:actions.edit")}
            onClick={onEdit}
          >
            <IconPencil size={15} />
          </ActionIcon>
          <ActionIcon
            variant="subtle"
            color="gray"
            size="sm"
            aria-label={t("download")}
            onClick={onDownload}
          >
            <IconDownload size={15} />
          </ActionIcon>
          <ActionIcon
            variant="subtle"
            color="red"
            size="sm"
            aria-label={t("delete")}
            onClick={onDelete}
          >
            <IconTrash size={15} />
          </ActionIcon>
        </Group>
      </Group>
    </Card>
  );
}
