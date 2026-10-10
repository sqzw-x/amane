import { Button } from "@mantine/core";
import { IconLanguage } from "@tabler/icons-react";
import { useCallback } from "react";
import { useTranslation } from "react-i18next";
import type { TranslationStatus } from "@/client/types.gen";

/** 逐字段结果的最小形状; 影片与演员两侧的 outcome 模型都满足. */
export interface TranslationOutcomeLike {
  status: TranslationStatus;
}

export interface TranslationNotice {
  message: string;
  color: string;
}

/** 逐字段结果 → 提示文案与颜色. 页面在 onSuccess 里调用返回的函数. */
export function useTranslationNotice(): (
  outcomes: readonly TranslationOutcomeLike[] | undefined,
) => TranslationNotice {
  const { t } = useTranslation("metadata");

  return useCallback(
    (outcomes: readonly TranslationOutcomeLike[] | undefined) => {
      let translated = 0;
      let unchanged = 0;
      let locked = 0;
      let failed = 0;
      for (const { status } of outcomes ?? []) {
        if (status === "translated") translated += 1;
        else if (status === "unchanged") unchanged += 1;
        else if (status === "locked") locked += 1;
        else failed += 1;
      }
      if (failed > 0) {
        return { message: t("detail.translate.failed", { n: failed }), color: "red" };
      }
      if (translated > 0) {
        return { message: t("detail.translate.translated", { n: translated }), color: "blue" };
      }
      if (locked > 0) {
        return { message: t("detail.translate.locked", { n: locked }), color: "yellow" };
      }
      if (unchanged > 0) {
        return { message: t("detail.translate.unchanged", { n: unchanged }), color: "blue" };
      }
      return { message: t("detail.translate.nothing"), color: "blue" };
    },
    [t],
  );
}

/** 独立翻译按钮: 只触发请求, 结果提示由调用方的 mutation 负责. */
export function TranslationButton({ pending, onClick }: { pending: boolean; onClick: () => void }) {
  const { t } = useTranslation("metadata");
  return (
    <Button
      size="xs"
      variant="light"
      color="cyan"
      leftSection={<IconLanguage size={14} />}
      loading={pending}
      onClick={onClick}
    >
      {t("detail.translate.action")}
    </Button>
  );
}
