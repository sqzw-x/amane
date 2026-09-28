/** 从 session events.jsonl 重建聊天消息 (时间序 blocks). */

export type TurnTokenUsage = {
  input: number;
  cache_read: number;
  cache_write: number;
  output: number;
  requests: number;
};

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

/** 助手回合内按时间序排列的块: 文本与工具交错. */
export type AssistantBlock =
  | { kind: "text"; id: string; text: string }
  | { kind: "tool"; tool: ToolCallView };

export type ChatMessage =
  | { role: "user"; text: string }
  | {
      role: "assistant";
      blocks: AssistantBlock[];
      savedQueryIds?: number[];
      streaming?: boolean;
      usage?: TurnTokenUsage;
    };

export type TraceEvent = {
  type: string;
  payload?: Record<string, unknown>;
  at?: string;
  seq?: number;
  text?: string;
  [key: string]: unknown;
};

type NeedsApproval = {
  approval_id: string;
  sql: string;
  tool: string;
};

let blockSeq = 0;
function nextBlockId(prefix: string): string {
  blockSeq += 1;
  return `${prefix}-${blockSeq}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function parseNeedsApproval(value: unknown): NeedsApproval | null {
  if (!isRecord(value)) return null;
  const src = isRecord(value.needs_approval) ? value.needs_approval : value;
  const approvalId = src.approval_id;
  const sql = src.sql;
  const tool = src.tool;
  if (typeof approvalId === "string" && typeof sql === "string" && typeof tool === "string") {
    return { approval_id: approvalId, sql, tool };
  }
  return null;
}

function parseUsage(value: unknown): TurnTokenUsage | undefined {
  if (!isRecord(value)) return undefined;
  const { input, cache_read, cache_write, output, requests } = value;
  if (
    typeof input !== "number" ||
    typeof cache_read !== "number" ||
    typeof cache_write !== "number" ||
    typeof output !== "number"
  ) {
    return undefined;
  }
  return {
    input,
    cache_read,
    cache_write,
    output,
    requests: typeof requests === "number" ? requests : 0,
  };
}

function extractSavedQueryId(result: unknown): number | undefined {
  if (!isRecord(result)) return undefined;
  const id = result.saved_query_id;
  return typeof id === "number" ? id : undefined;
}

function fieldText(raw: Record<string, unknown>): string {
  if (typeof raw.text === "string") return raw.text;
  const payload = raw.payload;
  if (isRecord(payload) && typeof payload.text === "string") return payload.text;
  return "";
}

/** 批准/拒绝注入模型的 follow-up; 旧 events 无 hidden 时也按文案跳过. */
function isInternalFollowUpText(text: string): boolean {
  const t = text.trim();
  if (
    t.startsWith("用户已批准") ||
    t.startsWith("用户已批量批准") ||
    t.startsWith("用户拒绝了操作") ||
    t.startsWith("批准后的操作失败")
  ) {
    return true;
  }
  if (t.startsWith("{")) {
    try {
      const parsed: unknown = JSON.parse(t);
      if (isRecord(parsed) && parsed.kind === "tool-return") return true;
    } catch {
      // ignore
    }
  }
  return false;
}

function appendText(blocks: AssistantBlock[], piece: string): AssistantBlock[] {
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

function upsertTool(
  blocks: AssistantBlock[],
  tool: ToolCallView,
  mode: "call" | "result",
): AssistantBlock[] {
  const next = [...blocks];
  const idx = next.findIndex((b) => b.kind === "tool" && b.tool.toolCallId === tool.toolCallId);
  if (idx >= 0 && next[idx]?.kind === "tool") {
    const prev = next[idx].tool;
    next[idx] = {
      kind: "tool",
      tool: {
        ...prev,
        name: tool.name || prev.name,
        args: mode === "call" ? tool.args : prev.args,
        result: mode === "result" ? tool.result : prev.result,
        approval: tool.approval ?? prev.approval,
      },
    };
    return next;
  }
  if (mode === "result") {
    const openIdx = next.findLastIndex(
      (b) => b.kind === "tool" && b.tool.name === tool.name && b.tool.result === undefined,
    );
    if (openIdx >= 0 && next[openIdx]?.kind === "tool") {
      next[openIdx] = {
        kind: "tool",
        tool: {
          ...next[openIdx].tool,
          result: tool.result,
          name: tool.name,
          approval: tool.approval ?? next[openIdx].tool.approval,
        },
      };
      return next;
    }
  }
  next.push({ kind: "tool", tool });
  return next;
}

function markApprovalStatus(
  blocks: AssistantBlock[],
  approvalId: string,
  status: ToolApproval["status"],
): AssistantBlock[] {
  return blocks.map((b) => {
    if (b.kind !== "tool") return b;
    const hit = b.tool.toolCallId === approvalId || b.tool.approval?.approval_id === approvalId;
    if (!hit || !b.tool.approval) return b;
    return { kind: "tool", tool: { ...b.tool, approval: { ...b.tool.approval, status } } };
  });
}

function attachApprovalToTool(blocks: AssistantBlock[], approval: NeedsApproval): AssistantBlock[] {
  // approval_id 即 tool_call_id
  const next = [...blocks];
  const matchIdx = (pred: (b: Extract<AssistantBlock, { kind: "tool" }>["tool"]) => boolean) =>
    next.findIndex((b) => b.kind === "tool" && pred(b.tool));

  const byCallId = matchIdx((t) => t.toolCallId === approval.approval_id);
  const byApprovalId =
    byCallId < 0 ? matchIdx((t) => t.approval?.approval_id === approval.approval_id) : byCallId;
  const idx = byCallId >= 0 ? byCallId : byApprovalId;
  if (idx >= 0 && next[idx]?.kind === "tool") {
    const prev = next[idx].tool;
    next[idx] = {
      kind: "tool",
      tool: {
        ...prev,
        approval: {
          ...approval,
          status:
            prev.approval?.status === "approved" || prev.approval?.status === "rejected"
              ? prev.approval.status
              : "pending",
        },
      },
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
    const prev = next[byName].tool;
    next[byName] = {
      kind: "tool",
      tool: { ...prev, approval: { ...approval, status: prev.approval?.status ?? "pending" } },
    };
    return next;
  }
  return next;
}

/** 将 events.jsonl 风格的事件列表还原为 UI 消息. */
export function messagesFromTrace(events: ReadonlyArray<TraceEvent | Record<string, unknown>>): {
  messages: ChatMessage[];
  lastSeq: number;
} {
  const messages: ChatMessage[] = [];
  let current: Extract<ChatMessage, { role: "assistant" }> | null = null;
  let lastSeq = 0;
  let sawTextDelta = false;

  const flush = () => {
    if (current) {
      messages.push({ ...current, streaming: false });
      current = null;
      sawTextDelta = false;
    }
  };

  const ensureAssistant = () => {
    if (!current) {
      current = { role: "assistant", blocks: [], savedQueryIds: [] };
    }
    return current;
  };

  const patchCurrentBlocks = (blocks: AssistantBlock[]) => {
    if (!current) return;
    current = { ...current, blocks };
  };

  for (let i = 0; i < events.length; i++) {
    const raw = events[i];
    if (!isRecord(raw)) continue;
    const evType = typeof raw.type === "string" ? raw.type : "";
    if (typeof raw.seq === "number" && raw.seq > lastSeq) lastSeq = raw.seq;
    const payload = isRecord(raw.payload) ? raw.payload : undefined;

    if (evType === "user_message") {
      // 批准/拒绝 follow-up 仍写入 events 供续订, 但对用户气泡隐藏
      if (raw.hidden === true || payload?.hidden === true) {
        continue;
      }
      const text = fieldText(raw);
      if (isInternalFollowUpText(text)) {
        continue;
      }
      flush();
      messages.push({ role: "user", text });
      continue;
    }

    if (evType === "text_delta") {
      const assistant = ensureAssistant();
      current = {
        ...assistant,
        blocks: appendText(assistant.blocks, fieldText(raw)),
        streaming: true,
      };
      sawTextDelta = true;
      continue;
    }

    if (evType === "tool_call") {
      const assistant = ensureAssistant();
      const streamId = raw.tool_call_id;
      const streamName = raw.name;
      let tool: ToolCallView;
      if (typeof streamId === "string" && typeof streamName === "string") {
        tool = { toolCallId: streamId, name: streamName, args: raw.args };
      } else {
        const name =
          typeof payload?.tool === "string"
            ? payload.tool
            : typeof raw.tool === "string"
              ? raw.tool
              : "tool";
        const args: Record<string, unknown> = {};
        const src = payload ?? raw;
        for (const [k, v] of Object.entries(src)) {
          if (k === "tool" || k === "type" || k === "seq" || k === "at" || k === "payload")
            continue;
          args[k] = v;
        }
        tool = { toolCallId: `trace-${i}`, name, args };
      }
      current = {
        ...assistant,
        blocks: upsertTool(assistant.blocks, tool, "call"),
        streaming: true,
      };
      continue;
    }

    if (evType === "tool_result") {
      const assistant = ensureAssistant();
      const streamId = raw.tool_call_id;
      const streamName = raw.name;
      const result = "result" in raw ? raw.result : payload?.result;
      // 旧 events 可能把 needs_approval 嵌在 tool_result; 新路径以 needs_approval 事件为准
      const parsed = parseNeedsApproval(result);
      const tool: ToolCallView =
        typeof streamId === "string"
          ? {
              toolCallId: streamId,
              name: typeof streamName === "string" ? streamName : "tool",
              result,
              approval: parsed ? { ...parsed, status: "pending" } : undefined,
            }
          : {
              toolCallId: `trace-${i}`,
              name:
                typeof payload?.tool === "string"
                  ? payload.tool
                  : typeof raw.tool === "string"
                    ? raw.tool
                    : "tool",
              result,
              approval: parsed ? { ...parsed, status: "pending" } : undefined,
            };
      const savedIds = [...(assistant.savedQueryIds ?? [])];
      const sq = extractSavedQueryId(result);
      if (sq !== undefined && !savedIds.includes(sq)) savedIds.push(sq);
      current = {
        ...assistant,
        blocks: upsertTool(assistant.blocks, tool, "result"),
        savedQueryIds: savedIds,
        streaming: true,
      };
      continue;
    }

    if (evType === "assistant_message") {
      const assistant = ensureAssistant();
      const text = fieldText(raw);
      const usage = parseUsage(raw.usage ?? payload?.usage);
      let blocks = assistant.blocks;
      if (!sawTextDelta && text) {
        const hasText = blocks.some((b) => b.kind === "text" && b.text);
        if (!hasText) blocks = appendText(blocks, text);
      }
      current = {
        ...assistant,
        blocks,
        usage: usage ?? assistant.usage,
        streaming: true,
      };
      continue;
    }

    if (evType === "needs_approval") {
      const parsed = parseNeedsApproval(payload ?? raw);
      const assistant = ensureAssistant();
      if (parsed) {
        current = {
          ...assistant,
          blocks: attachApprovalToTool(assistant.blocks, parsed),
          streaming: false,
        };
      } else {
        current = { ...assistant, streaming: false };
      }
      continue;
    }

    if (evType === "approval_granted" || evType === "approval_result") {
      const src = payload ?? raw;
      const approvalId = typeof src.approval_id === "string" ? src.approval_id : null;
      if (approvalId && current) {
        patchCurrentBlocks(markApprovalStatus(current.blocks, approvalId, "approved"));
      } else if (approvalId) {
        // 批准结果可能落在后续回合; 回溯直到命中含该 approval_id 的助手消息
        for (let mi = messages.length - 1; mi >= 0; mi--) {
          const m = messages[mi];
          if (m?.role !== "assistant") continue;
          const has = m.blocks.some(
            (b) =>
              b.kind === "tool" &&
              (b.tool.toolCallId === approvalId || b.tool.approval?.approval_id === approvalId),
          );
          if (!has) continue;
          messages[mi] = {
            ...m,
            blocks: markApprovalStatus(m.blocks, approvalId, "approved"),
          };
          break;
        }
      }
      continue;
    }

    if (evType === "approval_rejected") {
      const src = payload ?? raw;
      const approvalId = typeof src.approval_id === "string" ? src.approval_id : null;
      if (approvalId && current) {
        patchCurrentBlocks(markApprovalStatus(current.blocks, approvalId, "rejected"));
      } else if (approvalId) {
        for (let mi = messages.length - 1; mi >= 0; mi--) {
          const m = messages[mi];
          if (m?.role !== "assistant") continue;
          const has = m.blocks.some(
            (b) =>
              b.kind === "tool" &&
              (b.tool.toolCallId === approvalId || b.tool.approval?.approval_id === approvalId),
          );
          if (!has) continue;
          messages[mi] = {
            ...m,
            blocks: markApprovalStatus(m.blocks, approvalId, "rejected"),
          };
          break;
        }
      }
      continue;
    }

    if (evType === "done") {
      const assistant = ensureAssistant();
      const saved = raw.saved_query_ids;
      const savedIds = Array.isArray(saved)
        ? saved.filter((x): x is number => typeof x === "number")
        : (assistant.savedQueryIds ?? []);
      current = {
        ...assistant,
        streaming: false,
        savedQueryIds: savedIds,
        usage: parseUsage(raw.usage) ?? assistant.usage,
      };
      flush();
      continue;
    }

    if (evType === "error") {
      const assistant = ensureAssistant();
      const msg = typeof raw.message === "string" ? raw.message : "error";
      current = {
        ...assistant,
        blocks: appendText(assistant.blocks, `\n\n⚠ ${msg}`),
        streaming: false,
      };
      flush();
      continue;
    }

    if (evType === "cancelled") {
      if (current !== null) {
        current = {
          role: "assistant",
          blocks: current.blocks,
          savedQueryIds: current.savedQueryIds,
          usage: current.usage,
          streaming: false,
        };
        flush();
      }
    }
  }

  if (current?.streaming) {
    messages.push(current);
  } else {
    flush();
  }

  return { messages, lastSeq };
}
