import { Badge, Group } from "@mantine/core";
import {
  IconBadgeHd,
  IconBlur,
  IconDroplet,
  IconEye,
  IconLockOpen,
  IconSubtitles,
} from "@tabler/icons-react";
import type { TFunction } from "i18next";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { ContentType, FilePhaseSummary, Mosaic } from "@/client/types.gen";
import { OverlayChip, OverlayChipLabel } from "@/components/media/overlay-chip";
import { exhaustiveRecord } from "@/lib/exhaustive";

export type FilePhaseLike = {
  has_subtitle?: boolean;
  uncensored?: boolean;
  mosaics?: Array<Mosaic>;
  mosaic?: Mosaic | null;
  definition?: string | null;
  content_type?: ContentType;
};

type PhaseChipKey = "sub" | "cen" | "u" | "crack" | "leak" | "def";

type PhaseChip = { key: PhaseChipKey; label: string };

const PHASE_CHIP = exhaustiveRecord<PhaseChipKey>()({
  sub: {
    color: "orange",
    Icon: IconSubtitles,
    iconColor: "var(--mantine-color-orange-4)",
  },
  cen: {
    color: "gray",
    Icon: IconBlur,
    iconColor: "var(--mantine-color-gray-4)",
  },
  u: {
    color: "red",
    Icon: IconEye,
    iconColor: "var(--mantine-color-red-4)",
  },
  crack: {
    color: "grape",
    Icon: IconLockOpen,
    iconColor: "var(--mantine-color-grape-3)",
  },
  leak: {
    color: "teal",
    Icon: IconDroplet,
    iconColor: "var(--mantine-color-teal-3)",
  },
  def: {
    color: "blue",
    Icon: IconBadgeHd,
    iconColor: "var(--mantine-color-blue-4)",
  },
});

function showsUncensored(phase: FilePhaseLike): boolean {
  if (phase.uncensored != null) return phase.uncensored;
  return phase.mosaic === "uncensored" || phase.content_type === "uncensored";
}

function hasMosaic(phase: FilePhaseLike, mosaic: Mosaic): boolean {
  if (phase.mosaics?.includes(mosaic)) return true;
  return phase.mosaic === mosaic;
}

function mosaicChips(phase: FilePhaseLike, t: TFunction<"metadata">): PhaseChip[] {
  const chips: PhaseChip[] = [];
  if (hasMosaic(phase, "censored")) {
    chips.push({ key: "cen", label: t("filePhase.censored") });
  }
  if (showsUncensored(phase)) {
    chips.push({ key: "u", label: t("filePhase.uncensored") });
  }
  if (hasMosaic(phase, "cracked")) {
    chips.push({ key: "crack", label: t("filePhase.cracked") });
  }
  if (hasMosaic(phase, "leaked")) {
    chips.push({ key: "leak", label: t("filePhase.leaked") });
  }
  return chips;
}

function mediaChips(phase: FilePhaseLike, t: TFunction<"metadata">): PhaseChip[] {
  const chips: PhaseChip[] = [];
  if (phase.has_subtitle) {
    chips.push({ key: "sub", label: t("filePhase.subtitle") });
  }
  if (phase.definition) {
    chips.push({ key: "def", label: phase.definition });
  }
  return chips;
}

function PhaseOverlayChip({ chip }: { chip: PhaseChip }) {
  const meta = PHASE_CHIP[chip.key];
  const monospace = chip.key === "def";
  return (
    <OverlayChip>
      <meta.Icon size={12} color={meta.iconColor} />
      <OverlayChipLabel ff={monospace ? "monospace" : undefined} lts={monospace ? 0.3 : undefined}>
        {chip.label}
      </OverlayChipLabel>
    </OverlayChip>
  );
}

interface FilePhaseBadgesProps {
  phase: FilePhaseLike | FilePhaseSummary | null | undefined;
  size?: "xs" | "sm";
}

/** 表格/详情文件列表: 中字 / 有码 / 无码 / 破解 / 流出 / 清晰度. 无码看 mosaic 或 content_type. */
export function FilePhaseBadges({ phase, size = "xs" }: FilePhaseBadgesProps) {
  const { t } = useTranslation("metadata");
  if (phase == null) return null;

  const media = mediaChips(phase, t);
  const badges = [
    ...media.filter((chip) => chip.key === "sub"),
    ...mosaicChips(phase, t),
    ...media.filter((chip) => chip.key === "def"),
  ];
  if (badges.length === 0) return null;

  const iconSize = size === "sm" ? 12 : 10;
  return (
    <Group gap={4} wrap="wrap">
      {badges.map((badge) => {
        const meta = PHASE_CHIP[badge.key];
        return (
          <Badge
            key={badge.key}
            size={size}
            color={meta.color}
            variant="filled"
            leftSection={<meta.Icon size={iconSize} />}
          >
            {badge.label}
          </Badge>
        );
      })}
    </Group>
  );
}

const CORNER_GROUP = { zIndex: 1, maxWidth: "68%" } as const;

interface FilePhaseOverlayProps {
  phase: FilePhaseLike | FilePhaseSummary | null | undefined;
  /** 左下角追加 (出演年龄), 与中字/清晰度同组. */
  bottomExtra?: ReactNode;
}

/** 海报/封面 CSS 水印. 左上马赛克, 左下中字+清晰度; 样式对齐评分/日期 chip. */
export function FilePhaseOverlay({ phase, bottomExtra }: FilePhaseOverlayProps) {
  const { t } = useTranslation("metadata");
  const mosaic = phase != null ? mosaicChips(phase, t) : [];
  const media = phase != null ? mediaChips(phase, t) : [];

  return (
    <>
      {mosaic.length > 0 && (
        <Group pos="absolute" top={6} left={6} gap={4} wrap="wrap" style={CORNER_GROUP}>
          {mosaic.map((chip) => (
            <PhaseOverlayChip key={chip.key} chip={chip} />
          ))}
        </Group>
      )}
      {(media.length > 0 || bottomExtra != null) && (
        <Group pos="absolute" bottom={6} left={6} gap={4} wrap="wrap" style={CORNER_GROUP}>
          {media.map((chip) => (
            <PhaseOverlayChip key={chip.key} chip={chip} />
          ))}
          {bottomExtra}
        </Group>
      )}
    </>
  );
}
