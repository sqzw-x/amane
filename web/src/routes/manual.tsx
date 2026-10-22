import { Button, Code, CopyButton, Group, Paper, Stack, Text, Title, Tooltip } from "@mantine/core";
import { IconCheck, IconCopy } from "@tabler/icons-react";
import { createFileRoute } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

export const Route = createFileRoute("/manual")({
  component: ManualPage,
});

/**
 * 接入地址只取决于当前 origin, 因此浏览器侧拼出即可, 不必请求后端.
 * 路径与 `src/amane/api/routes/bootstrap.py` 的端点一致: 免鉴权, 只回固定文本.
 */
const GUIDE_PATH = "/api/agent-guide";

/**
 * 用户手册. 目前只有「配置外部 Agent」一节, 文档站的内容后续迁进来.
 *
 * 不做侧栏目录: 一节时它只是噪音. 等节数多到需要跳转再加, 那时每节一个锚点即可.
 */
function ManualPage() {
  const { t } = useTranslation("manual");

  return (
    <Stack gap="xl" maw={720}>
      <Title order={2}>{t("title")}</Title>
      <ConnectSection />
    </Stack>
  );
}

function ConnectSection() {
  const { t } = useTranslation("manual");
  const address = `${window.location.origin}${GUIDE_PATH}`;
  // 复制的是这句话而不是裸地址: 用户往往直接把剪贴板内容贴进对话框, 裸 URL 没有任何动作指引.
  const instruction = t("connect.instruction", { address });

  return (
    <Stack gap="lg">
      <Title order={4}>{t("connect.title")}</Title>

      <div>
        <Text size="sm" fw={600} mb="xs">
          {t("connect.copyHint")}
        </Text>
        <Group gap="xs" wrap="nowrap" align="center">
          {/* pre 默认不换行, 窄屏上地址会被裁掉; 长 URL 也只能按字符断开. */}
          <Code block style={{ flex: 1, whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
            {instruction}
          </Code>
          <CopyButton value={instruction} timeout={2000}>
            {({ copied, copy }) => (
              <Tooltip label={t("connect.copy")} withArrow>
                <Button
                  variant={copied ? "light" : "filled"}
                  color={copied ? "teal" : undefined}
                  leftSection={copied ? <IconCheck size={16} /> : <IconCopy size={16} />}
                  onClick={copy}
                >
                  {copied ? t("connect.copied") : t("connect.copy")}
                </Button>
              </Tooltip>
            )}
          </CopyButton>
        </Group>
        <Text size="sm" c="dimmed" mt="xs">
          {t("connect.effect")}
        </Text>
      </div>

      <Paper withBorder p="md">
        <Text size="sm" fw={600} mb={4}>
          {t("connect.token.title")}
        </Text>
        <Text size="sm" c="dimmed">
          {t("connect.token.body")}
        </Text>
      </Paper>

      <Paper withBorder p="md">
        <Text size="sm" fw={600} mb={4}>
          {t("connect.permission.title")}
        </Text>
        <Text size="sm" c="dimmed">
          {t("connect.permission.body")}
        </Text>
      </Paper>
    </Stack>
  );
}
