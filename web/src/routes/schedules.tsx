import {
  Badge,
  Button,
  Divider,
  Group,
  Modal,
  Paper,
  Stack,
  Switch,
  Text,
  TextInput,
  Title,
  Tooltip,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconClockPlay, IconPencil, IconPlus, IconTrash } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  createScheduleMutation,
  deleteScheduleMutation,
  getScheduleSchemaOptions,
  listSchedulesOptions,
  listSchedulesQueryKey,
  triggerScheduleMutation,
  updateScheduleMutation,
} from "@/client/@tanstack/react-query.gen";
import type { ScheduleCreateRequest, ScheduleResponse } from "@/client/types.gen";
import { CronPicker, CronSummary } from "@/components/cron-picker";
import { SchedulePayloadFacts, SchedulePayloadSummary } from "@/components/schedule/payload-view";
import { DiscriminatedSchemaForm } from "@/components/schema-form/discriminated-schema-form";
import { extractErrorMessage } from "@/lib/api-error";
import { confirm } from "@/lib/confirm";
import { DEFAULT_CRON, isUsableCron } from "@/lib/cron";
import { ROUTINE_TYPES } from "@/lib/exhaustive-maps";

export const Route = createFileRoute("/schedules")({ component: SchedulesPage });

function SchedulesPage() {
  const { t } = useTranslation(["schedules", "tasks", "common"]);
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery(listSchedulesOptions());

  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState("");
  const [cron, setCron] = useState(DEFAULT_CRON);

  const [editing, setEditing] = useState<ScheduleResponse | null>(null);
  const [editName, setEditName] = useState("");
  const [editCron, setEditCron] = useState("");
  const [editEnabled, setEditEnabled] = useState(true);

  const invalidate = () =>
    void queryClient.invalidateQueries({ queryKey: listSchedulesQueryKey() });

  const createMutation = useMutation({
    ...createScheduleMutation(),
    onSuccess: () => {
      notifications.show({ message: t("common:toast.scheduleCreated"), color: "blue" });
      setCreateOpen(false);
      setName("");
      setCron(DEFAULT_CRON);
      invalidate();
    },
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      }),
  });
  const updateMutation = useMutation({
    ...updateScheduleMutation(),
    onSuccess: () => {
      notifications.show({ message: t("common:toast.scheduleUpdated"), color: "blue" });
      setEditing(null);
      invalidate();
    },
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      }),
  });
  const toggleMutation = useMutation({ ...updateScheduleMutation(), onSuccess: invalidate });
  const deleteMutation = useMutation({
    ...deleteScheduleMutation(),
    onSuccess: () => {
      notifications.show({ message: t("common:toast.scheduleDeleted"), color: "blue" });
      invalidate();
    },
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      }),
  });
  const triggerMutation = useMutation({
    ...triggerScheduleMutation(),
    onSuccess: () =>
      notifications.show({ message: t("common:toast.scheduleTriggered"), color: "blue" }),
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      }),
  });

  function submitEdit() {
    if (!editing) return;
    updateMutation.mutate({
      path: { schedule_id: editing.id },
      body: { name: editName.trim() || null, cron: editCron.trim(), enabled: editEnabled },
    });
  }

  async function handleDelete(scheduleId: number) {
    const ok = await confirm({
      title: t("confirm.deleteTitle"),
      message: t("confirm.deleteMessage"),
      confirmLabel: t("common:actions.delete"),
    });
    if (!ok) return;
    deleteMutation.mutate({ path: { schedule_id: scheduleId } });
  }

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Title order={2}>{t("title")}</Title>
        <Button
          leftSection={<IconPlus size={16} />}
          onClick={() => {
            setName("");
            setCron(DEFAULT_CRON);
            setCreateOpen(true);
          }}
        >
          {t("newSchedule")}
        </Button>
      </Group>

      {!isLoading && (data?.items.length ?? 0) === 0 && (
        <Text c="dimmed" size="sm" ta="center" py="xl">
          {t("empty")}
        </Text>
      )}

      <Stack gap="sm">
        {(data?.items ?? []).map((schedule) => (
          <Paper key={schedule.id} withBorder radius="md" p="md">
            <Group justify="space-between" wrap="wrap">
              <Group gap="sm">
                <div
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    background: schedule.enabled
                      ? "var(--mantine-color-teal-6)"
                      : "var(--mantine-color-gray-5)",
                  }}
                />
                <div>
                  <Text fw={600}>{schedule.name || t(`tasks:filters.${schedule.task_type}`)}</Text>
                  <Group gap="xs">
                    <CronSummary expression={schedule.cron} />
                    <Text size="xs" c="dimmed" ff="monospace">
                      {schedule.cron}
                    </Text>
                    <Text size="xs" c="dimmed">
                      {t("labels.nextRun")}:{" "}
                      {schedule.next_run
                        ? new Date(schedule.next_run).toLocaleString()
                        : t("labels.never")}
                    </Text>
                  </Group>
                </div>
              </Group>
              <Group gap="xs">
                <Switch
                  checked={schedule.enabled}
                  onChange={() =>
                    toggleMutation.mutate({
                      path: { schedule_id: schedule.id },
                      body: { enabled: !schedule.enabled },
                    })
                  }
                />
                <Tooltip label={t("actions.trigger")}>
                  <Button
                    size="xs"
                    variant="light"
                    leftSection={<IconClockPlay size={14} />}
                    loading={triggerMutation.isPending}
                    onClick={() => triggerMutation.mutate({ path: { schedule_id: schedule.id } })}
                  >
                    {t("actions.trigger")}
                  </Button>
                </Tooltip>
                <Button
                  size="xs"
                  variant="light"
                  leftSection={<IconPencil size={14} />}
                  onClick={() => {
                    setEditing(schedule);
                    setEditName(schedule.name ?? "");
                    setEditCron(schedule.cron);
                    setEditEnabled(schedule.enabled);
                  }}
                >
                  {t("common:actions.edit")}
                </Button>
                <Button
                  size="xs"
                  variant="light"
                  color="red"
                  leftSection={<IconTrash size={14} />}
                  loading={deleteMutation.isPending}
                  onClick={() => void handleDelete(schedule.id)}
                >
                  {t("common:actions.delete")}
                </Button>
              </Group>
            </Group>
            <Group mt="xs" gap="xs" wrap="wrap">
              <Badge size="sm" variant="light">
                {t(`tasks:filters.${schedule.task_type}`)}
              </Badge>
              <SchedulePayloadSummary payload={schedule.payload} />
            </Group>
          </Paper>
        ))}
      </Stack>

      <Modal
        opened={createOpen}
        onClose={() => setCreateOpen(false)}
        title={t("newSchedule")}
        size="lg"
        centered
      >
        {createOpen && (
          <DiscriminatedSchemaForm
            types={ROUTINE_TYPES}
            defaultType="cleanup"
            active={createOpen}
            schemaQuery={getScheduleSchemaOptions()}
            saving={createMutation.isPending}
            submitDisabled={!isUsableCron(cron)}
            submitLabel={t("common:actions.save")}
            header={
              <Stack gap="md">
                <TextInput
                  label={t("fields.name")}
                  placeholder={t("fields.namePlaceholder")}
                  value={name}
                  onChange={(e) => setName(e.currentTarget.value)}
                />
                <CronPicker value={cron} onChange={setCron} />
              </Stack>
            }
            onSubmit={(submission) => {
              createMutation.mutate({
                body: {
                  name: name.trim() || null,
                  cron: cron.trim(),
                  enabled: true,
                  // Schema 表单产出松散 Record; 此处桥接到 OpenAPI submission 联合.
                  submission: submission as ScheduleCreateRequest["submission"],
                },
              });
            }}
          />
        )}
      </Modal>

      <Modal
        opened={editing != null}
        onClose={() => setEditing(null)}
        title={t("editSchedule")}
        size="lg"
        centered
      >
        <Stack gap="md">
          <TextInput
            label={t("fields.name")}
            value={editName}
            onChange={(e) => setEditName(e.currentTarget.value)}
          />
          {editing != null && (
            <CronPicker key={editing.id} value={editCron} onChange={setEditCron} />
          )}
          <Switch
            label={t("fields.enabled")}
            checked={editEnabled}
            onChange={(e) => setEditEnabled(e.currentTarget.checked)}
          />
          {editing != null && (
            <>
              <Divider />
              <SchedulePayloadFacts payload={editing.payload} />
            </>
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setEditing(null)}>
              {t("common:actions.cancel")}
            </Button>
            <Button
              loading={updateMutation.isPending}
              disabled={!isUsableCron(editCron)}
              onClick={submitEdit}
            >
              {t("common:actions.save")}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
