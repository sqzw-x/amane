import {
  Alert,
  Button,
  Center,
  Group,
  Paper,
  PasswordInput,
  Stack,
  Text,
  Title,
} from "@mantine/core";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { apiFetch } from "@/lib/api-token";
import { shellEnvironment, shellSwitchServer } from "@/lib/shell";

/**
 * API token 登录门. 首次访问 (无 cookie) 时整页替换 App; 提交后用
 * /api/system/desktop + 输入框中的 token 做 Bearer 校验, 成功时服务端下发
 * HttpOnly `amane_token` cookie, 之后所有请求经 cookie.
 */
interface LoginGateProps {
  onAuthed: () => void;
}

export function LoginGate({ onAuthed }: LoginGateProps) {
  const { t } = useTranslation("common");
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function submit() {
    const token = value.trim();
    if (!token) return;
    setSubmitting(true);
    setError(null);
    try {
      const resp = await apiFetch(`${import.meta.env.VITE_API_URL || ""}/api/system/desktop`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (resp.ok) {
        onAuthed();
      } else {
        setError(t("auth.invalidToken"));
      }
    } catch {
      setError(t("auth.connectionFailed"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Center h="var(--amane-vh)" px="md">
      <Paper withBorder p="xl" radius="md" w={380} maw="100%" shadow="sm">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          <Stack gap="md">
            <Title order={3}>{t("auth.title")}</Title>
            <Text size="sm" c="dimmed">
              {t("auth.description")}
            </Text>
            <PasswordInput
              value={value}
              onChange={(e) => setValue(e.currentTarget.value)}
              placeholder={t("auth.tokenPlaceholder")}
              autoFocus
            />
            {error && <Alert color="red">{error}</Alert>}
            <Group justify="space-between">
              {/* 壳内 token 由服务器页校验: 入口跳转过一次后仍未认证时, 这里给出回到那里的路径. */}
              {shellEnvironment() ? (
                <Button
                  type="button"
                  variant="subtle"
                  size="xs"
                  onClick={() => shellSwitchServer()}
                >
                  {t("auth.serverSettings")}
                </Button>
              ) : (
                <span />
              )}
              <Button type="submit" loading={submitting}>
                {t("actions.submit")}
              </Button>
            </Group>
          </Stack>
        </form>
      </Paper>
    </Center>
  );
}
