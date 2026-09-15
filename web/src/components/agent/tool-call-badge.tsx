import { Badge, Button, Code, Collapse, Group, Stack, Text, UnstyledButton } from "@mantine/core";
import { IconChevronDown, IconChevronRight, IconTool } from "@tabler/icons-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

export type ToolApprovalStatus = "pending" | "approved" | "rejected";

export type ToolApproval = {
  approval_id: string;
  sql: string;
  tool: string;
  status: ToolApprovalStatus;
};

export interface ToolCallView {
  toolCallId: string;
  name: string;
  args?: unknown;
  result?: unknown;
  approval?: ToolApproval;
}

export type ApprovalAction = "approve" | "reject" | "batch";

function formatJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function displayResult(result: unknown): unknown {
  if (result !== null && typeof result === "object" && "needs_approval" in result) {
    const { needs_approval: _drop, ...rest } = result as Record<string, unknown>;
    return Object.keys(rest).length > 0 ? rest : undefined;
  }
  return result;
}

export function ToolCallBadge({
  call,
  busy,
  onApprovalAction,
}: {
  call: ToolCallView;
  busy?: boolean;
  onApprovalAction?: (approval: ToolApproval, action: ApprovalAction) => void;
}) {
  const { t } = useTranslation("agent");
  const [open, setOpen] = useState(call.approval?.status === "pending");
  const approval = call.approval;
  const resultBody = displayResult(call.result);
  const pending = approval?.status === "pending";

  return (
    <Stack gap={4}>
      <UnstyledButton onClick={() => setOpen((v) => !v)} style={{ alignSelf: "flex-start" }}>
        <Badge
          leftSection={<IconTool size={12} />}
          rightSection={open ? <IconChevronDown size={12} /> : <IconChevronRight size={12} />}
          variant="light"
          color={pending ? "yellow" : approval?.status === "rejected" ? "red" : "gray"}
          style={{ cursor: "pointer" }}
        >
          {call.name}
        </Badge>
      </UnstyledButton>
      {pending && approval && onApprovalAction && (
        <Stack gap={6} pl={4}>
          {/* SQL 可能不含空格, 只按空白折行会撑破气泡并让消息区出现横向滚动. */}
          <Text size="xs" c="dimmed" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
            {approval.sql}
          </Text>
          <Group gap="xs">
            <Button
              size="compact-xs"
              loading={busy}
              onClick={() => onApprovalAction(approval, "approve")}
            >
              {t("approve")}
            </Button>
            <Button
              size="compact-xs"
              variant="light"
              color="red"
              disabled={busy}
              onClick={() => onApprovalAction(approval, "reject")}
            >
              {t("reject")}
            </Button>
            <Button
              size="compact-xs"
              variant="light"
              disabled={busy}
              onClick={() => onApprovalAction(approval, "batch")}
            >
              {t("batchApprove")}
            </Button>
          </Group>
        </Stack>
      )}
      {approval?.status === "approved" && (
        <Text size="xs" c="teal" pl={4}>
          {t("approvalApproved")}
        </Text>
      )}
      {approval?.status === "rejected" && (
        <Text size="xs" c="red" pl={4}>
          {t("approvalRejected")}
        </Text>
      )}
      <Collapse expanded={open}>
        <Stack
          gap="xs"
          p="sm"
          style={{
            border: "1px solid var(--mantine-color-default-border)",
            borderRadius: "var(--mantine-radius-md)",
            background: "var(--mantine-color-body)",
          }}
        >
          {call.args !== undefined && (
            <Stack gap={4}>
              <Text size="xs" c="dimmed" fw={600}>
                args
              </Text>
              <Code block style={{ maxHeight: 200, overflow: "auto", fontSize: 11 }}>
                {formatJson(call.args)}
              </Code>
            </Stack>
          )}
          {resultBody !== undefined && (
            <Stack gap={4}>
              <Text size="xs" c="dimmed" fw={600}>
                result
              </Text>
              <Code block style={{ maxHeight: 240, overflow: "auto", fontSize: 11 }}>
                {formatJson(resultBody)}
              </Code>
            </Stack>
          )}
        </Stack>
      </Collapse>
    </Stack>
  );
}

const COLLAPSE_THRESHOLD = 3;

/** 连续工具调用超过阈值时整组折叠; 有待批准时始终展开. */
export function ToolCallGroup({
  calls,
  busy,
  onApprovalAction,
}: {
  calls: ToolCallView[];
  busy?: boolean;
  onApprovalAction?: (approval: ToolApproval, action: ApprovalAction) => void;
}) {
  const { t } = useTranslation("agent");
  const hasPending = calls.some((c) => c.approval?.status === "pending");
  const [expanded, setExpanded] = useState(false);
  if (calls.length === 0) return null;

  if (calls.length <= COLLAPSE_THRESHOLD || hasPending) {
    return (
      <Stack gap={6}>
        {calls.map((call) => (
          <ToolCallBadge
            key={call.toolCallId}
            call={call}
            busy={busy}
            onApprovalAction={onApprovalAction}
          />
        ))}
      </Stack>
    );
  }

  return (
    <Stack gap={6}>
      <UnstyledButton onClick={() => setExpanded((v) => !v)} style={{ alignSelf: "flex-start" }}>
        <Group gap={4}>
          {expanded ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
          <Text size="xs" c="dimmed">
            {t("toolCallsCount", { n: calls.length })}
          </Text>
        </Group>
      </UnstyledButton>
      {expanded &&
        calls.map((call) => (
          <ToolCallBadge
            key={call.toolCallId}
            call={call}
            busy={busy}
            onApprovalAction={onApprovalAction}
          />
        ))}
    </Stack>
  );
}
