# 前端架构

> 入口: `web/src/`. 组件清单从源码可见; 本文只写信息架构、跨模块约定与易回归点.

## 信息架构

导航按域拆分, 不是扁平功能列表. 首页 `/` 是产品对话 (Amane), 不是片库.

| 域 | 路由重心 |
|----|---------|
| **Browse** | `/` 对话; `/meta` 片库; `/actors` 演员; `/catalog/...` 分类词云; `/saved-queries/$queryId` 查询结果; `/feeds` 阅读器 (`?feed=` / `?group=`) |
| **Manage** | `/libraries` `/libraries/$id`; `/plugins` 刮削插件; `/feeds/sources` 订阅源 |
| **Ops** | `/tasks` `/schedules` `/logs` |
| **Settings** | `/settings` (`?section=` 分组; Schema 表单) |

片库 / 演员 / 分类无独立「管理」路由: `view=grid|list` (分类 `cloud|list`). grid/cloud 只读; list 才有多选与破坏性操作. Feed 相反: 阅读器 (`/feeds`) 与源表 (`/feeds/sources`) 不共用布局, 选中源不在阅读器顶栏展开操作条. 侧栏点条目只筛选阅读器; 源/分组的左侧图标深链源表 (不修改当前选中), 源经由 `?feed=` (按当前筛排定位到该行所在页并高亮, `feed` 不是筛选条件, 修改搜索框会清除该参数), 分组经由 `?q=`. 「全部」与「未分组」没有这条入口. 侧栏 `NavLink` 用 TanStack `Link` 时, Mantine 把 `aria-current=page` 画成选中, 而 Link 默认把子路径也标成当前页; `/feeds` 必须 `activeOptions.exact` 且忽略 search, 否则打开 `/feeds/sources` 时「订阅」也会亮.

**入口分流**: 非演员实体 → `/catalog/$kind/$facetId`; 演员 → `/actors/$actorId`. 结果筛选在 `/meta` (`q` + 各 `*_id`; `saved_query_id` 与其它筛选项 AND, 见 [agent.md](agent.md)). `FacetBadge` 默认 `mode="catalog"` (actor 深链 `/actors/$id`), 筛选深链用 `mode="meta"`. 演员不进入 `/catalog`. 详情出演徽章的性别来自同一次 `Actor` 行查找 (`actor_genders`), 不是 `raw` 里的 `FilmActor`; 仅 `female` / `male` 画符号.

影片详情的用户标签与刮削标签分栏. 加减菜单勾选后保持打开, 底部按钮一次提交多名; 绑定/解绑是前端循环单条 API. `POST /api/metadata/batch/user-tags` 是多影片 × 单标签, 不允许用来给一部影片一次挂多个标签.

分类实体页必须是 `catalog.$kind_.$facetId.tsx` (trailing `_`): `$kind` 是词云叶页而非 layout, 写成 `catalog.$kind.$facetId` 会成为无 `<outlet />` 的父路由的子路由, 子页永不渲染.

演员浏览经由 `/api/actors`; 身份治理 (rename/merge/delete/rules) 仍调用 `/api/facets/actor`. 清空人物档案是 `PATCH /actors/{id}` 空字段 (保留 name/gender; `raw`/`field_sources` 不在对外可写面, 普通刮削可能从缓存填回), 不是删 facet. 筛选字段单一事实源 `lib/actors/browse.ts` (与 `ActorBrowseParams` 对齐). `/actors` 未带 `gender` 时默认仅女演员 (从 URL 剥掉); 清空性别写入 `gender=[]` 表示不限, 与缺省不是一回事. 批量刮削 / 批量性别 / 批量清空是前端循环, 无独立 batch 端点.

## 列表分页

片库 / 演员 / 分类的 **list**、订阅源、`/libraries/$id` 文件表采用 `BrowsePageShell fill`: 标题/搜索不滚, 剩余高度交给 children. 视口高度是 `APP_SHELL_MAIN_HEIGHT` (header + 上下 padding), `/feeds` 阅读器、`/tasks` 与 `/logs` 共用, 不允许再手写一份 calc. `/tasks` 页头 (状态/类型筛选) 留在 ListToolbar 外, 表体才采用 ListToolbar; 阅读器虚滚自管, 不套 ListToolbar. 库详情的扫描/整理/配置/删除在 ListToolbar `trailing` (表体右上), 与左侧多选批处理相对. 文件表行上的刷新图标只带 `media_id`; 「指定番号刮削」打开对话框, 番号必填、内容类型可选, 与 `media_id` 一同提交.

`ListToolbar` 是表体壳: 顶栏 (多选 / 规则入口) 不滚, 表体内滚, **唯一**分页固定于视口底; 翻页把表体滚回顶部. `grid` / `cloud` 禁止 fill — 演员墙 `VirtuosoGrid` 用 `useWindowScroll`. ListToolbar 的 overflow 区要求父级有界高度; 非 fill 页将其作为壳时, `flex` + `minHeight: 0` 会把表高收缩为 0.

## Schema 表单

Settings、任务提交、定时创建、metadata 编辑共用 `components/schema-form/`: Pydantic → OpenAPI → FieldRouter. `x-*` 清单见 `schema/types.ts`.

Dict 的用户 key 是父级对象上的字面量, 不写入 TanStack 点路径; 叶子读写经由 `DictEntryScope` 写入 `[key]` (主机名等含 `.` / `[` / `]` 的 key 才能原样保存).

Tabs 同时挂载全部条目, 叶子 `id`/`htmlFor` 必须经由 `useFieldDomId` 加上条目前缀, 不能只用 schema 相对名, 否则同名 Switch 会命中第一份控件. 标题栏滚轮不得切换条目.

`SchemaForm` 双模式: `patch` (dirty 门控, 只提交 diff) / `create` (完整值). dirty 保存条 (`UnsavedChangesBar`) : 设置页与影片/演员编辑弹窗均采用 `affix` (Portal 固定于视口底, 居中). Affix 默认 z-index 与 Modal 同为 200, 编辑弹窗里必须抬到 300 才能叠在弹窗之上. 不允许将 dirty 条放入 Modal 表单流 — Modal content 是滚动容器, 末尾的条必须滚到底才看见.

`fieldLayout="grid"` 把连续短 scalar 排列为两列 (`sm+`); title/plot 类长文本与 array/dict 仍整行 — 仅 metadata 编辑开启.

表单空值编码经由 `schema-form/encode.ts` (`encodeFormBody` / `encodeEmptyValue`), 按 **Create / 列 schema** 判空: 可空 string 空串 → `null`, 非空 string 空串 → `""`, 非空 array 空 → `[]`. 不允许对着 PATCH partial schema 编码 — `create_partial_model` 会把非空列标成 `T|null`, 空 glob 会被编成 JSON `null`. 手写 Library / Feed 表单经同一编码器出 body (`libraryFormToCreateBody` / `libraryFormToUpdateBody` 分别用 Create 与 Response schema).

可增减 key 的 dict (无 `x-frozen-keys`): 值为空数组 / 空对象 / `null` 的条目与缺席等价, 编码时删除, 条目控件把值清空时也删除该 key. 新增 key 的空默认值仍留在表单上供继续填写, 在写入值之前不构成变更. `x-frozen-keys` 必须保留全部 key, 空列表原样提交 (`content_routes` 的空列表是关停该类型; 缺席会被校验补回默认路由). dirty 与 PATCH 都比较编码后的值.

`/plugins` 通过 `/api/plugins` 取得插件自带 JSON Schema, 单独渲染每个来源配置; 插件配置不进入核心 HotSettings 表单. 安装用 `PathPicker` 选服务器目录/zip, 或上传本机 zip; 重新扫描 / 卸载经由同一资源的 POST/DELETE, 成功后同时失效插件列表与 config schema (路由 enum 会变).

任务 / 定时提交用 `DiscriminatedSchemaForm`: 外部选 `type` → schema variant → 去掉 const `type` 后交给 `create` 模式. 短枚举共用 `EnumToggle` (`components/common/enum-toggle.tsx`): 项间分隔线 + 滑动指示; `fullWidth` 占据整行 (任务/定时 type、cron 模式、订阅内容类型), 默认按文案宽度 (Schema 表单短枚举、库放置方式/自动化、间隔单位). 片库 grid/list 等页面 view 切换仍用 SegmentedControl. 定时的 cron 用 `CronPicker`: 可视化覆盖间隔/每天/每周/每月, 无法往返的表达式回落「高级」手写; 产出 5-field, 与后端 croniter 一致. 每天/每周/每月的时刻按浏览器本地墙钟填写, 写出时换算为 UTC 字段 (星期与日期随跨日平移); 间隔不换算; 「高级」手写按 UTC.

Library 表单仍手写 (create/edit 差 + `scan` 条件字段 + 库级 `automation` / 整理默认与预告片跳过正则); 路径模板占位符与后端 `resolve_paths` 同源, 经由 `GET /api/libraries/path-template-schema` 下发 name+map_keys, 徽章说明在 i18n `placeholders.items` (有闭合取值时 tooltip 附 `map_keys`); 可空占位符名带字面量 `?`, `{name|k=v}` 与可选组语法见 [data-model.md](data-model.md). 模板框是透明输入叠着色层 (`template-input.tsx`), 词法跟 Parser 的 `{name}` / `|k=v` / `[..]` / `[[..]]`; Accordion Collapse 展开时盒子从 0 增至目标高度; 着色层须在尺寸变化后再次与输入框对齐 (有值时输入框透明, overlay 高度为 0 会裁掉文字); 占位符名与有闭合取值的映射 key 对照 `path-template-schema` 标红, 清单未下发时只标语法错误.

Feed 表单手写 (分组伪路径 / 间隔默认小时 / 正则 / content_type / use_cache), 只出现在 `/feeds/sources`. `/feeds` 是侧栏目录 + 条目阅读器. 导航态在 URL (`feed` / `group` / `q` / `state` / `page` / `nodedupe`). `/feeds/sources` 复用 BrowsePageShell `fill` / ListToolbar / SelectionBar / SortableTh; 源列表无分页 API, 前端筛选、排序、分页. 排序仅经由表头; 搜索旁是启用/自动入队筛选按钮 (URL `enabled` / `auto_enqueue`). 阅读器侧栏源/分组左侧图标深链源表, 见上文信息架构. 条目 HTML 经由 DOMPurify 后渲染, 同番号折叠是前端显示层. 条目批量按 `feed_id` 分组经由现有 batch. 源级批量 (拉取 / 启用 / 停用 / 自动入队 / 删除) 是前端循环现有单源端点, 无独立 batch. OPML 把无 xmlUrl 的 outline 写成 `group`; 导入时可加分组前缀 (拼在 outline 路径前) 与 `auto_enqueue` (默认关). 番号是否已在库由 items 列表 JOIN 的 `metadata_id` 判断, 见 [feeds.md](feeds.md).

破坏性确认经由 `lib/confirm.tsx`, 不用 `window.confirm`.

`/tasks` 无行多选. 筛选批量按钮固定于 ListToolbar 顶栏, 按当前 URL `status`/`type` 调用 `POST /tasks/batch` (与列表筛选同形); 与该 action 不允许的状态求交后为空则隐藏按钮. 行级/详情操作同一端点, 只带 `task_ids`. Worker 暂停按钮只切换进程内领队开关, 文案暂停/恢复, 不解释执行器.

任务树 (`components/task/task-tree.tsx`) 不是 Table 缩进. 点击整行展开/收起该节点 — 子任务与详情是同一动作, 箭头只是状态指示不是第二套操作. 行是全宽网格: 只有名称列按深度画 ├/└ 导线, 错误/状态/耗时/编号/操作列对齐; 状态色只出现在当前层导线和有后继时的展开脊线上, 无后继不画向下脊线. 多级展开时祖先导线以虚线贯穿详情, 避免外侧断掉. 全部展开/收起固定于 ListToolbar `trailing`, 不随表体滚. 展开面板是独立滚动盒, 高度封在最近滚动容器的剩余视口; 用户点开时外侧把该行滚到容器顶部附近 (全部展开不抢焦点). 列表项带 `child_count` / `child_status`; 展开后 `/children` 带 `link_key`.

## 对话通道

`/` 的实现边界见 [agent.md](agent.md). 前端要点:

- 对话经由 **SSE** (`lib/agent/sse.ts` 手写 `fetch` + `ReadableStream`), 不经由 hey-api、也不经由 `/ws`.
- 请求统一经由 `lib/api-token.ts` 的 `apiFetch`: **纯透传**, 不注入 header, 只把 401 转成登录门. 鉴权采用 HttpOnly cookie, 见 [config.md](config.md).
- 不允许在包装里重建 headers (`fetch(input, { headers })`): 传入单个 `Request` 时 init.headers 会整体替换, 丢掉 `Content-Type`, FastAPI `strict_content_type` 下 JSON 会 422.

## 实时与状态

`lib/connection.ts`: 模块级 WS 单例 + 指数退避 + 断连轮询降级. 入站经由 `parseWSEvent` 窄化, 不识别的 type 丢弃. 任务列表与已展开子节点经由 `invalidateTaskQueries`: hey-api 生成的 query key 是 `[{ _id, baseUrl, path, query }]`, `invalidateQueries({ queryKey: ["getTaskChildren"] })` 对不上, 必须用同形对象前缀 `[{ _id: "getTaskChildren" }]`.

| 数据 | 存储位置 |
|------|--------|
| 列表 / 详情 | TanStack Query (invalidate) |
| 高频流 (进度 / 日志) | Zustand |
| 对话增量 | SSE (与 WS 正交) |
| 导航态 (筛选 / 排序 / page / view) | URL search |
| 列表密度 / 列宽 / 主题 | Zustand (`amane-web`) |

`/logs` 进入后滚到 scroller 真正的底 (`scrollTo` MAX, 不是 `scrollToIndex` LAST+end — 单行末项 align end 会把下边距裁掉). `initialTopMostItemIndex` 只用末项下标. 内容不足一屏时从顶部排布 (`alignToBottom` 会把短列表贴底, 禁止启用). `followOutput` 开着时回调恒返回 `auto`, 不用 `smooth`.

OpenAPI 字符串联合若需运行时迭代, 集中放置于 `lib/exhaustive-maps.ts`, 禁止在路由里再手抄一份.

## 图片

外站图经由 `/api/resources/proxy` (`proxyImageUrl`). `<img>` 不能带 Authorization, 鉴权靠 cookie. 裁切基准是 `thumb_urls[0]` 对应的 **Resource 本地文件** (与后端 `acquire` 同一份), 只提交像素坐标, 不上传 blob. 片库海报 / 详情封面相位水印是 CSS overlay (`FilePhaseOverlay`), 读列表聚合 `file_phase`, 不修改 Resource 像素. 无码 = mosaic 标记或片种 uncensored. 四角: 左上马赛克 (无码/破解/流出), 右上评分, 左下中字+清晰度 (出演墙还叠当时年龄), 右下发行日. 表格/文件列表仍用彩色 `FilePhaseBadges`, 不采用 overlay.

`FanartLightbox` 必须 `Portal` 到 `document.body`. Modal 打开态带 `transform` (`fade-down` 的 `translateY(0)`), 会把 `position: fixed` 的包含块变为弹窗本身, 大图被 content `overflow-y: auto` 裁切. Lightbox 拦截 mousedown/click 冒泡, 避免点预览被 Modal 当成 click-outside.

**proxy 限流**: 外链 `<img>` 经由 `ProxyImage` / `useQueuedImageUrl`. 浏览器对同 host HTTP/1.1 连接有限 (约 6); 慢速外链不限流会占满连接池, API 请求全部排队. 全局信号量限制 4 个并发; 探测到 h2+ 时放行. 本地 `/api/resources/*` 不经由队列. Intersection Observer 仅对邻近视口的 proxy 图申请信号量 (`rootMargin` 400px); 排队图一旦取得 `src` 立即请求, 不使用 `loading=lazy` (`lazy` 会让屏外图占用信号量却不发出请求, 视口内头像一直空白).

演员头像墙 (`/actors` grid): `VirtuosoGrid` + `useWindowScroll`, 每批 30; grid 模式不预先拉取 list 查询.

## `lib/` 分层

根目录只放置跨域工具 (`confirm` / `exhaustive*` / `api-token` / `connection` / `utils` 等). 只服务一个产品域的模块纳入 `lib/<domain>/` (`actors` / `feeds` / `agent`), 不允许再往根上堆叠带域前缀的文件. 不设根 barrel, 调用方直引文件.

## 工程入口

`just generate` → OpenAPI + TS client; SPA 产物 `web/dist` 由 `api/spa.py` 挂载.

- **禁止** `as never` / `as any` 绕过可推断位置. `as const satisfies` 用于收窄字面量.
- i18next 已接入资源类型: 缺 key 补翻译, 不允许 `as never`.
- 跨页相同文案放入 `common` (或该能力所属 namespace); 页面 namespace 只留本域特有文案.
- `useTranslation` 声明 namespace 后, 默认 ns 的 `t()` 不允许写 `ns:` 前缀; 跨 ns 才加 (`t("common:actions.save")`).
- **例外**: `schema-form/` 内动态 path / 运行时 schema 分组允许显式断言并注释原因.
- oxlint `correctness` 含 React Compiler 推荐规则. 用 props/key 重置本地 state 采用渲染期调整 (`useResettingState`), 不允许 `useEffect` + `setState`. 事件/观察者回调里要读「最新值」用 `useLatestRef`, 不允许在 render 里写 `ref.current`.
