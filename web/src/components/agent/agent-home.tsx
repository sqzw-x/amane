import {
  ActionIcon,
  Box,
  Button,
  Center,
  Group,
  Loader,
  Menu,
  Paper,
  ScrollArea,
  Stack,
  Text,
  TextInput,
  Title,
  UnstyledButton,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconDots, IconPencil, IconPlus, IconTrash } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useEffectEvent, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useLatestRef } from "@/hooks/use-latest-ref";
import {
  createAgentSessionMutation,
  deleteAgentSessionMutation,
  getAgentTraceOptions,
  listAgentSessionsOptions,
  listAgentSessionsQueryKey,
  listSavedQueriesQueryKey,
  updateAgentSessionMutation,
  updateSavedQueryMutation,
} from "@/client/@tanstack/react-query.gen";
import { cancelAgentTurn, getSavedQueryResult } from "@/client/sdk.gen";
import { ChatComposer, parseThinking, type ThinkingValue } from "@/components/agent/chat-composer";
import { HintedActionIcon } from "@/components/common/hinted-action-icon";
import {
  type AssistantBlock,
  type ChatMessage,
  MessageBubble,
  nextBlockId,
  type TurnTokenUsage,
} from "@/components/agent/message-bubble";
import type {
  ApprovalAction,
  ToolApproval,
  ToolCallView,
} from "@/components/agent/tool-call-badge";
import { SavedQueryManager } from "@/components/agent/saved-query-manager";
import {
  type AgentSseEvent,
  type AgentTokenUsage,
  streamAgentApprove,
  streamAgentEvents,
  streamAgentMessage,
  streamAgentReject,
} from "@/lib/agent/sse";
import {
  findPendingApprovals,
  markMessagesApprovalStatus,
  messagesFromTrace,
  parseNeedsApproval,
} from "@/lib/agent/trace";
import { confirm } from "@/lib/confirm";
import { extractErrorMessage } from "@/lib/api-error";

async function downloadSavedQueryResult(queryId: number) {
  const { data, error } = await getSavedQueryResult({
    path: { query_id: queryId },
    query: { offset: 0, limit: 5000 },
  });
  if (error || !data) {
    notifications.show({ color: "red", message: String(error) });
    return;
  }
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `saved-query-${queryId}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

function normalizeUsage(usage: AgentTokenUsage | undefined): TurnTokenUsage | undefined {
  if (!usage) return undefined;
  return {
    input: usage.input,
    cache_read: usage.cache_read,
    cache_write: usage.cache_write,
    output: usage.output,
    requests: usage.requests ?? 0,
  };
}

function appendTextBlock(blocks: AssistantBlock[], piece: string): AssistantBlock[] {
  if (!piece) return blocks;
  const next = [...blocks];
  const last = next[next.length - 1];
  if (last?.kind === "text") {
    next[next.length - 1] = { ...last, text: last.text + piece };
    return next;
  }
  next.push({ kind: "text", id: nextBlockId("t"), text: piece });
  return next;
}

function attachApproval(blocks: AssistantBlock[], approval: ToolApproval): AssistantBlock[] {
  // approval_id 即 tool_call_id (DeferredToolRequests)
  const next = [...blocks];
  const byCallId = next.findIndex(
    (b) => b.kind === "tool" && b.tool.toolCallId === approval.approval_id,
  );
  if (byCallId >= 0 && next[byCallId]?.kind === "tool") {
    next[byCallId] = {
      kind: "tool",
      tool: { ...next[byCallId].tool, approval },
    };
    return next;
  }
  const byApprovalId = next.findIndex(
    (b) => b.kind === "tool" && b.tool.approval?.approval_id === approval.approval_id,
  );
  if (byApprovalId >= 0 && next[byApprovalId]?.kind === "tool") {
    next[byApprovalId] = {
      kind: "tool",
      tool: { ...next[byApprovalId].tool, approval },
    };
    return next;
  }
  const byName = next.findLastIndex(
    (b) =>
      b.kind === "tool" &&
      b.tool.name === approval.tool &&
      (b.tool.approval === undefined || b.tool.approval.status === "pending"),
  );
  if (byName >= 0 && next[byName]?.kind === "tool") {
    next[byName] = {
      kind: "tool",
      tool: { ...next[byName].tool, approval },
    };
  }
  return next;
}

function applySseToAssistant(prev: ChatMessage[], event: AgentSseEvent): ChatMessage[] {
  if (event.type === "user_message" || event.type === "assistant_message") {
    return prev;
  }

  const messages = [...prev];
  let last = messages[messages.length - 1];
  if (!last || last.role !== "assistant") {
    messages.push({ role: "assistant", blocks: [], streaming: true });
    last = messages[messages.length - 1];
  }
  if (last.role !== "assistant") {
    return prev;
  }
  let blocks = [...last.blocks];
  let savedQueryIds = [...(last.savedQueryIds ?? [])];

  if (event.type === "text_delta") {
    blocks = appendTextBlock(blocks, event.text);
    messages[messages.length - 1] = { ...last, blocks, streaming: true };
    return messages;
  }

  if (event.type === "tool_call") {
    blocks.push({
      kind: "tool",
      tool: {
        toolCallId: event.tool_call_id,
        name: event.name,
        args: event.args,
      },
    });
    messages[messages.length - 1] = { ...last, blocks, streaming: true };
    return messages;
  }

  if (event.type === "tool_result") {
    // 新路径批准只经 needs_approval SSE; tool_result 内嵌 needs_approval 仅兼容旧 trace
    const parsed = parseNeedsApproval(event.result);
    const idx = blocks.findIndex(
      (b) => b.kind === "tool" && b.tool.toolCallId === event.tool_call_id,
    );
    const patch: ToolCallView = {
      toolCallId: event.tool_call_id,
      name: event.name,
      result: event.result,
      approval: parsed ? { ...parsed, status: "pending" } : undefined,
    };
    if (idx >= 0 && blocks[idx]?.kind === "tool") {
      blocks[idx] = {
        kind: "tool",
        tool: {
          ...blocks[idx].tool,
          ...patch,
          approval: patch.approval ?? blocks[idx].tool.approval,
        },
      };
    } else {
      blocks.push({ kind: "tool", tool: patch });
    }
    if (
      event.result !== null &&
      typeof event.result === "object" &&
      "saved_query_id" in event.result &&
      typeof event.result.saved_query_id === "number" &&
      !savedQueryIds.includes(event.result.saved_query_id)
    ) {
      savedQueryIds = [...savedQueryIds, event.result.saved_query_id];
    }
    messages[messages.length - 1] = { ...last, blocks, savedQueryIds, streaming: true };
    return messages;
  }

  if (event.type === "needs_approval") {
    blocks = attachApproval(blocks, {
      approval_id: event.approval_id,
      sql: event.sql,
      tool: event.tool,
      status: "pending",
    });
    messages[messages.length - 1] = { ...last, blocks, streaming: false };
    return messages;
  }

  if (event.type === "done") {
    messages[messages.length - 1] = {
      ...last,
      blocks,
      streaming: false,
      savedQueryIds: event.saved_query_ids.length > 0 ? event.saved_query_ids : savedQueryIds,
      usage: normalizeUsage(event.usage),
    };
    return messages;
  }

  if (event.type === "error") {
    blocks = appendTextBlock(blocks, `\n\n⚠ ${event.message}`);
    messages[messages.length - 1] = { ...last, blocks, streaming: false };
    return messages;
  }

  if (event.type === "cancelled") {
    messages[messages.length - 1] = { ...last, blocks, streaming: false };
    return messages;
  }

  return prev;
}

export function AgentHome() {
  const { t } = useTranslation(["agent", "common"]);
  const qc = useQueryClient();
  const sessionsQuery = useQuery(listAgentSessionsOptions());
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [historyEpoch, setHistoryEpoch] = useState(0);
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [renameValue, setRenameValue] = useState("");
  /** 会话思考覆盖; null = 继承全局默认. */
  const [sessionThinking, setSessionThinking] = useState<ThinkingValue | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const skipTraceLoad = useRef(false);
  const lastSeqRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  /** 历史恢复后需滚到底; 在 messages 提交后再清除. */
  const pendingScrollBottom = useRef(false);
  const messagesRef = useLatestRef(messages);
  /** 单次批准暂存: approval_id → tool; 同工具待批清空后再一次 approve/stream. */
  const stagedApprovalsRef = useRef(new Map<string, string>());

  if (sessionId == null && !sessionsQuery.isPending) {
    const latest = sessionsQuery.data?.items[0];
    if (latest != null) {
      setSessionId(latest.id);
      setSessionThinking(parseThinking(latest.thinking));
    }
  }

  const traceQuery = useQuery({
    ...getAgentTraceOptions({ path: { session_id: sessionId ?? 0 } }),
    enabled: sessionId != null && !streaming,
  });

  const createSession = useMutation({
    ...createAgentSessionMutation(),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: listAgentSessionsQueryKey() });
    },
  });

  const renameSession = useMutation({
    ...updateAgentSessionMutation(),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: listAgentSessionsQueryKey() });
      setRenamingId(null);
    },
  });

  const updateThinking = useMutation({
    ...updateAgentSessionMutation(),
    onSuccess: async (data) => {
      setSessionThinking(parseThinking(data.thinking));
      await qc.invalidateQueries({ queryKey: listAgentSessionsQueryKey() });
    },
    onError: (err) => {
      notifications.show({
        color: "red",
        message: extractErrorMessage(err, t("disabled")),
      });
    },
  });

  const deleteSession = useMutation({
    ...deleteAgentSessionMutation(),
    onSuccess: async (_data, vars) => {
      await qc.invalidateQueries({ queryKey: listAgentSessionsQueryKey() });
      if (sessionId === vars.path.session_id) {
        abortRef.current?.abort();
        stagedApprovalsRef.current.clear();
        setSessionId(null);
        setMessages([]);
        setSessionThinking(null);
        setStreaming(false);
      }
    },
  });

  const persistQuery = useMutation({
    ...updateSavedQueryMutation(),
    onSuccess: async () => {
      notifications.show({ color: "green", message: t("persist") });
      await qc.invalidateQueries({
        queryKey: listSavedQueriesQueryKey({ query: { persisted_only: true } }),
      });
      if (sessionId != null) {
        await qc.invalidateQueries({
          queryKey: listSavedQueriesQueryKey({ query: { session_id: sessionId } }),
        });
      }
    },
  });

  function scrollToBottom() {
    requestAnimationFrame(() => {
      const el = scrollRef.current;
      if (el) el.scrollTop = el.scrollHeight;
    });
  }

  async function consumeEvents(iter: AsyncGenerator<AgentSseEvent>): Promise<boolean> {
    let ok = true;
    for await (const event of iter) {
      if (typeof event.seq === "number") {
        lastSeqRef.current = Math.max(lastSeqRef.current, event.seq);
      }
      setMessages((prev) => applySseToAssistant(prev, event));
      scrollToBottom();
      if (event.type === "error") {
        ok = false;
        notifications.show({ color: "red", message: event.message });
      }
    }
    return ok;
  }

  const resumeTail = useEffectEvent(async (sid: number, after: number) => {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setStreaming(true);
    try {
      await consumeEvents(streamAgentEvents(sid, after, ac.signal));
      skipTraceLoad.current = true;
      await qc.invalidateQueries({
        queryKey: getAgentTraceOptions({ path: { session_id: sid } }).queryKey,
      });
    } catch (err) {
      if (ac.signal.aborted) return;
      notifications.show({
        color: "red",
        message: extractErrorMessage(err, t("disabled")),
      });
    } finally {
      if (abortRef.current === ac) {
        setStreaming(false);
        abortRef.current = null;
      }
    }
  });

  useEffect(() => {
    if (sessionId == null || streaming) return;
    // 再点当前会话时 sessionId 不变, 靠 historyEpoch 让本 effect 重新绑定.
    if (historyEpoch < 0) return;
    if (skipTraceLoad.current) {
      skipTraceLoad.current = false;
      return;
    }
    if (!traceQuery.data) return;
    const { messages: restored, lastSeq } = messagesFromTrace(traceQuery.data.events);
    lastSeqRef.current = Math.max(lastSeq, traceQuery.data.last_seq ?? 0);
    // External store → local transcript. Resume / scroll flags are side effects
    // that cannot run during render.
    // oxlint-disable-next-line react/set-state-in-effect
    setMessages(restored);
    setSessionThinking(parseThinking(traceQuery.data.meta?.thinking));
    if (traceQuery.data.turn_running) {
      void resumeTail(sessionId, lastSeqRef.current);
    } else if (restored.length > 0) {
      pendingScrollBottom.current = true;
    }
  }, [sessionId, historyEpoch, streaming, traceQuery.data]);

  useEffect(() => {
    if (!pendingScrollBottom.current) return;
    if (sessionId == null || streaming) return;
    // 切换会话会先清空 messages; 同一次 commit 里 trace 已置旗但 messages 仍为空 - 保留旗标等下一拍.
    if (messages.length === 0) return;
    pendingScrollBottom.current = false;
    requestAnimationFrame(() => {
      const el = scrollRef.current;
      if (el) el.scrollTop = el.scrollHeight;
    });
  }, [sessionId, messages, streaming]);

  function openSession(id: number) {
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
    setRenamingId(null);
    stagedApprovalsRef.current.clear();
    setSessionId(id);
    setMessages([]);
    const found = (sessionsQuery.data?.items ?? []).find((s) => s.id === id);
    setSessionThinking(parseThinking(found?.thinking));
    lastSeqRef.current = 0;
    setHistoryEpoch((n) => n + 1);
  }

  async function handleNewSession() {
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
    stagedApprovalsRef.current.clear();
    setInput("");
    try {
      const session = await createSession.mutateAsync({
        body: { title: t("newSession") },
      });
      skipTraceLoad.current = true;
      lastSeqRef.current = 0;
      setMessages([]);
      setSessionThinking(parseThinking(session.thinking));
      setSessionId(session.id);
    } catch (err) {
      notifications.show({
        color: "red",
        message: extractErrorMessage(err, t("disabled")),
      });
    }
  }

  async function runStream(
    sid: number,
    content: string,
    mode: "message" | "approve" | "reject",
    approvalIds?: readonly string[],
  ): Promise<boolean> {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setStreaming(true);
    if (mode === "message") {
      stagedApprovalsRef.current.clear();
      setMessages((prev) => [
        ...prev,
        { role: "user", text: content },
        { role: "assistant", blocks: [], streaming: true },
      ]);
    } else {
      setMessages((prev) => [...prev, { role: "assistant", blocks: [], streaming: true }]);
    }
    scrollToBottom();

    try {
      let iter: AsyncGenerator<AgentSseEvent>;
      if (mode === "approve" && approvalIds && approvalIds.length > 0) {
        iter = streamAgentApprove(sid, approvalIds, 60_000, ac.signal);
      } else if (mode === "reject" && approvalIds?.[0]) {
        iter = streamAgentReject(sid, approvalIds[0], ac.signal);
      } else {
        iter = streamAgentMessage(sid, content, ac.signal);
      }
      const streamOk = await consumeEvents(iter);
      skipTraceLoad.current = true;
      await qc.invalidateQueries({
        queryKey: getAgentTraceOptions({ path: { session_id: sid } }).queryKey,
      });
      return streamOk;
    } catch (err) {
      if (ac.signal.aborted) return false;
      notifications.show({
        color: "red",
        message: extractErrorMessage(err, t("disabled")),
      });
      setMessages((prev) => {
        const copy = [...prev];
        const last = copy[copy.length - 1];
        if (last?.role === "assistant") {
          copy[copy.length - 1] = { ...last, streaming: false };
        }
        return copy;
      });
      return false;
    } finally {
      if (abortRef.current === ac) {
        setStreaming(false);
        abortRef.current = null;
      }
    }
  }

  function markApprovalsLocally(ids: readonly string[], status: ToolApproval["status"]) {
    setMessages((prev) => {
      let next = prev;
      for (const id of ids) {
        next = markMessagesApprovalStatus(next, id, status);
      }
      messagesRef.current = next;
      return next;
    });
  }

  function takeStagedIds(tool: string): string[] {
    const ids: string[] = [];
    for (const [id, stagedTool] of stagedApprovalsRef.current) {
      if (stagedTool === tool) {
        ids.push(id);
        stagedApprovalsRef.current.delete(id);
      }
    }
    return ids;
  }

  /** 同工具暂存已齐 (无剩余 pending) 时一次性回灌. */
  async function flushStagedApprovals(sid: number, tool: string) {
    const ids = takeStagedIds(tool);
    if (ids.length === 0) return;
    const ok = await runStream(sid, "", "approve", ids);
    if (!ok) {
      markApprovalsLocally(ids, "pending");
      for (const id of ids) {
        stagedApprovalsRef.current.set(id, tool);
      }
    }
  }

  async function flushAllStaged(sid: number) {
    const tools = [...new Set(stagedApprovalsRef.current.values())];
    for (const tool of tools) {
      await flushStagedApprovals(sid, tool);
    }
  }

  async function handleApprovalAction(approval: ToolApproval, action: ApprovalAction) {
    if (sessionId == null || streaming || approval.status !== "pending") return;

    if (action === "reject") {
      stagedApprovalsRef.current.delete(approval.approval_id);
      markApprovalsLocally([approval.approval_id], "rejected");
      await runStream(sessionId, "", "reject", [approval.approval_id]);
      // 拒绝后若同工具已无 pending, 把此前暂存一并回灌
      if (findPendingApprovals(messagesRef.current, approval.tool).length === 0) {
        await flushStagedApprovals(sessionId, approval.tool);
      }
      return;
    }

    if (action === "batch") {
      const pendingIds = findPendingApprovals(messagesRef.current, approval.tool).map(
        (a) => a.approval_id,
      );
      const stagedIds = takeStagedIds(approval.tool);
      const ids = [...new Set([...stagedIds, ...pendingIds])];
      if (ids.length === 0) return;
      markApprovalsLocally(ids, "approved");
      const ok = await runStream(sessionId, "", "approve", ids);
      if (!ok) {
        markApprovalsLocally(ids, "pending");
      }
      return;
    }

    // 单次批准: 暂存; 同工具待批清空后再请求
    stagedApprovalsRef.current.set(approval.approval_id, approval.tool);
    markApprovalsLocally([approval.approval_id], "approved");
    if (findPendingApprovals(messagesRef.current, approval.tool).length === 0) {
      await flushStagedApprovals(sessionId, approval.tool);
    }
  }

  async function handleSend(raw?: string) {
    const text = (raw ?? input).trim();
    if (!text || streaming) return;
    setInput("");

    let sid = sessionId;
    if (sid == null) {
      try {
        const session = await createSession.mutateAsync({
          body: { title: text.slice(0, 40) || t("newSession") },
        });
        sid = session.id;
        skipTraceLoad.current = true;
        setSessionThinking(parseThinking(session.thinking));
        setSessionId(sid);
        setMessages([]);
      } catch (err) {
        notifications.show({
          color: "red",
          message: extractErrorMessage(err, t("disabled")),
        });
        return;
      }
    }
    // 发新消息前先冲掉暂存批准, 避免只修改了 UI 却未执行
    await flushAllStaged(sid);
    await runStream(sid, text, "message");
  }

  async function handleStop() {
    if (sessionId == null || !streaming) return;
    const { error } = await cancelAgentTurn({ path: { session_id: sessionId } });
    if (error) {
      notifications.show({ color: "red", message: String(error) });
      return;
    }
    setMessages((prev) => {
      const copy = [...prev];
      const last = copy[copy.length - 1];
      if (last?.role === "assistant" && last.streaming) {
        copy[copy.length - 1] = { ...last, streaming: false };
      }
      return copy;
    });
    setStreaming(false);
    abortRef.current?.abort();
    abortRef.current = null;
  }

  function startRename(id: number, title: string) {
    setRenamingId(id);
    setRenameValue(title);
  }

  function commitRename() {
    if (renamingId == null) return;
    const title = renameValue.trim();
    if (!title) {
      setRenamingId(null);
      return;
    }
    renameSession.mutate(
      { path: { session_id: renamingId }, body: { title } },
      {
        onError: (err) => {
          notifications.show({
            color: "red",
            message: err instanceof Error ? err.message : String(err),
          });
        },
      },
    );
  }

  async function handleDelete(id: number) {
    const ok = await confirm({
      title: t("deleteSession"),
      message: t("confirmDeleteSession"),
      confirmLabel: t("common:actions.delete"),
    });
    if (!ok) return;
    deleteSession.mutate({ path: { session_id: id } });
  }

  const sessions = sessionsQuery.data?.items ?? [];
  const sessionsReady = !sessionsQuery.isPending;
  const hasSessionHistory = sessions.length > 0;
  // 开幕落地态仅在确认没有任何历史会话时展示; 有历史或已选中会话时始终采用侧栏对话布局.
  const showLanding = sessionsReady && !hasSessionHistory && sessionId == null;
  const loadingHistory =
    sessionId != null && !streaming && traceQuery.isFetching && messages.length === 0;

  if (!sessionsReady && sessionId == null) {
    return (
      <Center style={{ minHeight: "calc(100vh - 140px)" }}>
        <Loader />
      </Center>
    );
  }

  if (showLanding) {
    return (
      <Center style={{ minHeight: "calc(100vh - 140px)" }}>
        <Stack gap="xl" maw={640} w="100%" px="md" align="stretch">
          <Stack gap={6} align="center">
            <Title order={1} style={{ letterSpacing: "-0.03em" }}>
              Amane
            </Title>
            <Text c="dimmed" size="sm" ta="center">
              {t("landingHint")}
            </Text>
          </Stack>
          <ChatComposer
            value={input}
            onChange={setInput}
            onSubmit={() => void handleSend()}
            onStop={() => void handleStop()}
            loading={streaming}
            disabled={createSession.isPending}
            large
          />
        </Stack>
      </Center>
    );
  }

  return (
    <Group
      align="stretch"
      gap="md"
      wrap="nowrap"
      style={{
        height: "calc(100vh - 120px)",
        minHeight: 0,
        overflow: "hidden",
      }}
    >
      <Paper
        withBorder
        radius="md"
        p="sm"
        w={260}
        style={{ flexShrink: 0, display: "flex", flexDirection: "column", minHeight: 0 }}
      >
        <Group justify="space-between" mb="sm" style={{ flexShrink: 0 }}>
          <Text fw={600} size="sm">
            {t("sessions")}
          </Text>
          <Group gap={4}>
            <SavedQueryManager sessionId={sessionId} />
            <HintedActionIcon
              variant="light"
              loading={createSession.isPending}
              label={t("newSession")}
              onClick={() => void handleNewSession()}
            >
              <IconPlus size={16} />
            </HintedActionIcon>
          </Group>
        </Group>
        <ScrollArea style={{ flex: 1, minHeight: 0 }} offsetScrollbars>
          <Stack gap={6}>
            {sessions.map((s) => (
              <Box
                key={s.id}
                p="xs"
                style={{
                  borderRadius: "var(--mantine-radius-md)",
                  border:
                    sessionId === s.id
                      ? "1px solid var(--mantine-color-default-border)"
                      : "1px solid transparent",
                  background: sessionId === s.id ? "var(--mantine-color-default-hover)" : undefined,
                }}
              >
                {renamingId === s.id ? (
                  <Stack gap={6}>
                    <TextInput
                      size="sm"
                      value={renameValue}
                      onChange={(e) => setRenameValue(e.currentTarget.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          commitRename();
                        }
                        if (e.key === "Escape") setRenamingId(null);
                      }}
                      autoFocus
                      aria-label={t("renamePrompt")}
                    />
                    <Group gap={6} justify="flex-end">
                      <Button
                        size="compact-xs"
                        variant="default"
                        onClick={() => setRenamingId(null)}
                      >
                        {t("common:actions.cancel")}
                      </Button>
                      <Button
                        size="compact-xs"
                        loading={renameSession.isPending}
                        onClick={() => commitRename()}
                      >
                        {t("common:actions.save")}
                      </Button>
                    </Group>
                  </Stack>
                ) : (
                  <Group gap={4} wrap="nowrap" align="flex-start">
                    <UnstyledButton
                      onClick={() => openSession(s.id)}
                      style={{ flex: 1, minWidth: 0, textAlign: "left" }}
                    >
                      <Text size="sm" fw={sessionId === s.id ? 600 : 400} lineClamp={2}>
                        {s.title}
                      </Text>
                    </UnstyledButton>
                    <Menu position="bottom-end" withinPortal>
                      <Menu.Target>
                        <ActionIcon
                          size="sm"
                          variant="subtle"
                          aria-label={t("sessions")}
                          onClick={(e) => e.stopPropagation()}
                        >
                          <IconDots size={14} />
                        </ActionIcon>
                      </Menu.Target>
                      <Menu.Dropdown>
                        <Menu.Item
                          leftSection={<IconPencil size={14} />}
                          onClick={() => startRename(s.id, s.title)}
                        >
                          {t("renameSession")}
                        </Menu.Item>
                        <Menu.Item
                          color="red"
                          leftSection={<IconTrash size={14} />}
                          onClick={() => void handleDelete(s.id)}
                        >
                          {t("deleteSession")}
                        </Menu.Item>
                      </Menu.Dropdown>
                    </Menu>
                  </Group>
                )}
              </Box>
            ))}
          </Stack>
        </ScrollArea>
      </Paper>

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
          <Box
            ref={scrollRef}
            style={{
              flex: 1,
              minHeight: 0,
              overflow: "auto",
              padding: "var(--mantine-spacing-md)",
            }}
          >
            <Stack gap="lg">
              {sessionId == null && (
                <Text c="dimmed" size="sm">
                  {t("selectSessionHint")}
                </Text>
              )}
              {loadingHistory && (
                <Group gap="xs">
                  <Loader size="sm" />
                  <Text c="dimmed" size="sm">
                    {t("loadingHistory")}
                  </Text>
                </Group>
              )}
              {sessionId != null && !loadingHistory && messages.length === 0 && (
                <Text c="dimmed" size="sm">
                  {t("continueHint")}
                </Text>
              )}
              {messages.map((m, i) => (
                <MessageBubble
                  key={`${m.role}-${i}`}
                  message={m}
                  approvalBusy={streaming}
                  onApprovalAction={(approval, action) => {
                    void handleApprovalAction(approval, action);
                  }}
                  onDownload={downloadSavedQueryResult}
                  onPersist={(id) =>
                    persistQuery.mutate({ path: { query_id: id }, body: { persisted: true } })
                  }
                />
              ))}
            </Stack>
          </Box>
        </Paper>

        <Box style={{ flexShrink: 0 }}>
          <ChatComposer
            value={input}
            onChange={setInput}
            onSubmit={() => void handleSend()}
            onStop={() => void handleStop()}
            loading={streaming}
            thinking={sessionId != null ? sessionThinking : undefined}
            onThinkingChange={
              sessionId != null
                ? (next) => {
                    setSessionThinking(next);
                    updateThinking.mutate({
                      path: { session_id: sessionId },
                      body: { thinking: next },
                    });
                  }
                : undefined
            }
            thinkingDisabled={streaming || updateThinking.isPending}
          />
        </Box>
      </Stack>
    </Group>
  );
}
