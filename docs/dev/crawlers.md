# 爬虫开发

> 提交: `bcf8d0c`
>
> 入口: `src/amane/crawlers/`. 本文解释爬虫架构、HTTP 层设计、限速机制, 以及添加新爬虫的完整步骤.
> 测试约定见 [crawler-testing.md](crawler-testing.md). 默认路由与站点覆盖见 [content-routes.md](content-routes.md).
> 外部影片来源见 [plugins.md](plugins.md).

## 架构概览

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

外部影片插件在同一个 `CrawlerFactory` 中按来源 ID 延迟创建。插件返回的 provider 经过适配后满足影片爬虫的 `fetch()` 协议，统一进入聚合、限速、HTTP 记录和站点结果摘要。第三方来源 ID 规则见 [plugins.md](plugins.md).

**核心设计:** 爬虫异步并发安全. `SiteConfig` 构造期注入, `__init__` 里 `_resolve_config()` 合并 profile 默认与用户配置 — 子类直接用 `self.base_url` / `self.cookies`; 实例可缓存, 仅配置变化时重建工厂.

- 演员站与影片站共用 HttpClient / 限速; 实现在 `crawlers/actor/`, 只注册 `actor_registry` (可以不在影片 `registry`). **双料站** = 同一 `SiteName` 在影片 / 演员注册表各有一个类, 共用 `site_config`; 不要在 `site_roles` 里手写双料名单.
- gFriends 额外依赖 `data_dir` (Filetree 缓存) 与 `actor_scraping.gfriends_repo`.
- **能力声明**在 `CrawlerProfile`: 演员爬虫必须显式 `capabilities` (`ACTOR_PROFILE` / `ACTOR_IMAGE`) 与 `genders`; 影片爬虫空 `capabilities` 视为 `film_metadata`, 消费 `FetchOptions.language` 的设 `multi_language=True`. Stash 指纹匹配站设 `uses_file_hash=True`, 刮削前才计算 oshash, 扫描不读文件内容. `site_roles` 只从两个注册表推导配置 schema 用的站点列表 (档案序 = `actor_registry.register` 序); 聚合引擎只对推导出的 `MULTI_LANGUAGE_SITES` 展开 `(site, lang)` 节点. Handler 按 `Actor.gender` 对 `profile().genders` 裁站, 见 [task-system.md](task-system.md). 演员聚合契约见 [config.md](config.md) `actor_scraping`.
- 生日 / 发行日输出均为 `YYYY-MM-DD` (`normalize_calendar_date`); 非法文本丢弃, 不写脏串.

## Crawler 基类

`crawlers/base.py::Crawler` 是 Template Method: 公开 `fetch()` (日志; HTTP / 拦截失败冒泡 `SourceError`), 子类实现 `_search` (番号 → URL) 与 `_scrape` (URL → `MediaMetadata`). 特殊源 (official / theporndb / r18dev) 可直接 override `fetch()`.

`profile()` 类方法给出内置来源 ID / `base_url` / 能力与性别 / 可选 cookies 与限速 URL. `__init__` 在 `profile()` 之后自动 `_resolve_config()`, 子类不要再调一次. 外部来源不要求继承 `Crawler`，实现契约见 [plugins.md](plugins.md).

## 番号入参

`SearchQuery.number` 就是 `ScrapePayload.number` (`handlers/scrape.py`). Handler **不再**跑一遍解析. 进路不同则字符串形态不同, 爬虫不能假设「一定已经带短横线、一定是大写」.

| 进路 | 番号从哪来 | 会不会重写 |
|------|------------|------------|
| 扫库 / 按 `media_id` 刮削 | `parse_file_info(path).number` (`api/models/tasks.py` `ScrapeRequest.resolve`; REFRESH 同样) | **会.** 路径解析命中已知形态时改写成目录号: 插入 `-` (`HEYZO3607` → `HEYZO-3607`)、DMM 连写拆零 (`midv00123` → `MIDV-123`)、FC2/HEYZO 族规则、字母大写、剥 CD/字幕尾标. 规则只在 `parsing/file_info.py` `_match` / `_prepare`, 本文不抄表. |
| 用户任务只填 `number` | submission 字符串 | **不会.** 原样进 payload; 空 `content_type` 只调用 `infer_content_type` 猜片种, 猜类型不改字符串. |
| RSS 自动入队 | `extract_number` (即 `parse_file_info(text=…)` ) | **会** (与路径同一套已知形态). 未命中则没有番号, **不会**把标题原文当番号. 源上若设了 `number_pattern` 则只走该正则, 见 [feeds.md](feeds.md). |

落库 `Metadata.number` 也是这份 payload 原样 (UNIQUE 忽略大小写, 首次写入的大小写保留, 见 [data-model.md](data-model.md)). 因此补刮 / RESCRAPE 可能再次把无横线手填号送进爬虫.

**爬虫侧:** 站点检索和「哪条结果算命中」自己处理分隔符. 站内 ID 带 `-` 时, 入参 `HEYZO-3607` 与 `HEYZO3607` 都应能对上 (精确优先, 再忽略短横线/空格). **不要**把 `_` 收成 `-`, 除非该站把两种当成同一部 (`010115_001` 与 `010115-001` 常是两部). 站点特例写在 [content-routes.md](content-routes.md), 不在这里重复.

## HTTP 层

`WebClient` (`net/http.py`) 是唯一出站 HTTP 通道: 失败抛 `RequestError` (`SourceError` 子类, `failure` 挂在异常上). `ok_statuses` (如 RSS 304) 仍算成功. `HttpClient` (`crawlers/http.py`) 是薄封装 (`get_rendered` / 浏览器); 爬虫与插件都走它.

- HTML 页用 `get_html`: `get_text` + `classify_block`, 命中拦截/空页抛 `SourceError`
- JSON API 用 `get_json` / `post_json`, 不跑 HTML 启发式. `post_json` 载荷可以是 object 或 array (Yii 式 RPC)
- `download` / `ResourceStore.acquire` 是机会主义的: 调用方 `except RequestError: return None` / 返回 `bool`, 不走第二套错误通道

多 URL 试探 (DMM 搜索、Prestige SKU) 可在子类 `except RequestError: continue`; 一次成功响应都没有则把最后一次异常冒出去, 不要吞成裸 `None`.

### 拦截判定

模式表在 `net/errors.py::classify_block` (正文启发式优先, 失败响应正文次之, HTTP 状态兜底). `get_html` 是 HTML 站的入口; 爬虫不要再自己判拦截、不要 import Recorder. 来源 outcome 只在 `invoke_source` 写入, 见 [observability.md](observability.md).

## 限速

`RateLimiters` (`net/http.py`) 为每个 host 维护独立的平滑漏桶.

优先级 (高 → 低):
1. `network.rate_limits[host]` — 用户显式设置
2. `scraping.site_config[site].rate_limit` — 站点级
3. 默认值 (`network.default_rate_limit`, 默认 5 req/s)

host 优先级高于 site 的原因: 多个站点可能共享同一 host (官方路由/CDN), host 是更精确的颗粒度.

实现: `AsyncLimiter(1, 1/rate)` — 桶容量 1, 严格平滑无突发. 有意为之: 突发高峰触发反爬检测 (`67cbdb3`).

## 浏览器指纹

`WebClient` 基于 curl_cffi, 每次请求从预设列表 (`chrome123`, `chrome124`, `chrome131`, `chrome136`, `firefox133`, `firefox135`) 轮换指纹. 可选 Patchright 无头浏览器用于 JS 渲染页面 (`get_rendered`).

## 添加新爬虫

1. `enums.py` 加 `SiteName` (frozen dict 加载时按代码枚举补默认槽, 见 [config.md](config.md)).
2. 影片: `crawlers/sites/{site}.py` 实现 `profile` + `_search` / `_scrape` (或 override `fetch`). 演员: `crawlers/actor/sites/{site}.py`, `profile()` 声明 `capabilities` 与 `genders`. 消费 language 的影片爬虫设 `multi_language=True`. 番号入参形态见上节, 不要只测带短横线的一种.
3. 导出后 `registry.register` / `actor_registry.register`. 双料站两个类同一 `SiteName`, 再 register 一次即可; **不要**改 `site_roles` 常量. 演员 `register` 顺序即默认 `profile_sites` 优先级.
4. 需要 cookie/token 时给 `SiteConfig` 加字段.
5. 加 TOML 用例, 见 [crawler-testing.md](crawler-testing.md).
6. `just test`.

## 特殊数据源: r18.dev 离线 PG 镜像

`src/amane/crawlers/r18dev/` + `sites/r18dev.py`. r18.dev 不提供逐番号 HTTP 接口, 而是发布完整
**PostgreSQL dump**. 这个源与其它所有爬虫范式相反, 设计上有几个刻意取舍:

**外部 PG, 项目托管.** 当前项目主库是 SQLite, 但 r18 dump 是 PG 专用 (COPY/Identity/角色系统),
无法转 SQLite. 因此引入一个**独立的只读 PG 镜像**: 用户自备 PG 实例并提供连接串 (`hot.r18.dsn`,
需 CREATEDB/CREATEROLE), 项目负责建库 / 导入 dump / 原子换名 / 创建只读角色全套. 这个库**不进
Alembic** (外部只读镜像, 定位同 `TranslationCache`, 见 [database.md](database.md)).

**配置在 Hot.** 改 dsn 走 `AppRuntime.rebuild()`, 见 [config.md](config.md).

**爬虫形态.** `R18DevCrawler` override `fetch()` 用 SQL 替代 HTTP 两步. 只读 `R18Database` 由 `CrawlerFactory` 构造期特判注入 (唯一需要 HTTP 之外依赖的爬虫). PG 未配置或镜像未导入时 `fetch()` 返回 `None`, 不中断多源聚合.

**鲁棒性: 固定 SQL 契约, 不映射全表.** r18 schema 不受我们控制, 随时可能改列. 对策三层:
- `repository.py` 用**固定显式列 SQL** (`SELECT content_id, dvd_id, ...`) 作为与 r18 schema 的
  **唯一契约** — 只点名我们用到的列, r18 新增/改动无关列零影响 (不映射全表, 刻意收窄依赖面).
- 结果映射进 `models.py` 的**宽松 Pydantic 读模型** (字段全 Optional + 默认值), 某列变 NULL/缺失
  降级为空而非崩溃.
- `R18Repository.schema_probes()` 提供与运行时**同源**的探针 SQL, 导入器用它校验刚导入的临时库;
  任一探针失败 (列被删/改名/类型不兼容) → 拒绝原子换名, 线上停留在上一个 good 版本. **坏 dump
  最多导致一次被跳过的导入, 永不污染线上.**

**导入流程** (`importer.py`, `TaskType.R18_IMPORT` 走 worker 非内联): HEAD 探测 ETag → 与已导入
版本比对 (持久化在 `data_dir/r18_import.json`, 相同则跳过) → 下载 → gunzip → 灌临时库 (`psql -f`
子进程) → schema 探针校验 → DROP 旧库 + RENAME 临时库 → 创建/授权只读角色. 依赖外部 `psql`
(容器需 `postgresql-client`). **定时导入无专属配置**: 用户像添加其它定时任务一样, 通过 Schedule
API 手动创建 `r18_import` 例行任务 (`RoutineType.R18_IMPORT`), 与 cleanup/upscale 无区别.

**番号 → content_id 匹配** (`repository.content_id_candidates`): r18 主键是 DMM `content_id`
(`midv00123`), 输入是标准番号 (`MIDV-123`). 当前为基础实现 (dvd_id 精确 + content_id 三变体:
5 位零填充 / 保留原始位数 / 去零), **刻意留余量**: 可后续加模糊匹配 / service_code 优选 /
检索类查询 (按演员列出全部作品等) —— 这些都挂在 `R18Repository` 上扩展, 不影响爬虫接口.

**图片 URL 补全** (`mapper.py`): r18 dump 中所有图片 URL 均为无域名、无扩展名的相对路径
(如 `digital/video/1hbad00051/1hbad00051pl`). 映射层负责补全:

- `digital/video/` 和 `digital/amateur/` 路径 → `awsimgsrc.dmm.co.jp/pics_dig/` (DMM Digital
  API 同源高清 CDN), 追加 `.jpg`. 优先用高清源, 下载失败由下游 HttpClient 按机会主义降级.
- 其余路径 (`mono/movie/`, `digital/e-book/` 等) → `pics.dmm.co.jp/` (通用标准分辨率),
  追加 `.jpg`.
- 剧照仅存首尾路径 (`gallery_full_first/last`), 首尾编号间为连续序列, `_generate_gallery`
  据此生成全量列表. `last` 以 `-0` 结尾视为单图标记 (仅保留 first).
