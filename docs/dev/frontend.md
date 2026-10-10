# 前端架构

> 入口: `web/src/`. 本文只写信息架构、跨模块约定与易回归点; 段内细节见所指向的文件与注释.

## 信息架构

导航按域拆分, 不是扁平功能列表. 首页 `/` 是产品对话 (Amane), 不是片库.

| 域 | 路由 | 页面文件 | 主要协作文件 |
|----|------|---------|-------------|
| **Browse** | `/` | `routes/index.tsx` | `components/agent/` (`agent-home.tsx` 为对话主体), `lib/agent/` |
| **Browse** | `/meta` | `routes/meta.tsx` + `meta.index.tsx` | `components/media/` (`poster-grid` / `meta-table` / `facet-filter-controls`), `lib/media/browse.ts` |
| **Browse** | `/meta/$metadataId` | `routes/meta.$metadataId.tsx` | `components/media/playback-panel.tsx` → `playback-player.tsx`, `comment-section.tsx` → `comment-body.tsx`, `lib/media/comment-segments.ts`, `lib/media/metadata-fields.ts`, `components/media/field-lock.tsx` |
| **Browse** | `/actors` | `routes/actors.tsx` + `actors.index.tsx` | `components/media/actor-grid.tsx` / `actor-table.tsx`, `lib/actors/browse.ts` |
| **Browse** | `/actors/$actorId` | `routes/actors.$actorId.tsx` | `components/media/actor-card.tsx` / `actor-edit-dialog.tsx` / `field-lock.tsx`, `lib/actors/fields.ts`, `hooks/use-facet-identity-actions.ts` |
| **Browse** | `/catalog/...` | `routes/catalog.tsx` + `catalog.index.tsx` + `catalog.$kind.tsx` + `catalog.$kind_.$facetId.tsx` | `components/media/catalog-facet-table.tsx`, `facet-rules-panel.tsx` |
| **Browse** | `/saved-queries` | `routes/saved-queries.index.tsx` | `components/saved-query/`, `lib/saved-query/` |
| **Browse** | `/saved-queries/$queryId` | `routes/saved-queries.$queryId.tsx` | `components/saved-query/`, `lib/saved-query/` |
| **Browse** | `/feeds` | `routes/feeds.tsx` + `feeds.index.tsx` | `components/feeds/feed-reader.tsx` / `feed-sidebar.tsx`, `lib/feeds/` |
| **Manage** | `/libraries` | `routes/libraries.tsx` + `libraries.index.tsx` | `components/library/library-form.tsx` |
| **Manage** | `/libraries/$libraryId` | `routes/libraries.$libraryId.tsx` | `components/library/` (文件表与扫描 / 整理入口) |
| **Manage** | `/plugins` | `routes/plugins.tsx` | `components/plugins/`, `components/path-picker/` |
| **Manage** | `/feeds/sources` | `routes/feeds.sources.tsx` | `components/feeds/feed-sources-table.tsx`, `lib/feeds/opml.ts` |
| **Ops** | `/tasks` | `routes/tasks.tsx` | `components/task/task-tree.tsx`, `lib/task/` |
| **Ops** | `/schedules` | `routes/schedules.tsx` | `components/cron-picker/`, `lib/cron.ts` |
| **Ops** | `/logs` | `routes/logs.tsx` | `components/log/`, `stores/logs.ts` |
| **Ops** | `/network` | `routes/network.tsx` | `components/network-check/`, `hooks/use-network-check.ts` |
| **Settings** | `/settings` | `routes/settings.tsx` | `components/schema-form/`, `hooks/use-config.ts` |

路由由 `@tanstack/router-vite-plugin` 从 `routes/` 生成 (`routeTree.gen.ts` 不手改): 文件名的点号即路径层级, 需要独立 URL 又共享布局的一层写成 `xxx.tsx` + `xxx.index.tsx`, 叶页与父级同段时用尾随 `_`. `tanstackRouter()` 必须排在 JSX 转换插件之前, 顺序颠倒时构建失败.

片库 / 演员 / 分类无独立「管理」路由, list 视图才有多选与破坏性操作; Feed 相反, 阅读器与源表不共用布局. 查询预设是管理页例外: `/saved-queries` 只列已保留的预设, 全选与批量操作作用于过滤后的可见集合; 会话内的临时预设只在会话页的预设面板查看. 侧栏「全部 / 未分组」不经深链进入. `/feeds` 的选中态必须 `activeOptions.exact` 且忽略 search, 否则打开 `/feeds/sources` 时「订阅」也会亮.

**列表默认**: 侧栏「片库 / 演员 / 订阅」按钮携带 `lib/nav-defaults.ts` 白名单内的默认列表参数 (排序、筛选、视图; 存于 ui store), 点击时经路由跳转整体替换 search, 因此 URL 仍是列表态的唯一事实来源 —— 页面内清除的筛选不会被默认重新写入. Mantine 的多态 props 把 `component={Link}` 的 `search` 收窄成 `never`, 参数只能这样送入; 中键与修饰键保留浏览器行为, 打开的地址因此不带默认参数.

**入口分流**: 非演员实体进 `/catalog/$kind/$facetId`, 演员进 `/actors/$actorId` (演员不进入 `/catalog`); `FacetBadge` 默认深链分类, 筛选深链 `/meta`.

**收藏**: 影片与演员共用一枚固定名称的用户标签充当收藏夹 (`lib/media/user-tags.ts`); 该名称是存量数据的稳定标识, 界面按当前语言显示, 改名或删除会让两侧同时失去收藏. 详情页标题行的星标 (`components/media/user-tag-star.tsx`) 与标签菜单走同一个挂载端点, 列表页的收藏夹入口则先把布尔参数折算成该标签的 id — 后端只认 id.

**评论**: 排序与编辑态只在组件内, 不写地址栏. 影片详情与演员详情的用户标签与刮削标签分栏; 加减菜单一次提交多名, 但固定为两次请求 — 待新建的名称先经 `POST /api/facets/user_tag` 换成 id, 再与已选 id 一起交给所在页的挂载端点 (影片 `/api/metadata/batch/user-tags`, 演员 `/api/actors/batch/user-tags`), 端点语义见 [api.md](api.md). 演员浏览经由 `/api/actors`, 身份治理仍调用 `/api/facets/actor`, 筛选字段的单一事实源是 `lib/actors/browse.ts`; 演员详情出演作品的排序项与片库同源 (`lib/media/browse.ts` 的 `METADATA_SORT_OPTIONS`), 记忆落在 ui store 的 `actorWorksSort`.

**播放**: 面板先取来源列表 (不调用插件因此立刻渲染), 流列表按需加载; 两级选择经 `EnumToggle` 平铺, 失败信息条给出重试入口. 来源顺序由用户在插件页 `PlaybackOrderSection` 维护, 面板按它重排, **位置 0 即默认探测的来源**. 主机侧契约见 [plugins.md](plugins.md), 手势、全屏与浮层的约定见 `components/media/playback-panel.tsx` 与 `playback-player.tsx` 的文件注释.

## 列表分页

**list** 视图与订阅源、库文件表采用 `BrowsePageShell fill`: 标题 / 搜索不滚, 剩余高度交给 children; 视口高度取 `APP_SHELL_MAIN_HEIGHT` (`components/layout/app-shell-metrics.ts`), 不允许再手写一份 calc. `ListToolbar` 是表体壳, **唯一**分页固定于视口底; `grid` / `cloud` 禁止 fill.

窄屏 (md 以下) 的 `BrowsePageShell` 只剩一行: 标题 + 视图切换 + 筛选入口, 摘要 / 搜索 / 高级筛选面板 / 附属控件 / 右侧动作全进底部面板; 带高级筛选的列表页必须把面板经 `filterPanel` 槽传入, 不允许把 `<Collapse>` 放进 children — 否则开关与面板会分处两地. 同一批控件只渲染一处: 两个断点各一份会让搜索框在 DOM 里存在两个. `SelectionBar` 无选中时在窄屏整条不渲染; 订阅浏览页的 `FeedReader` 用同一形态.

图标按钮的悬浮说明必须经 `HintedActionIcon` (Mantine Tooltip), 不允许 HTML `title`; disabled 控件须再包一层可接收指针事件的元素.

## 窄屏

**视口高度统一经 `--amane-vh`**: 默认 `100vh`, 入口检测到 `dvh` 时替换成 `100dvh`. 禁止在样式或组件属性里写死 `dvh` — Chromium < 108 的 WebView 会整条丢弃含它的声明; 第三方样式 (Mantine) 里的 `dvh` 由构建期替换成同一变量.

**CSS 数学表达式只能经 `style` 写入**, 禁止作为 Mantine 样式属性的值: 样式属性把字符串中的裸数字视为像素并除以 16 转成 rem, 于是 `calc()` 内出现长度相乘, 该声明在计算时判为无效并退回初始值. 只有以 `calc(` / `clamp(` 开头的字符串原样透传.

`style` 与 `mx="auto"` 并用时必须同时给 `w="100%"`: 外层是 flex 列容器时, `margin-inline: auto` 会取消拉伸, 元素宽度塌缩为内容宽.

导航栏在 `sm` (768px) 折叠, 页面内部布局 (三列标题行、并排分栏、内容侧栏) 一律用 `md` (992px): 768px 上导航栏刚展开, 内容宽度反而收窄. 新增断点只允许落在 `base` 至 `md`, `lg` 以上是已验收的宽屏基线, 不得改动.

显隐用 `visibleFrom` / `hiddenFrom`; 必须更换控件形态时用 `useNarrowViewport()` (`hooks/use-narrow-viewport.ts`), 其断点参数必须与同一处显隐用的 `hiddenFrom` 一致 — 不一致会在中间区间同时渲染两套控件. 钉高页面必须让顶栏 chrome 可折叠: 筛选与批量操作在窄屏收进 `Menu` / `Drawer`, 滚动区给出下界, 外层容器纵向可滚动 — 否则表体被压成 0 高且分页被裁掉. 窄屏侧栏统一采用 `routes/feeds.index.tsx` 的 Drawer 范式: 内容侧 `hiddenFrom`, 抽屉与触发按钮取同一断点. HTML5 拖拽排序在触屏设备不可用, 有序列表必须在窄屏提供等价入口.

## Schema 表单

Settings、任务提交、定时创建、metadata 编辑共用 `components/schema-form/`: Pydantic → OpenAPI → FieldRouter; `x-*` 清单见 `schema/types.ts`, 字段控件细节见该目录与字段注释.

`SchemaForm` 双模式 `patch` (dirty 门控, 只提交 diff) / `create` (完整值); dirty 保存条用 `affix` 固定于视口底, 编辑弹窗里必须抬到 Modal 之上. 空值编码统一经 `schema-form/encode.ts` (按 Create / 列 schema 判空), `x-frozen-keys` 必须保留全部 key; 不允许对着 PATCH partial schema 编码, 手写 Library / Feed 表单经同一个编码器出 body. 任务与定时提交用 `DiscriminatedSchemaForm`; 短枚举共用 `EnumToggle`, 定时 cron 用 `CronPicker` (产出 5-field, 与后端 croniter 一致).

## 对话通道

`/` 的实现边界见 [agent.md](agent.md). 前端四点: 对话经 AG-UI (`@ag-ui/client` + `@assistant-ui/react-ag-ui`), 不经 hey-api 也不经 `/ws`, 且 `@ag-ui/client` 必须停留在 0.0.x —— react-ag-ui 的 peer 依赖为 `^0.0.59`, 1.x 调整了 `Message` 内容部件的类型, 与之不兼容; 请求统一经由 `lib/api-token.ts` 的 `apiFetch` (纯透传, 只把 401 转成登录门); 读 assistant-ui 线程状态的 hook (`useAuiState` / `useAgUi*`) 须在 `AssistantRuntimeProvider` 之内 — 持有 provider 的那个组件读不到, 要另起子组件; 展示只认回放行 — `lib/agent/trace.ts::foldTrace` 的产物直接渲染, 本页发起的回合也跟随 `.../agui/events` (从 `after_seq` 接上, 回合未收尾时重开), 运行时的消息虽同步同一份 fold 但不参与渲染, 因此直播与刷新同形, 决不允许另建一份消息状态.

渲染侧的流式提示按「回合是否在跑」推导, 不来自部件状态: 末段正文带光标, 未拿到回执的工具卡片显示转圈, 思考折叠块同理. 用量与回合总计都是该轮消息里的部件, 保留在各轮轮末.

## 实时与状态

`lib/connection.ts` 是模块级 WS 单例 (指数退避 + 断连轮询降级), 入站经 `parseWSEvent` 窄化. 任务查询的失效必须用同形对象前缀 (`[{ _id: "getTaskChildren" }]`), hey-api 生成的 query key 不是字符串前缀.

| 数据 | 存储位置 |
|------|--------|
| 列表 / 详情 | TanStack Query (invalidate) |
| 高频流 (进度 / 日志) | Zustand |
| 对话增量 | AG-UI 事件流 (与 WS 正交) |
| 导航态 (筛选 / 排序 / page / view) | URL search |
| 列表密度 / 列宽 / 主题 / 播放源顺序 / 列表默认参数 / 新增媒体库的创建配置 | Zustand (`amane-web`) |

OpenAPI 字符串联合若需运行时迭代, 集中放置于 `lib/exhaustive-maps.ts`, 禁止在路由里再手抄一份.

## 图片

外站图经由 `/api/resources/proxy` (`proxyImageUrl`); `<img>` 不能带 Authorization, 鉴权靠 cookie. 裁切基准是源图 (海报 `thumb_urls[0]`, 演员头像 `image_urls[0]`) 对应的 Resource 本地文件; 两者共用 `ImageCropDialog`, 只提交像素坐标, 不上传 blob.

相位水印是 CSS overlay (`FilePhaseOverlay`), 读列表聚合的 `file_phase`, 不修改 Resource 像素; 表格与文件列表仍用 `FilePhaseBadges`. `FanartLightbox` 必须 Portal 到 `document.body`. 外链图片的并发限流见 `components/media/proxy-image.tsx` 与 `lib/image-loader.ts` 的注释.

## `lib/` 分层

根目录只放跨域工具 (`confirm` / `exhaustive*` / `api-token` / `connection` / `shell` / `utils` 等); 只服务一个产品域的模块纳入 `lib/<domain>/` (`actors` / `feeds` / `agent` / `task` / `media`), 不允许再往根上堆叠带域前缀的文件. 不设根 barrel, 调用方直引文件.

`lib/shell.ts` 判定是否在壳内并给出壳的动作, 判据与桥的分工见 [android.md](android.md). 壳内顶栏在语言切换旁多一个 `/client` 入口 (手机图标; 不放在侧栏 — 手机上侧栏要先展开才能点到), 页面见 `routes/client.tsx` → `components/shell/client-settings.tsx`; 登录门 (`components/auth/login-gate.tsx`)、错误边界 (`components/error-boundary.tsx`) 与顶栏入口都据此给出「切换服务器」, 非壳环境下整块不渲染. `lib/pull-refresh.ts` 在根上装一次, 把触点处是否还有可向上滚的内容推送至壳. 壳内还会在根元素上写入 `data-amane-shell`, 供样式区分壳环境.

## 工程入口

`just generate` → OpenAPI 导出 + TS client 生成 (`web/src/client/` 为产物, 不手改); SPA 产物 `web/dist` 由 `src/amane/api/spa.py` 挂载, 缓存头与条件请求的约定见该文件. `vite.config.ts` 的 `legacy()` 声明运行期下限 (语法目标 + core-js polyfill), 面向版本落后的 Android WebView — 下限取舍见 [android.md](android.md).

路由组件由 `tanstackRouter()` 的 `autoCodeSplitting` 拆为独立 chunk: 只服务单个路由的重型依赖必须留在该路由的 chunk, 从共享模块导入会把它移回入口 chunk. 类型、i18n 与 lint 的硬性约束由 `pnpm check` (tsc / oxlint / oxfmt / i18next extract) 把关.
