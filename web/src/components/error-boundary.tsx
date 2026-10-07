import { Alert, Button, Code, Group, Stack, Text } from "@mantine/core";
import { IconAlertTriangle, IconRefresh, IconServer } from "@tabler/icons-react";
import { Component, type ErrorInfo, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { shellEnvironment, shellSwitchServer } from "@/lib/shell";

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

interface ErrorFallbackProps {
  message: string;
  onRetry: () => void;
}

/** 兜底界面: 走 i18n, 语言切换后随之更新. */
function ErrorFallback({ message, onRetry }: ErrorFallbackProps) {
  const { t } = useTranslation("common");
  return (
    <Alert
      color="red"
      variant="light"
      icon={<IconAlertTriangle size={20} />}
      title={t("errorBoundary.title")}
      m="xl"
      radius="md"
    >
      <Stack gap="sm">
        <Text size="sm">{t("errorBoundary.description")}</Text>
        <Code block>{message}</Code>
        <Group gap="xs">
          <Button
            size="xs"
            variant="light"
            leftSection={<IconRefresh size={14} />}
            onClick={onRetry}
          >
            {t("errorBoundary.retry")}
          </Button>
          {/* 壳内渲染失败时页面入口点不到, 这里是唯一的补救操作. */}
          {shellEnvironment() ? (
            <Button
              size="xs"
              variant="light"
              color="gray"
              leftSection={<IconServer size={14} />}
              onClick={() => shellSwitchServer()}
            >
              {t("errorBoundary.switchServer")}
            </Button>
          ) : null}
        </Group>
      </Stack>
    </Alert>
  );
}

/** 捕获子树异常, 展示可重试提示而非白屏. */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("Unhandled render error", error, info.componentStack);
  }

  private readonly reset = () => this.setState({ error: null });

  override render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;

    return <ErrorFallback message={error.message} onRetry={this.reset} />;
  }
}
