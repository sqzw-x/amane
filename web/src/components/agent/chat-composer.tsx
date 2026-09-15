import { ActionIcon, Box, Group, Menu, Text, Textarea, UnstyledButton } from "@mantine/core";
import { IconBrain, IconChevronDown, IconPlayerStopFilled, IconSend } from "@tabler/icons-react";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNarrowViewport } from "@/hooks/use-narrow-viewport";
import classes from "./chat-composer.module.css";

export type ThinkingValue = "off" | "minimal" | "low" | "medium" | "high" | "xhigh";

export const THINKING_MODES: ThinkingValue[] = ["off", "minimal", "low", "medium", "high", "xhigh"];

/**
 * 窄屏输入框行数下限.
 * 一屏高度扣除顶栏与消息区后余量有限, 桌面行数会把发送键推到首屏之外.
 */
const NARROW_MIN_ROWS = 2;

export function parseThinking(raw: unknown): ThinkingValue | null {
  if (typeof raw !== "string") return null;
  for (const mode of THINKING_MODES) {
    if (mode === raw) return mode;
  }
  return null;
}

type ThinkingI18nKey =
  | "thinking.inherit"
  | "thinking.off"
  | "thinking.minimal"
  | "thinking.low"
  | "thinking.medium"
  | "thinking.high"
  | "thinking.xhigh";

function thinkingI18nKey(value: ThinkingValue | null): ThinkingI18nKey {
  if (value == null) return "thinking.inherit";
  switch (value) {
    case "off":
      return "thinking.off";
    case "minimal":
      return "thinking.minimal";
    case "low":
      return "thinking.low";
    case "medium":
      return "thinking.medium";
    case "high":
      return "thinking.high";
    case "xhigh":
      return "thinking.xhigh";
  }
}

export function ChatComposer({
  value,
  onChange,
  onSubmit,
  onStop,
  disabled,
  loading,
  placeholder,
  autosizeMinRows = 4,
  large = false,
  thinking,
  onThinkingChange,
  thinkingDisabled,
}: {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  onStop?: () => void;
  disabled?: boolean;
  loading?: boolean;
  placeholder?: string;
  autosizeMinRows?: number;
  large?: boolean;
  /** 传入时在输入框底栏显示思考强度菜单; null = 继承全局默认. */
  thinking?: ThinkingValue | null;
  onThinkingChange?: (v: ThinkingValue | null) => void;
  thinkingDisabled?: boolean;
}) {
  const { t } = useTranslation("agent");
  const composingRef = useRef(false);
  const [focused, setFocused] = useState(false);
  const narrow = useNarrowViewport();
  const minRows = narrow ? NARROW_MIN_ROWS : large ? 5 : autosizeMinRows;
  const showThinking = onThinkingChange != null;

  return (
    <Box
      onFocusCapture={() => setFocused(true)}
      onBlurCapture={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget)) setFocused(false);
      }}
      style={{
        border: focused
          ? "1px solid var(--mantine-primary-color-filled)"
          : "1px solid var(--mantine-color-default-border)",
        borderRadius: "var(--mantine-radius-lg)",
        background: "var(--mantine-color-body)",
        boxShadow: focused ? "0 0 0 1px var(--mantine-primary-color-filled)" : undefined,
        transition: "border-color 120ms ease, box-shadow 120ms ease",
      }}
    >
      <Textarea
        value={value}
        onChange={(e) => onChange(e.currentTarget.value)}
        placeholder={placeholder ?? t("placeholder")}
        disabled={disabled}
        autosize
        minRows={minRows}
        maxRows={12}
        variant="unstyled"
        size="md"
        px="md"
        pt="sm"
        pb={4}
        onCompositionStart={() => {
          composingRef.current = true;
        }}
        onCompositionEnd={() => {
          composingRef.current = false;
        }}
        onKeyDown={(e) => {
          if (e.key !== "Enter" || e.shiftKey) return;
          if (e.nativeEvent.isComposing || composingRef.current || e.keyCode === 229) {
            return;
          }
          e.preventDefault();
          if (!loading && value.trim()) onSubmit();
        }}
        styles={{
          input: {
            fontSize: "var(--mantine-font-size-md)",
            lineHeight: 1.55,
            paddingTop: 4,
            paddingBottom: 4,
          },
        }}
      />

      <Group
        className={classes.footer}
        justify="space-between"
        align="center"
        px="sm"
        pb="sm"
        pt={2}
        wrap="nowrap"
        gap="xs"
      >
        {showThinking ? (
          <Menu position="top-start" withinPortal shadow="md" width={168}>
            <Menu.Target>
              <UnstyledButton
                disabled={disabled || thinkingDisabled}
                aria-label={t("thinking.label")}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "4px 10px",
                  borderRadius: "var(--mantine-radius-xl)",
                  background: "var(--mantine-color-default-hover)",
                  color: "var(--mantine-color-dimmed)",
                  fontSize: "var(--mantine-font-size-xs)",
                  fontWeight: 500,
                  lineHeight: 1,
                  opacity: disabled || thinkingDisabled ? 0.55 : 1,
                  cursor: disabled || thinkingDisabled ? "not-allowed" : "pointer",
                }}
              >
                <IconBrain size={14} stroke={1.75} />
                <Text span size="xs" fw={500} c="dimmed">
                  {t(thinkingI18nKey(thinking ?? null))}
                </Text>
                <IconChevronDown size={12} stroke={2} />
              </UnstyledButton>
            </Menu.Target>
            <Menu.Dropdown>
              <Menu.Label>{t("thinking.label")}</Menu.Label>
              <Menu.Item onClick={() => onThinkingChange(null)} fw={thinking == null ? 600 : 400}>
                {t("thinking.inherit")}
              </Menu.Item>
              <Menu.Divider />
              {THINKING_MODES.map((mode) => (
                <Menu.Item
                  key={mode}
                  onClick={() => onThinkingChange(mode)}
                  fw={thinking === mode ? 600 : 400}
                >
                  {t(thinkingI18nKey(mode))}
                </Menu.Item>
              ))}
            </Menu.Dropdown>
          </Menu>
        ) : (
          <span />
        )}

        {loading ? (
          <ActionIcon
            variant="filled"
            color="gray"
            size={36}
            radius="xl"
            onClick={onStop}
            disabled={disabled || onStop == null}
            aria-label={t("stop")}
          >
            <IconPlayerStopFilled size={16} />
          </ActionIcon>
        ) : (
          <ActionIcon
            variant="filled"
            size={36}
            radius="xl"
            onClick={onSubmit}
            disabled={disabled || !value.trim()}
            aria-label={t("send")}
          >
            <IconSend size={17} />
          </ActionIcon>
        )}
      </Group>
    </Box>
  );
}
