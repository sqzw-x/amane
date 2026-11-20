import {
  compileFilterFormula,
  FilterExpressionError,
  formulaFromTree,
  formulaGroup,
  parseFilterFormula,
  type FilterEntity,
  type FilterFormula,
  type FilterGroup,
  type FilterNode,
  type FormulaTerm,
} from "@/lib/filter-expression";

export function andLegacyFilters(formula: FilterFormula | null, legacy: readonly FilterNode[]) {
  if (!legacy.length) return formula;
  const prefix = formulaFromTree({ type: "group", op: "and", children: [...legacy] });
  const terms: FormulaTerm[] = !formula
    ? []
    : formula.negate || formula.terms.some(({ join }) => join === "or")
      ? [{ join: "and", node: formula }]
      : formula.terms;
  return formulaGroup([...prefix.terms, ...terms]);
}

export function readBrowseFilter(
  entity: FilterEntity,
  search: { f?: unknown; saved_query_id?: number },
  legacy: readonly FilterNode[],
) {
  const active = search.f !== undefined;
  const presetId = search.saved_query_id;
  const conflict = active && presetId != null;
  const outerFilters = active ? legacy : [];
  let value: FilterFormula | null = null;
  let expression: FilterGroup | null = null;
  let error: FilterExpressionError | null = null;
  try {
    value = parseFilterFormula(search.f, entity);
    if (active) expression = compileFilterFormula(andLegacyFilters(value, outerFilters), entity);
  } catch (cause) {
    if (!(cause instanceof FilterExpressionError)) throw cause;
    error = cause;
  }
  return {
    entity,
    active,
    presetId,
    conflict,
    error,
    blocked: conflict || error !== null,
    editorValue: active ? value : andLegacyFilters(null, legacy),
    expression,
    outerFilters,
  };
}

export type BrowseFilter = ReturnType<typeof readBrowseFilter>;
