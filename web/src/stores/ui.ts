import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { ColumnWidths } from "@/hooks/use-resizable-columns";
import {
  DEFAULT_METADATA_SORT_PREFERENCE,
  type MetadataSortPreference,
  metadataSortPreferenceSchema,
} from "@/lib/media/browse";
import {
  actorListDefaultsSchema,
  feedsListDefaultsSchema,
  type NavListDefaults,
  type NavListDefaultsUpdate,
  type NavListKey,
  metaListDefaultsSchema,
  withoutListDefault,
  withListDefault,
} from "@/lib/nav-defaults";
import {
  clampPageSize,
  DEFAULT_PAGE_SIZES,
  type PageSize,
  type PageSizeKey,
} from "@/lib/page-size";
import type { LogLevel } from "./logs";

type Theme = "light" | "dark" | "auto";
type Language = "zh-CN" | "en";

const STORAGE_KEY = "amane-web";

/** 与 MetaTable 列 key 对齐. */
export type MetaTableColumnKey =
  | "number"
  | "title"
  | "studio"
  | "release"
  | "updated_at"
  | "file_count"
  | "score";

/** 与 ActorTable 列 key 对齐. */
export type ActorTableColumnKey =
  | "name"
  | "count"
  | "gender"
  | "birthday"
  | "height"
  | "bust"
  | "waist"
  | "hip"
  | "cup"
  | "has_image"
  | "updated_at";

interface UIState {
  navbarCollapsed: boolean;
  theme: Theme;
  language: Language;
  autoScroll: boolean;
  logLevelFilter: LogLevel[];
  pageSizes: Record<PageSizeKey, PageSize>;
  /** 片库 list 列宽覆盖; 缺省列采用组件内默认值. */
  metaColumnWidths: ColumnWidths<MetaTableColumnKey>;
  /** 演员 list 列宽覆盖. */
  actorColumnWidths: ColumnWidths<ActorTableColumnKey>;
  /**
   * 播放源的用户顺序, 元素是来源 ID.
   *
   * 顺序只影响展示: 后端仍按来源 ID 返回, 详情页面板与插件页都按这里排. 不在表里的来源保持
   * 后端顺序追加在后, 因此新装与重装的来源落末位; 卸载不清理, 装回来仍在原位.
   */
  playbackSourceOrder: string[];
  /**
   * 侧栏「片库 / 演员 / 订阅」条目携带的默认列表参数.
   *
   * 只在从侧栏进入时注入 URL, 页面内不再引用; 读取经 `lib/nav-defaults.ts` 的 schema 校验, 非法项丢弃.
   */
  listDefaults: Partial<NavListDefaults>;
  /**
   * 演员详情页出演作品的排序记忆.
   *
   * 该页没有其它导航态, 排序不写地址栏, 由这里在会话之间保留; 片库排序仍以 URL 为准.
   */
  actorWorksSort: MetadataSortPreference;
  toggleNavbar: () => void;
  setNavbarCollapsed: (collapsed: boolean) => void;
  setTheme: (theme: Theme) => void;
  setLanguage: (language: Language) => void;
  setAutoScroll: (autoScroll: boolean) => void;
  setLogLevelFilter: (levels: LogLevel[]) => void;
  setPageSize: (key: PageSizeKey, size: PageSize) => void;
  setMetaColumnWidths: (widths: ColumnWidths<MetaTableColumnKey>) => void;
  setActorColumnWidths: (widths: ColumnWidths<ActorTableColumnKey>) => void;
  setPlaybackSourceOrder: (order: string[]) => void;
  setListDefault: (update: NavListDefaultsUpdate) => void;
  clearListDefault: (key: NavListKey) => void;
  setActorWorksSort: (sort: MetadataSortPreference) => void;
}

export const useUIStore = create<UIState>()(
  persist(
    (set) => ({
      navbarCollapsed: false,
      theme: "dark",
      language: "zh-CN",
      autoScroll: true,
      logLevelFilter: [],
      pageSizes: { ...DEFAULT_PAGE_SIZES },
      metaColumnWidths: {},
      actorColumnWidths: {},
      playbackSourceOrder: [],
      listDefaults: {},
      actorWorksSort: { ...DEFAULT_METADATA_SORT_PREFERENCE },
      toggleNavbar: () => set((s) => ({ navbarCollapsed: !s.navbarCollapsed })),
      setNavbarCollapsed: (collapsed) => set({ navbarCollapsed: collapsed }),
      setTheme: (theme) => set({ theme }),
      setLanguage: (language) => set({ language }),
      setAutoScroll: (autoScroll) => set({ autoScroll }),
      setLogLevelFilter: (levels) => set({ logLevelFilter: levels }),
      setPageSize: (key, size) =>
        set((s) => ({
          pageSizes: { ...s.pageSizes, [key]: size },
        })),
      setMetaColumnWidths: (widths) => set({ metaColumnWidths: widths }),
      setActorColumnWidths: (widths) => set({ actorColumnWidths: widths }),
      setPlaybackSourceOrder: (order) => set({ playbackSourceOrder: order }),
      setListDefault: (update) =>
        set((state) => ({ listDefaults: withListDefault(state.listDefaults, update) })),
      clearListDefault: (key) =>
        set((state) => ({ listDefaults: withoutListDefault(state.listDefaults, key) })),
      setActorWorksSort: (sort) => set({ actorWorksSort: sort }),
    }),
    {
      name: STORAGE_KEY,
      merge: (persisted, current) => {
        const p = persisted as Partial<UIState> | undefined;
        const pageSizes = { ...DEFAULT_PAGE_SIZES } as Record<PageSizeKey, PageSize>;
        for (const key of Object.keys(DEFAULT_PAGE_SIZES) as PageSizeKey[]) {
          const raw = p?.pageSizes?.[key];
          pageSizes[key] = clampPageSize(
            key,
            typeof raw === "number" ? raw : DEFAULT_PAGE_SIZES[key],
          );
        }
        return {
          ...current,
          ...p,
          pageSizes,
          metaColumnWidths: p?.metaColumnWidths ?? {},
          actorColumnWidths: p?.actorColumnWidths ?? {},
          playbackSourceOrder: p?.playbackSourceOrder ?? [],
          // 持久化值未经校验: 逐项过 schema, 非法项丢弃.
          listDefaults: {
            meta: metaListDefaultsSchema.safeParse(p?.listDefaults?.meta).data,
            actors: actorListDefaultsSchema.safeParse(p?.listDefaults?.actors).data,
            feeds: feedsListDefaultsSchema.safeParse(p?.listDefaults?.feeds).data,
          },
          actorWorksSort: metadataSortPreferenceSchema.safeParse(p?.actorWorksSort).data ?? {
            ...DEFAULT_METADATA_SORT_PREFERENCE,
          },
        };
      },
    },
  ),
);
