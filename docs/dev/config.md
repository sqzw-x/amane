# 配置系统

> 入口: `src/amane/config/`. 本文记录 Cold / Hot 分层、进程内 rebuild 与配置项增补规范.
> 启动编排见 [architecture.md](architecture.md).

## Cold / Hot 分层

`src/amane/config/manager.py` 把配置划分为两份:

- **ColdSettings** (`AMANE_*` 环境变量) — 路径类、安全边界等进程级绑定. 修改后必须重启, 因为 `data_dir` 等会派生出 SQLite 路径、TOML 路径、resources 目录.
- **HotSettings** (TOML, 经 `PATCH /api/config` 写入) — 行为参数. 多数变更经由 `AppRuntime.rebuild()`, 不重启进程.

`data_dir` 不允许放入热配置: 切换目录须迁移 DB、resources 与未完成任务. 判据是 Cold = 派生路径 / 进程级绑定, Hot = 行为参数.

## API token (`AMANE_TOKEN`, Cold)

API 鉴权是冷配置: 中间件在请求路径上, 不能在进程内 rebuild 中替换.

- **未设置 (auto, 默认)**: 启动时生成随机 token 并持久化到 `data_dir/token` (0600, 重启复用). 除 `GET /api/health` 外 `/api/*` 需 `Authorization: Bearer` 或中间件下发的 HttpOnly SameSite=Lax cookie; 浏览器侧全部经由 cookie, token 不落 localStorage, 不出现在 URL 与访问日志. 启动日志打印 token, 容器从 `docker logs` 取.
- **`AMANE_TOKEN=off`**: 显式关闭, 仅当反代已实现等价鉴权时使用.
- **`AMANE_TOKEN=<value>`**: 显式 token.

信任边界: 持有 token 即视为用户本人, `safe_dirs` 只作纵深防御, 同机能读 `data_dir/token` 视为已信任. `AMANE_SAFE_DIRS=ALLOW_ALL` 关闭路径边界 (桌面壳默认); Docker 与无哨兵时仍按目录名单约束. 桌面 argv 取得 token 的方式见 [desktop.md](desktop.md).

## 进程内 rebuild

`AppRuntime.rebuild()` 重建依赖 HotSettings 的对象链:

```
RateLimiters → WebClient → HttpClient → CrawlerFactory
  → Handlers (含 Translator / R18ImportHandler) → AsyncWorker
  → PlaybackFactory (独立码流客户端)
  → AgentService.rebuild (仅换 Agent 工厂 / 缓存参数)
```

`logging.level` 在 rebuild 内直接修改 logger, 不依赖对象重建.

**不重建的对象**: `Repository`、`EventBus`、`WatcherService`、`FeedService`、`ResourceStore`、`TranslationCache`、`ProxyFailureCache`、`AgentService` 内的 `ResultCache` — 它们的状态是会话级的 (DB 连接池、WS 客户端、watchdog observer、feed 轮询循环、资源去重表、译文缓存、负缓存、交付结果缓存), 重建会切断现有连接或丢掉缓存句柄. `rebuild()` 只把新 `WebClient` 交给 `FeedService.set_web_client`.

`watcher.use_polling` / `media_extensions` / `debounce_seconds` 在 `start_app` 构造时一次性注入, **不随 rebuild 更新**, 修改 TOML 后须重启; Library 级的 `automation` / `ingest` / `cloud_path` / 路径 / `trailer_pattern` 等由 libraries 路由热更新, 与这三项无关. 契约见 [watcher.md](watcher.md).

旧 worker 在 rebuild 后被替换: 调用方必须排空旧 worker 再启动新的, 否则两个 worker 会同时认领任务. 新 worker 继承 pause. 配置 PATCH、插件启用 / 禁用、插件安装 / 卸载 / 重新扫描都经由 `AppRuntime.apply_rebuild()`, 串行化这段替换.

**r18 只读引擎**: 只在 `hot.r18` 实际变化时重建. rebuild 是同步的, 无法 await 释放 asyncpg 连接池, 旧引擎暂存 `_old_r18_db`, 由 config 路由随后 `dispose_old_r18()` 异步关闭.

## TOML 持久化

写 TOML 必须用临时文件 + `os.replace` 原子化 — 直接覆盖时写入中途进程中止会留下空文件. `tomli_w` 不接受 `None` / `set`, 持久化前须按 JSON 模式导出并剔除 `None` 与默认值.

## 配置项增补规范

字段加在 `src/amane/config/manager.py` 对应 section model (全部配置 model 集中在该文件), 然后:

1. 需要 UI 展示时添加 `json_schema_extra` 的 `x-*` 扩展 (`x-*` 清单见 `web/src/components/schema-form/schema/types.ts`). 站点列表字段必须用 `site_roles` 的 schema 收窄 `items.enum`, 不允许直接暴露完整 `SiteName`.
2. 若新字段影响限速 / HTTP / 爬虫 / LLM / handler 行为, 确认 `rebuild()` 链能传播变更; 若影响 WatcherService 构造参数, 须标明「重启生效」.
3. `just generate` 同步前端 schema, 并补 `web/src/i18n/` 翻译, 否则构建失败.

`x-frozen-keys` 全量 dict (`site_config` / `content_routes` / `field_language`): `default_factory` 只在整段缺席时生效; 文件里已有该字段但缺 key 时, 校验按代码枚举补默认并丢弃未知 key. UI 不能加 key, 不补则新项无法配置. `GET /api/config` 始终返回全集.

`content_routes`、`field_priority` 与 `field_blacklist` 的值允许第三方 `namespace.local` 来源 ID, 实际可用性由 `PluginManager` 校验; 已禁用或未安装的来源可以留在路由里, 刮削时跳过, 不阻断启动或配置写入. 插件自己的配置放入 `plugins.<source_id>`, 由插件提供的 Pydantic model 校验, 不放入 `site_config`.

`AMANE_SUPERVISED=1` 声明进程外监督者在场 (compose 与桌面壳设置). 不允许在无监督循环的 `amane.server` 内设置该变量, 否则 `exit 3` 会使进程退出且无人再次启动. 为真时 `POST /api/system/restart` 可用. 不探测 cgroup, 以免在 K8s 里误开应用内重启.

`AMANE_UPDATE_URL` 覆盖版本检查的 GitHub 地址 (空 = 官方 API 的发布列表; 指向镜像的 `/releases/latest` 也可以, 单条发布的响应同样接受).

## `scraping` 影片路由 (Hot)

`content_routes` 是按内容类型的**有序站点链**: 资格真值 + 该类型默认字段顺序. 实际请求的站点 ⊆ 此表, 空表则该类型刮削直接失败. 关闭某类型刮削须把该项设为空列表, 不允许删除 key.

`field_priority` 是稀疏字段例外: 只写需要提前尝试的站; 编译时与该类型路由求交后前置, 其余路由站点保序回退. `field_blacklist` 是稀疏字段排除: 只写该字段不采用的站. 二者同时列出同一站时以黑名单为准. 不在该类型路由中的站无效, 也不额外发请求.

外部影片来源的 descriptor 参与路由校验: 声明 `content_types` 时路由类型必须在声明集合内, 声明 `metadata_fields` 时字段优先级与黑名单只能选择声明过的字段; `multi_language` 参与聚合节点展开.

建图对编译后站点链的消费见 [task-system.md](task-system.md), 默认表取舍见 [content-routes.md](content-routes.md).

## `actor_scraping` (Hot)

演员刮削与影片 `scraping` 分 section: 影片管线不读演员站列表, 演员任务也不读 `field_priority` / `field_blacklist`. 契约 (实现见 `ActorScrapeHandler` / `aggregate.actor`):

- **`profile_sites`** / **`image_sites`**: 档案与头像的来源顺序 (标量填空优先级, 头像优先于档案站附图). 默认与 schema 枚举都是注册表里声明了对应能力的插入序, 不在配置层手写站点名单.
- **`download_images`**: 是否经 ResourceStore 缓存头像 (URL 仍为远端 locator).
- **`auto_scrape`**: 影片刮削成功后自动链式入队该片演员的 `ACTOR_SCRAPE` 任务; 见 [task-system.md](task-system.md).
- **`gfriends_repo`**: gFriends 仓库 URL, Filetree 缓存在 `data_dir`.

跨角色站点写入会被 section validator 拒绝. 发请求前仍按 `Actor.gender` 与站点性别覆盖再过滤 (见 [crawlers.md](crawlers.md)).

## `watermark` (Hot)

只在 ORGANIZE 落盘封面时绘制, 不修改 Resource; 片库 CSS overlay 不读 `enabled`. 角标高度 = 图高 × `scale` (不按宽、不按 PNG 原图像素); `corners` 是 `x-frozen-keys` 五类到四角的映射, 同角按中字 / 无码 / 破解 / 流出 / 清晰度向内叠. PNG 覆盖仍是 `{data_dir}/watermarks/{stem}.png`.
