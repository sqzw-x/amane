import { Alert, Button, Group, VisuallyHidden } from "@mantine/core";
import {
  IconArrowBackUp,
  IconArrowForwardUp,
  IconGripVertical,
  IconMathFunction,
  IconPencil,
  IconPlus,
  IconX,
} from "@tabler/icons-react";
import { type PointerEvent, type ReactNode, Fragment, useId, useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";
import { HintedActionIcon } from "@/components/common/hinted-action-icon";
import { QuickFilterControls } from "../quick-filter-controls";
import { useResettingState } from "@/hooks/use-resetting-state";
import {
  FilterExpressionError,
  type FilterEntity,
  compileFilterFormula,
  type FilterFormula,
  type FilterRule,
  filterRuleError,
  serializeFilterFormula,
} from "@/lib/filter-expression";
import {
  appendNodes,
  canNegate,
  canWrap,
  emptyDraft,
  moveOperand,
  moveSibling,
  operandRange,
  operandsOf,
  pathKey,
  removeSelection,
  replaceRule,
  toggleConnector,
  toggleNegate,
  ungroup,
  wrapSelection,
  type FilterDraft,
  type OperandSelection,
  type OperandRange,
} from "./draft";
import { PredicateEditor } from "./predicate-editor";
import { PredicateLabel } from "./predicate-label";
import type { FilterLibrary } from "./value-input";
import classes from "./advanced-filter-editor.module.css";

export interface AdvancedFilterEditorProps {
  entity: FilterEntity;
  value: FilterFormula | null;
  onApply: (value: FilterFormula | null) => void;
  onCancel?: () => void;
  opened?: boolean;
  disabled?: boolean;
  libraries?: readonly FilterLibrary[];
}

interface DraftHistory {
  past: FilterDraft[];
  present: FilterDraft;
  future: FilterDraft[];
}

interface PointerDrag {
  from: number;
  to: number;
  x: number;
  y: number;
  active: boolean;
}

export function AdvancedFilterEditor({
  entity,
  value,
  onApply,
  onCancel,
  opened = true,
  disabled = false,
  libraries,
}: AdvancedFilterEditorProps) {
  const { t } = useTranslation(["metadata", "common"]);
  const id = useId();
  const initial = useMemo(() => value ?? emptyDraft(), [value]);
  const resetKey = entity + ":" + opened + ":" + JSON.stringify(value);
  const [history, setHistory] = useResettingState<DraftHistory>(
    () => ({ past: [], present: initial, future: [] }),
    resetKey,
  );
  const [selection, setSelection] = useResettingState<OperandSelection | null>(
    () => null,
    resetKey,
  );
  const [rangeMode, setRangeMode] = useResettingState(() => false, resetKey);
  const [editing, setEditing] = useResettingState<{ path: readonly number[] | null } | null>(
    () => null,
    resetKey,
  );
  const [announcement, setAnnouncement] = useResettingState(() => "", resetKey);
  const [dropIndex, setDropIndex] = useResettingState<number | null>(() => null, resetKey);
  const formulaRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<PointerDrag | null>(null);
  const predicateEditorRef = useRef<HTMLDivElement>(null);
  const editTriggerRef = useRef<HTMLButtonElement>(null);
  const addRuleRef = useRef<HTMLButtonElement>(null);
  const predicateEditorId = id + "-predicate-editor";
  const formulaDisabled = disabled;
  const draft = history.present;
  const dirty = useMemo(() => JSON.stringify(draft) !== JSON.stringify(initial), [draft, initial]);
  const operands = useMemo(() => operandsOf(draft), [draft]);
  const indexByPath = useMemo(
    () => new Map(operands.map((operand, index) => [pathKey(operand.path), index])),
    [operands],
  );
  const range = operandRange(selection, operands.length);
  const errorText = useMemo(() => {
    try {
      compileFilterFormula(draft, entity);
      serializeFilterFormula(draft, entity);
      return null;
    } catch (error) {
      if (error instanceof FilterExpressionError) return t(error.messageKey);
      throw error;
    }
  }, [draft, entity, t]);

  /** 收起行内条件表单, 不抢焦点: 用户已经去碰别处了. */
  const dismissEditing = () => {
    if (editing !== null) setEditing(null);
  };
  const apply = (next: FilterDraft | null, message?: string) => {
    if (next === null) return;
    setHistory((current) => ({
      past: [...current.past.slice(-39), current.present],
      present: next,
      future: [],
    }));
    setSelection(null);
    if (message !== undefined) setAnnouncement(message);
  };
  const undo = () => {
    dismissEditing();
    setHistory((current) => {
      const previous = current.past.at(-1);
      return previous
        ? {
            past: current.past.slice(0, -1),
            present: previous,
            future: [current.present, ...current.future],
          }
        : current;
    });
    setSelection(null);
  };
  const redo = () => {
    dismissEditing();
    setHistory((current) => {
      const next = current.future[0];
      return next
        ? {
            past: [...current.past, current.present],
            present: next,
            future: current.future.slice(1),
          }
        : current;
    });
    setSelection(null);
  };
  const selectOperand = (index: number, extend: boolean) => {
    setSelection((current) =>
      extend && current ? { anchor: current.anchor, head: index } : { anchor: index, head: index },
    );
  };
  const moveBy = (delta: number) => {
    if (range === null || range.start !== range.end) return;
    const operand = operands[range.start];
    if (!operand) return;
    const next = moveSibling(draft, operand.path, delta);
    if (next === null) return;
    apply(next, t("advancedFilter.formula.moved"));
  };
  /** 拖拽只允许落到同一父组的兄弟上, 免得树形结构被拖成看不懂的样子. */
  const dragTarget = (event: PointerEvent<HTMLButtonElement>): number | null => {
    const drag = dragRef.current;
    if (!drag) return null;
    const element = document
      .elementFromPoint(event.clientX, event.clientY)
      ?.closest("[data-formula-index]");
    const raw = element?.getAttribute("data-formula-index");
    const index = raw === null || raw === undefined ? NaN : Number(raw);
    const source = operands[drag.from];
    const target = operands[index];
    if (!source || !target || !Number.isInteger(index)) return null;
    if (pathKey(source.path.slice(0, -1)) !== pathKey(target.path.slice(0, -1))) return null;
    if (!element || !formulaRef.current?.contains(element)) return null;
    return index;
  };
  const beginDrag = (event: PointerEvent<HTMLButtonElement>, index: number) => {
    if (formulaDisabled || event.button !== 0 || !event.isPrimary) return;
    dismissEditing();
    dragRef.current = { from: index, to: index, x: event.clientX, y: event.clientY, active: false };
    event.currentTarget.setPointerCapture(event.pointerId);
    selectOperand(index, false);
  };
  const updateDrag = (event: PointerEvent<HTMLButtonElement>) => {
    const drag = dragRef.current;
    if (!drag) return;
    if (Math.hypot(event.clientX - drag.x, event.clientY - drag.y) < 6 && !drag.active) return;
    drag.active = true;
    const target = dragTarget(event);
    drag.to = target ?? drag.from;
    setDropIndex(target);
  };
  const endDrag = () => {
    const drag = dragRef.current;
    dragRef.current = null;
    setDropIndex(null);
    if (!drag?.active || drag.from === drag.to) return;
    const source = operands[drag.from];
    const target = operands[drag.to];
    if (!source || !target) return;
    const next = moveOperand(draft, source.path, target.path);
    if (next !== null) apply(next, t("advancedFilter.formula.moved"));
  };
  const removeParens = (path: readonly number[]) => {
    dismissEditing();
    apply(ungroup(draft, path));
    formulaRef.current?.focus();
  };
  const removeOperand = (index: number) => {
    dismissEditing();
    apply(removeSelection(draft, { start: index, end: index }));
    formulaRef.current?.focus();
  };
  const remove = () => {
    dismissEditing();
    if (range === null) return;
    apply(removeSelection(draft, range));
    formulaRef.current?.focus();
  };
  const openEditor = (path: readonly number[] | null, trigger: HTMLButtonElement) => {
    editTriggerRef.current = trigger;
    setEditing({ path });
    requestAnimationFrame(() => predicateEditorRef.current?.focus());
  };
  const closeEditor = () => {
    setEditing(null);
    requestAnimationFrame(() => {
      const trigger = editTriggerRef.current;
      const target = trigger?.isConnected && !trigger.disabled ? trigger : addRuleRef.current;
      if (target && !target.disabled) target.focus({ preventScroll: true });
      else formulaRef.current?.focus({ preventScroll: true });
      editTriggerRef.current = null;
    });
  };
  const savePredicate = (rule: FilterRule) => {
    if (!editing || disabled) return;
    const next =
      editing.path === null ? appendNodes(draft, [rule]) : replaceRule(draft, editing.path, rule);
    if (next !== null) apply(next);
    closeEditor();
  };
  const isSelected = (index: number): boolean =>
    range !== null && index >= range.start && index <= range.end;
  const selectedRange = (): OperandRange | null => range;
  const editPath = editing?.path === null ? null : (editing?.path ?? null);
  const editingRule: FilterRule | null = (() => {
    const path = editing?.path;
    if (path === null || path === undefined) return null;
    const key = pathKey(path);
    const node = operands.find((operand) => pathKey(operand.path) === key)?.node;
    return node?.type === "rule" ? node : null;
  })();

  const operatorLabel = (op: "and" | "or") =>
    t(`advancedFilter.formula.${op === "and" ? "and" : "or"}`);

  const renderRule = (rule: FilterRule, path: readonly number[], index: number): ReactNode => (
    <span
      key={pathKey(path)}
      className={classes.predicate}
      data-formula-index={index}
      data-selected={isSelected(index) || undefined}
      data-drop={dropIndex === index || undefined}
    >
      <button
        type="button"
        className={classes.grip}
        aria-label={t("advancedFilter.formula.drag")}
        disabled={formulaDisabled}
        onPointerDown={(event) => beginDrag(event, index)}
        onPointerMove={updateDrag}
        onPointerUp={endDrag}
        onPointerCancel={() => {
          dragRef.current = null;
          setDropIndex(null);
        }}
        onLostPointerCapture={() => {
          dragRef.current = null;
          setDropIndex(null);
        }}
        onKeyDown={(event) => {
          if (!event.altKey || (event.key !== "ArrowLeft" && event.key !== "ArrowRight")) return;
          event.preventDefault();
          moveBy(event.key === "ArrowLeft" ? -1 : 1);
        }}
      >
        <IconGripVertical size={13} />
      </button>
      <button
        type="button"
        id={id + "-operand-" + index}
        className={classes.predicateBody}
        disabled={formulaDisabled}
        aria-pressed={isSelected(index)}
        onClick={(event) => {
          dismissEditing();
          selectOperand(index, rangeMode || event.shiftKey);
        }}
      >
        <span className={classes.predicateText}>
          <PredicateLabel entity={entity} rule={rule} libraries={libraries} />
        </span>
      </button>
      <span className={classes.edit}>
        <HintedActionIcon
          size={22}
          variant="subtle"
          color="gray"
          label={t("advancedFilter.formula.editPredicate")}
          disabled={formulaDisabled}
          onClick={(event) => {
            selectOperand(index, false);
            openEditor(path, event.currentTarget);
          }}
        >
          <IconPencil size={13} />
        </HintedActionIcon>
        <HintedActionIcon
          size={22}
          variant="subtle"
          color="gray"
          label={t("common:actions.remove")}
          disabled={formulaDisabled}
          onClick={() => removeOperand(index)}
        >
          <IconX size={13} />
        </HintedActionIcon>
      </span>
    </span>
  );

  const renderGroup = (group: FilterDraft, path: readonly number[], isRoot: boolean): ReactNode => {
    const body = group.terms.map(({ join, node }, position) => {
      const childPath = [...path, position];
      const index = indexByPath.get(pathKey(childPath)) ?? 0;
      return (
        <Fragment key={pathKey(childPath)}>
          {position > 0 && (
            <button
              type="button"
              className={classes.operator}
              data-operator={join}
              disabled={formulaDisabled}
              aria-label={t("advancedFilter.formula.changeOperator", {
                operator: operatorLabel(join),
              })}
              onClick={() => {
                dismissEditing();
                apply(toggleConnector(draft, path, position));
              }}
            >
              {operatorLabel(join)}
            </button>
          )}
          {node.type === "rule"
            ? renderRule(node, childPath, index)
            : renderGroup(node, childPath, false)}
        </Fragment>
      );
    });
    const negation = group.negate && (
      <button
        type="button"
        className={classes.operator}
        data-operator="not"
        disabled={formulaDisabled}
        aria-label={t("advancedFilter.formula.removeNot")}
        onClick={() => {
          dismissEditing();
          apply(toggleNegate(draft, path));
        }}
      >
        {t("advancedFilter.formula.not")}
      </button>
    );
    if (isRoot) {
      return group.negate ? (
        <span className={classes.group}>
          {negation}(<span className={classes.groupBody}>{body}</span>)
        </span>
      ) : (
        body
      );
    }
    const selected = isSelected(indexByPath.get(pathKey(path)) ?? -1);
    const groupIndex = indexByPath.get(pathKey(path)) ?? 0;
    const canRemoveParens = selected && !rangeMode && !group.negate;
    const paren = (text: string) => (
      <button
        type="button"
        className={classes.paren}
        disabled={formulaDisabled}
        aria-label={t(
          canRemoveParens
            ? "advancedFilter.formula.removeGroup"
            : "advancedFilter.formula.selectGroup",
        )}
        aria-pressed={selected}
        onClick={() => {
          if (canRemoveParens) {
            removeParens(path);
            return;
          }
          dismissEditing();
          selectOperand(groupIndex, rangeMode);
        }}
      >
        {text}
      </button>
    );
    return (
      <span key={pathKey(path)} className={classes.group} data-selected={selected || undefined}>
        {negation}
        {paren("(")}
        <span className={classes.groupBody} data-formula-index={groupIndex}>
          {body}
        </span>
        {paren(")")}
      </span>
    );
  };

  if (!opened) return null;

  return (
    <section className={classes.editor} aria-label={t("advancedFilter.formula.title")}>
      <Group justify="space-between" gap="sm" wrap="wrap" className={classes.toolbar}>
        <Group gap="xs">
          <Button
            size="compact-sm"
            variant="light"
            leftSection={<IconPlus size={14} />}
            ref={addRuleRef}
            aria-expanded={editing?.path === null}
            aria-controls={editing?.path === null ? predicateEditorId : undefined}
            onClick={(event) => openEditor(null, event.currentTarget)}
            disabled={formulaDisabled || operands.length >= 256}
          >
            {t("advancedFilter.addRule")}
          </Button>
        </Group>
        <Group gap={4}>
          <HintedActionIcon
            label={t("advancedFilter.formula.undo")}
            disabled={formulaDisabled || !history.past.length}
            onClick={undo}
          >
            <IconArrowBackUp size={17} />
          </HintedActionIcon>
          <HintedActionIcon
            label={t("advancedFilter.formula.redo")}
            disabled={formulaDisabled || !history.future.length}
            onClick={redo}
          >
            <IconArrowForwardUp size={17} />
          </HintedActionIcon>
        </Group>
      </Group>
      {editing && (
        <div
          id={predicateEditorId}
          ref={predicateEditorRef}
          tabIndex={-1}
          className={classes.predicateEdit}
          onKeyDown={(event) => {
            if (event.target !== event.currentTarget || event.key !== "Escape") return;
            event.preventDefault();
            event.stopPropagation();
            closeEditor();
          }}
        >
          <PredicateEditor
            key={editPath === null ? "new" : pathKey(editPath)}
            entity={entity}
            rule={editingRule}
            libraries={libraries}
            onSave={savePredicate}
            onClose={closeEditor}
            disabled={disabled}
          />
        </div>
      )}
      <div
        ref={formulaRef}
        className={classes.formula}
        tabIndex={0}
        role="group"
        aria-label={t("advancedFilter.formula.expression")}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            setSelection(null);
            setRangeMode(false);
          }
          if (
            event.target === event.currentTarget &&
            (event.key === "Delete" || event.key === "Backspace") &&
            !formulaDisabled
          ) {
            event.preventDefault();
            remove();
          }
        }}
      >
        {!operands.length && (
          <div className={classes.emptyState}>
            <IconMathFunction size={24} stroke={1.5} aria-hidden />
          </div>
        )}
        {renderGroup(draft, [], true)}
      </div>
      {operands.length > 0 && (
        <Group
          justify="space-between"
          align="center"
          gap="xs"
          wrap="wrap"
          className={classes.tools}
        >
          <Group gap={4} wrap="nowrap">
            <Button
              size="compact-sm"
              variant="subtle"
              color="gray"
              disabled={formulaDisabled || (!draft.terms.length && !dirty)}
              onClick={() => {
                dismissEditing();
                apply(emptyDraft());
              }}
            >
              {t("advancedFilter.clear")}
            </Button>
            <Button
              size="compact-sm"
              variant={rangeMode ? "light" : "subtle"}
              disabled={formulaDisabled}
              onClick={() => {
                setRangeMode((active) => !active);
                setSelection(null);
              }}
              aria-pressed={rangeMode}
            >
              {t("advancedFilter.formula.selectRange")}
            </Button>
            <Button
              size="compact-sm"
              variant="subtle"
              disabled={formulaDisabled || !canWrap(draft, selectedRange())}
              onClick={() => {
                apply(wrapSelection(draft, range!, false));
                setRangeMode(false);
              }}
            >
              {t("advancedFilter.formula.parenthesize")}
            </Button>
            <Button
              size="compact-sm"
              variant="subtle"
              disabled={formulaDisabled || !canNegate(draft, selectedRange())}
              onClick={() => {
                apply(wrapSelection(draft, range!, true));
                setRangeMode(false);
              }}
            >
              {t("advancedFilter.formula.negate")}
            </Button>
          </Group>
        </Group>
      )}
      <QuickFilterControls
        entity={entity}
        libraries={libraries}
        disabled={disabled}
        onInsert={(rules) => {
          if (disabled) return;
          dismissEditing();
          const usable = rules.filter((rule) => filterRuleError(entity, rule) === null);
          if (usable.length > 0) apply(appendNodes(draft, usable));
        }}
      />
      {errorText !== null && (
        <Alert color="red" role="alert" p="xs">
          {errorText}
        </Alert>
      )}
      <div className={classes.footer}>
        <Group gap="xs" ml="auto">
          <Button
            size="sm"
            variant="default"
            disabled={formulaDisabled}
            onClick={() => {
              dismissEditing();
              setHistory({ past: [], present: initial, future: [] });
              setSelection(null);
              onCancel?.();
            }}
          >
            {t("common:actions.cancel")}
          </Button>
          <Button
            size="sm"
            disabled={formulaDisabled || !dirty || errorText !== null}
            onClick={() => {
              dismissEditing();
              onApply(draft.terms.length ? draft : null);
            }}
          >
            {t("advancedFilter.apply")}
          </Button>
        </Group>
      </div>
      <VisuallyHidden role="status" aria-live="polite">
        {announcement}
      </VisuallyHidden>
    </section>
  );
}
