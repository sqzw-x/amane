import { Button, Group, Stack, Text } from "@mantine/core";
import { IconDownload, IconExternalLink, IconTable } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { getSavedQueryOptions } from "@/client/@tanstack/react-query.gen";
import { savedQueryBrowseHref, SAVED_QUERY_OPEN_LABEL_KEY } from "@/lib/saved-query/display";

export function SavedQueryActions({
  ids,
  onDownload,
  onPersist,
}: {
  ids: number[];
  onDownload: (id: number) => void;
  onPersist: (id: number) => void;
}) {
  return (
    <Stack gap="xs" mt={4}>
      {ids.map((id) => (
        <SavedQueryActionRow key={id} id={id} onDownload={onDownload} onPersist={onPersist} />
      ))}
    </Stack>
  );
}

function SavedQueryActionRow({
  id,
  onDownload,
  onPersist,
}: {
  id: number;
  onDownload: (id: number) => void;
  onPersist: (id: number) => void;
}) {
  const { t } = useTranslation(["agent", "savedQueries"]);
  const { data } = useQuery(getSavedQueryOptions({ path: { query_id: id } }));
  const href = data != null ? savedQueryBrowseHref({ id, entity: data.entity }) : null;
  const label = data?.name ?? t("savedQueryChip", { id });
  return (
    <Group gap={6} wrap="wrap">
      <Text
        size="sm"
        fw={500}
        lineClamp={1}
        title={label}
        style={{ minWidth: 0, flex: "0 1 auto" }}
      >
        {label}
      </Text>
      <Button
        component="a"
        href={`/saved-queries/${id}`}
        target="_blank"
        rel="noreferrer"
        size="xs"
        variant="light"
        leftSection={<IconTable size={14} />}
      >
        {t(SAVED_QUERY_OPEN_LABEL_KEY.data, { ns: "savedQueries" })}
      </Button>
      {data != null && href != null && (
        <Button
          component="a"
          href={href}
          target="_blank"
          rel="noreferrer"
          size="xs"
          variant="light"
          leftSection={<IconExternalLink size={14} />}
        >
          {t(SAVED_QUERY_OPEN_LABEL_KEY[data.entity], { ns: "savedQueries" })}
        </Button>
      )}
      <Button
        size="xs"
        variant="default"
        onClick={() => onPersist(id)}
        disabled={data?.persisted === true}
      >
        {t("persist", { ns: "savedQueries" })}
      </Button>
      <Button
        size="xs"
        variant="default"
        leftSection={<IconDownload size={14} />}
        onClick={() => onDownload(id)}
      >
        {t("download", { ns: "savedQueries" })}
      </Button>
    </Group>
  );
}
