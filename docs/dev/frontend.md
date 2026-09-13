# 前端架构

> 入口: `web/src/`. 本文只负责定位: 改哪一块该看哪些文件. 契约与约束写在对应文件的注释里, 本文不重复.

## 路由 → 文件

| 路由 | 页面文件 | 主要协作文件 |
|----|---------|------|
| `/` | `routes/index.tsx` | `components/agent/` (`agent-home.tsx` 为对话主体), `lib/agent/` |
| `/meta` | `routes/meta.tsx` + `routes/meta.index.tsx` | `components/media/` (`poster-grid` / `meta-table` / `facet-filter-controls`) |
| `/meta/$metadataId` | `routes/meta.$metadataId.tsx` | `components/media/playback-panel.tsx` → `playback-player.tsx`, `comment-body.tsx`, `lib/media/comment-timestamps.ts` |
| `/actors` | `routes/actors.tsx` + `routes/actors.index.tsx` | `components/media/actor-grid.tsx` / `actor-table.tsx`, `lib/actors/browse.ts` |
| `/actors/$actorId` | `routes/actors.$actorId.tsx` | `components/media/actor-card.tsx` / `actor-edit-dialog.tsx` / `meta-table.tsx`, `hooks/use-facet-identity-actions.ts` |
| `/catalog/...` | `routes/catalog.tsx` + `catalog.index.tsx` + `catalog.$kind.tsx` + `catalog.$kind_.$facetId.tsx` | `components/media/catalog-facet-table.tsx`, `facet-rules-panel.tsx` |
| `/saved-queries/$queryId` | `routes/saved-queries.$queryId.tsx` | `lib/agent/saved-query.ts` |
| `/libraries` | `routes/libraries.tsx` + `routes/libraries.index.tsx` | `components/library/library-form.tsx` |
| `/libraries/$libraryId` | `routes/libraries.$libraryId.tsx` | `components/library/` (文件表与扫描/整理入口) |
| `/plugins` | `routes/plugins.tsx` | `components/plugins/`, `components/path-picker/` |
| `/feeds` | `routes/feeds.tsx` + `routes/feeds.index.tsx` | `components/feeds/feed-reader.tsx` / `feed-sidebar.tsx`, `lib/feeds/` |
| `/feeds/sources` | `routes/feeds.sources.tsx` | `components/feeds/feed-sources-table.tsx`, `lib/feeds/opml.ts` |
| `/tasks` | `routes/tasks.tsx` | `components/task/task-tree.tsx`, `lib/task/` |
| `/schedules` | `routes/schedules.tsx` | `components/cron-picker/`, `lib/cron.ts` |
| `/logs` | `routes/logs.tsx` | `components/log/`, `stores/logs.ts` |
| `/settings` | `routes/settings.tsx` | `components/schema-form/`, `hooks/use-config.ts` |

路由由 `@tanstack/router-vite-plugin` 从 `routes/` 生成 (`routeTree.gen.ts` 不手改). 文件名的点号即路径层级; 需要独立 URL 又共享布局的一层写成 `xxx.tsx` + `xxx.index.tsx`, 叶页与父级同段时用尾随 `_`.

## 跨页共用

- 组件按产品域分目录 (`components/<domain>/`), 跨域复用件放 `components/common/`: 列表壳 (`browse-page-shell` / `list-toolbar` / `list-pagination` / `selection-bar` / `sortable-th` / `sort-menu` / `page-size-select`)、通用控件 (`enum-toggle` / `hinted-action-icon` / `unsaved-changes-bar` / `infinite-scroll-sentinel`).
- 布局与视口高度: `components/layout/app-shell.tsx` + `app-shell-metrics.ts`.
- 列表与详情走 TanStack Query (`client/` 为 generate 产物); 高频流 (进度 / 日志) 与界面偏好走 Zustand (`stores/`); 导航态 (筛选 / 排序 / page / view) 在 URL search, schema 定义在各自路由文件.
- `lib/` 分层: 根目录只放跨域工具, 单域模块进 `lib/<domain>/`. 不设 barrel, 调用方直引文件.

## 工程入口

- `just generate` → OpenAPI 导出 + TS client 生成 (`web/src/client/`, 不手改); SPA 产物 `web/dist` 由 `src/amane/api/spa.py` 挂载.
- 类型、i18n 与 lint 的硬性约束由 `pnpm check` (tsc / oxlint / oxfmt / i18next extract) 把关, 规则出处见 `web/package.json` 的脚本.

## 相关文档

- 后端与 API 契约: [index.md](index.md)
- 对话通道后端: [agent.md](agent.md)
- 订阅源数据流: [feeds.md](feeds.md)
- 路径模板语法: [data-model.md](data-model.md)
