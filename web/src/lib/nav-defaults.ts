/**
 * 侧栏列表按钮的默认参数.
 *
 * 默认只在从侧栏进入时注入 URL: 侧栏条目按各自的 search schema 构造 search. URL 仍是列表态的唯一
 * 事实来源, 页面内修改或清除筛选不会被默认重新写入, 因此默认不需要表示「本次已清空」的哨兵值.
 *
 * 白名单排除当次浏览的临时态: 搜索词、页码、agent 深链, 以及订阅阅读器的去重开关; 订阅的寻址参数
 * (`feed` / `group`) 由阅读器侧栏在页内给出. 分页大小是独立偏好, 存在 `stores/ui.ts` 的 `pageSizes`.
 *
 * 持久化格式的读取必须经这三份 schema 校验: 后端枚举或排序字段变化会让旧值非法, 非法项整份丢弃
 * 而不是逐字段兜底 —— 后者需要反射 schema 的字段清单, 代价高于收益, 用户重新设置一次可恢复.
 */

import type { MouseEvent } from "react";
import { z } from "zod";
import { actorsBrowseSearchSchema, type ActorsBrowseSearch } from "@/lib/actors/browse";
import { feedsSearchSchema, type FeedsSearch } from "@/lib/feeds/browse";
import { metaSearchSchema, type MetaSearch } from "@/lib/media/browse";

/** 带列表参数的侧栏条目; 分类入口是种类索引页, 没有可承载的参数. */
export type NavListKey = "meta" | "actors" | "feeds";

export const metaListDefaultsSchema = metaSearchSchema.omit({
  q: true,
  page: true,
  saved_query_id: true,
});
export const actorListDefaultsSchema = actorsBrowseSearchSchema.omit({
  q: true,
  page: true,
  saved_query_id: true,
});
export const feedsListDefaultsSchema = feedsSearchSchema.omit({
  feed: true,
  group: true,
  q: true,
  page: true,
  nodedupe: true,
});

export type MetaListDefaults = z.infer<typeof metaListDefaultsSchema>;
export type ActorListDefaults = z.infer<typeof actorListDefaultsSchema>;
export type FeedListDefaults = z.infer<typeof feedsListDefaultsSchema>;

/** 侧栏各条目的默认参数; 缺失表示该条目没有默认. */
export type NavListDefaults = {
  meta: MetaListDefaults;
  actors: ActorListDefaults;
  feeds: FeedListDefaults;
};

/** 写入用的判别联合: 键与值不能错配. */
export type NavListDefaultsUpdate = {
  [K in NavListKey]: { key: K; value: NavListDefaults[K] };
}[NavListKey];

/*
 * 由页面 search 取默认参数.
 *
 * 路由的 validateSearch 抛错时返回未校验的原值, URL 里的非法枚举因此会原样到达页面: 取默认参数必须
 * 兜底, 否则页面会在渲染期抛错. 取不到白名单就退回 schema 自身的默认值.
 */

export function metaListDefaults(search: MetaSearch): MetaListDefaults {
  return metaListDefaultsSchema.safeParse(search).data ?? metaListDefaultsSchema.parse({});
}

export function actorListDefaults(search: ActorsBrowseSearch): ActorListDefaults {
  return actorListDefaultsSchema.safeParse(search).data ?? actorListDefaultsSchema.parse({});
}

export function feedListDefaults(search: FeedsSearch): FeedListDefaults {
  return feedsListDefaultsSchema.safeParse(search).data ?? feedsListDefaultsSchema.parse({});
}

/** 写入一项默认参数; 逐键分派是为了不把联合键写进对象字面量 (会被推断为索引签名). */
export function withListDefault(
  current: Partial<NavListDefaults>,
  update: NavListDefaultsUpdate,
): Partial<NavListDefaults> {
  switch (update.key) {
    case "meta":
      return { ...current, meta: update.value };
    case "actors":
      return { ...current, actors: update.value };
    case "feeds":
      return { ...current, feeds: update.value };
  }
}

/** 清除一项默认参数. */
export function withoutListDefault(
  current: Partial<NavListDefaults>,
  key: NavListKey,
): Partial<NavListDefaults> {
  switch (key) {
    case "meta":
      return { ...current, meta: undefined };
    case "actors":
      return { ...current, actors: undefined };
    case "feeds":
      return { ...current, feeds: undefined };
  }
}

/**
 * 侧栏条目的普通左键跳转.
 *
 * 列表条目要带上该页的默认参数, 而 Mantine 的多态 props 把 `component={Link}` 的 `search` 收窄成
 * `never`, 参数只能经路由跳转送入. 中键与修饰键直接返回, 由浏览器按链接地址打开 —— 该地址不带默认参数.
 */
export function navItemClick(
  event: MouseEvent<HTMLElement>,
  onNavigate: (() => void) | undefined,
  go: () => void,
): void {
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
    return;
  }
  event.preventDefault();
  onNavigate?.();
  go();
}
