import { List } from "@mantine/core";

/** 合并确认框的效果清单: 首条为动作概述, 其余逐条列出副作用. */
export function MergeNotice({ lead, items }: { lead: string; items: readonly string[] }) {
  return (
    <List size="sm" spacing={4}>
      <List.Item>{lead}</List.Item>
      {items.map((item) => (
        <List.Item key={item}>{item}</List.Item>
      ))}
    </List>
  );
}
