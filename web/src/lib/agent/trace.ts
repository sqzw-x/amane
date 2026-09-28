/** 回放行 → 页面消息.

行是服务端成形的展示事实 (正文已按块归好、工具参数已解析、用量按到达顺序落位), 这里只做归块与消息切分,
不认识 AG-UI 事件, 也不推断字段形状.

页面只渲染这里的产物: 直播与回放共用同一份形状, 运行时话题本只用于运行控制 (见 `components/agent/agent-home.tsx`).
返回类型比库的 `ThreadMessageLike` 更窄, 用量部件因此带确切载荷.
*/

import type {
  AgentTraceResponse,
  Interrupt,
  RequestTokenUsage,
  TurnTokenUsage,
} from "@/client/types.gen";

/** 页面契约的行联合, 由 OpenAPI 生成; `type` 必填, 故下面的 switch 可做穷尽检查.

    契约里不含协议透传行: 那类行只在 `POST .../agui` 通道分发, `/trace` 与跟随端点都会滤掉.
*/
export type TraceRow = AgentTraceResponse["events"][number];

/** 未决审批寄存的位置: 库读消息 metadata 的这一项来恢复审批入口 (键名由库约定). */
const AGUI_METADATA_KEY = "agui";

/** 正文: 一段回复或一轮里的若干段, 归块键是协议给的块 id. */
export type AgentTextPart = { readonly type: "text"; readonly text: string };

/** 思考: 与正文同处一条消息, 渲染时相邻的若干段合成一个折叠块. */
export type AgentReasoningPart = { readonly type: "reasoning"; readonly text: string };

/** 工具调用: 参数只留 JSON 文本, `result` 为空表示这次调用还没有回执. */
export type AgentToolCallPart = {
  readonly type: "tool-call";
  readonly toolCallId: string;
  readonly toolName: string;
  readonly argsText: string;
  readonly result?: unknown;
};

/** 单次请求的用量; 协议里没有它的位置, 以 data 部件随消息走. */
export type AgentRequestUsagePart = {
  readonly type: "data-request-usage";
  readonly data: RequestTokenUsage;
};

/** 回合总计: 该轮消息的最后一个部件, 因此每轮的总计都留在自己轮末. */
export type AgentTurnUsagePart = {
  readonly type: "data-turn-usage";
  readonly data: TurnTokenUsage;
};

export type AgentPart =
  | AgentTextPart
  | AgentReasoningPart
  | AgentToolCallPart
  | AgentRequestUsagePart
  | AgentTurnUsagePart;

export type AgentMessage = {
  readonly id: string;
  readonly role: "user" | "assistant";
  /** 只在有未决审批时出现; 库从 metadata 读同一份中断清单. */
  readonly status?: { readonly type: "requires-action"; readonly reason: "interrupt" };
  readonly metadata?: { readonly custom: Record<string, unknown> };
  readonly content: readonly AgentPart[];
};

/** 归块键: 正文与思考用协议给的块 id, 工具用调用 id. */
function blockKey(kind: "text" | "reasoning", blockId: string): string {
  return `${kind}:${blockId}`;
}

function toolKey(toolCallId: string): string {
  return `tool:${toolCallId}`;
}

/** 未处理的行类型.

    `never` 让编译期仍然强制穷尽; 运行期服务端可能先于页面 bundle 升级, 此时丢弃这一行并告警,
    而不是让整段会话的重建失败.
*/
function unknownRow(row: never): void {
  console.warn("[agent] 丢弃无法识别的回放行", row);
}

export function foldTrace(rows: readonly TraceRow[]): AgentMessage[] {
  const messages: AgentMessage[] = [];
  let parts: AgentPart[] = [];
  let keys: (string | null)[] = [];
  let approvals: Interrupt[] = [];

  const flush = () => {
    if (parts.length === 0) return;
    messages.push({ id: `trace-a-${messages.length}`, role: "assistant", content: parts });
    parts = [];
    keys = [];
  };

  const push = (key: string | null, part: AgentPart) => {
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
        // 参数只存 JSON 文本: 回执与渲染两侧都按同一份文本解析, 不必在行里再存一遍对象
        push(toolKey(row.tool_call_id), {
          type: "tool-call",
          toolCallId: row.tool_call_id,
          toolName: row.name,
          argsText: jsonText(row.args),
        });
        break;

      case "tool_result": {
        // 名字与参数在同 id 的 tool_call 行; 找不到锚点只可能来自被截断的日志
        const index = at(toolKey(row.tool_call_id));
        const part = index < 0 ? undefined : parts[index];
        if (part?.type === "tool-call") {
          parts[index] = { ...part, result: row.result };
        } else {
          console.warn("[agent] 工具回执找不到对应的调用, 已丢弃", row);
        }
        break;
      }

      case "request_usage":
        push(null, { type: "data-request-usage", data: row.usage });
        break;

      case "turn_usage":
        push(null, { type: "data-turn-usage", data: row.usage });
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

      default:
        unknownRow(row);
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

  return messages;
}

/** 工具参数已是解析过的 JSON; 字符串原样保留, 其余转文本. */
function jsonText(value: unknown): string {
  if (typeof value === "string") return value;
  return value === undefined ? "" : JSON.stringify(value);
}
