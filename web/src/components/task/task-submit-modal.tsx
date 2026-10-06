import { Modal } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  getTaskSchemaOptions,
  listTasksQueryKey,
  submitTaskMutation,
} from "@/client/@tanstack/react-query.gen";
import { DiscriminatedSchemaForm } from "@/components/schema-form/discriminated-schema-form";
import { extractErrorMessage } from "@/lib/api-error";
import { type TaskPayload, MANUAL_TASK_TYPES } from "@/lib/exhaustive-maps";

interface TaskSubmitModalProps {
  opened: boolean;
  onClose: () => void;
}

export function TaskSubmitModal({ opened, onClose }: TaskSubmitModalProps) {
  const { t } = useTranslation(["tasks", "common"]);
  const queryClient = useQueryClient();

  const submitMutation = useMutation({
    ...submitTaskMutation(),
    onSuccess: () => {
      notifications.show({ message: t("common:toast.taskCreated"), color: "blue" });
      void queryClient.invalidateQueries({ queryKey: listTasksQueryKey() });
      onClose();
    },
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      }),
  });

  return (
    <Modal opened={opened} onClose={onClose} title={t("actions.submit")} size="lg">
      {opened && (
        <DiscriminatedSchemaForm
          types={MANUAL_TASK_TYPES}
          defaultType="scrape"
          active={opened}
          schemaQuery={getTaskSchemaOptions()}
          saving={submitMutation.isPending}
          onSubmit={(value) => {
            // Schema 表单产出松散 Record; 此处桥接到 OpenAPI TaskPayload 联合.
            submitMutation.mutate({ body: value as TaskPayload });
          }}
        />
      )}
    </Modal>
  );
}
