import type { ParseKeys } from "i18next";
import type { SavedQueryEntity } from "@/client/types.gen";

/** 实体交付的片库 / 演员深链; data 实体无实体页, 返回 null. switch 穷尽由类型系统强制. */
export function savedQueryBrowseHref(item: {
  id: number;
  entity: SavedQueryEntity;
}): string | null {
  switch (item.entity) {
    case "metadata":
      return `/meta?saved_query_id=${item.id}`;
    case "actor":
      return `/actors?saved_query_id=${item.id}`;
    case "data":
      return null;
  }
}

export const SAVED_QUERY_ENTITY_LABEL_KEY = {
  metadata: "entityMetadata",
  actor: "entityActor",
  data: "entityData",
} as const satisfies Record<SavedQueryEntity, ParseKeys<"savedQueries">>;

/** 「打开」按钮翻译 key; metadata/actor 为深链, data 为数据页. */
export const SAVED_QUERY_OPEN_LABEL_KEY = {
  metadata: "openMeta",
  actor: "openActors",
  data: "openData",
} as const satisfies Record<SavedQueryEntity, ParseKeys<"savedQueries">>;

export const SAVED_QUERY_BADGE_COLOR = {
  metadata: "blue",
  actor: "pink",
  data: "gray",
} as const satisfies Record<SavedQueryEntity, string>;
