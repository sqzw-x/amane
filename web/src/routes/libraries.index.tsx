import { Anchor, Badge, Button, Group, Modal, Paper, Stack, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconFolders, IconPlus, IconRefresh } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  createLibraryMutation,
  listLibrariesOptions,
  listLibrariesQueryKey,
  updateLibraryMutation,
} from "@/client/@tanstack/react-query.gen";
import type { LibraryResponse } from "@/client/types.gen";
import { LibraryActionButtons } from "@/components/library/library-actions";
import {
  LIBRARY_AUTOMATION_BADGE_COLOR,
  LIBRARY_FORM_MODAL_SIZE,
  emptyLibraryForm,
  libraryFormFromResponse,
  LibraryFormFields,
  libraryFormToCreateBody,
  libraryFormToUpdateBody,
  type LibraryFormState,
} from "@/components/library/library-form";
import { extractErrorMessage } from "@/lib/api-error";
import { applyLibraryCreateConfig, libraryCreateConfigFromForm } from "@/lib/library/create-config";
import { useUIStore } from "@/stores/ui";

export const Route = createFileRoute("/libraries/")({ component: LibrariesPage });

function LibraryCard({ library, onEdit }: { library: LibraryResponse; onEdit: () => void }) {
  const { t } = useTranslation(["library", "common"]);

  return (
    <Paper withBorder radius="md" p="md">
      <Group justify="space-between" wrap="wrap" align="flex-start">
        <Group gap="sm" align="flex-start" style={{ minWidth: 0 }}>
          <IconFolders size={22} style={{ marginTop: 2, flexShrink: 0 }} />
          <div style={{ minWidth: 0 }}>
            <Group gap="xs">
              <Link
                to="/libraries/$libraryId"
                params={{ libraryId: String(library.id) }}
                style={{ textDecoration: "none" }}
              >
                <Anchor component="span" underline="hover" fw={600} c="inherit">
                  {library.name}
                </Anchor>
              </Link>
              <Badge
                size="xs"
                color={LIBRARY_AUTOMATION_BADGE_COLOR[library.automation]}
                variant="light"
              >
                {t(`automation.${library.automation}`)}
              </Badge>
              <Badge size="xs" variant="light">
                {library.recursive ? t("recursive") : t("nonRecursive")}
              </Badge>
            </Group>
            <Text size="xs" c="dimmed" ff="monospace" truncate="end">
              {library.path}
            </Text>
          </div>
        </Group>

        <LibraryActionButtons library={library} onConfigure={onEdit} />
      </Group>
    </Paper>
  );
}

function LibrariesPage() {
  const { t } = useTranslation(["library", "common"]);
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery(listLibrariesOptions());
  const rememberedCreateConfig = useUIStore((s) => s.libraryCreateConfig);
  const setLibraryCreateConfig = useUIStore((s) => s.setLibraryCreateConfig);

  const [createOpen, setCreateOpen] = useState(false);
  const [createForm, setCreateForm] = useState<LibraryFormState>(emptyLibraryForm());
  const [editing, setEditing] = useState<LibraryResponse | null>(null);
  const [editForm, setEditForm] = useState<LibraryFormState>(emptyLibraryForm());

  const invalidate = () =>
    void queryClient.invalidateQueries({ queryKey: listLibrariesQueryKey() });

  const createMutation = useMutation({
    ...createLibraryMutation(),
    onSuccess: () => {
      notifications.show({ message: t("common:toast.watchPathAdded"), color: "blue" });
      setCreateOpen(false);
      setCreateForm(emptyLibraryForm());
      invalidate();
    },
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      }),
  });
  const updateMutation = useMutation({
    ...updateLibraryMutation(),
    onSuccess: () => {
      notifications.show({ message: t("common:toast.watchPathUpdated"), color: "blue" });
      setEditing(null);
      invalidate();
    },
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      }),
  });

  function submitCreate() {
    const config = libraryCreateConfigFromForm(createForm);
    createMutation.mutate(
      { body: libraryFormToCreateBody(createForm) },
      { onSuccess: () => setLibraryCreateConfig(config) },
    );
  }

  /** 新增弹窗打开时的初值: 上次成功创建的配置之上留空 name / path / scan. */
  function openCreate() {
    setCreateForm(applyLibraryCreateConfig(emptyLibraryForm(), rememberedCreateConfig));
    setCreateOpen(true);
  }

  /** 清空表单与记忆, 下次打开回到 schema 默认值. */
  function resetCreate() {
    setCreateForm(emptyLibraryForm());
    setLibraryCreateConfig(undefined);
  }

  function submitEdit() {
    if (!editing) return;
    updateMutation.mutate({
      path: { library_id: editing.id },
      body: libraryFormToUpdateBody(editForm),
    });
  }

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Title order={2}>{t("title")}</Title>
        <Button leftSection={<IconPlus size={16} />} onClick={openCreate}>
          {t("common:actions.add")}
        </Button>
      </Group>

      {!isLoading && (data?.items.length ?? 0) === 0 && (
        <Text c="dimmed" size="sm" ta="center" py="xl">
          {t("emptyList")}
        </Text>
      )}

      <Stack gap="sm">
        {(data?.items ?? []).map((lib) => (
          <LibraryCard
            key={lib.id}
            library={lib}
            onEdit={() => {
              setEditing(lib);
              setEditForm(libraryFormFromResponse(lib));
            }}
          />
        ))}
      </Stack>

      <Modal
        opened={createOpen}
        onClose={() => setCreateOpen(false)}
        title={t("common:actions.add")}
        size={LIBRARY_FORM_MODAL_SIZE}
        centered
      >
        <Stack gap="md">
          <LibraryFormFields value={createForm} onChange={setCreateForm} showCreateOnly />
          <Group justify="space-between">
            <Button
              variant="subtle"
              color="gray"
              leftSection={<IconRefresh size={16} />}
              onClick={resetCreate}
            >
              {t("createReset")}
            </Button>
            <Group>
              <Button variant="default" onClick={() => setCreateOpen(false)}>
                {t("common:actions.cancel")}
              </Button>
              <Button
                loading={createMutation.isPending}
                disabled={!createForm.path.trim() || !createForm.video_template.trim()}
                onClick={submitCreate}
              >
                {createMutation.isPending ? t("common:actions.saving") : t("common:actions.save")}
              </Button>
            </Group>
          </Group>
        </Stack>
      </Modal>

      <Modal
        opened={editing != null}
        onClose={() => setEditing(null)}
        title={t("configureLibrary")}
        size={LIBRARY_FORM_MODAL_SIZE}
        centered
      >
        <Stack gap="md">
          <LibraryFormFields value={editForm} onChange={setEditForm} showCreateOnly={false} />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setEditing(null)}>
              {t("common:actions.cancel")}
            </Button>
            <Button
              loading={updateMutation.isPending}
              disabled={!editForm.video_template.trim()}
              onClick={submitEdit}
            >
              {updateMutation.isPending ? t("common:actions.saving") : t("common:actions.save")}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
