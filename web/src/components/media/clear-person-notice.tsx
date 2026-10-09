import { List } from "@mantine/core";
import { useTranslation } from "react-i18next";

/** 清空人物档案确认框的条目清单; 首条措辞随单个 / 批量而不同. */
export function ClearPersonNotice({ first }: { first: string }) {
  const { t } = useTranslation("metadata");
  return (
    <List size="sm" spacing={4}>
      <List.Item>{first}</List.Item>
      <List.Item>{t("actors.clearPersonLocks")}</List.Item>
      <List.Item>{t("actors.clearPersonCache")}</List.Item>
      <List.Item>{t("actors.clearPersonKeep")}</List.Item>
    </List>
  );
}
