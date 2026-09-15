import { Anchor, Code, List, Table, Text, Title } from "@mantine/core";
import type { Components } from "react-markdown";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const components: Components = {
  p: ({ children }) => (
    <Text size="sm" mb="xs" style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
      {children}
    </Text>
  ),
  h1: ({ children }) => (
    <Title order={3} mb="xs">
      {children}
    </Title>
  ),
  h2: ({ children }) => (
    <Title order={4} mb="xs">
      {children}
    </Title>
  ),
  h3: ({ children }) => (
    <Title order={5} mb="xs">
      {children}
    </Title>
  ),
  h4: ({ children }) => (
    <Title order={6} mb="xs">
      {children}
    </Title>
  ),
  a: ({ href, children }) => (
    <Anchor href={href} target="_blank" rel="noreferrer" size="sm">
      {children}
    </Anchor>
  ),
  ul: ({ children }) => (
    <List size="sm" mb="xs" withPadding>
      {children}
    </List>
  ),
  ol: ({ children }) => (
    <List type="ordered" size="sm" mb="xs" withPadding>
      {children}
    </List>
  ),
  li: ({ children }) => <List.Item>{children}</List.Item>,
  code: ({ className, children }) => {
    const isBlock = Boolean(className?.includes("language-")) || String(children).includes("\n");
    if (isBlock) {
      return (
        <Code block mb="xs" style={{ fontSize: 12, overflow: "auto", maxHeight: 320 }}>
          {children}
        </Code>
      );
    }
    return (
      <Code style={{ fontSize: 12 }} px={4}>
        {children}
      </Code>
    );
  },
  pre: ({ children }) => <>{children}</>,
  blockquote: ({ children }) => (
    <Text
      component="blockquote"
      size="sm"
      c="dimmed"
      pl="md"
      mb="xs"
      style={{ borderLeft: "3px solid var(--mantine-color-default-border)", margin: 0 }}
    >
      {children}
    </Text>
  ),
  table: ({ children }) => (
    <Table.ScrollContainer minWidth={280} mb="xs">
      <Table withTableBorder withColumnBorders horizontalSpacing="sm" verticalSpacing={4} fz="sm">
        {children}
      </Table>
    </Table.ScrollContainer>
  ),
  thead: ({ children }) => <Table.Thead>{children}</Table.Thead>,
  tbody: ({ children }) => <Table.Tbody>{children}</Table.Tbody>,
  tr: ({ children }) => <Table.Tr>{children}</Table.Tr>,
  th: ({ children }) => <Table.Th>{children}</Table.Th>,
  td: ({ children }) => <Table.Td>{children}</Table.Td>,
  hr: () => (
    <hr
      style={{
        border: "none",
        borderTop: "1px solid var(--mantine-color-default-border)",
        margin: "var(--mantine-spacing-sm) 0",
      }}
    />
  ),
  // 模型输出的图片宽度不受控, 缺宽度上限时宽图撑破气泡; Mantine 始终不设置 img 的宽度.
  img: ({ src, alt }) => (
    <img
      src={src}
      alt={alt ?? ""}
      style={{
        display: "block",
        maxWidth: "100%",
        height: "auto",
        marginBottom: "var(--mantine-spacing-xs)",
      }}
    />
  ),
};

export function MarkdownContent({
  text,
  streaming = false,
}: {
  text: string;
  streaming?: boolean;
}) {
  if (!text && !streaming) return null;
  return (
    <div style={{ fontSize: "var(--mantine-font-size-sm)" }}>
      {text ? (
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
          {text}
        </ReactMarkdown>
      ) : null}
      {streaming ? (
        <Text span c="dimmed" size="sm">
          ▍
        </Text>
      ) : null}
    </div>
  );
}
