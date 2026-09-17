import { Alert, Button, Code, Group, Stack, Text } from "@mantine/core";
import { IconAlertTriangle, IconRefresh, IconServer } from "@tabler/icons-react";
import { Component, type ErrorInfo, type ReactNode } from "react";

import { shellEnvironment, shellSwitchServer } from "@/lib/shell";

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
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

    return (
      <Alert
        color="red"
        variant="light"
        icon={<IconAlertTriangle size={20} />}
        title="页面发生错误"
        m="xl"
        radius="md"
      >
        <Stack gap="sm">
          <Text size="sm">渲染此页面时抛出了未捕获的异常, 详情见下方.</Text>
          <Code block>{error.message}</Code>
          <Group gap="xs">
            <Button
              size="xs"
              variant="light"
              leftSection={<IconRefresh size={14} />}
              onClick={this.reset}
            >
              重试
            </Button>
            {/* 壳内渲染失败时页面入口点不到, 这里是唯一的出口. */}
            {shellEnvironment() ? (
              <Button
                size="xs"
                variant="light"
                color="gray"
                leftSection={<IconServer size={14} />}
                onClick={() => shellSwitchServer()}
              >
                切换服务器
              </Button>
            ) : null}
          </Group>
        </Stack>
      </Alert>
    );
  }
}
