# 配置系统

> 入口: `src/amane/config/`. 本文记录 Cold / Hot 分层、进程内 rebuild 与配置项增补规范.
> 启动编排见 [architecture.md](architecture.md).

## Cold / Hot 分层

`src/amane/config/manager.py` 把配置划分为两份:

- **ColdSettings** (`AMANE_*` 环境变量) — 路径类、安全边界等进程级绑定. 修改后必须重启, 因为 `data_dir` 等会派生出 SQLite 路径、TOML 路径、resources 目录.
- **HotSettings** (TOML, 通过 `PATCH /api/config` 写入) — 行为参数. 多数变更经由 `AppRuntime.rebuild()`, 不重启进程; 例外见下.

不允许将 `data_dir` 放入热配置: 切换目录须迁移 DB、resources、未完成任务. Cold = 派生路径 / 进程级绑定; Hot = 行为参数.

## API token (`AMANE_TOKEN`, Cold)

API 鉴权是冷配置: 中间件在请求路径上, 不能在进程内 rebuild 中替换.

- **未设置 (auto, 默认)**: 启动时生成随机 token 并持久化到 `data_dir/token` (0600, 重启复用). 除 `GET /api/health` 外, `/api/*` 需 `Authorization: Bearer` 或中间件下发的 HttpOnly SameSite=Lax cookie (`amane_token`, Path=/api). 浏览器侧全部经由 cookie — 登录门输入一次 Bearer, 之后 REST / SSE / WS / `<img>` 自动携带; token 不落 localStorage、不出现在 URL / 访问日志. 启动日志打印 token, 容器从 `docker logs` 取.
- **`AMANE_TOKEN=off`**: 显式关闭 — 仅当反代已实现等价鉴权时使用. 默认 auto 与反代不必互斥.
- **`AMANE_TOKEN=<value>`**: 显式 token (自行保证随机性).

信任边界: 持有 token = 用户本人, `safe_dirs` 只作纵深防御; 同机能读 `data_dir/token` 视为已信任. cookie 防 XSS / 跨站; 局域网裸 HTTP 以家庭网络为信任前提. 桌面 argv 取得 token 的方式见 [desktop.md](desktop.md). `AMANE_SAFE_DIRS=ALLOW_ALL` 关闭路径边界 (桌面壳默认); Docker / 无哨兵时仍按目录名单约束.

## 进程内 rebuild

`AppRuntime.rebuild()` 重建依赖 HotSettings 的对象链:

```
RateLimiters → WebClient → HttpClient → CrawlerFactory
  → Handlers (含 Translator / R18ImportHandler) → AsyncWorker
  → AgentService.rebuild (仅换 Agent 工厂 / 缓存参数)
```

`logging.level` 在 rebuild 内直接修改 logger, 不依赖对象重建.

**不重建的对象**: `Repository`、`EventBus`、`WatcherService`、`FeedService`、`ResourceStore`、`TranslationCache`、`ProxyFailureCache`、`AgentService` 内的 `ResultCache` — 它们的状态是会话级的 (DB 连接池、WS 客户端、watchdog observer、feed 轮询循环、资源去重表、译文缓存连接、proxy-image 失败负缓存、交付结果内存缓存), 重建会切断现有连接或丢掉缓存句柄. `rebuild()` 只把新 `WebClient` 交给 `FeedService.set_web_client`.

`watcher.use_polling` / `media_extensions` / `debounce_seconds` 在 `start_app` 构造时一次性注入, **不随 rebuild 更新**, 修改 TOML 后须重启. Library 的 `automation` / path / recursive / patterns / `trailer_pattern` / `blacklist_patterns` / `min_file_size` 由 libraries 路由热增删监控根, 与上述三项无关.

旧 worker 在 rebuild 后被替换, 调用方必须排空它再启动新 worker, 否则两个 worker 会同时认领任务. 新 worker 继承 pause, 避免 PATCH 时意外恢复领队. 配置 PATCH、插件启用 / 禁用、以及插件安装 / 卸载 / 重新扫描都经由 `AppRuntime.apply_rebuild()`, 串行化这段替换.

**r18 只读引擎**: `hot.r18` 实际变化时 rebuild 才重建 r18 PG 引擎 (`build_r18_db`). rebuild 是同步的、无法 await 释放 asyncpg 连接池, 旧引擎暂存在 `_old_r18_db`, 由 config 路由随后调用 `dispose_old_r18()` 异步关闭.

## TOML 持久化

写 TOML 用临时文件 + `os.replace` 原子化. 不允许直接覆盖目标文件, 否则写入中途进程中止会留下空文件.

`tomli_w` 不接受 `None` / `set`, 因此持久化时用 `model_dump(mode="json", exclude_none=True, exclude_defaults=True)`.

## 配置项增补规范

1. 在 `src/amane/config/manager.py` 对应的 section model 中加字段 (给默认值). 所有配置 model (Cold / Hot / 各 section) 都集中在 `manager.py`.
2. 需要 UI 展示时添加 `json_schema_extra` 中的 `x-*` 扩展 (见 [frontend.md](frontend.md)). 站点列表字段用 `site_roles.site_list_schema` / `site_list_value_schema` 收窄 `items.enum` (影片 / 演员档案 / 演员头像分列), 不允许直接暴露完整 `SiteName`.
3. 如果新字段影响限速 / HTTP / 爬虫 / LLM / handler 行为, 确认 `rebuild()` 链能传播变更; 若影响 WatcherService 构造参数, 须标明「重启生效」.
4. 运行 `just generate` 同步前端 schema.
5. 补翻译 (`web/src/i18n/`), 否则构建失败.

`x-frozen-keys` 全量 dict (`site_config` / `content_routes` / `field_language`): `default_factory` 只在整段缺席时生效; 文件里已有该字段但缺 key 时, 校验按代码枚举补默认并丢弃未知 key. UI 不能加 key, 不补则新项无法配置. `GET /api/config` 始终返回全集.

`content_routes`、`field_priority` 与 `field_blacklist` 的值允许第三方 `namespace.local` 来源 ID; 实际可用性、来源能力和插件配置由 `PluginManager` 校验. 已禁用或当前未安装的第三方来源可以留在路由里, 刮削时跳过, 不阻断启动或配置写入. 插件自己的配置不放入 `site_config`, 而是放入 `plugins.<source_id>`, 并由插件提供的 Pydantic model 校验.

Cold 配置同样加到 `manager.py::ColdSettings`, 无需 UI — 只通过 `AMANE_*` 环境变量设置.

`AMANE_SUPERVISED=1` 声明进程外监督者在场 (compose 与桌面壳设置). 不允许在无监督循环的 `amane.server` 内设置该变量, 否则 `exit 3` 会使进程退出且无人再次启动. 为真时 `POST /api/system/restart` 可用. 不探测 cgroup, 以免在 K8s 里误开应用内重启.

`AMANE_UPDATE_URL` 覆盖 GitHub `/releases/latest` (空 = 官方 API). 仓库私有或要演示「有新版本」时指向本地 mock: `uv run python scripts/mock_github_release.py`.

## `scraping` 影片路由 (Hot)

`content_routes` 是按内容类型的**有序站点链**: 资格真值 + 该类型默认字段顺序. 该类型实际请求的站点 ⊆ 此表, 空表则该类型刮削直接失败. 关闭某类型刮削时须将该项设置为空列表; 不允许删除 key.

`field_priority` 是稀疏字段例外: 只写需要提前尝试的站. 编译时与该类型路由求交后前置, 其余路由站点保序回退 (`aggregate.compile_priority`). 不在该类型路由中的站无效, 也不额外发请求.

`field_blacklist` 是稀疏字段排除: 只写该字段不采用的站. 编译时从该字段链上剔除. 与 `field_priority` 同时列出同一站时以黑名单为准. 不在该类型路由中的站无效. 某站仅从部分字段排除时, 其它字段仍会请求该站.

外部影片来源的 descriptor 参与路由校验. 来源声明 `content_types` 时, 路由类型必须在声明集合内; 声明 `metadata_fields` 时, 字段优先级与字段黑名单只能选择声明过的字段. 多语言来源通过 descriptor 的 `multi_language` 参与聚合节点展开.

建图对编译后站点链的消费见 [task-system.md](task-system.md). 默认表取舍、各站覆盖见 [content-routes.md](content-routes.md).

## `actor_scraping` (Hot)

演员刮削与影片 `scraping` 分 section: 影片管线不读演员站列表, 演员任务也不读 `field_priority` / `field_blacklist`.

契约 (实现见 `ActorScrapeHandler` / `aggregate.actor`):

- **`profile_sites`**: 档案源顺序 — 标量填空优先级. 默认与 schema 枚举都是 `actor_registry` 里声明了 `ACTOR_PROFILE` 的插入序 (见 [crawlers.md](crawlers.md)), 不在配置层手写站点名单.
- **`image_sites`**: 头像源顺序 — 优先于档案站附图拼接 `image_urls`; 默认是声明了 `ACTOR_IMAGE` 的插入序.
- **`download_images`**: 是否经 ResourceStore 缓存头像 (URL 仍为远端 locator).
- **`auto_scrape`**: 影片刮削成功后自动链式入队该片演员的 `ACTOR_SCRAPE` 任务 (已刮过的 Actor 跳过; 见 [task-system.md](task-system.md) ACTOR_SCRAPE).
- **`gfriends_repo`**: gFriends 仓库 URL; Filetree 缓存在 `data_dir`, 由工厂注入爬虫.

跨角色站点写入会被 section validator 拒绝; Schema 侧 `site_list_schema` 只暴露对应能力子集. 发请求前仍按 `Actor.gender` 与站点性别覆盖再过滤 (见 [crawlers.md](crawlers.md) / [task-system.md](task-system.md)).

## `watermark` (Hot)

ORGANIZE 落盘封面才画, 不修改 Resource. 片库 CSS overlay 不读 `enabled`.

- **`scale`**: 角标高度 = 图高 × scale. 不按宽、不按 PNG 原图像素. 海报与封面同高时视觉一样大.
- **`corners`**: `x-frozen-keys` 五类 → 四角. 同角按中字 / 无码 / 破解 / 流出 / 清晰度向内叠 (上往下、下往上, 右角右对齐). 不配间距、不配横排. PNG 覆盖仍是 `{data_dir}/watermarks/{stem}.png`.
