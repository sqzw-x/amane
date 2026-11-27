import {
  formulaGroup,
  type FilterFormula,
  type FormulaTerm,
  type FilterRule,
} from "@/lib/filter-expression";

export type FilterDraft = FilterFormula;
type Term = FormulaTerm;
type DraftNode = Term["node"];

interface DraftOperand {
  path: readonly number[];
  node: DraftNode;
}

export interface OperandRange {
  start: number;
  end: number;
}

export interface OperandSelection {
  anchor: number;
  head: number;
}

export function emptyDraft(): FilterDraft {
  return formulaGroup([]);
}

export function pathKey(path: readonly number[]): string {
  return path.join(".");
}

export function operandsOf(root: FilterDraft): DraftOperand[] {
  const operands: DraftOperand[] = [];
  function visit(group: FilterDraft, path: readonly number[]) {
    group.terms.forEach(({ node }, index) => {
      const childPath = [...path, index];
      operands.push({ path: childPath, node });
      if (node.type === "group") visit(node, childPath);
    });
  }
  visit(root, []);
  return operands;
}

export function operandRange(
  selection: OperandSelection | null,
  count: number,
): OperandRange | null {
  if (selection === null || count === 0) return null;
  const anchor = Math.min(Math.max(selection.anchor, 0), count - 1);
  const head = Math.min(Math.max(selection.head, 0), count - 1);
  return { start: Math.min(anchor, head), end: Math.max(anchor, head) };
}

function rangeOperands(root: FilterDraft, range: OperandRange | null): DraftOperand[] {
  if (!range || range.start < 0 || range.start > range.end) return [];
  const operands = operandsOf(root);
  return range.end < operands.length ? operands.slice(range.start, range.end + 1) : [];
}

function siblingRun(root: FilterDraft, range: OperandRange | null) {
  const slice = rangeOperands(root, range);
  const paths = new Set(slice.map(({ path }) => pathKey(path)));
  const siblings = slice.filter(({ path }) => {
    for (let depth = 1; depth < path.length; depth += 1) {
      if (paths.has(pathKey(path.slice(0, depth)))) return false;
    }
    return true;
  });
  if (!siblings.length) return null;
  const firstPath = siblings[0].path;
  const parentPath = firstPath.slice(0, -1);
  const start = firstPath.at(-1)!;
  const end = start + siblings.length;
  return siblings.every(
    ({ path }, index) =>
      path.at(-1) === start + index && pathKey(path.slice(0, -1)) === pathKey(parentPath),
  )
    ? { parentPath, start, end }
    : null;
}

export function canWrap(root: FilterDraft, range: OperandRange | null): boolean {
  const run = siblingRun(root, range);
  return run !== null && run.end - run.start > 1;
}

export function canNegate(root: FilterDraft, range: OperandRange | null): boolean {
  return siblingRun(root, range) !== null;
}

function updateGroup(
  root: FilterDraft,
  path: readonly number[],
  update: (group: FilterDraft) => FilterDraft | null,
): FilterDraft | null {
  if (!path.length) return update(root);
  const [index, ...rest] = path;
  const term = root.terms[index];
  if (term?.node.type !== "group") return null;
  const node = updateGroup(term.node, rest, update);
  return (
    node && {
      ...root,
      terms: root.terms.map((entry, i) => (index === i ? { ...entry, node } : entry)),
    }
  );
}

function updateTerm(
  root: FilterDraft,
  path: readonly number[],
  update: (term: Term) => Term[] | null,
): FilterDraft | null {
  const index = path.at(-1);
  if (index === undefined) return null;
  return updateGroup(root, path.slice(0, -1), (group) => {
    const term = group.terms[index];
    const replacement = term && update(term);
    if (!replacement) return null;
    return {
      ...group,
      terms: [...group.terms.slice(0, index), ...replacement, ...group.terms.slice(index + 1)],
    };
  });
}

export function wrapSelection(
  root: FilterDraft,
  range: OperandRange,
  negate = false,
): FilterDraft | null {
  const run = siblingRun(root, range);
  if (!run) return null;
  const { parentPath, start, end } = run;
  if (end - start === 1 && !negate) return null;
  return updateGroup(root, parentPath, (group) => {
    const terms = group.terms.slice(start, end);
    const { join, node } = terms[0];
    const wrapped =
      negate && terms.length === 1 && node.type === "group"
        ? formulaGroup([...node.terms], !node.negate)
        : formulaGroup(terms, negate);
    return {
      ...group,
      terms: [...group.terms.slice(0, start), { join, node: wrapped }, ...group.terms.slice(end)],
    };
  });
}

export function ungroup(root: FilterDraft, path: readonly number[]): FilterDraft | null {
  return updateTerm(root, path, ({ join, node }) => {
    // 否定作用于整组, 必须先取消否定才能移除括号.
    if (node.type !== "group" || node.negate) return null;
    return node.terms.map((term, index) => (index === 0 ? { ...term, join } : term));
  });
}

export function removeSelection(root: FilterDraft, range: OperandRange): FilterDraft | null {
  const paths = new Set(rangeOperands(root, range).map(({ path }) => pathKey(path)));
  if (!paths.size) return null;
  function remove(group: FilterDraft, path: readonly number[]): FilterDraft {
    const terms = group.terms.flatMap((term, index) => {
      const childPath = [...path, index];
      if (paths.has(pathKey(childPath))) return [];
      if (term.node.type === "rule") return [term];
      const node = remove(term.node, childPath);
      return node.terms.length ? [{ ...term, node }] : [];
    });
    return formulaGroup(terms, group.negate);
  }
  return remove(root, []);
}

export function moveOperand(
  root: FilterDraft,
  from: readonly number[],
  to: readonly number[],
): FilterDraft | null {
  const parentPath = from.slice(0, -1);
  const source = from.at(-1);
  const target = to.at(-1);
  if (
    source === undefined ||
    target === undefined ||
    source === target ||
    pathKey(parentPath) !== pathKey(to.slice(0, -1))
  )
    return null;
  return updateGroup(root, parentPath, (group) => {
    const nodes = group.terms.map(({ node }) => node);
    if (!nodes[source] || !nodes[target]) return null;
    const [moved] = nodes.splice(source, 1);
    nodes.splice(target, 0, moved);
    return {
      ...group,
      terms: group.terms.map(({ join }, index) => ({ join, node: nodes[index] })),
    };
  });
}

export function moveSibling(
  root: FilterDraft,
  path: readonly number[],
  delta: number,
): FilterDraft | null {
  const from = path.at(-1);
  return from === undefined ? null : moveOperand(root, path, [...path.slice(0, -1), from + delta]);
}

export function toggleConnector(
  root: FilterDraft,
  path: readonly number[],
  position: number,
): FilterDraft | null {
  if (position < 1) return null;
  return updateTerm(root, [...path, position], (term) => [
    { ...term, join: term.join === "and" ? "or" : "and" },
  ]);
}

export function toggleNegate(root: FilterDraft, path: readonly number[]): FilterDraft | null {
  return updateGroup(root, path, (group) => formulaGroup([...group.terms], !group.negate));
}

export function replaceRule(
  root: FilterDraft,
  path: readonly number[],
  rule: FilterRule,
): FilterDraft | null {
  return updateTerm(root, path, (term) =>
    term.node.type === "rule" ? [{ ...term, node: rule }] : null,
  );
}

export function appendNodes(root: FilterDraft, rules: readonly FilterRule[]): FilterDraft {
  if (!rules.length) return root;
  const terms: Term[] = root.negate ? [{ join: "and", node: root }] : root.terms;
  return formulaGroup([...terms, ...rules.map((node): Term => ({ join: "and", node }))]);
}
