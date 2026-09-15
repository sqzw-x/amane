# 前端架构

> 入口: `web/src/`. 本文只写信息架构、跨模块约定与易回归点; 段内细节见所指向的文件与注释.

## 信息架构

导航按域拆分, 不是扁平功能列表. 首页 `/` 是产品对话 (Amane), 不是片库.

| 域 | 路由重心 |
|----|---------|
| **Browse** | `/` 对话; `/meta` 片库; `/actors` 演员; `/catalog/...` 分类词云; `/saved-queries/$queryId` 查询结果; `/feeds` 阅读器 (`?feed=` / `?group=`) |
| **Manage** | `/libraries` `/libraries/$id`; `/plugins` 来源插件; `/feeds/sources` 订阅源 |
| **Ops** | `/tasks` `/schedules` `/logs` |
| **Settings** | `/settings` (`?section=` 分组; Schema 表单) |

路由组件与协作文件的对应见下表.

| 路由 | 页面文件 | 主要协作文件 |
|----|---------|------|
| `/` | `routes/index.tsx` | `components/agent/` (`agent-home.tsx` 为对话主体), `lib/agent/` |
| `/meta` | `routes/meta.tsx` + `meta.index.tsx` | `components/media/` (`poster-grid` / `meta-table` / `facet-filter-controls`) |
| `/meta/$metadataId` | `routes/meta.$metadataId.tsx` | `components/media/playback-panel.tsx` → `playback-player.tsx`, `comment-section.tsx` → `comment-body.tsx`, `lib/media/comment-segments.ts` |
| `/actors` | `routes/actors.tsx` + `actors.index.tsx` | `components/media/actor-grid.tsx` / `actor-table.tsx`, `lib/actors/browse.ts` |
| `/actors/$actorId` | `routes/actors.$actorId.tsx` | `components/media/actor-card.tsx` / `actor-edit-dialog.tsx`, `hooks/use-facet-identity-actions.ts` |
| `/catalog/...` | `routes/catalog.tsx` + `catalog.index.tsx` + `catalog.$kind.tsx` + `catalog.$kind_.$facetId.tsx` | `components/media/catalog-facet-table.tsx`, `facet-rules-panel.tsx` |
| `/saved-queries/$queryId` | `routes/saved-queries.$queryId.tsx` | `lib/agent/saved-query.ts` |
| `/libraries` | `routes/libraries.tsx` + `libraries.index.tsx` | `components/library/library-form.tsx` |
| `/libraries/$libraryId` | `routes/libraries.$libraryId.tsx` | `components/library/` (文件表与扫描 / 整理入口) |
| `/plugins` | `routes/plugins.tsx` | `components/plugins/`, `components/path-picker/` |
| `/feeds` | `routes/feeds.tsx` + `feeds.index.tsx` | `components/feeds/feed-reader.tsx` / `feed-sidebar.tsx`, `lib/feeds/` |
| `/feeds/sources` | `routes/feeds.sources.tsx` | `components/feeds/feed-sources-table.tsx`, `lib/feeds/opml.ts` |
| `/tasks` | `routes/tasks.tsx` | `components/task/task-tree.tsx`, `lib/task/` |
| `/schedules` | `routes/schedules.tsx` | `components/cron-picker/`, `lib/cron.ts` |
| `/logs` | `routes/logs.tsx` | `components/log/`, `stores/logs.ts` |
| `/settings` | `routes/settings.tsx` | `components/schema-form/`, `hooks/use-config.ts` |

路由由 `@tanstack/router-vite-plugin` 从 `routes/` 生成 (`routeTree.gen.ts` 不手改): 文件名的点号即路径层级, 需要独立 URL 又共享布局的一层写成 `xxx.tsx` + `xxx.index.tsx`, 叶页与父级同段时用尾随 `_`.

片库 / 演员 / 分类无独立「管理」路由, list 视图才有多选与破坏性操作; Feed 相反, 阅读器与源表不共用布局. 侧栏「全部 / 未分组」不经深链进入. `/feeds` 的选中态必须 `activeOptions.exact` 且忽略 search, 否则打开 `/feeds/sources` 时「订阅」也会亮.

**入口分流**: 非演员实体进 `/catalog/$kind/$facetId`, 演员进 `/actors/$actorId` (演员不进入 `/catalog`); `FacetBadge` 默认深链分类, 筛选深链 `/meta`.

**评论**: 排序与编辑态只在组件内, 不写地址栏; 时间戳跳转把秒数写进 `t`, 该次导航必须 `resetScroll: false` — 路由默认在位置提交后把页面滚动到顶部. 评论与关联文件在 `lg` (1200px) 以上并排各占一半 — **断点不能降到 `md`**, 再窄时文件路径会明显截断. 其余样式理由见 `components/media/comment-section.module.css` 的注释.

影片详情: 用户标签与刮削标签分栏; 加减菜单一次提交多名 — `POST /api/metadata/batch/user-tags` 是多影片 × 单标签, 不允许一次挂多个. 演员浏览经由 `/api/actors`, 身份治理仍调用 `/api/facets/actor`, 筛选字段的单一事实源是 `lib/actors/browse.ts`.

**播放**: 面板先取来源列表 (不调用插件因此立刻渲染), 流列表按需加载; 两级选择经 `EnumToggle` 平铺 (超过 4 项回落下拉菜单), 切换来源要重置流的选择, 否则会按旧 `key` 探测. 来源顺序由用户在插件页 `PlaybackOrderSection` 维护, 面板按它重排, **位置 0 即默认探测的来源**. 播放窗口常驻且尺寸由比例决定 — **状态变化不得改变外框尺寸**, 否则页面高度突变会把滚动位置夹回顶部. 播放器组件的其余约定集中在 `components/media/playback-player.tsx` 的文件注释里.

## 列表分页

**list** 视图与订阅源、库文件表采用 `BrowsePageShell fill`: 标题 / 搜索不滚, 剩余高度交给 children; 视口高度取 `APP_SHELL_MAIN_HEIGHT` (`components/layout/app-shell-metrics.ts`), 不允许再手写一份 calc — 该常量由 AppShell 写入 `:root` 的 `--app-shell-header-height` 与 `--app-shell-padding` 推导.

`ListToolbar` 是表体壳: 顶栏不滚, 表体内滚, **唯一**分页固定于视口底, 翻页把表体滚回顶部; 它的 overflow 区要求父级有界高度. `grid` / `cloud` 禁止 fill — 演员墙用 `VirtuosoGrid` + `useWindowScroll`.

图标按钮的悬浮说明必须经 `HintedActionIcon` (Mantine Tooltip), 不允许 HTML `title`; disabled 控件须再包一层可接收指针事件的元素.

## 窄屏

导航栏在 `sm` (768px) 折叠, 页面内部布局 (三列标题行、并排分栏、内容侧栏) 一律用 `md` (992px): 768px 上导航栏刚展开, 内容宽度反而收窄, 跟随 `sm` 会同时触发挤压与换行. 新增断点只允许落在 `base` 至 `md`, `lg` 以上是已验收的宽屏基线, 不得改动.

显隐用 `visibleFrom` / `hiddenFrom` (生成 `display: none !important`, `Table.Th` / `Table.Td` 同样支持); 必须更换控件形态时用 `useNarrowViewport()` (`hooks/use-narrow-viewport.ts`), 例如枚举超过 4 项回退 `Select`、行内操作收进 `Menu`. `Group` 的 `wrap` 与 `gap` 是 CSS 变量, 不接受响应式对象, 换行写 CSS Module 的 `@media (max-width: 47.99em)`.

钉高页面必须让顶栏 chrome 可折叠: 筛选与批量操作在窄屏收进 `Menu` / `Drawer`, 滚动区给出下界, 外层容器纵向可滚动 — 否则表体被压成 0 高且分页被裁掉. 窄屏侧栏统一采用 `routes/feeds.index.tsx` 的 Drawer 范式: 内容侧 `hiddenFrom`, 抽屉与触发按钮取同一断点.

HTML5 拖拽排序在触屏设备不可用, 有序列表必须在窄屏提供等价入口 (`DraggableChips` 的 `onMove` 渲染上移 / 下移按钮), 拖动只作 `md` 以上的增强.

## Schema 表单

Settings、任务提交、定时创建、metadata 编辑共用 `components/schema-form/`: Pydantic → OpenAPI → FieldRouter; `x-*` 清单见 `schema/types.ts`, 字段控件细节见该目录与字段注释.

dict 的用户 key 是字面量, 不写入 TanStack 点路径, 叶子读写经 `DictEntryScope` 写入 `[key]`, 含 `.` / `[` / `]` 的 key 才能原样保存. Tabs 同时挂载全部条目, 叶子 `id` / `htmlFor` 必须经 `useFieldDomId` 加条目前缀, 否则同名控件互相命中.

`SchemaForm` 双模式 `patch` (dirty 门控, 只提交 diff) / `create` (完整值); dirty 保存条用 `affix` 固定于视口底, 编辑弹窗里必须抬到 Modal 之上, 不允许放进 Modal 表单流. 空值编码统一经 `schema-form/encode.ts` (按 Create / 列 schema 判空), 可增减 key 的 dict 与缺席等价, `x-frozen-keys` 必须保留全部 key; 不允许对着 PATCH partial schema 编码, 手写 Library / Feed 表单经同一个编码器出 body. 任务与定时提交用 `DiscriminatedSchemaForm`; 短枚举共用 `EnumToggle` (窄屏超过 4 项自动回退 `Select`), 定时 cron 用 `CronPicker` (产出 5-field, 与后端 croniter 一致).

## 对话通道

`/` 的实现边界见 [agent.md](agent.md). 前端两点: 对话经由 `lib/agent/sse.ts` 手写 SSE, 不经 hey-api 也不经 `/ws`; 请求统一经由 `lib/api-token.ts` 的 `apiFetch` — **纯透传**, 只把 401 转成登录门, 鉴权用 HttpOnly cookie. 不允许在包装里重建 headers: 传入单个 `Request` 时 `init.headers` 会整体替换, 丢掉 `Content-Type` 后 FastAPI 会 422.

## 实时与状态

`lib/connection.ts` 是模块级 WS 单例 (指数退避 + 断连轮询降级), 入站经 `parseWSEvent` 窄化, 不识别的 type 丢弃. 任务查询的失效必须用同形对象前缀 (`[{ _id: "getTaskChildren" }]`), hey-api 生成的 query key 不是字符串前缀.

| 数据 | 存储位置 |
|------|--------|
| 列表 / 详情 | TanStack Query (invalidate) |
| 高频流 (进度 / 日志) | Zustand |
| 对话增量 | SSE (与 WS 正交) |
| 导航态 (筛选 / 排序 / page / view) | URL search |
| 列表密度 / 列宽 / 主题 / 播放源顺序 | Zustand (`amane-web`) |

虚拟滚动的落底、短列表排布与 `followOutput` 约定见 `/logs` 与 `/tasks` 路由及其组件注释. OpenAPI 字符串联合若需运行时迭代, 集中放置于 `lib/exhaustive-maps.ts`, 禁止在路由里再手抄一份.

## 图片

外站图经由 `/api/resources/proxy` (`proxyImageUrl`); `<img>` 不能带 Authorization, 鉴权靠 cookie. 裁切基准是 `thumb_urls[0]` 对应的 Resource 本地文件, 只提交像素坐标, 不上传 blob.

相位水印是 CSS overlay (`FilePhaseOverlay`), 读列表聚合的 `file_phase`, 不修改 Resource 像素; 表格与文件列表仍用 `FilePhaseBadges`. `FanartLightbox` 必须 Portal 到 `document.body` — Modal 打开态的 `transform` 会把 `position: fixed` 的包含块变成弹窗, 大图被 content 的 `overflow-y: auto` 裁切.

外链图片的并发限流 (为什么限、阈值与观测判据) 见 `components/media/proxy-image.tsx` 与 `lib/image-loader.ts` 的注释.

## `lib/` 分层

根目录只放跨域工具 (`confirm` / `exhaustive*` / `api-token` / `connection` / `utils` 等); 只服务一个产品域的模块纳入 `lib/<domain>/` (`actors` / `feeds` / `agent` / `task` / `media`), 不允许再往根上堆叠带域前缀的文件. 不设根 barrel, 调用方直引文件.

## 工程入口

`just generate` → OpenAPI 导出 + TS client 生成 (`web/src/client/` 为产物, 不手改); SPA 产物 `web/dist` 由 `src/amane/api/spa.py` 挂载.

路由组件由 `tanstackRouter()` 的 `autoCodeSplitting` 拆为独立 chunk: 只服务单个路由的重型依赖必须留在该路由的 chunk, 从共享模块导入会把它移回入口 chunk. `tanstackRouter()` 必须排在 JSX 转换插件之前, 顺序颠倒时构建失败. 类型、i18n 与 lint 的硬性约束由 `pnpm check` (tsc / oxlint / oxfmt / i18next extract) 把关.
