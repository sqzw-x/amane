import {
  Autocomplete,
  Badge,
  Button,
  Checkbox,
  FileButton,
  Group,
  Modal,
  ScrollArea,
  Stack,
  Switch,
  Text,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconFileImport } from "@tabler/icons-react";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { createFeed } from "@/client/sdk.gen";
import { tryJoinFeedGroup, tryNormalizeFeedGroup } from "@/lib/feeds/groups";
import { type OpmlOutline, OpmlParseError, parseOpml } from "@/lib/feeds/opml";

export function OpmlImportButton({
  existingUrls,
  groupOptions,
  onImported,
}: {
  existingUrls: ReadonlySet<string>;
  groupOptions: readonly string[];
  onImported: () => void;
}) {
  const { t } = useTranslation(["feeds", "common"]);
  const resetRef = useRef<() => void>(null);
  const [items, setItems] = useState<OpmlOutline[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [opened, setOpened] = useState(false);
  const [importing, setImporting] = useState(false);
  const [progress, setProgress] = useState<{ current: number; total: number } | null>(null);
  const [groupPrefix, setGroupPrefix] = useState("");
  const [autoEnqueue, setAutoEnqueue] = useState(false);

  const prefixValid = tryNormalizeFeedGroup(groupPrefix) != null;

  function resolvedGroup(item: OpmlOutline): string {
    return tryJoinFeedGroup(groupPrefix, item.group) ?? item.group;
  }

  async function handleFile(file: File | null) {
    resetRef.current?.();
    if (file == null) {
      return;
    }
    try {
      const parsed = parseOpml(await file.text());
      if (parsed.length === 0) {
        notifications.show({ message: t("importEmpty"), color: "red" });
        return;
      }
      setItems(parsed);
      setSelected(parsed.filter((item) => !existingUrls.has(item.url)).map((item) => item.url));
      setGroupPrefix("");
      setAutoEnqueue(false);
      setOpened(true);
    } catch (err) {
      notifications.show({
        message:
          err instanceof OpmlParseError ? t("importInvalid") : t("common:toast.operationFailed"),
        color: "red",
      });
    }
  }

  async function handleImport() {
    const prefix = tryNormalizeFeedGroup(groupPrefix);
    if (prefix == null) {
      return;
    }
    const chosen = items.filter(
      (item) => selected.includes(item.url) && !existingUrls.has(item.url),
    );
    if (chosen.length === 0) {
      setOpened(false);
      return;
    }
    setImporting(true);
    setProgress({ current: 0, total: chosen.length });
    let created = 0;
    let skipped = 0;
    let failed = 0;
    try {
      for (const [index, item] of chosen.entries()) {
        setProgress({ current: index + 1, total: chosen.length });
        const group = tryJoinFeedGroup(prefix, item.group);
        if (group == null) {
          failed += 1;
          continue;
        }
        try {
          const result = await createFeed({
            body: {
              name: item.name,
              url: item.url,
              group,
              auto_enqueue: autoEnqueue,
            },
          });
          if (result.response?.status === 201 && result.data !== undefined) {
            created += 1;
          } else if (result.response?.status === 409) {
            skipped += 1;
          } else {
            failed += 1;
          }
        } catch {
          failed += 1;
        }
      }
    } finally {
      setImporting(false);
      setProgress(null);
      setOpened(false);
      onImported();
    }
    notifications.show({
      message: t("importResult", { created, skipped, failed }),
      color: failed > 0 ? "red" : "blue",
    });
  }

  const importableCount = selected.filter((url) => !existingUrls.has(url)).length;

  return (
    <>
      <FileButton
        resetRef={resetRef}
        accept=".opml,.xml,text/xml,application/xml,text/x-opml"
        onChange={handleFile}
      >
        {(props) => (
          <Button {...props} variant="default" leftSection={<IconFileImport size={16} />}>
            {t("importOpml")}
          </Button>
        )}
      </FileButton>
      <Modal
        opened={opened}
        onClose={() => {
          if (!importing) setOpened(false);
        }}
        title={t("importTitle")}
        size="lg"
        centered
        closeOnClickOutside={!importing}
        closeOnEscape={!importing}
      >
        <Stack gap="md">
          <Text size="sm" c="dimmed">
            {t("importHint")}
          </Text>
          <Autocomplete
            label={t("importPrefix")}
            placeholder={t("importPrefixPlaceholder")}
            description={t("importPrefixHint")}
            data={[...groupOptions]}
            value={groupPrefix}
            error={prefixValid ? undefined : t("fields.groupInvalid")}
            disabled={importing}
            onChange={setGroupPrefix}
          />
          <Switch
            label={t("fields.autoEnqueue")}
            description={t("importAutoEnqueueHint")}
            checked={autoEnqueue}
            disabled={importing}
            onChange={(event) => setAutoEnqueue(event.currentTarget.checked)}
          />
          <Checkbox.Group value={selected} onChange={setSelected}>
            {/* 窄屏降低列表高度, 避免弹窗自身滚动与列表滚动形成双层. */}
            <ScrollArea.Autosize mah={{ base: 180, sm: 360 }} scrollbars="y">
              <Stack gap="xs">
                {items.map((item) => {
                  const exists = existingUrls.has(item.url);
                  const group = resolvedGroup(item);
                  return (
                    <Checkbox
                      key={item.url}
                      value={item.url}
                      disabled={exists || importing}
                      label={
                        <Stack gap={0}>
                          <Group gap="xs">
                            <Text size="sm">{item.name}</Text>
                            {group !== "" && (
                              <Badge size="xs" variant="light" color="gray">
                                {group}
                              </Badge>
                            )}
                            {exists && (
                              <Badge size="xs" variant="light">
                                {t("importExisting")}
                              </Badge>
                            )}
                          </Group>
                          <Text
                            size="xs"
                            c="dimmed"
                            ff="monospace"
                            style={{ overflowWrap: "anywhere" }}
                          >
                            {item.url}
                          </Text>
                        </Stack>
                      }
                    />
                  );
                })}
              </Stack>
            </ScrollArea.Autosize>
          </Checkbox.Group>
          {progress != null && (
            <Text size="sm" c="dimmed">
              {t("importProgress", progress)}
            </Text>
          )}
          <Group justify="flex-end">
            <Button variant="default" disabled={importing} onClick={() => setOpened(false)}>
              {t("common:actions.cancel")}
            </Button>
            <Button
              loading={importing}
              disabled={importableCount === 0 || !prefixValid}
              onClick={() => void handleImport()}
            >
              {t("importAction", { count: importableCount })}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </>
  );
}
