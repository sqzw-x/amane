import { Badge, Button, Group, Modal, Stack, Table, Text } from "@mantine/core";
import { IconListCheck, IconTrash } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { listFacetRulesOptions } from "@/client/@tanstack/react-query.gen";
import type { FacetKind } from "@/client/types.gen";
import { HintedActionIcon } from "@/components/common/hinted-action-icon";

export interface FacetRulesPanelProps {
  kind: FacetKind;
  /** 单条撤销 (来自 useFacetIdentityActions 的 deleteRule). */
  onDeleteRule: (ruleId: number) => void;
}

/**
 * 爬取侧分类的用户规则 (别名 / 黑名单) 入口 + 弹窗.
 * 按钮位于列表壳顶栏 (不随表体滚动), 弹窗内展示规则表并支持单条撤销.
 * user_tag 无规则, 由调用方自行不渲染.
 */
export function FacetRulesPanel({ kind, onDeleteRule }: FacetRulesPanelProps) {
  const { t } = useTranslation(["metadata"]);
  const [opened, setOpened] = useState(false);
  const { data: rulesData } = useQuery(listFacetRulesOptions({ path: { kind } }));
  const rules = rulesData?.items ?? [];

  return (
    <>
      <Button
        variant="default"
        size="sm"
        leftSection={<IconListCheck size={16} />}
        onClick={() => setOpened(true)}
      >
        {t("manage.rulesTitle")}
        {rules.length > 0 ? ` (${rules.length})` : ""}
      </Button>

      <Modal
        opened={opened}
        onClose={() => setOpened(false)}
        title={t("manage.rulesTitle")}
        size="xl"
        centered
      >
        <Stack gap="sm">
          <Text size="sm" c="dimmed">
            {t("manage.rulesHint")}
          </Text>
          {rules.length === 0 ? (
            <Text size="sm" c="dimmed">
              {t("manage.rulesEmpty")}
            </Text>
          ) : (
            <Table highlightOnHover verticalSpacing="xs">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>{t("manage.ruleSource")}</Table.Th>
                  <Table.Th>{t("manage.ruleAction")}</Table.Th>
                  <Table.Th>{t("manage.ruleTarget")}</Table.Th>
                  <Table.Th ta="right" w={80}>
                    {t("columns.actions")}
                  </Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {rules.map((rule) => (
                  <Table.Tr key={rule.id}>
                    <Table.Td>{rule.source_name}</Table.Td>
                    <Table.Td>
                      <Badge variant="light" color={rule.action === "block" ? "red" : "blue"}>
                        {rule.action === "block" ? t("manage.ruleBlock") : t("manage.ruleAlias")}
                      </Badge>
                    </Table.Td>
                    <Table.Td>{rule.target_name ?? "—"}</Table.Td>
                    <Table.Td>
                      <Group justify="flex-end">
                        <HintedActionIcon
                          variant="subtle"
                          color="red"
                          label={t("manage.ruleRemove")}
                          onClick={() => onDeleteRule(rule.id)}
                        >
                          <IconTrash size={16} />
                        </HintedActionIcon>
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}
        </Stack>
      </Modal>
    </>
  );
}
