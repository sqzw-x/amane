import { Box, Text, Textarea } from "@mantine/core";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import classes from "./field-replacement-editor.module.css";

const FIELDS = new Set([
  "title",
  "actors",
  "studio",
  "publisher",
  "tags",
  "series",
  "plot",
  "directors",
]);

interface FieldReplacementEditorProps {
  value: string;
  placeholder: string;
  saveError: string | null;
  onChange: (value: string) => void;
}

type RuleError =
  | "header"
  | "unknownField"
  | "duplicateField"
  | "missingHeader"
  | "ruleFormat"
  | "emptyFind"
  | "invalidRegex";

interface ParsedRules {
  error: RuleError | null;
  errorLine: number | null;
}

export function validateFieldReplacementRules(value: string): ParsedRules {
  let field: string | null = null;
  const fields = new Set<string>();

  for (const [index, rawLine] of value.split("\n").entries()) {
    const line = rawLine.trim();
    const lineNumber = index + 1;
    if (line === "" || line.startsWith("#")) continue;

    if (line.startsWith("[")) {
      if (!line.endsWith("]")) {
        return { error: "header", errorLine: lineNumber };
      }
      field = line.slice(1, -1).trim();
      if (!FIELDS.has(field)) return { error: "unknownField", errorLine: lineNumber };
      if (fields.has(field)) return { error: "duplicateField", errorLine: lineNumber };
      fields.add(field);
      continue;
    }

    if (field === null) return { error: "missingHeader", errorLine: lineNumber };
    const separator = line.indexOf("=");
    if (separator < 0) return { error: "ruleFormat", errorLine: lineNumber };
    const find = line.slice(0, separator).trim();
    if (find === "") return { error: "emptyFind", errorLine: lineNumber };
    if (find.startsWith("re:")) {
      try {
        RegExp(find.slice(3));
      } catch {
        return { error: "invalidRegex", errorLine: lineNumber };
      }
    }
  }

  return { error: null, errorLine: null };
}

export function FieldReplacementEditor({
  value,
  placeholder,
  saveError,
  onChange,
}: FieldReplacementEditorProps) {
  const { t } = useTranslation("settings");
  const parsed = useMemo(() => validateFieldReplacementRules(value), [value]);
  const rows = value.split("\n");

  return (
    <Box>
      <div className={classes.editor}>
        <pre className={classes.highlight} aria-hidden>
          {rows.map((line, index) => {
            const trimmed = line.trim();
            const isError = parsed.errorLine === index + 1;
            const kind = trimmed.startsWith("[")
              ? "header"
              : trimmed.startsWith("#")
                ? "comment"
                : line.includes("=")
                  ? "rule"
                  : "plain";
            return (
              <span
                key={index}
                className={[classes.line, classes[kind], isError ? classes.invalid : ""].join(" ")}
              >
                {kind === "rule" ? (
                  <>
                    {line.slice(0, line.indexOf("=")).startsWith("re:") ? (
                      <>
                        <span className={classes.regex}>re:</span>
                        {line.slice(3, line.indexOf("="))}
                      </>
                    ) : (
                      line.slice(0, line.indexOf("="))
                    )}
                    <span className={classes.separator}>=</span>
                    {line.slice(line.indexOf("=") + 1) || " "}
                  </>
                ) : (
                  line || " "
                )}
                {"\n"}
              </span>
            );
          })}
        </pre>
        <Textarea
          value={value}
          placeholder={placeholder}
          minRows={12}
          autosize
          spellCheck={false}
          aria-label={placeholder}
          aria-invalid={parsed.error !== null || saveError !== null}
          classNames={{ input: classes.input }}
          onChange={(event) => onChange(event.currentTarget.value)}
          onScroll={(event) => {
            const highlight = event.currentTarget.previousElementSibling;
            if (highlight instanceof HTMLElement) {
              highlight.scrollTop = event.currentTarget.scrollTop;
              highlight.scrollLeft = event.currentTarget.scrollLeft;
            }
          }}
        />
      </div>
      {parsed.error !== null && parsed.errorLine !== null && (
        <Text c="red" size="sm" mt={4} role="alert">
          {t(`fieldReplacements.validation.${parsed.error}`, { line: parsed.errorLine })}
        </Text>
      )}
      {parsed.error === null && saveError !== null && (
        <Text c="red" size="sm" mt={4} role="alert">
          {saveError}
        </Text>
      )}
    </Box>
  );
}
