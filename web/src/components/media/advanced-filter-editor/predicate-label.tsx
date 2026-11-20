import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { getFacetOptions, listLibrariesOptions } from "@/client/@tanstack/react-query.gen";
import type { FacetKind } from "@/client/types.gen";
import type { FilterEntity, FilterRule } from "@/lib/filter-expression";
import { filterFieldFor } from "./fields";
import type { FilterLibrary } from "./value-input";

function FacetName({ kind, id }: { kind: FacetKind; id: number }) {
  const query = useQuery(getFacetOptions({ path: { kind, facet_id: id } }));
  return <>{query.data?.name ?? `#${id}`}</>;
}

function LibraryName({ id, libraries }: { id: number; libraries?: readonly FilterLibrary[] }) {
  const query = useQuery({ ...listLibrariesOptions(), enabled: libraries === undefined });
  return <>{(libraries ?? query.data?.items)?.find((item) => item.id === id)?.name ?? `#${id}`}</>;
}

export function PredicateLabel({
  entity,
  rule,
  libraries,
}: {
  entity: FilterEntity;
  rule: FilterRule;
  libraries?: readonly FilterLibrary[];
}) {
  const { t } = useTranslation("metadata");
  const field = filterFieldFor(entity, rule.field);
  let value = <>{String(rule.value)}</>;
  if (field?.control === "facet" && typeof rule.value === "number")
    value = <FacetName kind={field.kind} id={rule.value} />;
  else if (field?.control === "library" && typeof rule.value === "number")
    value = <LibraryName id={rule.value} libraries={libraries} />;
  else if (field?.control === "enum") {
    const option = field.options.find((item) => item.value === rule.value);
    value = <>{option?.labelKey ? t(option.labelKey) : String(rule.value)}</>;
  } else if (typeof rule.value === "boolean")
    value = <>{t(rule.value ? "search.yes" : "search.no")}</>;
  else if (field?.control === "text") value = <>“{String(rule.value)}”</>;
  return (
    <>
      {field ? t(field.labelKey) : rule.field} {t(`advancedFilter.operators.${rule.operator}`)}{" "}
      {value}
    </>
  );
}
