/** AG-UI 页: assistant-ui runtime 驱动对话, 渲染复用 Amane 的 markdown 与输入框.

会话状态在客户端 thread 里, 事件来自 `/agent/sessions/{id}/agui`; 首屏历史经
`/agent/sessions/{id}/trace` 回放行重建 (AG-UI 协议本身没有历史回放).
*/

import { HttpAgent, type AgentSubscriber } from "@ag-ui/client";
import {
  AssistantRuntimeProvider,
  MessagePrimitive,
  ThreadPrimitive,
  useAuiState,
  type AssistantRuntime,
  type PartState,
  type TextMessagePartProps,
  type ThreadMessageLike,
  type ToolCallMessagePartProps,
} from "@assistant-ui/react";
import {
  useAgUiInterrupts,
  useAgUiRuntime,
  useAgUiSubmitInterruptResponses,
  type AgUiInterrupt,
} from "@assistant-ui/react-ag-ui";
import {
  Alert,
  Badge,
  Box,
  Button,
  Center,
  Code,
  Drawer,
  Group,
  Loader,
  Menu,
  Paper,
  ScrollArea,
  Stack,
  Text,
  TextInput,
  UnstyledButton,
} from "@mantine/core";
import { useDisclosure, useInterval } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import {
  IconCheck,
  IconChevronDown,
  IconChevronRight,
  IconDots,
  IconList,
  IconPencil,
  IconPlus,
  IconTool,
  IconTrash,
} from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ComponentProps,
  type ReactNode,
} from "react";
import { useTranslation } from "react-i18next";
import {
  createAgentSessionMutation,
  deleteAgentSessionMutation,
  generateAgentSessionTitleMutation,
  listAgentSessionsOptions,
  listAgentSessionsQueryKey,
  updateAgentSessionMutation,
} from "@/client/@tanstack/react-query.gen";
import { getAgentTrace } from "@/client/sdk.gen";
import type { AgentSessionResponse } from "@/client/types.gen";
import {
  listSavedQueriesQueryKey,
  updateSavedQueryMutation,
} from "@/client/@tanstack/react-query.gen";
import { ChatComposer, parseThinking, type ThinkingValue } from "@/components/agent/chat-composer";
import { MarkdownContent } from "@/components/agent/markdown-content";
import type { ChatMessage, RequestTokenUsage, TurnTokenUsage } from "@/lib/agent/trace";
import { SavedQueryActions } from "@/components/agent/saved-query-actions";
import {
  downloadSavedQueryResult,
  SavedQueryManager,
} from "@/components/agent/saved-query-manager";
import { APP_SHELL_MAIN_HEIGHT } from "@/components/layout/app-shell-metrics";
import { useFold } from "@/lib/agent/fold";
import { messagesFromTrace } from "@/lib/agent/trace";
import { TokenUsageBar, RequestUsageBar } from "@/components/agent/token-usage-bar";
import { confirm } from "@/lib/confirm";

function apiBase(): string {
  return import.meta.env.VITE_API_URL || "";
}

/** 回合失败时弹出提示; 主动取消 (AbortError) 不算失败. */
class NotifyingHttpAgent extends HttpAgent {
  override async runAgent(
    parameters?: Parameters<HttpAgent["runAgent"]>[0],
    subscriber?: AgentSubscriber,
  ) {
    return super.runAgent(parameters, {
      ...subscriber,
      onRunFailed: (params) => {
        if (params.error.name !== "AbortError") {
          notifications.show({ color: "red", message: params.error.message });
        }
        return subscriber?.onRunFailed?.(params);
      },
    });
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };

function toJsonValue(value: unknown): JsonValue {
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  ) {
    return value;
  }
  if (Array.isArray(value)) return value.map(toJsonValue);
  if (isRecord(value)) {
    const out: { [key: string]: JsonValue } = {};
    for (const [key, item] of Object.entries(value)) out[key] = toJsonValue(item);
    return out;
  }
  return String(value);
}

function toJsonObject(value: unknown): { [key: string]: JsonValue } {
  const converted = toJsonValue(value);
  return typeof converted === "object" && converted !== null && !Array.isArray(converted)
    ? converted
    : {};
}

function JsonBlock({ value }: { value: unknown }) {
  return (
    <Code block style={{ fontSize: 11, maxHeight: 200, overflow: "auto" }}>
      {JSON.stringify(value ?? null, null, 2)}
    </Code>
  );
}

function isRunning(status: { readonly type: string }): boolean {
  return status.type === "running";
}

function TextPart({ text, status }: Pick<TextMessagePartProps, "text" | "status">) {
  return <MarkdownContent text={text} streaming={isRunning(status)} />;
}

/** 思考块: 默认折叠; 同一步的多段思考由 ReasoningBlock 合并为一块. */
function ReasoningBlock({ running, children }: { running: boolean; children: ReactNode }) {
  const { t } = useTranslation("agent");
  const { open, toggle, headerRef } = useFold();
  return (
    <Box mb="xs">
      <UnstyledButton ref={headerRef} onClick={toggle}>
        <Group gap={4} wrap="nowrap">
          {open ? <IconChevronDown size={13} /> : <IconChevronRight size={13} />}
          <Text size="xs" c="dimmed">
            {t("thinking.label")}
          </Text>
          {running && <Loader size={12} />}
        </Group>
      </UnstyledButton>
      {open && (
        <Box pl="sm" mt={4} style={{ borderLeft: "2px solid var(--mantine-color-default-border)" }}>
          {children}
        </Box>
      )}
    </Box>
  );
}

/** 单段思考正文; 折叠由 ReasoningBlock 统一负责. */
function ReasoningPart({ text }: { text: string }) {
  return (
    <Text size="xs" c="dimmed" style={{ whiteSpace: "pre-wrap" }}>
      {text}
    </Text>
  );
}

/** 单次请求的用量条: 回放行把它落在该次请求产出的内容之后. */
function RequestUsagePart({ data }: { data: RequestTokenUsage }) {
  return <RequestUsageBar usage={data} />;
}

/** 一段活动里工具调用超过这个数就整组折叠. */
const ACTIVITY_TOOL_LIMIT = 3;

/** 工具调用超过阈值的连续活动 (思考 + 工具调用) 折叠为一块. */
function ActivityGroup({ count, children }: { count: number; children: ReactNode }) {
  const { t } = useTranslation("agent");
  const { open, toggle, headerRef } = useFold();
  return (
    <Box mb="xs">
      <UnstyledButton ref={headerRef} onClick={toggle}>
        <Group gap={4} wrap="nowrap">
          {open ? <IconChevronDown size={13} /> : <IconChevronRight size={13} />}
          <Text size="xs" c="dimmed">
            {t("toolCallsCount", { n: count })}
          </Text>
        </Group>
      </UnstyledButton>
      {open && (
        <Box
          pl="sm"
          mt="xs"
          style={{ borderLeft: "2px solid var(--mantine-color-default-border)" }}
        >
          {children}
        </Box>
      )}
    </Box>
  );
}

type AssistantPartsComponents = ComponentProps<typeof MessagePrimitive.PartByIndex>["components"];

/** 回放行里的逐请求用量在协议里没有位置, 以 data 部件随消息重建. */
const REQUEST_USAGE_PART = "request-usage";

const ASSISTANT_PARTS = {
  Text: TextPart,
  Reasoning: ReasoningPart,
  tools: { Override: ToolCallPart },
  data: { by_name: { [REQUEST_USAGE_PART]: RequestUsagePart } },
} satisfies AssistantPartsComponents;

function PartAt({ index }: { index: number }) {
  return <MessagePrimitive.PartByIndex index={index} components={ASSISTANT_PARTS} />;
}

/** 一段部件: 连续思考合并成一块折叠, 其余各归各. */
function PartRun({
  parts,
  start,
  end,
}: {
  parts: readonly PartState[];
  start: number;
  end: number;
}) {
  const runs = useMemo(() => reasoningRuns(parts, start, end), [parts, start, end]);
  return (
    <>
      {runs.map((run) =>
        run.kind === "reasoning" ? (
          <ReasoningBlock
            key={`thought-${run.indices[0]}`}
            running={run.indices.some((index) => parts[index]?.status.type === "running")}
          >
            {run.indices.map((index) => (
              <PartAt key={index} index={index} />
            ))}
          </ReasoningBlock>
        ) : (
          <PartAt key={run.index} index={run.index} />
        ),
      )}
    </>
  );
}

type PartRunChunk = { kind: "reasoning"; indices: number[] } | { kind: "part"; index: number };

/** 把 [start, end) 切成连续思考块与单部件. */
function reasoningRuns(parts: readonly PartState[], start: number, end: number): PartRunChunk[] {
  const chunks: PartRunChunk[] = [];
  let index = start;
  while (index < end) {
    if (parts[index]?.type !== "reasoning") {
      chunks.push({ kind: "part", index });
      index += 1;
      continue;
    }
    const indices: number[] = [];
    while (index < end && parts[index]?.type === "reasoning") {
      indices.push(index);
      index += 1;
    }
    chunks.push({ kind: "reasoning", indices });
  }
  return chunks;
}

/** 助手部件: 默认折叠思考; 整条消息工具调用够多时, 首尾活动 (含夹在中间的文本) 折成一块. */
function AssistantParts() {
  const approval = useContext(ApprovalContext);
  const parts = useAuiState((state) => state.message.parts);
  const tools = parts.flatMap((part) => (part.type === "tool-call" ? [part.toolCallId] : []));
  const first = parts.findIndex((part) => part.type === "reasoning" || part.type === "tool-call");
  const last = parts.findLastIndex(
    (part) => part.type === "reasoning" || part.type === "tool-call",
  );
  // 未决审批在活动里时保持展开, 否则批准入口会被折没.
  const awaiting = tools.some((id) =>
    approval?.interrupts.some((interrupt) => interrupt.toolCallId === id),
  );

  if (tools.length <= ACTIVITY_TOOL_LIMIT || first < 0 || awaiting) {
    return <PartRun parts={parts} start={0} end={parts.length} />;
  }
  return (
    <>
      <PartRun parts={parts} start={0} end={first} />
      <ActivityGroup count={tools.length}>
        <PartRun parts={parts} start={first} end={last + 1} />
      </ActivityGroup>
      <PartRun parts={parts} start={last + 1} end={parts.length} />
    </>
  );
}

/** 交付的 SQL 视图: 只有 sql_deliver 的回执进芯片, sql_explore 的探查视图不进. */
function savedQueryIdOf(toolName: string, result: unknown): number | null {
  if (toolName !== "sql_deliver" || !isRecord(result)) return null;
  const id = result.saved_query_id;
  return typeof id === "number" ? id : null;
}

/** 参数视图: 优先已解析的对象; 流式未成形或日志里只留 JSON 文本时退回解析文本. */
function argsBodyOf(args: unknown, argsText: string): unknown {
  if (isRecord(args) && Object.keys(args).length > 0) return args;
  const text = argsText.trim();
  if (!text) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function ToolCallPart({
  toolCallId,
  toolName,
  args,
  argsText,
  result,
  status,
}: Pick<
  ToolCallMessagePartProps,
  "toolCallId" | "toolName" | "args" | "argsText" | "result" | "status"
>) {
  const { open, toggle, headerRef } = useFold();
  const queryClient = useQueryClient();
  const savedQueryId = savedQueryIdOf(toolName, result);
  const argsBody = argsBodyOf(args, argsText);
  const persist = useMutation({
    ...updateSavedQueryMutation(),
    onSuccess: () =>
      void queryClient.invalidateQueries({
        queryKey: listSavedQueriesQueryKey({ query: { persisted_only: true } }),
      }),
  });

  return (
    <Box
      mb="xs"
      style={{
        border: "1px solid var(--mantine-color-default-border)",
        borderRadius: "var(--mantine-radius-md)",
        overflow: "hidden",
      }}
    >
      <UnstyledButton ref={headerRef} px="sm" py={6} w="100%" onClick={toggle}>
        <Group gap={6} wrap="nowrap">
          <IconTool size={14} stroke={1.6} />
          <Text size="xs" fw={500} ff="monospace">
            {toolName}
          </Text>
          {isRunning(status) ? (
            <Loader size={12} />
          ) : result !== undefined ? (
            <IconCheck size={13} color="var(--mantine-color-teal-6)" />
          ) : null}
          <Box style={{ marginLeft: "auto", display: "flex", lineHeight: 0 }}>
            {open ? <IconChevronDown size={13} /> : <IconChevronRight size={13} />}
          </Box>
        </Group>
      </UnstyledButton>
      <ApprovalGate toolCallId={toolCallId} />
      {savedQueryId !== null && (
        <Box px="sm" pb="sm">
          <SavedQueryActions
            ids={[savedQueryId]}
            onDownload={(id) => void downloadSavedQueryResult(id)}
            onPersist={(id) =>
              persist.mutate({ path: { query_id: id }, body: { persisted: true } })
            }
          />
        </Box>
      )}
      {open && (
        // 与工具名对齐: 图标 14 + 间距 6 + 卡片内边距 12
        <Stack gap={4} pl="xl" pr="sm" pb="sm">
          {argsBody !== undefined && (
            <>
              <Text size="xs" c="dimmed">
                参数
              </Text>
              <JsonBlock value={argsBody} />
            </>
          )}
          {result !== undefined && (
            <>
              <Text size="xs" c="dimmed" mt={4}>
                结果
              </Text>
              <JsonBlock value={result} />
            </>
          )}
        </Stack>
      )}
    </Box>
  );
}

function Message({ usage }: { usage: TurnTokenUsage | null }) {
  const { t } = useTranslation("agent");
  return (
    <Box mb="sm">
      <MessagePrimitive.If user>
        <UserMessage />
      </MessagePrimitive.If>
      <MessagePrimitive.If assistant>
        <Text size="xs" c="dimmed" mb={4}>
          {t("assistant")}
        </Text>
        <AssistantParts />
        {usage && (
          <MessagePrimitive.If last>
            <TokenUsageBar usage={usage} />
          </MessagePrimitive.If>
        )}
      </MessagePrimitive.If>
    </Box>
  );
}

/** 用户输入按原文展示, 不走 markdown. */
function UserTextPart({ text }: { text: string }) {
  return (
    <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
      {text}
    </Text>
  );
}

/** 用户消息: 标签在左, 正文收进气泡, 与助手的纯文本回复区分. */
function UserMessage() {
  const { t } = useTranslation("agent");
  return (
    <Stack gap={4} mb="sm">
      <Text size="xs" c="dimmed">
        {t("you")}
      </Text>
      <Box
        px="sm"
        py="xs"
        style={{
          width: "fit-content",
          maxWidth: "100%",
          background: "var(--mantine-color-default-hover)",
          borderRadius: "var(--mantine-radius-md)",
          wordBreak: "break-word",
        }}
      >
        <MessagePrimitive.Parts components={{ Text: UserTextPart }} />
      </Box>
    </Stack>
  );
}

/** 最近一回合的用量.

   服务端已在 `RUN_FINISHED.usage` 上给出 (协议字段, 官方适配器不填), 但当前客户端解析器会丢弃该
   字段, 故读回放行里的同一份数据.
*/
function useTurnUsage(sessionId: number, running: boolean): TurnTokenUsage | null {
  const [usage, setUsage] = useState<TurnTokenUsage | null>(null);

  useEffect(() => {
    if (running) return;
    void (async () => {
      const { data } = await getAgentTrace({ path: { session_id: sessionId } });
      if (!data) return;
      const messages = messagesFromTrace(data.events).messages;
      const last = messages.findLast((message) => message.role === "assistant");
      setUsage(last?.role === "assistant" ? (last.usage ?? null) : null);
    })();
  }, [sessionId, running]);

  return usage;
}

/** 观察线程: 上报用量, 并在服务端回合结束时通知外层重建历史.

    回合在服务端后台执行; 刷新或换页后连接已断, 因此这里不接流, 只轮询回放行直到它跑完.
*/
function ThreadObserver({
  sessionId,
  onUsage,
  onTurnEnd,
}: {
  sessionId: number;
  onUsage: (usage: TurnTokenUsage | null) => void;
  onTurnEnd: () => void;
}) {
  const { t } = useTranslation("agent");
  const running = useAuiState((state) => state.thread.isRunning);
  const usage = useTurnUsage(sessionId, running);
  const [serverTurn, setServerTurn] = useState(false);

  useEffect(() => onUsage(usage), [onUsage, usage]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const { data } = await getAgentTrace({ path: { session_id: sessionId } });
      if (!cancelled) setServerTurn(data?.turn_running ?? false);
    })();
    return () => {
      cancelled = true;
    };
  }, [sessionId, running]);

  const watching = !running && serverTurn;

  useInterval(() => {
    if (!watching) return;
    void (async () => {
      const { data } = await getAgentTrace({ path: { session_id: sessionId } });
      if (data?.turn_running) return;
      setServerTurn(false);
      onTurnEnd();
    })();
  }, 1500);

  if (!watching) return null;
  return (
    <Group gap="xs" px="sm">
      <Loader size="xs" />
      <Text size="xs" c="dimmed">
        {t("turnRunning")}
      </Text>
    </Group>
  );
}

type Decision = "approve" | "reject";

type ApprovalQueue = {
  interrupts: readonly AgUiInterrupt[];
  decisions: Record<string, Decision>;
  submitting: boolean;
  decide: (interruptId: string, decision: Decision) => void;
  approveAll: () => void;
};

/** 审批队列.

    一次 resume 必须回答全部打开的中断, 故逐个点选只暂存决定, 最后一个决定落下时才整批提交;
    批量批准即对全部中断一次暂存 approve.
*/
function useApprovalQueue(): ApprovalQueue {
  const interrupts = useAgUiInterrupts();
  const submit = useAgUiSubmitInterruptResponses();
  const [decisions, setDecisions] = useState<Record<string, Decision>>({});
  const [submitting, setSubmitting] = useState(false);

  const flush = (next: Record<string, Decision>) => {
    setSubmitting(true);
    void (async () => {
      try {
        await submit(
          interrupts.map((item) => ({
            interruptId: item.id,
            status: "resolved" as const,
            payload:
              next[item.id] === "approve"
                ? { approved: true }
                : { approved: false, reason: "已拒绝" },
          })),
        );
      } catch (error) {
        notifications.show({ color: "red", message: String(error) });
      } finally {
        setSubmitting(false);
        setDecisions({});
      }
    })();
  };

  const record = (next: Record<string, Decision>) => {
    setDecisions(next);
    if (interrupts.every((item) => next[item.id] !== undefined)) flush(next);
  };

  return {
    interrupts,
    decisions,
    submitting,
    decide: (interruptId, decision) => record({ ...decisions, [interruptId]: decision }),
    approveAll: () =>
      record(Object.fromEntries(interrupts.map((item) => [item.id, "approve" as const]))),
  };
}

const ApprovalContext = createContext<ApprovalQueue | null>(null);

/** SQL 可能不含空格, 只按空白折行会撑破气泡并让消息区出现横向滚动. */
function SqlText({ sql }: { sql: string }) {
  return (
    <Text size="xs" c="dimmed" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
      {sql}
    </Text>
  );
}

/** 工具卡片内的审批入口; 该工具调用没有对应中断时不渲染. */
function ApprovalGate({ toolCallId }: { toolCallId: string }) {
  const { t } = useTranslation("agent");
  const queue = useContext(ApprovalContext);
  const interrupt = queue?.interrupts.find((item) => item.toolCallId === toolCallId);
  if (!queue || !interrupt) return null;
  const decision = queue.decisions[interrupt.id];
  const sql = interrupt.metadata?.sql;

  return (
    <Stack gap={6} px="sm" pb="sm">
      {typeof sql === "string" && <SqlText sql={sql} />}
      {decision === undefined ? (
        <Group gap="xs">
          <Button
            size="compact-xs"
            disabled={queue.submitting}
            onClick={() => queue.decide(interrupt.id, "approve")}
          >
            {t("approve")}
          </Button>
          <Button
            size="compact-xs"
            variant="light"
            color="red"
            disabled={queue.submitting}
            onClick={() => queue.decide(interrupt.id, "reject")}
          >
            {t("reject")}
          </Button>
          <Button
            size="compact-xs"
            variant="light"
            disabled={queue.submitting}
            onClick={queue.approveAll}
          >
            {t("batchApprove")}
          </Button>
        </Group>
      ) : (
        <Text size="xs" c={decision === "approve" ? "teal" : "red"}>
          {decision === "approve" ? t("approvalApproved") : t("approvalRejected")}
        </Text>
      )}
    </Stack>
  );
}

/** 无工具卡片可挂的中断 (以及批量批准) 的兜底入口. */
function ApprovalPanel() {
  const { t } = useTranslation("agent");
  const queue = useContext(ApprovalContext);
  const orphans = queue?.interrupts.filter((item) => item.toolCallId === undefined) ?? [];

  if (!queue || (orphans.length === 0 && queue.interrupts.length < 2)) return null;

  return (
    <Stack gap="xs" p="sm" style={{ flexShrink: 0 }}>
      {orphans.map((interrupt) => (
        <Alert key={interrupt.id} color="yellow" title={interrupt.message ?? t("approve")}>
          <Stack gap="xs">
            {typeof interrupt.metadata?.sql === "string" && (
              <SqlText sql={interrupt.metadata.sql} />
            )}
            <Group gap="xs">
              <Button
                size="xs"
                disabled={queue.submitting}
                onClick={() => queue.decide(interrupt.id, "approve")}
              >
                {t("approve")}
              </Button>
              <Button
                size="xs"
                variant="default"
                disabled={queue.submitting}
                onClick={() => queue.decide(interrupt.id, "reject")}
              >
                {t("reject")}
              </Button>
            </Group>
          </Stack>
        </Alert>
      ))}
      {queue.interrupts.length > 1 && (
        <Group gap="xs">
          <Button size="xs" variant="light" disabled={queue.submitting} onClick={queue.approveAll}>
            {t("batchApprove")}
          </Button>
          <Text size="xs" c="dimmed">
            {queue.interrupts.length}
          </Text>
        </Group>
      )}
    </Stack>
  );
}

/** 回放行的参数: 日志里既可能是对象 (本地落盘) 也可能是 JSON 文本 (旧路径写的). */
function toolArgsObject(value: unknown): { [key: string]: JsonValue } {
  if (typeof value !== "string") return toJsonObject(value);
  try {
    return toJsonObject(JSON.parse(value));
  } catch {
    return {};
  }
}

function toolArgsText(value: unknown): string {
  if (typeof value === "string") return value;
  return value === undefined ? "" : JSON.stringify(value);
}

/** 回放行 → 首屏可见的历史 (AG-UI 协议本身没有历史回放). */
function toThreadMessageLike(message: ChatMessage, index: number): ThreadMessageLike {
  if (message.role === "user") {
    return {
      id: `trace-u-${index}`,
      role: "user",
      content: [{ type: "text", text: message.text }],
    };
  }
  return {
    id: `trace-a-${index}`,
    role: "assistant",
    content: message.blocks.map((block, blockIndex) => {
      if (block.kind === "text") return { type: "text" as const, text: block.text };
      if (block.kind === "reasoning") return { type: "reasoning" as const, text: block.text };
      if (block.kind === "usage") {
        return { type: `data-${REQUEST_USAGE_PART}` as const, data: block.usage };
      }
      return {
        type: "tool-call" as const,
        toolCallId: block.tool.toolCallId || `trace-tool-${index}-${blockIndex}`,
        toolName: block.tool.name,
        args: toolArgsObject(block.tool.args),
        argsText: toolArgsText(block.tool.args),
        result: block.tool.result,
      };
    }),
  };
}

/** 回放行末尾若停在中断上, 取出这组未决中断. */
function pendingInterrupts(events: readonly unknown[]): AgUiInterrupt[] {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const row = events[index];
    if (!isRecord(row) || row.type !== "agui" || !isRecord(row.event)) continue;
    if (row.event.type !== "RUN_FINISHED") continue;
    const outcome = row.event.outcome;
    if (!isRecord(outcome) || outcome.type !== "interrupt") return [];
    return Array.isArray(outcome.interrupts) ? outcome.interrupts.filter(isInterrupt) : [];
  }
  return [];
}

function isInterrupt(value: unknown): value is AgUiInterrupt {
  return isRecord(value) && typeof value.id === "string";
}

/** 用回放行重建会话; 未决中断挂到最后一条助手消息上, 刷新后仍可继续审批. */
async function seedThread(runtime: AssistantRuntime, sessionId: number): Promise<void> {
  const { data } = await getAgentTrace({ path: { session_id: sessionId } });
  if (!data) return;
  const like = messagesFromTrace(data.events).messages.map(toThreadMessageLike);
  const interrupts = pendingInterrupts(data.events);
  const last = like.at(-1);
  if (interrupts.length > 0 && last?.role === "assistant") {
    like[like.length - 1] = {
      ...last,
      status: { type: "requires-action", reason: "interrupt" },
      metadata: { custom: { agui: { interrupts } } },
    };
  }
  runtime.thread.reset(like);
}

/** 审批队列须在 runtime 内读取, 故单独一层 Provider. */
function ApprovalProvider({ children }: { children: ReactNode }) {
  const queue = useApprovalQueue();
  return <ApprovalContext.Provider value={queue}>{children}</ApprovalContext.Provider>;
}

function AgUiThread({
  sessionId,
  firstMessage,
  onFirstMessageSent,
  thinking,
  onThinkingChange,
  thinkingDisabled,
}: {
  sessionId: number;
  firstMessage: string | null;
  onFirstMessageSent: () => void;
  thinking: ThinkingValue | null;
  onThinkingChange: (value: ThinkingValue | null) => void;
  thinkingDisabled: boolean;
}) {
  const { t } = useTranslation("agent");
  const queryClient = useQueryClient();
  const agent = useMemo(
    () =>
      new NotifyingHttpAgent({
        url: `${apiBase()}/api/agent/sessions/${sessionId}/agui`,
        threadId: String(sessionId),
      }),
    [sessionId],
  );
  const runtime = useAgUiRuntime({ agent });
  const [loadingHistory, setLoadingHistory] = useState(true);
  const [usage, setUsage] = useState<TurnTokenUsage | null>(null);

  const seed = useCallback(() => seedThread(runtime, sessionId), [runtime, sessionId]);

  useEffect(() => {
    void (async () => {
      await seed();
      setLoadingHistory(false);
    })();
  }, [seed]);

  // 落地页首条消息: 会话建好后才能发, 故等历史重建完成再补发.
  useEffect(() => {
    if (loadingHistory || firstMessage === null) return;
    onFirstMessageSent();
    runtime.thread.append(firstMessage);
  }, [loadingHistory, firstMessage, onFirstMessageSent, runtime]);

  const handleTurnEnd = useCallback(() => {
    void seed();
    void queryClient.invalidateQueries({ queryKey: listAgentSessionsQueryKey() });
  }, [seed, queryClient]);

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ApprovalProvider>
        <Stack style={{ flex: 1, minWidth: 0, minHeight: 0, height: "100%" }} gap="sm">
          <Paper
            withBorder
            radius="md"
            style={{
              flex: 1,
              minHeight: 0,
              display: "flex",
              flexDirection: "column",
              overflow: "hidden",
            }}
          >
            <ApprovalPanel />
            <ThreadPrimitive.Root
              style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}
            >
              <ThreadPrimitive.Viewport
                style={{
                  flex: 1,
                  minHeight: 0,
                  overflow: "auto",
                  padding: "var(--mantine-spacing-md)",
                }}
              >
                {loadingHistory ? (
                  <Group gap="xs">
                    <Loader size="sm" />
                    <Text c="dimmed" size="sm">
                      {t("loadingHistory")}
                    </Text>
                  </Group>
                ) : (
                  <ThreadPrimitive.Empty>
                    <Text c="dimmed" size="sm">
                      {t("continueHint")}
                    </Text>
                  </ThreadPrimitive.Empty>
                )}
                <ThreadPrimitive.Messages>
                  {() => <Message usage={usage} />}
                </ThreadPrimitive.Messages>
              </ThreadPrimitive.Viewport>
            </ThreadPrimitive.Root>
          </Paper>
          <ThreadObserver sessionId={sessionId} onUsage={setUsage} onTurnEnd={handleTurnEnd} />
          <Composer
            runtime={runtime}
            sessionId={sessionId}
            thinking={thinking}
            onThinkingChange={onThinkingChange}
            thinkingDisabled={thinkingDisabled}
          />
        </Stack>
      </ApprovalProvider>
    </AssistantRuntimeProvider>
  );
}

function Composer({
  runtime,
  sessionId,
  thinking,
  onThinkingChange,
  thinkingDisabled,
}: {
  runtime: AssistantRuntime;
  sessionId: number;
  thinking: ThinkingValue | null;
  onThinkingChange: (value: ThinkingValue | null) => void;
  thinkingDisabled: boolean;
}) {
  const [value, setValue] = useState("");
  const running = useAuiState((state) => state.thread.isRunning);

  return (
    <Box style={{ flexShrink: 0 }}>
      <ChatComposer
        value={value}
        onChange={setValue}
        onSubmit={() => {
          const text = value.trim();
          if (!text || running) return;
          setValue("");
          runtime.thread.append(text);
        }}
        onStop={() => {
          // 回合在服务端后台跑, 断开连接停不住它: 先让服务端终止, 再收掉本地流.
          void fetch(`${apiBase()}/api/agent/sessions/${sessionId}/agui/cancel`, {
            method: "POST",
          });
          runtime.thread.cancelRun();
        }}
        loading={running}
        thinking={thinking}
        onThinkingChange={onThinkingChange}
        thinkingDisabled={thinkingDisabled}
      />
    </Box>
  );
}

function SessionItem({
  session,
  active,
  onSelect,
  onRename,
  onDelete,
}: {
  session: AgentSessionResponse;
  active: boolean;
  onSelect: () => void;
  onRename: (title: string) => void;
  onDelete: () => void;
}) {
  const { t } = useTranslation("agent");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(session.title);

  if (editing) {
    return (
      <TextInput
        autoFocus
        size="xs"
        value={draft}
        placeholder={t("renamePrompt")}
        onChange={(event) => setDraft(event.currentTarget.value)}
        onBlur={() => {
          setEditing(false);
          const next = draft.trim();
          if (next && next !== session.title) onRename(next);
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter") event.currentTarget.blur();
          if (event.key === "Escape") {
            setDraft(session.title);
            setEditing(false);
          }
        }}
      />
    );
  }

  return (
    <Group
      gap={4}
      wrap="nowrap"
      px="xs"
      py="xs"
      style={{
        borderRadius: "var(--mantine-radius-sm)",
        background: active ? "var(--mantine-primary-color-light)" : undefined,
      }}
    >
      <UnstyledButton onClick={onSelect} style={{ flex: 1, minWidth: 0 }}>
        <Group gap={6} wrap="nowrap">
          <Text size="sm" truncate style={{ flex: 1 }}>
            {session.title}
          </Text>
          {session.status === "awaiting_approval" && (
            <Badge size="xs" color="yellow" variant="light">
              {t("approve")}
            </Badge>
          )}
        </Group>
      </UnstyledButton>
      <Menu position="bottom-end" withinPortal shadow="md">
        <Menu.Target>
          <UnstyledButton
            aria-label={t("sessions")}
            p={6}
            style={{ display: "flex", lineHeight: 0 }}
          >
            <IconDots size={14} />
          </UnstyledButton>
        </Menu.Target>
        <Menu.Dropdown>
          <Menu.Item leftSection={<IconPencil size={14} />} onClick={() => setEditing(true)}>
            {t("renameSession")}
          </Menu.Item>
          <Menu.Item color="red" leftSection={<IconTrash size={14} />} onClick={onDelete}>
            {t("deleteSession")}
          </Menu.Item>
        </Menu.Dropdown>
      </Menu>
    </Group>
  );
}

function SessionsPanel({
  sessions,
  currentId,
  onSelect,
  onCreate,
}: {
  sessions: AgentSessionResponse[];
  currentId: number | null;
  onSelect: (id: number) => void;
  onCreate: () => void;
}) {
  const { t } = useTranslation(["agent", "common"]);
  const queryClient = useQueryClient();
  const removeSession = useMutation({
    ...deleteAgentSessionMutation(),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: listAgentSessionsQueryKey() });
      notifications.show({ message: t("sessionDeleted"), color: "blue" });
    },
    onError: (error) => notifications.show({ color: "red", message: String(error) }),
  });
  const renameSession = useMutation({
    ...updateAgentSessionMutation(),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: listAgentSessionsQueryKey() }),
  });

  return (
    <Paper
      withBorder
      radius="md"
      w={260}
      style={{ display: "flex", flexDirection: "column", minHeight: 0 }}
    >
      <Group justify="space-between" px="sm" py="xs" wrap="nowrap">
        <Text size="sm" fw={600}>
          {t("sessions")}
        </Text>
        <Group gap={4} wrap="nowrap">
          <SavedQueryManager sessionId={currentId} />
          <Button
            size="compact-xs"
            variant="light"
            leftSection={<IconPlus size={13} />}
            onClick={onCreate}
          >
            {t("newSession")}
          </Button>
        </Group>
      </Group>
      <ScrollArea style={{ flex: 1, minHeight: 0 }} px={4} pb="xs">
        <Stack gap={2}>
          {sessions.map((session) => (
            <SessionItem
              key={session.id}
              session={session}
              active={session.id === currentId}
              onSelect={() => onSelect(session.id)}
              onRename={(title) =>
                renameSession.mutate({ path: { session_id: session.id }, body: { title } })
              }
              onDelete={() => {
                void (async () => {
                  const ok = await confirm({
                    title: t("deleteSession"),
                    message: t("confirmDeleteSession"),
                    confirmLabel: t("common:actions.delete"),
                  });
                  if (!ok) return;
                  removeSession.mutate({ path: { session_id: session.id } });
                  if (session.id === currentId) onSelect(-1);
                })();
              }}
            />
          ))}
        </Stack>
      </ScrollArea>
    </Paper>
  );
}

export function AgentHome() {
  const { t } = useTranslation("agent");
  const queryClient = useQueryClient();
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [firstMessage, setFirstMessage] = useState<string | null>(null);
  const [input, setInput] = useState("");
  /** 「新对话」进入的草稿态: 首条消息发出前不建会话. */
  const [draft, setDraft] = useState(false);
  const [drawerOpened, drawer] = useDisclosure(false);
  const sessions = useQuery(listAgentSessionsOptions());
  const items = sessions.data?.items ?? [];

  const createSession = useMutation({
    ...createAgentSessionMutation(),
    onSuccess: (created) => {
      void queryClient.invalidateQueries({ queryKey: listAgentSessionsQueryKey() });
      setDraft(false);
      setSessionId(created.id);
    },
    onError: (error) => notifications.show({ color: "red", message: String(error) }),
  });
  const updateThinking = useMutation({
    ...updateAgentSessionMutation(),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: listAgentSessionsQueryKey() }),
    onError: (error) => notifications.show({ color: "red", message: String(error) }),
  });
  // 标题与回合并行生成, 先到先显; 失败保留建会话时的标题, 不打扰用户.
  const nameSession = useMutation({
    ...generateAgentSessionTitleMutation(),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: listAgentSessionsQueryKey() }),
  });

  // 未指定会话时进入最近一个; 一条会话都没有或处于草稿态时只留落地输入框.
  const activeId = draft ? null : (sessionId ?? items[0]?.id ?? null);
  const hasSessions = items.length > 0;

  const current = items.find((item) => item.id === activeId);
  const handleCreate = () => {
    drawer.close();
    setDraft(true);
    setSessionId(null);
    setFirstMessage(null);
  };
  const handleSelect = (id: number) => {
    drawer.close();
    setDraft(false);
    setSessionId(id < 0 ? null : id);
    setFirstMessage(null);
  };
  /** 草稿态首条消息: 建会话后另行请求标题 — 标题与回合并行, 不等回合结束. */
  const startSession = (text: string) => {
    void (async () => {
      try {
        const created = await createSession.mutateAsync({ body: { title: t("newSession") } });
        nameSession.mutate({ path: { session_id: created.id }, body: { prompt: text } });
      } catch {
        // 建会话失败已由 createSession.onError 提示.
      }
    })();
  };

  const sessionsPanel = (
    <SessionsPanel
      sessions={items}
      currentId={activeId}
      onSelect={handleSelect}
      onCreate={handleCreate}
    />
  );

  return (
    <Stack gap="sm" style={{ height: APP_SHELL_MAIN_HEIGHT, minHeight: 0 }}>
      {hasSessions && (
        <Group hiddenFrom="md" style={{ flexShrink: 0 }}>
          <Button
            variant="default"
            size="sm"
            leftSection={<IconList size={16} />}
            onClick={drawer.open}
          >
            {t("sessions")}
          </Button>
        </Group>
      )}

      <Group
        align="stretch"
        gap="md"
        wrap="nowrap"
        style={{ flex: 1, minHeight: 0, overflow: "hidden" }}
      >
        {hasSessions && (
          <Box visibleFrom="md" style={{ display: "flex", flexShrink: 0, minHeight: 0 }}>
            {sessionsPanel}
          </Box>
        )}

        {activeId !== null ? (
          <AgUiThread
            key={activeId}
            sessionId={activeId}
            firstMessage={firstMessage}
            onFirstMessageSent={() => setFirstMessage(null)}
            thinking={parseThinking(current?.thinking ?? null)}
            thinkingDisabled={updateThinking.isPending}
            onThinkingChange={(next) =>
              updateThinking.mutate({ path: { session_id: activeId }, body: { thinking: next } })
            }
          />
        ) : (
          <Center style={{ flex: 1 }}>
            {sessions.isPending ? (
              <Loader size="sm" />
            ) : (
              <Stack align="center" gap="md" maw={640} w="100%">
                <Text size="xl" fw={700}>
                  Amane
                </Text>
                <Text c="dimmed" size="sm">
                  {t("landingHint")}
                </Text>
                <Box w="100%">
                  <ChatComposer
                    large
                    value={input}
                    onChange={setInput}
                    onSubmit={() => {
                      const text = input.trim();
                      if (!text) return;
                      setInput("");
                      setFirstMessage(text);
                      startSession(text);
                    }}
                    loading={createSession.isPending}
                  />
                </Box>
              </Stack>
            )}
          </Center>
        )}
      </Group>

      {hasSessions && (
        <Drawer
          opened={drawerOpened}
          onClose={drawer.close}
          title={t("sessions")}
          size="xs"
          hiddenFrom="md"
        >
          {sessionsPanel}
        </Drawer>
      )}
    </Stack>
  );
}
