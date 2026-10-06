import { Alert, Group, Loader, Modal, Stack, Text } from "@mantine/core";
import { IconAlertTriangle } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { expandCleanupSelection } from "@/client/sdk.gen";
import { PlanSelection } from "@/components/library/plan-selection";

interface MediaDeleteDialogProps {
  libraryId: number;
  mediaFileIds: number[];
  /** 连同作品文件夹一起删除; 目录不满足条件时后端给出原因. */
  includeWorkDir: boolean;
  opened: boolean;
  onClose: () => void;
  onDeleted: () => void;
}

/** 删除前先把选中项展开成清单给用户看: 完整清单里不只有正片, 还有刮削产物与字幕. */
export function MediaDeleteDialog({
  libraryId,
  mediaFileIds,
  includeWorkDir,
  opened,
  onClose,
  onDeleted,
}: MediaDeleteDialogProps) {
  const { t } = useTranslation(["library", "common"]);
  const preview = useQuery({
    queryKey: ["cleanup-selection", libraryId, mediaFileIds.join(","), includeWorkDir],
    queryFn: async () => {
      const { data } = await expandCleanupSelection({
        path: { library_id: libraryId },
        body: { media_file_ids: mediaFileIds, include_work_dir: includeWorkDir },
        throwOnError: true,
      });
      return data;
    },
    enabled: opened && mediaFileIds.length > 0,
  });

  return (
    <Modal opened={opened} onClose={onClose} title={t("cleanup.previewTitle")} size="xl">
      {preview.isLoading ? (
        <Group justify="center" p="lg">
          <Loader size="sm" />
        </Group>
      ) : !preview.data?.exists ? (
        <Text size="sm" c="dimmed">
          {t("cleanup.empty")}
        </Text>
      ) : (
        <Stack gap="xs">
          {(preview.data.notices ?? []).map((notice) => (
            <Alert
              key={notice}
              color="yellow"
              variant="light"
              icon={<IconAlertTriangle size={16} />}
            >
              <Text size="xs">{notice}</Text>
            </Alert>
          ))}
          <PlanSelection
            key={preview.data.plan_id}
            libraryId={libraryId}
            planId={preview.data.plan_id ?? ""}
            nodes={preview.data.nodes ?? []}
            entryCount={preview.data.entry_count ?? 0}
            entryBytes={preview.data.entry_bytes ?? 0}
            onDone={() => {
              onDeleted();
              onClose();
            }}
          />
        </Stack>
      )}
    </Modal>
  );
}
