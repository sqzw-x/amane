/** AG-UI 页: 对话展示只认回放行, 运行时只负责运行控制 (发起 / 取消 / 续批) 与话题本.

页面渲染的是 `foldTrace` 的产物, 直播与回放因此同形: 本页发起的回合也跟随 `.../agui/events`, 与切走再回来
走同一条路径; `.../trace` 取整段历史, 回合收尾时再落定一次. 运行时的消息来自同一份 fold — 库从消息 metadata
读待批中断, 续批与运行输入也要它, 但它的直播聚合不再是展示源.

注: AG-UI 协议没有历史回放, 重建一律靠回放行.
*/

import { HttpAgent } from "@ag-ui/client";
import { AssistantRuntimeProvider, useAuiState, type AssistantRuntime } from "@assistant-ui/react";
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
import { useDisclosure } from "@mantine/hooks";
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
  useRef,
  useState,
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
import { cancelAguiTurn, getAgentTrace } from "@/client/sdk.gen";
import type { AgentSessionResponse } from "@/client/types.gen";
import {
  listSavedQueriesQueryKey,
  updateSavedQueryMutation,
} from "@/client/@tanstack/react-query.gen";
import { ChatComposer, parseThinking, type ThinkingValue } from "@/components/agent/chat-composer";
import { MarkdownContent } from "@/components/agent/markdown-content";
import { SavedQueryActions } from "@/components/agent/saved-query-actions";
import {
  downloadSavedQueryResult,
  SavedQueryManager,
} from "@/components/agent/saved-query-manager";
import { APP_SHELL_MAIN_HEIGHT } from "@/components/layout/app-shell-metrics";
import i18n from "@/i18n";
import { extractErrorMessage } from "@/lib/api-error";
import { apiFetch } from "@/lib/api-token";
import { useFold } from "@/lib/agent/fold";
import {
  foldTrace,
  type AgentMessage,
  type AgentPart,
  type AgentToolCallPart,
  type TraceRow,
} from "@/lib/agent/trace";
import { TokenUsageBar, RequestUsageBar } from "@/components/agent/token-usage-bar";
import { confirm } from "@/lib/confirm";

function apiBase(): string {
  return import.meta.env.VITE_API_URL || "";
}

/** 非 null 且非数组的对象. 工具参数与回执的形状判定需要排除数组, 故不用 `lib/utils.ts::isRecord` (含数组). */
function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function JsonBlock({ value }: { value: unknown }) {
  return (
    <Code block style={{ fontSize: 11, maxHeight: 200, overflow: "auto" }}>
      {JSON.stringify(value ?? null, null, 2)}
    </Code>
  );
}

/** 正文段: 回合进行中且它是消息的最后一段时带光标. */
function TextPart({ text, streaming }: { text: string; streaming: boolean }) {
  return <MarkdownContent text={text} streaming={streaming} />;
}

/** 折叠块: 标题行固定为一行说明, 展开内容统一左缩进; 折叠与标题位置保持见 useFold. */
function FoldBlock({
  label,
  running,
  children,
}: {
  label: string;
  running?: boolean;
  children: ReactNode;
}) {
  const { open, toggle, headerRef } = useFold();
  return (
    <Box mb="xs">
      <UnstyledButton ref={headerRef} onClick={toggle}>
        <Group gap={4} wrap="nowrap">
          {open ? <IconChevronDown size={13} /> : <IconChevronRight size={13} />}
          <Text size="xs" c="dimmed">
            {label}
          </Text>
          {running && <Loader size={12} />}
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

/** 单段思考正文; 折叠由 FoldBlock 统一负责. */
function ReasoningPart({ text }: { text: string }) {
  return (
    <Text size="xs" c="dimmed" style={{ whiteSpace: "pre-wrap" }}>
      {text}
    </Text>
  );
}

/** 单个部件: 一个部件一个渲染分支, 用量部件因此直接拿到确切载荷. */
function Part({ part, streaming }: { part: AgentPart; streaming: boolean }) {
  switch (part.type) {
    case "text":
      return <TextPart text={part.text} streaming={streaming} />;
    case "reasoning":
      return <ReasoningPart text={part.text} />;
    case "tool-call":
      return <ToolCallPart part={part} running={streaming && part.result === undefined} />;
    case "data-request-usage":
      return <RequestUsageBar usage={part.data} />;
    case "data-turn-usage":
      return <TokenUsageBar usage={part.data} />;
  }
}

/** 一段活动里工具调用超过这个数就整组折叠. */
const ACTIVITY_TOOL_LIMIT = 3;

type PartChunk = { kind: "reasoning"; indices: number[] } | { kind: "part"; index: number };

/** 把 [start, end) 切成连续思考块与单部件. */
function partChunks(parts: readonly AgentPart[], start: number, end: number): PartChunk[] {
  const chunks: PartChunk[] = [];
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

/** 一段部件: 连续思考合并成一块折叠, 其余各归各. 光标与转圈只给消息的最后一段. */
function PartRun({
  parts,
  start,
  end,
  streaming,
}: {
  parts: readonly AgentPart[];
  start: number;
  end: number;
  streaming: boolean;
}) {
  const { t } = useTranslation("agent");
  const chunks = useMemo(() => partChunks(parts, start, end), [parts, start, end]);
  const lastIndex = parts.length - 1;
  return (
    <>
      {chunks.map((chunk) =>
        chunk.kind === "reasoning" ? (
          <FoldBlock
            key={`thought-${chunk.indices[0]}`}
            label={t("thinking.label")}
            running={streaming && chunk.indices.at(-1) === lastIndex}
          >
            {chunk.indices.map((index) => (
              <Part key={index} part={parts[index]} streaming={false} />
            ))}
          </FoldBlock>
        ) : (
          <Part
            key={chunk.index}
            part={parts[chunk.index]}
            streaming={streaming && chunk.index === lastIndex}
          />
        ),
      )}
    </>
  );
}

/** 助手部件: 默认折叠思考; 整条消息工具调用够多时, 首尾活动 (含夹在中间的文本) 折叠为一块. */
function AssistantParts({ parts, streaming }: { parts: readonly AgentPart[]; streaming: boolean }) {
  const { t } = useTranslation("agent");
  const approval = useContext(ApprovalContext);
  const tools = parts.flatMap((part) => (part.type === "tool-call" ? [part.toolCallId] : []));
  const first = parts.findIndex((part) => part.type === "reasoning" || part.type === "tool-call");
  const last = parts.findLastIndex(
    (part) => part.type === "reasoning" || part.type === "tool-call",
  );
  // 未决审批在活动里时保持展开, 否则批准入口会被折叠隐藏.
  const awaiting = tools.some((id) =>
    approval?.interrupts.some((interrupt) => interrupt.toolCallId === id),
  );

  if (tools.length <= ACTIVITY_TOOL_LIMIT || first < 0 || awaiting) {
    return <PartRun parts={parts} start={0} end={parts.length} streaming={streaming} />;
  }
  return (
    <>
      <PartRun parts={parts} start={0} end={first} streaming={streaming} />
      <FoldBlock label={t("toolCallsCount", { n: tools.length })}>
        <PartRun parts={parts} start={first} end={last + 1} streaming={streaming} />
      </FoldBlock>
      <PartRun parts={parts} start={last + 1} end={parts.length} streaming={streaming} />
    </>
  );
}

/** 交付的 SQL 视图: 只有 sql_deliver 的回执进芯片, sql_explore 的探查视图不进. */
function savedQueryIdOf(toolName: string, result: unknown): number | null {
  if (toolName !== "sql_deliver" || !isPlainObject(result)) return null;
  const id = result.saved_query_id;
  return typeof id === "number" ? id : null;
}

/** 参数视图: 回放行只留 JSON 文本, 解析失败就原样展示. */
function argsBodyOf(argsText: string): unknown {
  const text = argsText.trim();
  if (!text) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function ToolCallPart({ part, running }: { part: AgentToolCallPart; running: boolean }) {
  const { t } = useTranslation("agent");
  const { open, toggle, headerRef } = useFold();
  const queryClient = useQueryClient();
  const { toolCallId, toolName, result } = part;
  const savedQueryId = savedQueryIdOf(toolName, result);
  const argsBody = argsBodyOf(part.argsText);
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
          {running ? (
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
                {t("toolArgs")}
              </Text>
              <JsonBlock value={argsBody} />
            </>
          )}
          {result !== undefined && (
            <>
              <Text size="xs" c="dimmed" mt={4}>
                {t("toolResult")}
              </Text>
              <JsonBlock value={result} />
            </>
          )}
        </Stack>
      )}
    </Box>
  );
}

function Message({ message, streaming }: { message: AgentMessage; streaming: boolean }) {
  const { t } = useTranslation("agent");
  if (message.role === "user") return <UserMessage text={message.content.map(textOf).join("")} />;
  return (
    <Box mb="sm">
      <Text size="xs" c="dimmed" mb={4}>
        {t("assistant")}
      </Text>
      <AssistantParts parts={message.content} streaming={streaming} />
    </Box>
  );
}

function textOf(part: AgentPart): string {
  return part.type === "text" ? part.text : "";
}

/** 用户消息: 标签在左, 正文收进气泡, 与助手的纯文本回复区分; 按原文展示, 不走 markdown. */
function UserMessage({ text }: { text: string }) {
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
        <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
          {text}
        </Text>
      </Box>
    </Stack>
  );
}

/** 跟随端点的重建节拍: 行到得比渲染密, 逐行重建会把整段对话反复重排. */
const FOLLOW_RENDER_MS = 300;

/** 跟随端点的重试间隔: 订阅可能赶在服务端登记回合之前打开, 空转即返时要再接上. */
const FOLLOW_RETRY_MS = 500;

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

/** 跟随 `afterSeq` 之后的回放行; 服务端在回合结束且追平后关闭, 断连由 signal 终止. */
async function followTraceRows(
  sessionId: number,
  afterSeq: number,
  signal: AbortSignal,
  onRows: (rows: TraceRow[]) => void,
): Promise<void> {
  const response = await apiFetch(
    `${apiBase()}/api/agent/sessions/${sessionId}/agui/events?after_seq=${afterSeq}`,
    { signal },
  );
  if (!response.ok || response.body === null) return;
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) return;
      buffer += value;
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";
      const rows: TraceRow[] = [];
      for (const frame of frames) {
        const line = frame.split("\n").find((item) => item.startsWith("data:"));
        if (line === undefined) continue;
        // 单处断言: 帧由本服务端按回放行契约写出, 逐行做运行时校验得不偿失
        rows.push(JSON.parse(line.slice(5)) as TraceRow);
      }
      if (rows.length > 0) onRows(rows);
    }
  } catch (error) {
    if (!signal.aborted) throw error;
  } finally {
    void reader.cancel().catch(() => undefined);
  }
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
  const { t } = useTranslation("agent");
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
            // 拒绝理由随 payload 进 ToolDenied 再到 messages.json, 是模型的输入而不是页面徽章文案
            payload:
              next[item.id] === "approve"
                ? { approved: true }
                : { approved: false, reason: t("approvalRejectReason") },
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

/** 审批队列须在 runtime 内读取, 故单独一层 Provider. */
function ApprovalProvider({ children }: { children: ReactNode }) {
  const queue = useApprovalQueue();
  return <ApprovalContext.Provider value={queue}>{children}</ApprovalContext.Provider>;
}

/** 已发出的用户输入, 连同送出时已有的消息条数. */
type Pending = { readonly text: string; readonly afterMessages: number };

/** 该输入是否已进消息列表: 只在送出位置之后找, 重复内容不会误判成已到. */
function landed(messages: readonly AgentMessage[], pending: Pending): boolean {
  return messages
    .slice(pending.afterMessages)
    .some(
      (message) =>
        message.role === "user" &&
        message.content.some((part) => part.type === "text" && part.text === pending.text),
    );
}

/** 线程运行态的两个边沿: 开始运行要接上回放行跟随, 运行结束要落定一次.

读线程状态须在 provider 之内, 故单列一个组件而不放在持有 provider 的 `AgUiThread` 里.
*/
function RunEdges({ onStart, onEnd }: { onStart: () => void; onEnd: () => void }) {
  const running = useAuiState((state) => state.thread.isRunning);
  const wasRunning = useRef(false);
  useEffect(() => {
    const started = !wasRunning.current && running;
    const ended = wasRunning.current && !running;
    wasRunning.current = running;
    if (started) onStart();
    if (ended) void onEnd();
  }, [running, onStart, onEnd]);
  return null;
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
  const [loadingHistory, setLoadingHistory] = useState(true);
  const [serverTurn, setServerTurn] = useState(false);
  /** 展示源: 回放行折叠出的消息, 直播与回放共用. */
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [draft, setDraft] = useState("");
  /** 最近一次发出的用户输入: 回放行里还没出现时先占位, 失败则退回输入框. */
  const [pending, setPending] = useState<Pending | null>(null);
  /** 回放行攒到哪算到哪; 展示与运行时话题本都以它为准. */
  const rowsRef = useRef<TraceRow[]>([]);
  /** 进行中的跟随订阅; 同一时刻只允许一个. */
  const followRef = useRef<AbortController | null>(null);
  const pendingRef = useRef<Pending | null>(null);
  /** 已折叠出的消息, 供回调读取而不进依赖. */
  const messagesRef = useRef<AgentMessage[]>([]);
  /** 挂载引导状态: 回调身份会随运行时状态变化, 故完整跑过一次就不再跑; 被取消的那些
   *  (StrictMode 模拟卸载) 退回 idle, 由下一次 setup 重跑. */
  const bootRef = useRef<"idle" | "running" | "done">("idle");
  const scrollRef = useRef<HTMLDivElement>(null);
  /** 视图是否贴底: 用户上滚查看历史时不再跟随新内容. */
  const stickRef = useRef(true);

  const agent = useMemo(
    () =>
      new HttpAgent({
        url: `${apiBase()}/api/agent/sessions/${sessionId}/agui`,
        threadId: String(sessionId),
        fetch: apiFetch,
      }),
    [sessionId],
  );
  const runtime = useAgUiRuntime({
    agent,
    onError: (error) => {
      notifications.show({ color: "red", message: error.message });
      // 回合没起来 (输入还在等回执) 时把内容还给输入框, 不让它悄悄消失
      const failed = pendingRef.current;
      if (failed !== null) {
        pendingRef.current = null;
        setPending(null);
        setDraft(failed.text);
      }
    },
  });

  /** 用回放行重建展示; `syncRuntime` 只在回合不在运行时开: 它的直播聚合不再是展示源, 话题本只需在落定处对齐. */
  const applyRows = useCallback(
    (rows: readonly TraceRow[], syncRuntime: boolean) => {
      const folded = foldTrace(rows);
      messagesRef.current = folded;
      setMessages(folded);
      if (syncRuntime) runtime.thread.reset(folded);
    },
    [runtime],
  );

  /** 取整段回放行: 重建展示, 并把运行时话题本与待批态对齐过去; 失败抛错, 由调用处决定出路. */
  const snapshot = useCallback(async () => {
    const { data, error } = await getAgentTrace({ path: { session_id: sessionId } });
    // hey-api 客户端把 HTTP / 网络失败都收进 error 字段而不抛错, 这里统一转成异常
    if (!data) throw error;
    rowsRef.current = data.events;
    applyRows(data.events, true);
  }, [applyRows, sessionId]);

  /** 回合收尾后重取快照并刷新会话列表 (标题与审批态可能在回合里变过). */
  const settle = useCallback(async () => {
    // 快照失败不打断落定: 展示已由跟随收尾, 条幅照常刷新
    await snapshot().catch(() => undefined);
    void queryClient.invalidateQueries({ queryKey: listAgentSessionsQueryKey() });
  }, [queryClient, snapshot]);

  /** 跟随回放行: 从已有位置接上, 新行并进; 服务端在回合结束且追平后关闭. */
  const startFollow = useCallback(() => {
    if (followRef.current !== null) return;
    const controller = new AbortController();
    followRef.current = controller;
    setServerTurn(true);
    void (async () => {
      let rendered = -1;
      const timer = window.setInterval(() => {
        if (rowsRef.current.length === rendered) return;
        rendered = rowsRef.current.length;
        setLoadingHistory(false);
        applyRows(rowsRef.current, false);
      }, FOLLOW_RENDER_MS);
      try {
        while (!controller.signal.aborted) {
          const from = rowsRef.current.at(-1)?.seq ?? 0;
          await followTraceRows(sessionId, from, controller.signal, (rows) => {
            rowsRef.current = [...rowsRef.current, ...rows];
          });
          if (!runtime.thread.getState().isRunning) break;
          // 回合刚发出时订阅可能先到: 服务端那时尚未登记回合, 空转即返; 稍后再接上, 副本由 cursor 去重
          await wait(FOLLOW_RETRY_MS);
        }
      } catch {
        // 订阅建不起来: 退回一次性快照; 再失败无碍, 收尾照常清掉加载态
        if (!controller.signal.aborted) await snapshot().catch(() => undefined);
      } finally {
        window.clearInterval(timer);
        followRef.current = null;
        if (!controller.signal.aborted) {
          setLoadingHistory(false);
          setServerTurn(false);
          // 流关闭即回合已结束且行已追平: 此刻的行是完整的, 据此落定
          applyRows(rowsRef.current, true);
          void queryClient.invalidateQueries({ queryKey: listAgentSessionsQueryKey() });
        }
      }
    })();
  }, [applyRows, queryClient, runtime, sessionId, snapshot]);

  // 挂载: 先取整段历史, 再接上跟随 (挂载时回合可能正在后台运行, 跟随会一路跟到它结束).
  // 取历史失败不留在加载态: 提示后放行, 页面照常可用.
  useEffect(() => {
    if (bootRef.current !== "idle") return;
    bootRef.current = "running";
    let cancelled = false;
    void (async () => {
      let failure: unknown = null;
      try {
        await snapshot();
      } catch (error) {
        failure = error;
      }
      if (cancelled) return;
      bootRef.current = "done";
      setLoadingHistory(false);
      if (failure !== null) {
        notifications.show({
          color: "red",
          // 经单例取文案: t 的身份会随语言变化, 不属于引导的依赖
          message: extractErrorMessage(failure, i18n.t("historyLoadFailed", { ns: "agent" })),
        });
        return;
      }
      startFollow();
    })();
    return () => {
      cancelled = true;
      // 没跑完就被卸载 (StrictMode 模拟卸载): 退回 idle, 由下一次 setup 重跑
      if (bootRef.current === "running") bootRef.current = "idle";
      followRef.current?.abort();
    };
  }, [snapshot, startFollow]);

  // 落地页首条消息: 会话建好后才能发, 故等历史重建完成再补发.
  useEffect(() => {
    if (loadingHistory || firstMessage === null) return;
    onFirstMessageSent();
    pendingRef.current = { text: firstMessage, afterMessages: messagesRef.current.length };
    runtime.thread.append(firstMessage);
  }, [loadingHistory, firstMessage, onFirstMessageSent, runtime]);

  /* oxlint-disable react/exhaustive-effect-dependencies --
   * 这两项是"内容变了"的触发器, 不参与计算: 滚动位置只由 DOM 与贴底状态决定.
   */
  useEffect(() => {
    const element = scrollRef.current;
    if (element === null || !stickRef.current) return;
    element.scrollTop = element.scrollHeight;
  }, [messages, pending]);

  // 占位气泡只活到该输入进消息列表为止; 进过就不再渲染, 无须再清状态.
  const pendingText = pending !== null && !landed(messages, pending) ? pending.text : null;

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <RunEdges onStart={startFollow} onEnd={settle} />
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
            <Box
              ref={scrollRef}
              onScroll={() => {
                const element = scrollRef.current;
                if (element === null) return;
                stickRef.current =
                  element.scrollHeight - element.scrollTop - element.clientHeight < 80;
              }}
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
              ) : messages.length === 0 && pendingText === null ? (
                <Text c="dimmed" size="sm">
                  {t("continueHint")}
                </Text>
              ) : null}
              {messages.map((message) => (
                <Message key={message.id} message={message} streaming={serverTurn} />
              ))}
              {pendingText !== null && <UserMessage text={pendingText} />}
            </Box>
          </Paper>
          {serverTurn && !loadingHistory && (
            <Group gap="xs" px="sm">
              <Loader size="xs" />
              <Text size="xs" c="dimmed">
                {t("turnRunning")}
              </Text>
            </Group>
          )}
          <Composer
            runtime={runtime}
            sessionId={sessionId}
            value={draft}
            onChange={setDraft}
            onSubmit={(text) => {
              const sent: Pending = { text, afterMessages: messages.length };
              pendingRef.current = sent;
              setPending(sent);
              runtime.thread.append(text);
            }}
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
  value,
  onChange,
  onSubmit,
  thinking,
  onThinkingChange,
  thinkingDisabled,
}: {
  runtime: AssistantRuntime;
  sessionId: number;
  /** 输入内容由会话层持有: 回合没起来时要把内容还回输入框. */
  value: string;
  onChange: (value: string) => void;
  onSubmit: (text: string) => void;
  thinking: ThinkingValue | null;
  onThinkingChange: (value: ThinkingValue | null) => void;
  thinkingDisabled: boolean;
}) {
  const running = useAuiState((state) => state.thread.isRunning);

  return (
    <Box style={{ flexShrink: 0 }}>
      <ChatComposer
        value={value}
        onChange={onChange}
        onSubmit={() => {
          const text = value.trim();
          if (!text || running) return;
          onChange("");
          onSubmit(text);
        }}
        onStop={() => {
          // 回合在服务端后台运行, 断开连接停不住它: 先让服务端终止, 再终止本地流.
          void cancelAguiTurn({ path: { session_id: sessionId } }).then(({ error }) => {
            if (error) notifications.show({ color: "red", message: String(error) });
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
