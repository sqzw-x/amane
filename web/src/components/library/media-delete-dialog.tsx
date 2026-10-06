import { Alert, Group, Loader, Modal, Stack, Text } from "@mantine/core";
import { IconAlertTriangle } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { expandCleanupSelection } from "@/client/sdk.gen";
import { InventoryTree } from "@/components/library/inventory-tree";

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
    // 展开是有副作用的读取 (每次都产出新清单): 窗口重新聚焦不该换掉用户正在勾选的清单.
    refetchOnWindowFocus: false,
  });

  return (
    <Modal opened={opened} onClose={onClose} title={t("cleanup.previewTitle")} size="xl" centered>
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
          <InventoryTree
            key={preview.data.inventory_id}
            libraryId={libraryId}
            inventoryId={preview.data.inventory_id ?? ""}
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
