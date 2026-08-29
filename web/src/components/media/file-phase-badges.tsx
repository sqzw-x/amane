import { Badge, Group } from "@mantine/core";
import { useTranslation } from "react-i18next";
import type { ContentType, FilePhaseSummary, Mosaic } from "@/client/types.gen";

export type FilePhaseLike = {
  has_subtitle?: boolean;
  uncensored?: boolean;
  mosaics?: Array<Mosaic>;
  mosaic?: Mosaic | null;
  definition?: string | null;
  content_type?: ContentType;
};

function showsUncensored(phase: FilePhaseLike): boolean {
  if (phase.uncensored != null) return phase.uncensored;
  return phase.mosaic === "uncensored" || phase.content_type === "uncensored";
}

function hasMosaic(phase: FilePhaseLike, mosaic: Mosaic): boolean {
  if (phase.mosaics?.includes(mosaic)) return true;
  return phase.mosaic === mosaic;
}

interface FilePhaseBadgesProps {
  phase: FilePhaseLike | FilePhaseSummary | null | undefined;
  size?: "xs" | "sm";
}

/** 中字 / 无码 / 破解 / 流出 / 清晰度角标. 无码看 mosaic 或 content_type. */
export function FilePhaseBadges({ phase, size = "xs" }: FilePhaseBadgesProps) {
  const { t } = useTranslation("metadata");
  if (phase == null) return null;

  const badges: Array<{ key: string; label: string; color: string }> = [];
  if (phase.has_subtitle) {
    badges.push({ key: "sub", label: t("filePhase.subtitle"), color: "orange" });
  }
  if (showsUncensored(phase)) {
    badges.push({ key: "u", label: t("filePhase.uncensored"), color: "red" });
  }
  if (hasMosaic(phase, "cracked")) {
    badges.push({ key: "crack", label: t("filePhase.cracked"), color: "grape" });
  }
  if (hasMosaic(phase, "leaked")) {
    badges.push({ key: "leak", label: t("filePhase.leaked"), color: "teal" });
  }
  if (phase.definition) {
    badges.push({ key: "def", label: phase.definition, color: "blue" });
  }
  if (badges.length === 0) return null;

  return (
    <Group gap={4} wrap="wrap">
      {badges.map((badge) => (
        <Badge key={badge.key} size={size} color={badge.color} variant="filled">
          {badge.label}
        </Badge>
      ))}
    </Group>
  );
}
