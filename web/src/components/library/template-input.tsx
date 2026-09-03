import { Input } from "@mantine/core";
import { useId } from "@mantine/hooks";
import { useCallback, useLayoutEffect, useRef, type ChangeEvent, type ReactNode } from "react";
import {
  tokenizeTemplate,
  type TemplateCatalog,
  type TemplateTokenKind,
} from "./template-highlight";
import classes from "./template-input.module.css";

const KIND_CLASS = {
  text: undefined,
  group: classes.group,
  brace: classes.brace,
  name: classes.name,
  punct: classes.punct,
  mapKey: classes.mapKey,
  mapValue: classes.mapValue,
  error: classes.error,
} as const satisfies Record<TemplateTokenKind, string | undefined>;

function copyInputMetrics(input: HTMLInputElement, highlight: HTMLElement): void {
  const box = input.parentElement;
  if (box == null) {
    return;
  }
  highlight.style.top = `${box.offsetTop}px`;
  highlight.style.left = `${box.offsetLeft}px`;
  highlight.style.width = `${box.offsetWidth}px`;
  highlight.style.height = `${box.offsetHeight}px`;
  const style = getComputedStyle(input);
  highlight.style.font = style.font;
  highlight.style.letterSpacing = style.letterSpacing;
  highlight.style.padding = style.padding;
  highlight.style.borderStyle = "solid";
  highlight.style.borderWidth = style.borderWidth;
  highlight.style.borderColor = "transparent";
  highlight.style.boxSizing = style.boxSizing;
  highlight.style.lineHeight = style.lineHeight;
  highlight.scrollLeft = input.scrollLeft;
}

interface TemplateInputProps {
  label?: ReactNode;
  description?: ReactNode;
  placeholder?: string;
  value: string;
  onChange: (event: ChangeEvent<HTMLInputElement>) => void;
  catalog?: TemplateCatalog;
  id?: string;
}

/** 路径 / STRM 模板输入: 透明字叠着色层, 外观与 TextInput 相同. */
export function TemplateInput({
  label,
  description,
  placeholder,
  value,
  onChange,
  catalog,
  id,
}: TemplateInputProps) {
  const uid = useId(id);
  const inputRef = useRef<HTMLInputElement>(null);
  const highlightRef = useRef<HTMLDivElement>(null);

  const sync = useCallback((): void => {
    const input = inputRef.current;
    const highlight = highlightRef.current;
    if (input == null || highlight == null) {
      return;
    }
    copyInputMetrics(input, highlight);
  }, []);

  useLayoutEffect(() => {
    const input = inputRef.current;
    const highlight = highlightRef.current;
    if (input == null || highlight == null) {
      return;
    }
    const box = input.parentElement;
    if (box == null) {
      return;
    }
    // Accordion Collapse 从高度 0 展开. 仅在挂载或 value 变化时同步尺寸, 会读到空盒子;
    // 有值时输入框透明, 着色层 overflow 裁掉文字. 盒子尺寸变化后再同步.
    sync();
    const observer = new ResizeObserver(sync);
    observer.observe(box);
    return () => {
      observer.disconnect();
    };
  }, [sync, value]);

  const filled = value.length > 0;
  const tokens = filled ? tokenizeTemplate(value, catalog) : [];

  return (
    <Input.Wrapper label={label} description={description} id={uid}>
      <div className={classes.shell}>
        <div ref={highlightRef} className={classes.highlight} aria-hidden>
          <span className={classes.highlightInner}>
            {tokens.map((token, index) => (
              <span key={index} className={KIND_CLASS[token.kind]}>
                {token.text}
              </span>
            ))}
          </span>
        </div>
        <Input
          id={uid}
          ref={inputRef}
          value={value}
          placeholder={placeholder}
          spellCheck={false}
          autoComplete="off"
          classNames={{
            input: filled ? `${classes.input} ${classes.inputFilled}` : classes.input,
          }}
          onChange={(event) => {
            onChange(event);
            requestAnimationFrame(sync);
          }}
          onScroll={sync}
        />
      </div>
    </Input.Wrapper>
  );
}
