import type { ParseKeys } from "i18next";
import type { MetadataField } from "@/client/types.gen";
import { assertExhaustive, exhaustiveRecord } from "@/lib/exhaustive";

/** 顺序即锁菜单显示顺序. */
export const LOCKABLE_FIELDS = [
  "title",
  "actors",
  "studio",
  "publisher",
  "release",
  "runtime",
  "tags",
  "series",
  "plot",
  "directors",
  "poster_urls",
  "thumb_urls",
  "trailer_urls",
  "extrafanart",
  "score",
] as const satisfies readonly MetadataField[];

assertExhaustive<MetadataField>()(LOCKABLE_FIELDS);

/** MetadataField → metadata 命名空间下的字段标签 key. */
export const FIELD_LABEL_KEY = exhaustiveRecord<MetadataField>()({
  title: "detail.fields.title",
  plot: "detail.fields.plot",
  actors: "detail.fields.actors",
  directors: "detail.fields.directors",
  tags: "detail.fields.tags",
  series: "detail.fields.series",
  release: "detail.fields.release",
  runtime: "detail.fields.runtime",
  publisher: "detail.fields.publisher",
  studio: "detail.fields.studio",
  poster_urls: "detail.fields.poster",
  thumb_urls: "detail.fields.thumb",
  trailer_urls: "detail.fields.trailer",
  extrafanart: "detail.fields.extrafanart",
  score: "detail.fields.score",
} as const satisfies Record<MetadataField, ParseKeys<"metadata">>);
