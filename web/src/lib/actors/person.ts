import type { ActorUpdateRequest } from "@/client/types.gen";

/**
 * 清空人物档案的 PATCH 体.
 * 保留 name (身份) 与 gender (刮削路由); raw / field_sources 不在对外可写字段, 普通刮削仍可能从缓存填回.
 */
export const CLEARED_ACTOR_PERSON_PATCH: ActorUpdateRequest = {
  aliases: [],
  birthday: null,
  birthplace: null,
  height: null,
  bust: null,
  waist: null,
  hip: null,
  cup: null,
  overview: null,
  tagline: null,
  image_urls: [],
  provider_ids: {},
  source_urls: {},
};
