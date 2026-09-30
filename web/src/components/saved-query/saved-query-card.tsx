import { ActionIcon, Badge, Card, Checkbox, Group, Stack, Text } from "@mantine/core";
import { IconDownload, IconPencil, IconTrash } from "@tabler/icons-react";
import { Link } from "@tanstack/react-router";
import type { CSSProperties, ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { SavedQueryResponse } from "@/client/types.gen";
import { formatRelativeTime } from "@/lib/format-relative-time";
import { SAVED_QUERY_BADGE_COLOR, SAVED_QUERY_ENTITY_LABEL_KEY } from "@/lib/saved-query/display";

const CARD_LINK_STYLE: CSSProperties = {
  flex: 1,
  minWidth: 0,
  textAlign: "left",
  textDecoration: "none",
  color: "inherit",
  display: "block",
};

/** 用 Link 承载卡片本体, 保留中键 / 修饰键新标签页与复制链接. */
function SavedQueryCardLink({
  query,
  children,
}: {
  query: SavedQueryResponse;
  children: ReactNode;
}) {
  if (query.entity === "metadata") {
    return (
      <Link to="/meta" search={{ saved_query_id: query.id }} style={CARD_LINK_STYLE}>
        {children}
      </Link>
    );
  }
  if (query.entity === "actor") {
    return (
      <Link to="/actors" search={{ saved_query_id: query.id }} style={CARD_LINK_STYLE}>
        {children}
      </Link>
    );
  }
  return (
    <Link
      to="/saved-queries/$queryId"
      params={{ queryId: String(query.id) }}
      style={CARD_LINK_STYLE}
    >
      {children}
    </Link>
  );
}

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
        <SavedQueryCardLink query={query}>
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
        </SavedQueryCardLink>
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
