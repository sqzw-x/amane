import type { ParseKeys } from "i18next";
import type { SavedQueryEntity } from "@/client/types.gen";

export const SAVED_QUERY_ENTITY_LABEL_KEY = {
  metadata: "entityMetadata",
  actor: "entityActor",
  data: "entityData",
} as const satisfies Record<SavedQueryEntity, ParseKeys<"savedQueries">>;

export const SAVED_QUERY_BADGE_COLOR = {
  metadata: "blue",
  actor: "pink",
  data: "gray",
} as const satisfies Record<SavedQueryEntity, string>;
