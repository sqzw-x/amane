import { Accordion, Code, Stack, Switch, Text, Title } from "@mantine/core";
import { useId, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { UnsavedChangesBar } from "@/components/common/unsaved-changes-bar";
import type { FieldReplacementConfig } from "@/client/types.gen";
import { FieldReplacementEditor, validateFieldReplacementRules } from "./field-replacement-editor";

interface FieldReplacementDraft {
  enabled: boolean;
  rules: string;
}

interface FieldReplacementsSettingsProps {
  value: FieldReplacementConfig;
  saving: boolean;
  saveError: string | null;
  onSave: (value: FieldReplacementDraft) => void;
}

export function FieldReplacementsSettings({
  value,
  saving,
  saveError,
  onSave,
}: FieldReplacementsSettingsProps) {
  const { t } = useTranslation("settings");
  const formId = useId();
  const [draft, setDraft] = useState<FieldReplacementDraft>({
    enabled: value.enabled ?? false,
    rules: value.rules ?? "",
  });
  const dirty = draft.enabled !== (value.enabled ?? false) || draft.rules !== (value.rules ?? "");

  function handleSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    if (validateFieldReplacementRules(draft.rules).error !== null) return;
    onSave(draft);
  }

  return (
    <form id={formId} onSubmit={handleSubmit} aria-label={t("fieldReplacements.title")}>
      <Stack gap="md">
        <Switch
          checked={draft.enabled}
          label={t("fieldReplacements.enabled")}
          description={t("fieldReplacements.enabledDescription")}
          onChange={(event) => {
            const enabled = event.currentTarget.checked;
            setDraft((current) => ({ ...current, enabled }));
          }}
        />

        <Accordion variant="separated" defaultValue={null}>
          <Accordion.Item value="instructions">
            <Accordion.Control>{t("fieldReplacements.guideTitle")}</Accordion.Control>
            <Accordion.Panel>
              <Stack gap="xs">
                <Text size="sm">{t("fieldReplacements.guideIntro")}</Text>
                <Text size="sm">
                  {t("fieldReplacements.guideFields")} <Code>[title]</Code>、<Code>[actors]</Code>、
                  <Code>[studio]</Code>、<Code>[publisher]</Code>、<Code>[tags]</Code>、
                  <Code>[series]</Code>、<Code>[plot]</Code>、<Code>[directors]</Code>。
                </Text>
                <Text size="sm">
                  <Code>{t("fieldReplacements.ruleSyntax")}</Code> —{" "}
                  {t("fieldReplacements.guideRule")}
                </Text>
                <Text size="sm">{t("fieldReplacements.guideRegex")}</Text>
                <Text size="sm">{t("fieldReplacements.guideDelete")}</Text>
                <Text size="sm">{t("fieldReplacements.guideOrder")}</Text>
              </Stack>
            </Accordion.Panel>
          </Accordion.Item>
        </Accordion>

        <Stack gap="xs">
          <Title order={5}>{t("fieldReplacements.rulesLabel")}</Title>
          <FieldReplacementEditor
            value={draft.rules}
            placeholder={t("fieldReplacements.placeholder")}
            saveError={saveError}
            onChange={(rules) => setDraft((current) => ({ ...current, rules }))}
          />
        </Stack>

        <UnsavedChangesBar
          dirty={dirty}
          saving={saving}
          formId={formId}
          placement="affix"
          onDiscard={() => setDraft({ enabled: value.enabled ?? false, rules: value.rules ?? "" })}
        />
      </Stack>
    </form>
  );
}
