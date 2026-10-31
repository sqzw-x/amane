import { Chip, Group, SegmentedControl, Stack, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { setMetadataWatermarkMutation } from "@/client/@tanstack/react-query.gen";
import type { FilePhaseSummary, WatermarkKind } from "@/client/types.gen";
import { FilePhaseBadges } from "@/components/media/file-phase-badges";
import { useConfig } from "@/hooks/use-config";
import { extractErrorMessage } from "@/lib/api-error";
import { FILE_DEFINITIONS } from "@/lib/exhaustive-maps";

/** 勾选顺序与 ``WatermarkKind`` 声明序一致: 同角叠放时据此向内排列. */
const KINDS: readonly WatermarkKind[] = [
  "subtitle",
  "uncensored",
  "censored",
  "cracked",
  "leaked",
  "definition",
];

/** 分辨率单独成组: 必须选中一个档位才算勾上. */
const PLAIN_KINDS = KINDS.filter((kind) => kind !== "definition");

function isPlainKind(value: string): value is WatermarkKind {
  return PLAIN_KINDS.some((kind) => kind === value);
}

function isDefinition(value: string): value is (typeof FILE_DEFINITIONS)[number] {
  return FILE_DEFINITIONS.some((definition) => definition === value);
}

/** 自动规则会产出的类别; 有码不在其中 (见 ``media/images.py::_stamp_stems``). */
function hasAutoHit(phase: FilePhaseSummary | null | undefined): boolean {
  if (phase == null) return false;
  return (
    phase.has_subtitle === true ||
    phase.uncensored === true ||
    (phase.mosaics?.length ?? 0) > 0 ||
    phase.definition != null
  );
}

function withDefinition(kinds: WatermarkKind[]): WatermarkKind[] {
  return kinds.includes("definition") ? kinds : [...kinds, "definition"];
}

function withoutDefinition(kinds: WatermarkKind[]): WatermarkKind[] {
  return kinds.filter((kind) => kind !== "definition");
}

export interface WatermarkPickerProps {
  metadataId: number;
  auto: boolean;
  kinds: WatermarkKind[];
  definition: string | null;
  phase: FilePhaseSummary | null | undefined;
  onSaved: () => void;
}

/**
 * 条目级封面水印覆盖. 只在全局启用水印时出现; auto 交给自动规则, 手动则完全按勾选打.
 * 手动分辨率必须选一个档位: 已有命中档位时, 勾上分辨率就用该档. 只影响下次整理落盘的封面 / 海报.
 */
export function WatermarkPicker({
  metadataId,
  auto,
  kinds,
  definition,
  phase,
  onSaved,
}: WatermarkPickerProps) {
  const { t } = useTranslation(["metadata", "common"]);
  const { data: config } = useConfig();
  // 勾了分辨率但还没选档位: 本地展开档位行, 不落库.
  const [levelOpen, setLevelOpen] = useState(false);
  const mutation = useMutation({
    ...setMetadataWatermarkMutation(),
    onSuccess: () => onSaved(),
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      }),
  });

  // 未启用水印时不暴露该设置: 打不开的开关只会让人困惑.
  if (config?.watermark?.enabled !== true) return null;

  const resolutionOn = kinds.includes("definition") || levelOpen;
  const hitDefinition =
    phase?.definition != null && isDefinition(phase.definition) ? phase.definition : null;
  const presetDefinition = definition ?? hitDefinition;

  function save(nextAuto: boolean, nextKinds: WatermarkKind[], nextDefinition: string | null) {
    mutation.mutate({
      path: { metadata_id: metadataId },
      body: { auto: nextAuto, kinds: nextKinds, definition: nextDefinition },
    });
  }

  function saveManual(nextKinds: WatermarkKind[], nextDefinition: string | null) {
    // 手动勾了分辨率却没有档位不算选择成功: 不能交给后端拒绝.
    const hasDefinition = nextKinds.includes("definition") && nextDefinition !== null;
    save(
      false,
      hasDefinition ? nextKinds : withoutDefinition(nextKinds),
      hasDefinition ? nextDefinition : null,
    );
  }

  function toggleResolution() {
    if (resolutionOn) {
      setLevelOpen(false);
      saveManual(withoutDefinition(kinds), null);
      return;
    }
    // 已经命中档位时, 勾上分辨率就直接用这一档, 不再要求多选一次.
    if (presetDefinition !== null) {
      setLevelOpen(false);
      saveManual(withDefinition(kinds), presetDefinition);
      return;
    }
    setLevelOpen(true);
  }

  function pickDefinition(value: string | null) {
    if (value === null) {
      setLevelOpen(false);
      saveManual(withoutDefinition(kinds), null);
      return;
    }
    if (!isDefinition(value)) return;
    setLevelOpen(false);
    saveManual(withDefinition(kinds), value);
  }

  return (
    <div>
      <Text size="xs" c="dimmed">
        {t("detail.watermark.title")}
      </Text>
      <Stack gap={6}>
        <Group gap="xs" align="center" wrap="wrap">
          <SegmentedControl
            size="xs"
            value={auto ? "auto" : "manual"}
            disabled={mutation.isPending}
            onChange={(value) => {
              setLevelOpen(false);
              if (value === "auto") {
                save(true, kinds, definition);
                return;
              }
              // 手动的分辨率必须有档位, 缺档位的条目不带这一项.
              const manualKinds =
                kinds.includes("definition") && definition === null
                  ? withoutDefinition(kinds)
                  : kinds;
              save(false, manualKinds, manualKinds.includes("definition") ? definition : null);
            }}
            data={[
              { value: "auto", label: t("detail.watermark.auto") },
              { value: "manual", label: t("detail.watermark.manual") },
            ]}
          />
          {auto &&
            (hasAutoHit(phase) ? (
              // 有码由自动规则排除, 这里同样不显示, 避免与实际落盘不一致.
              <FilePhaseBadges phase={{ ...phase, censored: false }} />
            ) : (
              <Text size="xs" c="dimmed">
                {t("detail.watermark.autoNone")}
              </Text>
            ))}
        </Group>

        {!auto && (
          <>
            <Group gap={6} align="center" wrap="wrap">
              <Chip.Group
                multiple
                value={withoutDefinition(kinds)}
                onChange={(values) =>
                  saveManual(
                    [
                      ...values.filter(isPlainKind),
                      ...kinds.filter((kind) => kind === "definition"),
                    ],
                    definition,
                  )
                }
              >
                <Group gap={6}>
                  {PLAIN_KINDS.map((kind) => (
                    <Chip key={kind} value={kind} size="xs" disabled={mutation.isPending}>
                      {t(`detail.watermark.kinds.${kind}`)}
                    </Chip>
                  ))}
                </Group>
              </Chip.Group>
              <Chip
                checked={resolutionOn}
                onChange={toggleResolution}
                size="xs"
                disabled={mutation.isPending}
              >
                {t("detail.watermark.kinds.definition")}
              </Chip>
            </Group>
            {resolutionOn && (
              <Group gap={6} align="center" wrap="wrap">
                <Text size="xs" c="dimmed">
                  {t("detail.watermark.level")}
                </Text>
                <Chip.Group
                  value={kinds.includes("definition") ? definition : null}
                  onChange={pickDefinition}
                >
                  <Group gap={4}>
                    {FILE_DEFINITIONS.map((level) => (
                      <Chip key={level} value={level} size="xs" disabled={mutation.isPending}>
                        {level}
                      </Chip>
                    ))}
                  </Group>
                </Chip.Group>
                {!kinds.includes("definition") && (
                  <Text size="xs" c="dimmed">
                    {t("detail.watermark.levelRequired")}
                  </Text>
                )}
              </Group>
            )}
            {kinds.length === 0 && !resolutionOn && (
              <Text size="xs" c="dimmed">
                {t("detail.watermark.none")}
              </Text>
            )}
          </>
        )}
      </Stack>
    </div>
  );
}
