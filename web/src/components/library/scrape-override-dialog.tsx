import { Button, Group, Input, Modal, Stack, TextInput } from "@mantine/core";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { ContentType, MediaFileResponse } from "@/client/types.gen";
import { EnumToggle } from "@/components/common/enum-toggle";
import { CONTENT_TYPES } from "@/lib/exhaustive-maps";

const CONTENT_TYPE_OPTIONS = ["", ...CONTENT_TYPES] as const;

type ContentTypeChoice = (typeof CONTENT_TYPE_OPTIONS)[number];

interface ScrapeOverrideDialogProps {
  target: MediaFileResponse | null;
  saving: boolean;
  onClose: () => void;
  onSubmit: (number: string, contentType: ContentType | undefined) => void;
}

export function ScrapeOverrideDialog({
  target,
  saving,
  onClose,
  onSubmit,
}: ScrapeOverrideDialogProps) {
  const { t } = useTranslation(["library", "common"]);

  return (
    <Modal opened={target != null} onClose={onClose} title={t("scrapeOverride.title")} size="lg">
      {target != null && (
        <ScrapeOverrideForm
          key={target.id}
          initialNumber={target.number ?? ""}
          saving={saving}
          onClose={onClose}
          onSubmit={onSubmit}
        />
      )}
    </Modal>
  );
}

function ScrapeOverrideForm({
  initialNumber,
  saving,
  onClose,
  onSubmit,
}: {
  initialNumber: string;
  saving: boolean;
  onClose: () => void;
  onSubmit: (number: string, contentType: ContentType | undefined) => void;
}) {
  const { t } = useTranslation(["library", "common"]);
  const [number, setNumber] = useState(initialNumber);
  const [contentType, setContentType] = useState<ContentTypeChoice>("");
  const trimmed = number.trim();

  return (
    <Stack>
      <TextInput
        label={t("scrapeOverride.number")}
        placeholder={t("scrapeOverride.numberPlaceholder")}
        value={number}
        onChange={(event) => setNumber(event.currentTarget.value)}
        data-autofocus
      />
      <Input.Wrapper
        label={t("scrapeOverride.contentType")}
        description={t("scrapeOverride.contentTypeHint")}
      >
        <EnumToggle
          fullWidth
          options={CONTENT_TYPE_OPTIONS}
          value={contentType}
          onChange={setContentType}
          getLabel={(value) =>
            value === ""
              ? t("scrapeOverride.contentTypeAuto")
              : t(`scrapeOverride.contentTypes.${value}`)
          }
        />
      </Input.Wrapper>
      <Group justify="flex-end">
        <Button variant="default" onClick={onClose} disabled={saving}>
          {t("common:actions.cancel")}
        </Button>
        <Button
          loading={saving}
          disabled={trimmed === ""}
          onClick={() => onSubmit(trimmed, contentType === "" ? undefined : contentType)}
        >
          {t("scrapeOverride.submit")}
        </Button>
      </Group>
    </Stack>
  );
}
