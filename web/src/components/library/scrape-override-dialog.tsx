import { Button, Group, Input, Modal, SegmentedControl, Stack, TextInput } from "@mantine/core";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { ContentType, MediaFileResponse } from "@/client/types.gen";
import { EnumToggle } from "@/components/common/enum-toggle";
import { CONTENT_TYPES } from "@/lib/exhaustive-maps";

const CONTENT_TYPE_OPTIONS = ["", ...CONTENT_TYPES] as const;

type ContentTypeChoice = (typeof CONTENT_TYPE_OPTIONS)[number];

type SourceMode = "number" | "url";

export interface ScrapeOverrideInput {
  number?: string;
  url?: string;
}

interface ScrapeOverrideDialogProps {
  target: MediaFileResponse | null;
  saving: boolean;
  onClose: () => void;
  onSubmit: (source: ScrapeOverrideInput, contentType: ContentType | undefined) => void;
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
  onSubmit: (source: ScrapeOverrideInput, contentType: ContentType | undefined) => void;
}) {
  const { t } = useTranslation(["library", "common"]);
  const [mode, setMode] = useState<SourceMode>("number");
  const [number, setNumber] = useState(initialNumber);
  const [url, setUrl] = useState("");
  const [contentType, setContentType] = useState<ContentTypeChoice>("");
  const trimmedNumber = number.trim();
  const trimmedUrl = url.trim();
  const urlValid = trimmedUrl === "" || /^https?:\/\/\S+$/i.test(trimmedUrl);
  const canSubmit = mode === "number" ? trimmedNumber !== "" : trimmedUrl !== "" && urlValid;

  function submit() {
    const source: ScrapeOverrideInput =
      mode === "number" ? { number: trimmedNumber } : { url: trimmedUrl };
    // 网址刮削的类型由页面番号推断, 不提交内容类型.
    onSubmit(source, mode === "number" && contentType !== "" ? contentType : undefined);
  }

  return (
    <Stack>
      <SegmentedControl
        fullWidth
        value={mode}
        onChange={(value) => setMode(value === "url" ? "url" : "number")}
        data={[
          { value: "number", label: t("scrapeOverride.mode.number") },
          { value: "url", label: t("scrapeOverride.mode.url") },
        ]}
      />
      {mode === "number" ? (
        <TextInput
          label={t("scrapeOverride.number")}
          placeholder={t("scrapeOverride.numberPlaceholder")}
          value={number}
          onChange={(event) => setNumber(event.currentTarget.value)}
          data-autofocus
        />
      ) : (
        <TextInput
          label={t("scrapeOverride.url")}
          placeholder={t("scrapeOverride.urlPlaceholder")}
          description={t("scrapeOverride.urlHint")}
          value={url}
          onChange={(event) => setUrl(event.currentTarget.value)}
          error={urlValid ? undefined : t("scrapeOverride.urlInvalid")}
          data-autofocus
        />
      )}
      {mode === "number" && (
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
      )}
      <Group justify="flex-end">
        <Button variant="default" onClick={onClose} disabled={saving}>
          {t("common:actions.cancel")}
        </Button>
        <Button loading={saving} disabled={!canSubmit} onClick={submit}>
          {t("scrapeOverride.submit")}
        </Button>
      </Group>
    </Stack>
  );
}
