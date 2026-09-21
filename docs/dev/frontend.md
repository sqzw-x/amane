# 前端架构

> 入口: `web/src/`. 本文只写信息架构、跨模块约定与易回归点; 段内细节见所指向的文件与注释.

## 信息架构

导航按域拆分, 不是扁平功能列表. 首页 `/` 是产品对话 (Amane), 不是片库.

| 域 | 路由重心 |
|----|---------|
| **Browse** | `/` 对话; `/meta` 片库; `/actors` 演员; `/catalog/...` 分类词云; `/saved-queries/$queryId` 查询结果; `/feeds` 阅读器 (`?feed=` / `?group=`) |
| **Manage** | `/libraries` `/libraries/$id`; `/plugins` 来源插件; `/feeds/sources` 订阅源 |
| **Ops** | `/tasks` `/schedules` `/logs` |
| **Settings** | `/settings` (`?section=` 分组; Schema 表单); `/client` (壳内的客户端设置, 见 [android.md](android.md)) |

路由组件与协作文件的对应见下表.

| 路由 | 页面文件 | 主要协作文件 |
|----|---------|------|
| `/` | `routes/index.tsx` | `components/agent/` (`agent-home.tsx` 为对话主体), `lib/agent/` |
| `/meta` | `routes/meta.tsx` + `meta.index.tsx` | `components/media/` (`poster-grid` / `meta-table` / `facet-filter-controls`), `lib/media/browse.ts` |
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

**列表默认**: 侧栏「片库 / 演员 / 订阅」按钮携带 `lib/nav-defaults.ts` 白名单内的默认列表参数 (排序、筛选、视图; 存于 ui store), 点击时经路由跳转整体替换 search, 因此 URL 仍是列表态的唯一事实来源 —— 页面内清除的筛选不会被默认重新写入. Mantine 的多态 props 把 `component={Link}` 的 `search` 收窄成 `never`, 参数只能这样送入; 中键与修饰键保留浏览器行为, 打开的地址因此不带默认参数. 参数按各页的 search schema 逐项校验, 非法项丢弃 (后端枚举或排序字段变化后旧值即失效). 存取入口用行内按钮, **不使用浮层菜单**: 窄屏底部面板会在浮层交互后关闭.

**入口分流**: 非演员实体进 `/catalog/$kind/$facetId`, 演员进 `/actors/$actorId` (演员不进入 `/catalog`); `FacetBadge` 默认深链分类, 筛选深链 `/meta`.

**评论**: 排序与编辑态只在组件内, 不写地址栏; 时间戳跳转把秒数写进 `t`, 该次导航必须 `resetScroll: false` — 路由默认在位置提交后把页面滚动到顶部. 评论与关联文件在 `lg` (1200px) 以上并排各占一半 — **断点不能降到 `md`**, 再窄时文件路径会明显截断. 其余样式理由见 `components/media/comment-section.module.css` 的注释.

影片详情: 用户标签与刮削标签分栏; 加减菜单一次提交多名 — `POST /api/metadata/batch/user-tags` 是多影片 × 单标签, 不允许一次挂多个. 演员浏览经由 `/api/actors`, 身份治理仍调用 `/api/facets/actor`, 筛选字段的单一事实源是 `lib/actors/browse.ts`.

**播放**: 面板先取来源列表 (不调用插件因此立刻渲染), 流列表按需加载; 两级选择经 `EnumToggle` 平铺 (超过 4 项回落下拉菜单), 切换来源要重置流的选择, 否则会按旧 `key` 探测. 失败信息条给出重试入口, 媒体类型不支持不给: 探测失败不写入后端探测缓存, 重取必定重新调用插件; 播放失败只重新挂载播放器而不重取流列表, 后者会让已失效的选择静默回退到另一条流. 来源顺序由用户在插件页 `PlaybackOrderSection` 维护, 面板按它重排, **位置 0 即默认探测的来源**. 播放窗口常驻且尺寸由比例决定 — **状态变化不得改变外框尺寸**, 否则页面高度突变会把滚动位置夹回顶部. **触屏**: 按住加速、横滑拖进度、双击播放 / 暂停、竖直滑动调音量 (右半屏) 与亮度 (左半屏) 由 `playback-player.tsx` 的 `useTouchGestures` 承担, 它只接受 `pointerType === "touch"` — 鼠标的单击 / 双击 / 右键原样保留. 音量取媒体元素自身的音量, 亮度写入叠加层的透明度 (网页无法读取系统音量与亮度). 控制器声明 `touch-action: none`、纵向拖动因此归播放器; 菜单与倍速面板各自声明 `pan-y` 保住内部滚动, 壳的下拉刷新也据此让位 (`lib/pull-refresh.ts` 把播放器区域算作"可滚动"). 按住加速的徽标、横滑的提示与音量 / 亮度提示都留在控制器内 — **全屏只渲染控制器子树**, 放在外面整层会随全屏消失 (它们不是 media-chrome 控件, 自动隐藏管不到); 从菜单选的倍速不显示徽标. 进度条的目标时间由 media-chrome 的预览盒给出 (默认隐藏, CSS 在 `:hover` / `[dragging]` 时打开). 拖动进度条期间媒体手势必须停手 — 控件的 `pointerdown` 在冒泡阶段置位拖动标记, 晚于手势层的捕获监听, 让位判定只能放在移动与松手时. 手势期间控制器带 `data-amane-gesture`, 控制条、进度条、底部渐变遮罩与居中大按钮整段隐藏: 媒体的手势接收层把任何指针活动都当成用户操作, 控件与遮罩随之淡入, 横滑时画面底部会平白压暗一条. 粗指针 (`pointer: coarse`) 上额外给一个居中大播放键, 判定按指针类型而不是视口宽度 — 全屏播放是横屏, 手机上视口同样很宽; 鼠标端不渲染, 淡出后它必须让出指针, 让位判定依据控制器上的 `userinactive` 与 `mediapaused` — 只有后者能区分播放与暂停. 窄屏下音量按钮只切换静音. 长按在 Chromium 里会同时派发 `contextmenu`, 该钩子在捕获阶段拦掉那一次, 右键菜单因此不会被长按带出来; 触屏因此没有打开右键菜单 (复制时间戳 / 复制当前时间的链接) 的入口 — 这是取舍而不是遗漏, 需要时从控制条补一个可见按钮. 全屏播放转横屏由页面自己锁 (`screen.orientation.lock('landscape')`, 只在播放器自己的全屏期间), 退出时交还系统 — 壳内原生层也锁方向, 浏览器里没有别的地方可锁; 锁失败与不支持该 API 的浏览器都忽略. 控制器内部的行高是 0 (media-chrome 给自身 chrome 用的), 放在控制器里的提示与面板必须自己声明 `line-height`, 否则行盒只剩下内边距. 播放器组件的其余约定集中在 `components/media/playback-player.tsx` 的文件注释里.

## 列表分页

**list** 视图与订阅源、库文件表采用 `BrowsePageShell fill`: 标题 / 搜索不滚, 剩余高度交给 children; 视口高度取 `APP_SHELL_MAIN_HEIGHT` (`components/layout/app-shell-metrics.ts`), 不允许再手写一份 calc — 该常量由 AppShell 写入 `:root` 的 `--app-shell-header-height` 与 `--app-shell-padding` 推导.

`ListToolbar` 是表体壳: 顶栏不滚, 表体内滚, **唯一**分页固定于视口底, 翻页把表体滚回顶部; 它的 overflow 区要求父级有界高度. 分页宽度不足收合阈值时只保留「第 N / M 页」, 判定由 `components/common/list-pagination.tsx` 按实测宽度写成内联样式 — Mantine 的收合经由容器查询, 旧内核整条丢弃, 分页在壳里会一直显示页码按钮; 收合文案经 i18n 给出, 不用 Mantine 的英文默认值. `grid` / `cloud` 禁止 fill — 演员墙用 `VirtuosoGrid` + `useWindowScroll`, 并按历史条目缓存虚拟化快照: 路由器复位窗口滚动时虚拟列表尚未产生高度, 复位会被夹回顶部, 位置还原由挂载后应用快照完成.

窄屏 (md 以下) 的 `BrowsePageShell` 只剩一行: 标题 + 视图切换 + 筛选入口, 摘要 / 搜索 / 高级筛选面板 / 附属控件 / 右侧动作全进底部面板. **带高级筛选的列表页必须把面板经 `filterPanel` 槽传进来, 不允许把 `<Collapse>` 放进 children** — 否则窄屏上开关与面板会分处两地: 开关在面板里, 面板渲染在主屏上. 面板里的浮层控件 (`Select` 及其 `searchable` 形态) 保持默认 portal, 不允许因为面板在抽屉里就改成 `withinPortal: false` — 后者会让浮层交给抽屉的内容容器裁切. 浮层挂载到 body 上不会关掉抽屉 (实测范围: Mantine 9.6, 窄屏断点) — 上游的"点击外部"只由 overlay 的 `onClick` 触发, 浮层的 z-index 更高, 碰不到它. 同一批控件只渲染一处: 两个断点各一份会让搜索框在 DOM 里存在两个. 标题行的枚举选择器在窄屏整块不渲染: 放不进一行, 换行会把标题区撑成好几行, 而分类浏览入口本身就是用来换种类的. `SelectionBar` 无选中时在窄屏整条不渲染. 订阅浏览页的 `FeedReader` 用同一形态, 条目在窄屏把行内图标操作收进菜单.

图标按钮的悬浮说明必须经 `HintedActionIcon` (Mantine Tooltip), 不允许 HTML `title`; disabled 控件须再包一层可接收指针事件的元素.

## 窄屏

**视口高度统一经 `--amane-vh`**: 默认 `100vh` (`src/global.css`), 入口检测到 `dvh` 时替换成 `100dvh`. 禁止在样式或组件属性里写死 `dvh` — Chromium < 108 的 WebView 会整条丢弃含它的声明, 而弹窗上限、AppShell 高度与钉高页面都靠它 (失效时弹窗没有上限也不滚). 第三方样式 (Mantine) 里的 `dvh` 由 `vite.config.ts` 的 `viewportUnitFallback` 在构建期换成同一变量, 因此不必逐个改 Mantine 的类名.
导航栏在 `sm` (768px) 折叠, 页面内部布局 (三列标题行、并排分栏、内容侧栏) 一律用 `md` (992px): 768px 上导航栏刚展开, 内容宽度反而收窄, 跟随 `sm` 会同时触发挤压与换行. 新增断点只允许落在 `base` 至 `md`, `lg` 以上是已验收的宽屏基线, 不得改动.

显隐用 `visibleFrom` / `hiddenFrom` (生成 `display: none !important`, `Table.Th` / `Table.Td` 同样支持); 必须更换控件形态时用 `useNarrowViewport()` (`hooks/use-narrow-viewport.ts`). 该 hook 的断点参数必须与同一处显隐用的 `hiddenFrom` 一致 — 不一致会在中间区间同时渲染两套控件, 窄套的入口点不开 (它的面板只由窄判定打开). `Group` 的 `wrap` 与 `gap` 是 CSS 变量, 不接受响应式对象, 换行写 CSS Module 的 `@media (max-width: 47.99em)`.

钉高页面必须让顶栏 chrome 可折叠: 筛选与批量操作在窄屏收进 `Menu` / `Drawer`, 滚动区给出下界, 外层容器纵向可滚动 — 否则表体被压成 0 高且分页被裁掉. 窄屏侧栏统一采用 `routes/feeds.index.tsx` 的 Drawer 范式: 内容侧 `hiddenFrom`, 抽屉与触发按钮取同一断点, 侧栏的 `ScrollArea` 视口必须声明 `overscroll-behavior: contain` — 滚到尽头时不允许把滚动传给底下的页面.

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
| 列表密度 / 列宽 / 主题 / 播放源顺序 / 列表默认参数 | Zustand (`amane-web`) |

虚拟滚动的落底、短列表排布与 `followOutput` 约定见 `/logs` 与 `/tasks` 路由及其组件注释. OpenAPI 字符串联合若需运行时迭代, 集中放置于 `lib/exhaustive-maps.ts`, 禁止在路由里再手抄一份.

## 图片

外站图经由 `/api/resources/proxy` (`proxyImageUrl`); `<img>` 不能带 Authorization, 鉴权靠 cookie. 裁切基准是 `thumb_urls[0]` 对应的 Resource 本地文件, 只提交像素坐标, 不上传 blob.

相位水印是 CSS overlay (`FilePhaseOverlay`), 读列表聚合的 `file_phase`, 不修改 Resource 像素; 表格与文件列表仍用 `FilePhaseBadges`. `FanartLightbox` 必须 Portal 到 `document.body` — Modal 打开态的 `transform` 会把 `position: fixed` 的包含块变成弹窗, 大图被 content 的 `overflow-y: auto` 裁切.

外链图片的并发限流 (为什么限、阈值与观测判据) 见 `components/media/proxy-image.tsx` 与 `lib/image-loader.ts` 的注释.

## `lib/` 分层

根目录只放跨域工具 (`confirm` / `exhaustive*` / `api-token` / `connection` / `shell` / `utils` 等); 只服务一个产品域的模块纳入 `lib/<domain>/` (`actors` / `feeds` / `agent` / `task` / `media`), 不允许再往根上堆叠带域前缀的文件. 不设根 barrel, 调用方直引文件.

`lib/shell.ts` 判定是否在壳内并给出壳的动作, 判据与桥的分工见 [android.md](android.md). 壳内顶栏在语言切换旁多一个 `/client` 入口 (手机图标; 不放在侧栏 — 手机上侧栏要先展开才能点到), 页面见 `routes/client.tsx` → `components/shell/client-settings.tsx`; 登录门 (`components/auth/login-gate.tsx`)、错误边界 (`components/error-boundary.tsx`) 与顶栏入口都据此给出「切换服务器」, 非壳环境下整块不渲染. `lib/pull-refresh.ts` 在根上装一次, 把触点处是否还有可向上滚的内容推送至壳. 壳内还会在根元素上写入 `data-amane-shell`, 供样式区分壳环境.

## 工程入口

`just generate` → OpenAPI 导出 + TS client 生成 (`web/src/client/` 为产物, 不手改); SPA 产物 `web/dist` 由 `src/amane/api/spa.py` 挂载. 入口文档与 dist 里未改名的文件回 `public, no-cache` (每次加载重新校验, 未变则 304), `/assets` 下的带 hash 产物回 `immutable` — 直接缓存入口文档会让壳停在旧的那份. 两条都由 `tests/api/test_middleware.py` 钉住 (条件请求必须回 304 — 只用 `FileResponse` 发响应会整份重传), 机制见 `src/amane/api/spa.py`. `vite.config.ts` 的 `legacy()` 声明运行期下限 (语法目标 + core-js polyfill), 面向版本落后的 Android WebView — 下限取舍见 [android.md](android.md).

路由组件由 `tanstackRouter()` 的 `autoCodeSplitting` 拆为独立 chunk: 只服务单个路由的重型依赖必须留在该路由的 chunk, 从共享模块导入会把它移回入口 chunk. `tanstackRouter()` 必须排在 JSX 转换插件之前, 顺序颠倒时构建失败. 类型、i18n 与 lint 的硬性约束由 `pnpm check` (tsc / oxlint / oxfmt / i18next extract) 把关.
