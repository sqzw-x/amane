import {
  Accordion,
  Anchor,
  Button,
  Group,
  Modal,
  NumberInput,
  Select,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
  Textarea,
  TagsInput,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ActorGender, ActorResponse, ActorUpdateRequest } from "@/client/types.gen";
import { UnsavedChangesBar } from "@/components/common/unsaved-changes-bar";
import { FanartLightbox } from "@/components/media/fanart-lightbox";
import { SortableImageList } from "@/components/media/sortable-image-list";
import { useResettingState } from "@/hooks/use-resetting-state";

interface ActorDraft {
  gender: ActorGender;
  birthday: string;
  birthplace: string;
  height: number | string;
  bust: number | string;
  waist: number | string;
  hip: number | string;
  cup: string;
  tagline: string;
  overview: string;
  aliases: string[];
  imageUrls: string[];
}

function draftFromActor(actor: ActorResponse): ActorDraft {
  return {
    gender: actor.gender ?? "unknown",
    birthday: actor.birthday ?? "",
    birthplace: actor.birthplace ?? "",
    height: actor.height ?? "",
    bust: actor.bust ?? "",
    waist: actor.waist ?? "",
    hip: actor.hip ?? "",
    cup: actor.cup ?? "",
    tagline: actor.tagline ?? "",
    overview: actor.overview ?? "",
    aliases: [...(actor.aliases ?? [])],
    imageUrls: [...(actor.image_urls ?? [])],
  };
}

function numOrNull(v: number | string): number | null {
  if (v === "" || v == null) return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

function sameStringList(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((v, i) => v === b[i]);
}

function isDraftDirty(draft: ActorDraft, actor: ActorResponse): boolean {
  const initial = draftFromActor(actor);
  return (
    draft.gender !== initial.gender ||
    draft.birthday !== initial.birthday ||
    draft.birthplace !== initial.birthplace ||
    numOrNull(draft.height) !== numOrNull(initial.height) ||
    numOrNull(draft.bust) !== numOrNull(initial.bust) ||
    numOrNull(draft.waist) !== numOrNull(initial.waist) ||
    numOrNull(draft.hip) !== numOrNull(initial.hip) ||
    draft.cup !== initial.cup ||
    draft.tagline !== initial.tagline ||
    draft.overview !== initial.overview ||
    !sameStringList(draft.aliases, initial.aliases) ||
    !sameStringList(draft.imageUrls, initial.imageUrls)
  );
}

export interface ActorEditDialogProps {
  actor: ActorResponse;
  opened: boolean;
  onClose: () => void;
  onSave: (patch: ActorUpdateRequest) => void;
  saving?: boolean;
}

export function ActorEditDialog({
  actor,
  opened,
  onClose,
  onSave,
  saving = false,
}: ActorEditDialogProps) {
  const { t } = useTranslation(["metadata", "common"]);
  const formId = useId();
  const formKey = opened ? actor.id : "closed";
  const [draft, setDraft] = useResettingState(() => draftFromActor(actor), formKey);
  const [newImageUrl, setNewImageUrl] = useResettingState(() => "", formKey);
  const [lightboxOpen, { open: openLightbox, close: closeLightbox }] = useDisclosure(false);
  const [lightboxIndex, setLightboxIndex] = useState(0);

  const [prevFormKey, setPrevFormKey] = useState(formKey);
  if (formKey !== prevFormKey) {
    setPrevFormKey(formKey);
    closeLightbox();
  }

  const rawSites = Object.entries(actor.raw ?? {});
  const dirty = isDraftDirty(draft, actor);

  function patchDraft(partial: Partial<ActorDraft>) {
    setDraft((prev) => ({ ...prev, ...partial }));
  }

  function handleSave() {
    onSave({
      gender: draft.gender,
      birthday: draft.birthday.trim() || null,
      birthplace: draft.birthplace.trim() || null,
      height: numOrNull(draft.height),
      bust: numOrNull(draft.bust),
      waist: numOrNull(draft.waist),
      hip: numOrNull(draft.hip),
      cup: draft.cup.trim() || null,
      tagline: draft.tagline.trim() || null,
      overview: draft.overview.trim() || null,
      aliases: draft.aliases.map((a) => a.trim()).filter(Boolean),
      image_urls: draft.imageUrls,
    });
  }

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={`${t("actors.editTitle")} — ${actor.name}`}
      size="72rem"
      styles={{ body: { paddingBottom: 88 } }}
    >
      <Stack
        component="form"
        id={formId}
        gap="sm"
        onSubmit={(e) => {
          e.preventDefault();
          if (dirty) handleSave();
        }}
      >
        <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="sm">
          <Select
            label={t("browse.person.gender")}
            description={t("actors.genderHint")}
            value={draft.gender}
            onChange={(v) => {
              if (v === "female" || v === "male" || v === "unknown") patchDraft({ gender: v });
            }}
            data={[
              { value: "female", label: t("browse.person.genderFemale") },
              { value: "male", label: t("browse.person.genderMale") },
              { value: "unknown", label: t("browse.person.genderUnknown") },
            ]}
            allowDeselect={false}
          />
          <TextInput
            label={t("browse.person.birthday")}
            description={t("browse.person.birthdayFormat")}
            value={draft.birthday}
            onChange={(e) => patchDraft({ birthday: e.currentTarget.value })}
            placeholder="YYYY-MM-DD"
          />
          <TextInput
            label={t("browse.person.birthplace")}
            value={draft.birthplace}
            onChange={(e) => patchDraft({ birthplace: e.currentTarget.value })}
          />
          <Group grow align="flex-end">
            <NumberInput
              label={t("browse.person.height")}
              value={draft.height}
              onChange={(v) => patchDraft({ height: v })}
              min={0}
              allowDecimal={false}
            />
            <TextInput
              label={t("browse.person.cup")}
              value={draft.cup}
              onChange={(e) => patchDraft({ cup: e.currentTarget.value })}
            />
          </Group>
        </SimpleGrid>
        <Group grow>
          <NumberInput
            label="B"
            value={draft.bust}
            onChange={(v) => patchDraft({ bust: v })}
            min={0}
            allowDecimal={false}
          />
          <NumberInput
            label="W"
            value={draft.waist}
            onChange={(v) => patchDraft({ waist: v })}
            min={0}
            allowDecimal={false}
          />
          <NumberInput
            label="H"
            value={draft.hip}
            onChange={(v) => patchDraft({ hip: v })}
            min={0}
            allowDecimal={false}
          />
        </Group>
        <TextInput
          label={t("browse.person.tagline")}
          value={draft.tagline}
          onChange={(e) => patchDraft({ tagline: e.currentTarget.value })}
        />
        <Textarea
          label={t("browse.person.overview")}
          value={draft.overview}
          onChange={(e) => patchDraft({ overview: e.currentTarget.value })}
          minRows={3}
          autosize
        />
        <TagsInput
          label={t("browse.person.aliases")}
          description={t("actors.aliasesHint")}
          value={draft.aliases}
          onChange={(aliases) => patchDraft({ aliases })}
          splitChars={[",", "，", "\n"]}
          clearable
        />

        <Stack gap="xs">
          <Text size="sm" fw={500}>
            {t("actors.imagesEdit")}
          </Text>
          <Text size="xs" c="dimmed">
            {t("actors.imagesEditHint")}
          </Text>
          <SortableImageList
            urls={draft.imageUrls}
            onChange={(imageUrls) => patchDraft({ imageUrls })}
            onOpen={(index) => {
              setLightboxIndex(index);
              openLightbox();
            }}
            primaryLabel={t("actors.primaryImage")}
            removeLabel={t("actors.removeImage")}
            reorderLabel={t("actors.reorderImage")}
          />
          <Group gap="xs" wrap="nowrap">
            <TextInput
              style={{ flex: 1 }}
              placeholder="https://"
              value={newImageUrl}
              onChange={(e) => setNewImageUrl(e.currentTarget.value)}
            />
            <Button
              variant="light"
              disabled={!/^https?:\/\//i.test(newImageUrl.trim())}
              onClick={() => {
                const url = newImageUrl.trim();
                setDraft((prev) => ({
                  ...prev,
                  imageUrls: prev.imageUrls.includes(url)
                    ? prev.imageUrls
                    : [...prev.imageUrls, url],
                }));
                setNewImageUrl("");
              }}
            >
              {t("common:actions.add")}
            </Button>
          </Group>
        </Stack>

        {rawSites.length > 0 && (
          <Stack gap="xs" pt="xs">
            <Text size="sm" fw={500}>
              {t("actors.rawSources")}
            </Text>
            <Text size="xs" c="dimmed">
              {t("actors.rawSourcesHint")}
            </Text>
            <Accordion variant="separated" radius="md">
              {rawSites.map(([site, payload]) => (
                <Accordion.Item key={site} value={site}>
                  <Accordion.Control>{site}</Accordion.Control>
                  <Accordion.Panel>
                    <RawSiteSummary site={site} payload={payload} />
                  </Accordion.Panel>
                </Accordion.Item>
              ))}
            </Accordion>
          </Stack>
        )}

        <UnsavedChangesBar
          dirty={dirty}
          saving={saving}
          formId={formId}
          placement="affix"
          onDiscard={() => {
            setDraft(draftFromActor(actor));
            setNewImageUrl("");
          }}
        />
      </Stack>

      {lightboxOpen && draft.imageUrls.length > 0 && (
        <FanartLightbox
          images={draft.imageUrls}
          initialIndex={lightboxIndex}
          onClose={closeLightbox}
        />
      )}
    </Modal>
  );
}

function RawSiteSummary({ site, payload }: { site: string; payload: unknown }) {
  const { t } = useTranslation("metadata");
  if (!payload || typeof payload !== "object") {
    return (
      <Text size="xs" c="dimmed">
        —
      </Text>
    );
  }
  const data = payload as Record<string, unknown>;
  const rows: { label: string; value: string }[] = [];
  const push = (label: string, value: unknown) => {
    if (value == null || value === "") return;
    if (typeof value === "string" || typeof value === "number") {
      rows.push({ label, value: String(value) });
    }
  };
  push(t("browse.person.birthday"), data.birthday);
  push(t("browse.person.birthplace"), data.birthplace);
  push(t("browse.person.height"), data.height);
  push(t("browse.person.cup"), data.cup);
  push(t("browse.person.tagline"), data.tagline);
  push(t("browse.person.overview"), data.overview);
  push(t("browse.person.sourceUrl"), data.source_url);
  if (Array.isArray(data.aliases) && data.aliases.length > 0) {
    rows.push({
      label: t("browse.person.aliases"),
      value: data.aliases.filter((a): a is string => typeof a === "string").join(" · "),
    });
  }
  if (Array.isArray(data.image_urls) && data.image_urls.length > 0) {
    rows.push({
      label: t("browse.person.images"),
      value: String(data.image_urls.length),
    });
  }

  if (rows.length === 0) {
    return (
      <Text size="xs" c="dimmed" ff="monospace" style={{ whiteSpace: "pre-wrap" }}>
        {JSON.stringify(data, null, 2)}
      </Text>
    );
  }

  return (
    <Stack gap={6}>
      {rows.map((row) => (
        <Group key={`${site}-${row.label}`} gap="xs" align="flex-start" wrap="nowrap">
          <Text size="xs" c="dimmed" w={72} style={{ flexShrink: 0 }}>
            {row.label}
          </Text>
          {row.label === t("browse.person.sourceUrl") && /^https?:\/\//i.test(row.value) ? (
            <Anchor href={row.value} target="_blank" rel="noreferrer" size="xs" lineClamp={2}>
              {row.value}
            </Anchor>
          ) : (
            <Text
              size="xs"
              style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", minWidth: 0 }}
            >
              {row.value}
            </Text>
          )}
        </Group>
      ))}
    </Stack>
  );
}
