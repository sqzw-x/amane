import { listActorsQueryKey, listMetadataQueryKey } from "@/client/@tanstack/react-query.gen";
import type { ActorSearchRequest, MetadataSearchRequest } from "@/client/types.gen";

/** POST searches share the GET list prefix so existing mutations invalidate both result sets. */
export function metadataAdvancedFilterQueryKey(
  view: "grid" | "list",
  filter: unknown,
  body: Omit<MetadataSearchRequest, "offset" | "limit">,
  limit: number,
  offset: number | null = null,
) {
  return [...listMetadataQueryKey(), "expression", view, filter, body, limit, offset];
}

export function actorAdvancedFilterQueryKey(
  view: "grid" | "list",
  filter: unknown,
  body: Omit<ActorSearchRequest, "offset" | "limit">,
  limit: number,
  offset: number | null = null,
) {
  return [...listActorsQueryKey(), "expression", view, filter, body, limit, offset];
}
