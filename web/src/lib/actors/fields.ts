import type { ParseKeys } from "i18next";
import type { ActorField } from "@/client/types.gen";
import { assertExhaustive, exhaustiveRecord } from "@/lib/exhaustive";

/** 顺序即锁菜单显示顺序. */
export const LOCKABLE_ACTOR_FIELDS = [
  "gender",
  "birthday",
  "birthplace",
  "height",
  "bust",
  "waist",
  "hip",
  "cup",
  "overview",
  "tagline",
  "image_urls",
  "aliases",
] as const satisfies readonly ActorField[];

assertExhaustive<ActorField>()(LOCKABLE_ACTOR_FIELDS);

/** ActorField → metadata 命名空间下的字段标签 key. */
export const ACTOR_FIELD_LABEL_KEY = exhaustiveRecord<ActorField>()({
  gender: "browse.person.gender",
  birthday: "browse.person.birthday",
  birthplace: "browse.person.birthplace",
  height: "browse.person.height",
  bust: "browse.person.bust",
  waist: "browse.person.waist",
  hip: "browse.person.hip",
  cup: "browse.person.cup",
  overview: "browse.person.overview",
  tagline: "browse.person.tagline",
  image_urls: "actors.imagesEdit",
  aliases: "browse.person.aliases",
} as const satisfies Record<ActorField, ParseKeys<"metadata">>);
