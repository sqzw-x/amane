/** 回放行 → 页面消息.

行是服务端成形的展示事实 (正文已按块归好、工具参数已解析、用量标出位置), 这里只做归块、定位与消息切分,
不认识 AG-UI 事件, 也不推断字段形状.
*/

import type { ThreadMessageLike } from "@assistant-ui/react";
import type { AgentTraceResponse, Interrupt, TurnTokenUsage } from "@/client/types.gen";

/** 服务端的回放行联合, 由 OpenAPI 生成; `type` 必填, 故下面的 switch 可做穷尽检查. */
export type TraceRow = AgentTraceResponse["events"][number];

/** 逐请求用量在协议里没有位置, 以 data 部件随消息重建. */
export const REQUEST_USAGE_PART = "request-usage";

/** 未决审批寄存的位置: 库从这里读取待批态以恢复审批入口 (键名由库约定). */
const AGUI_METADATA_KEY = "agui";

export type TraceFold = {
  messages: ThreadMessageLike[];
  /** 最近一回合的聚合用量; 回合未收尾时为空. */
  turnUsage: TurnTokenUsage | null;
};

type AssistantPart = Exclude<NonNullable<ThreadMessageLike["content"]>, string>[number];

/** 归块键: 正文与思考用协议给的块 id, 工具用调用 id. */
function blockKey(kind: "text" | "reasoning", blockId: string): string {
  return `${kind}:${blockId}`;
}

function toolKey(toolCallId: string): string {
  return `tool:${toolCallId}`;
}

function assertNever(value: never): never {
  throw new Error(`未处理的行: ${JSON.stringify(value)}`);
}

export function foldTrace(rows: readonly TraceRow[]): TraceFold {
  const messages: ThreadMessageLike[] = [];
  let parts: AssistantPart[] = [];
  let keys: (string | null)[] = [];
  let approvals: Interrupt[] = [];
  let turnUsage: TurnTokenUsage | null = null;

  const flush = () => {
    if (parts.length === 0) return;
    messages.push({ id: `trace-a-${messages.length}`, role: "assistant", content: parts });
    parts = [];
    keys = [];
  };

  const push = (key: string | null, part: AssistantPart) => {
    parts.push(part);
    keys.push(key);
  };

  const at = (key: string) => keys.indexOf(key);

  const appendDelta = (key: string, kind: "text" | "reasoning", text: string) => {
    const index = at(key);
    const part = index < 0 ? undefined : parts[index];
    if (part?.type === kind) {
      parts[index] = { ...part, text: part.text + text };
      return;
    }
    push(key, { type: kind, text });
  };

  for (const row of rows) {
    switch (row.type) {
      case "user_message": {
        flush();
        messages.push({
          id: `trace-u-${messages.length}`,
          role: "user",
          content: [{ type: "text", text: row.text }],
        });
        break;
      }

      case "reasoning_delta":
        appendDelta(blockKey("reasoning", row.block_id), "reasoning", row.text);
        break;

      case "text_delta":
        appendDelta(blockKey("text", row.block_id), "text", row.text);
        break;

      case "tool_call":
        // 只给 argsText: 库会解析成对象填进 args, 前端不必再判一次形状
        push(toolKey(row.tool_call_id), {
          type: "tool-call",
          toolCallId: row.tool_call_id,
          toolName: row.name,
          argsText: jsonText(row.args),
        });
        break;

      case "tool_result": {
        // 名字与参数在同 id 的 tool_call 行; 结果先到只可能是日志被截断, 无处归属
        const index = at(toolKey(row.tool_call_id));
        const part = index < 0 ? undefined : parts[index];
        if (part?.type === "tool-call") parts[index] = { ...part, result: row.result };
        break;
      }

      case "request_usage": {
        const part: AssistantPart = { type: `data-${REQUEST_USAGE_PART}`, data: row.usage };
        const anchor = row.usage.after_tool_call;
        const index = anchor == null ? -1 : at(toolKey(anchor));
        if (index < 0) {
          push(null, part);
          break;
        }
        parts.splice(index + 1, 0, part);
        keys.splice(index + 1, 0, null);
        break;
      }

      case "turn_usage":
        turnUsage = row.usage;
        break;

      case "approvals":
        approvals = [...row.interrupts];
        break;

      case "error":
        // 失败写入本轮气泡, 与已产出的内容同处一段上下文
        push(null, { type: "text", text: `⚠ ${row.message}` });
        flush();
        break;

      case "cancelled":
        flush();
        break;

      case "agui":
        break;

      default:
        assertNever(row);
    }
  }

  flush();

  if (approvals.length > 0) {
    const last = messages.at(-1);
    if (last?.role === "assistant") {
      messages[messages.length - 1] = {
        ...last,
        status: { type: "requires-action", reason: "interrupt" },
        metadata: { custom: { [AGUI_METADATA_KEY]: { interrupts: approvals } } },
      };
    }
  }

  return { messages, turnUsage };
}

/** 工具参数已是解析过的 JSON; 字符串原样保留, 其余转文本. */
function jsonText(value: unknown): string {
  if (typeof value === "string") return value;
  return value === undefined ? "" : JSON.stringify(value);
}
