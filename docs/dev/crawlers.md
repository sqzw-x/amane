# 爬虫

> 入口: `src/amane/crawlers/`. 本文记录爬虫架构、HTTP 层、限速与新爬虫接入.
> 测试约定见 [crawler-testing.md](crawler-testing.md). 默认路由与站点覆盖见 [content-routes.md](content-routes.md). 外部影片来源见 [plugins.md](plugins.md).

## 架构

```
CrawlerFactory (缓存实例)
  ├── Crawler 子类 (影片, sites/ + registry)
  │     ├── profile() → CrawlerProfile
  │     ├── _search(query, options) → URL | None
  │     └── _scrape(url, options) → MediaMetadata | None
  └── ActorCrawler 子类 (演员, crawlers/actor/ + actor_registry)
        ├── fetch(name) → ActorMetadata | None
        └── 默认 Template: _search / _scrape; 纯索引源可 override fetch
              └── HttpClient → WebClient → RateLimiters
```

外部影片插件在同一个 `CrawlerFactory` 中按来源 ID 延迟创建: 插件返回的 provider 经适配后满足影片爬虫的 `fetch()` 协议, 统一进入聚合、限速、HTTP 记录和站点结果摘要. 第三方来源 ID 规则见 [plugins.md](plugins.md).

爬虫异步并发安全. `SiteConfig` 在构造期注入, `__init__` 内合并 profile 默认与用户配置, 子类直接使用 `self.base_url` / `self.cookies`; 实例可缓存, 仅配置变化时重建工厂.

- 演员站与影片站共用 HttpClient / 限速; 实现位于 `crawlers/actor/`, 只注册 `actor_registry` (可以不在影片 `registry`). **双料站**指同一 `SiteName` 在影片 / 演员注册表各有一个类并共用 `site_config`; 不允许在 `site_roles` 中手写双料名单.
- gFriends 额外依赖 `data_dir` (Filetree 缓存) 与 `actor_scraping.gfriends_repo`.
- **能力声明**位于 `CrawlerProfile`: 演员爬虫必须显式给出 `capabilities` (`ACTOR_PROFILE` / `ACTOR_IMAGE`) 与 `genders`; 影片爬虫空 `capabilities` 视为 `film_metadata`. 消费 `FetchOptions.language` 的设置 `multi_language=True`. Stash 指纹匹配站设置 `uses_file_hash=True`, 刮削前才计算 oshash, 扫描不读取文件内容. `site_roles` 只从两个注册表推导配置 schema 用的站点列表 (档案序 = 注册序); 聚合引擎只对推导出的多语言站点展开 `(site, lang)` 节点. Handler 按 `Actor.gender` 对 `profile().genders` 裁站, 见 [task-system.md](task-system.md).

## 影片出演者

`MediaMetadata.actors` 是 `list[FilmActor]` (`name` + `gender`); 旧 `list[str]` 与站点级 raw 快照经 validator 收成 `gender=unknown`. 名单语义能判定性别时爬虫必须写出 `female` / `male`, 栏位无法判定则保持 `unknown`. 聚合锁定与落库填空见 [data-model.md](data-model.md).

## 片商与发行商

`studio` 是片商 (メーカー), `publisher` 是发行商 (レーベル); DMM 侧即 メーカー 与 レーベル 两栏. 集团旗下的多个厂牌站必须按作品写出真实发行商 (faleno.jp 兼发 maryGOLD / JimmyScandal), 不允许用站点名或集团名顶替.

## Crawler 基类

`crawlers/base.py::Crawler` 是 Template Method: 公开 `fetch()` (负责日志; HTTP / 拦截失败冒泡 `SourceError`), 子类实现 `_search` (番号 → URL) 与 `_scrape` (URL → `MediaMetadata`); 特殊源可直接 override `fetch()`. `profile()` 类方法给出内置来源 ID / `base_url` / 能力与性别 / 可选 cookies 与限速 URL; `__init__` 在 `profile()` 之后自动合并配置, 子类不得再次调用. 外部来源不要求继承 `Crawler`, 契约见 [plugins.md](plugins.md).

## 番号入参

`SearchQuery.number` 就是 `ScrapePayload.number`; Handler 不解析番号. 来源路径不同则字符串形态不同, 爬虫不能假设「一定已经带短横线、一定是大写」:

| 来源路径 | 番号来源 | 是否重写 |
|------|------------|------------|
| 库扫描 / 按 `media_id` 刮削 | `parse_file_info(path).number` | **会.** 路径解析命中已知形态时改写成目录号 (插入 `-`、拆分前导零、FC2/HEYZO 族规则、字母大写、剥 CD / 字幕尾标). 规则只在 `parsing/file_info.py`, 本文不列出规则表 |
| 用户任务只填 `number` | submission 字符串 | **不会.** 原样写入 payload; 空 `content_type` 只调用 `infer_content_type`. |
| 按 `media_id` 同时填写 `number` | submission 字符串 | **不会.** 空白 `number` 视为未填写, 回退到只按路径解析. |
| RSS 自动入队 | `extract_number` | **会** (与路径同一套已知形态); 未命中则没有番号, **不会**把标题原文当番号. 源上设置了 `number_pattern` 时只采用该正则, 见 [feeds.md](feeds.md) |

落库 `Metadata.number` 也是这份 payload 原样 (UNIQUE 忽略大小写, 保留首次写入的大小写), 因此补刮 / RESCRAPE 可能再次把无横线手填号送入爬虫.

站点检索与「哪条结果算命中」由爬虫自行处理分隔符: 站内 ID 带 `-` 时, 入参 `HEYZO-3607` 与 `HEYZO3607` 都应能对上 (精确优先, 再忽略短横线与空格). 不允许把 `_` 改写为 `-`, 除非该站把两种当成同一部. 站点特例见 [content-routes.md](content-routes.md).

## HTTP 层

`WebClient` (`net/http.py`) 是唯一出站 HTTP 通道: 失败抛出 `RequestError` (`SourceError` 子类, `failure` 位于异常上), `ok_statuses` (如 RSS 304) 仍算成功. `HttpClient` (`crawlers/http.py`) 是其薄封装 (`get_rendered` / 浏览器), 爬虫与插件均经由它.

- HTML 页用 `get_html`: `get_text` + `classify_block`, 命中拦截 / 空页抛出 `SourceError`.
- JSON API 用 `get_json` / `post_json`, 不执行 HTML 启发式; `post_json` 载荷可以是 object 或 array (Yii 式 RPC).
- `download` / `ResourceStore.acquire` 是机会主义的: 调用方 `except RequestError: return None` / 返回 `bool`, 不经由第二套错误通道.
- 多 URL 试探可在子类 `except RequestError: continue`; 一次成功响应都没有则把最后一次异常冒出去, 不允许将异常吞没为裸 `None`.

### 拦截判定

模式表在 `net/errors.py::classify_block` (正文启发式优先, 失败响应正文次之, HTTP 状态兜底). `get_html` 是 HTML 站的入口; 爬虫不得自行判定拦截、不得 import Recorder — 来源 outcome 只在 `invoke_source` 写入, 见 [observability.md](observability.md).

## 限速

`RateLimiters` (`net/http.py`) 为每个 host 维护独立的平滑漏桶, 优先级 (高 → 低): `network.rate_limits[host]` → `scraping.site_config[site].rate_limit` → `network.default_rate_limit`. host 是更精确的颗粒度 (多个站点可能共享同一 host), 因此 host 优先级高于 site. 实现是容量 1 的严格平滑桶, 不允许突发 — 突发会触发反爬检测.

## 浏览器指纹

`WebClient` 基于 curl_cffi, 每次请求从预设列表轮换指纹; 可选 Patchright 无头浏览器用于 JS 渲染页面 (`get_rendered`).

## 外部 API 读模型

外部站点的 schema 不受本项目控制, 对象字段必须声明为 `T | None = None`, 标量与列表保留空默认值. 漏标可空时单个 null 会让整条响应解析失败, 而 `_scrape*` 对解析失败与来源无内容都返回 `None`, 该错误只有 `debug` 级日志.

## 新爬虫接入

1. `enums.py` 加 `SiteName` (frozen dict 加载时按代码枚举补默认槽, 见 [config.md](config.md)).
2. 影片: `crawlers/sites/{site}.py` 实现 `profile` + `_search` / `_scrape` (或 override `fetch`); 演员: `crawlers/actor/sites/{site}.py`, `profile()` 声明 `capabilities` 与 `genders`. 消费 language 的影片爬虫设置 `multi_language=True`; 测试须覆盖番号有无短横线两种形态.
3. 导出后 `registry.register` / `actor_registry.register`. 双料站两个类用同一 `SiteName` 各注册一次; 不允许修改 `site_roles` 常量. 演员 `register` 顺序即默认 `profile_sites` 优先级. 需要 cookie / token 时给 `SiteConfig` 加字段.
4. 加 TOML 用例 (见 [crawler-testing.md](crawler-testing.md)) 并 `just test`.

## 特殊数据源: r18.dev 离线 PG 镜像

`src/amane/crawlers/r18dev/` + `sites/r18dev.py`. r18.dev 不提供逐番号 HTTP 接口, 而是发布完整 **PostgreSQL dump**; dump 是 PG 专用 (COPY / Identity / 角色系统), 无法转为 SQLite, 因此使用独立的只读 PG 镜像: 用户自备 PG 实例并提供连接串 (`hot.r18.dsn`, 需 CREATEDB / CREATEROLE), 项目负责建库 / 导入 / 原子换名 / 创建只读角色. 该库不纳入 Alembic (外部只读镜像, 定位同 `TranslationCache`, 见 [database.md](database.md)); 配置在 Hot, 修改 dsn 经由 `AppRuntime.rebuild()`.

`R18DevCrawler` override `fetch()` 用 SQL 替代 HTTP 两步; 只读 `R18Database` 由 `CrawlerFactory` 构造期特判注入. PG 未配置或镜像未导入时 `fetch()` 返回 `None`, 不中断多源聚合.

**固定 SQL 契约, 不映射全表**: r18 schema 不受本项目控制, 列可能随时变更, 因此用**固定显式列 SQL** 作为与 r18 schema 的唯一契约 (只点名用到的列), 结果映射进字段全 Optional 的宽松 Pydantic 读模型, 某列变 NULL / 缺失降级为空而非崩溃. `R18Repository.schema_probes()` 提供与运行时**同源**的探针 SQL, 导入器用它校验刚导入的临时库; 任一探针失败则拒绝原子换名, 线上停留在上一个 good 版本.

**导入流程** (`importer.py`, 经 `TaskType.R18_IMPORT` 由 worker 非内联执行): HEAD 探测 ETag → 与已导入版本比对 (持久化在 `data_dir/r18_import.json`, 相同则跳过) → 下载 → gunzip → 写入临时库 (`psql -f` 子进程) → schema 探针校验 → DROP 旧库 + RENAME 临时库 → 创建 / 授权只读角色. 依赖外部 `psql` (容器需 `postgresql-client`). **定时导入无专属配置**: 通过 Schedule API 手动创建 `r18_import` 例行任务, 与 cleanup / upscale 无区别.

**番号 → content_id 匹配**: r18 主键是 DMM `content_id` (`midv00123`), 输入是标准番号 (`MIDV-123`), 当前为基础实现 (dvd_id 精确 + content_id 三种零填充变体). 模糊匹配、service_code 优选、检索类查询的扩展点位于 `R18Repository`, 不影响爬虫接口.

**图片 URL 补全** (`mapper.py`): dump 中所有图片 URL 均为无域名、无扩展名的相对路径, 映射层负责补全 — `digital/video/` 与 `digital/amateur/` 走 DMM Digital 高清 CDN (`awsimgsrc.dmm.co.jp/pics_dig/`, 下载失败由下游 HttpClient 按机会主义降级), 其余路径走 `pics.dmm.co.jp/`, 均追加 `.jpg`. 剧照 dump 仅存首尾路径, 首尾编号之间为连续序列, 据此生成全量列表; `last` 以 `-0` 结尾视为单图标记.
