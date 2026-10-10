import { Badge } from "@mantine/core";
import { IconView360 } from "@tabler/icons-react";
import { useTranslation } from "react-i18next";
import { OverlayChip, OverlayChipLabel } from "@/components/media/overlay-chip";

/** 表格里的 VR 标识; 判定口径见后端 `parsing.is_vr`. */
export function VrBadge({ size = "xs" }: { size?: "xs" | "sm" }) {
  const { t } = useTranslation("metadata");
  return (
    <Badge
      size={size}
      color="violet"
      variant="filled"
      leftSection={<IconView360 size={size === "sm" ? 12 : 10} />}
    >
      {t("detail.fields.vr")}
    </Badge>
  );
}

/** 海报左上角的 VR 水印; 与相位水印同形. */
export function VrOverlayChip() {
  const { t } = useTranslation("metadata");
  return (
    <OverlayChip>
      <IconView360 size={12} color="var(--mantine-color-violet-3)" />
      <OverlayChipLabel>{t("detail.fields.vr")}</OverlayChipLabel>
    </OverlayChip>
  );
}
