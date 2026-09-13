# 远程发现源 (RSS/Atom)

> 与 Watcher / Schedule 的分工、去重真值、每源配置契约. 字段去源码.

## 定位

`Feed` 是远程目录事件源, 与 `Library` + Watcher (本地目录事件) 并列. 产物只有 by-number `SCRAPE` (`media_file_id=None`); 不下载 enclosure / 种子, 不写入 Library, 文件之后出现仍由 Watcher 关联 MediaFile.

**不是** `Schedule`: Schedule 是用户 routine → 入队 Task 并占用 Worker, Feed 拉取是生产者, 间隔写在源上 (`interval_seconds` + `next_fetch_at`), 不纳入 HotSettings, 不并入 `CronScheduler`.

没有原生 RSS 的站点用站外适配器转成 feed, 产品不内置站点列表爬虫. `name` 可空; 成功解析且当前名为空时写入 feed 自带标题, 不覆盖用户填的名称.

`Feed.group` 是斜杠伪路径, 空串为未分组; 库内不存目录实体或父子关系, 前端按 `/` 拆段建树. 规范化: 去首尾空白与斜杠、折叠重复分隔、反斜杠当斜杠; 路径段不能是 `.` / `..` 或含控制字符.

## 调度

`FeedService` (`scheduler/feeds.py`) 每 60s 扫描 `enabled AND (next_fetch_at IS NULL OR <= now)`. `enabled` 只控制是否进入这轮扫描 — 关闭后创建时的立即拉取与 `POST /feeds/{id}/poll` 仍执行. 完成后按该源间隔排列下次; 失败也按间隔重试, 不另做指数退避. `rebuild()` 只替换 WebClient 引用, 循环不停.

`auto_enqueue` 只决定发现**新**条目且解析出番号时是否入队 SCRAPE (`priority=-1`, 避免一次追赶抢占手动刮削的优先级); 关闭后仍写 `FeedItem`, 定时与立即拉取经由同一 `poll_one` 遵守同一开关. 已见过的 `item_key` 不会在后来打开开关时补入队.

`ignore_keywords` 是每源字面量列表 (大小写不敏感子串, 去空白去重), 只匹配标题与番号不匹配正文; 新条目命中任一关键词则写入 `ignored_at` 且不入队 SCRAPE. 保存该列表时当前未忽略的匹配条目同样写入忽略; 取消忽略后不会在下次拉取自动再忽略同一 `item_key`. 空列表关闭过滤.

## 去重与历史

两项相互独立:

- **HTTP 304**: `Feed.etag` / `last_modified`; 整份 XML 没变则不解析. 304 不能当失败重试.
- **条目身份**: `FeedItem(feed_id, item_key)` UNIQUE, `item_key` = guid → link → title. 同一条目不再入队; 解析失败也插一行 (`number` 空), 避免每轮重试垃圾标题.

不去重 `Metadata.number` — 已有条目仍入队 SCRAPE (默认 `use_cache`); 同一 tick、同一源内相同番号只入队一次. 删 Feed 应用层级联删 FeedItem, CLEANUP 不处理此表.

`published_at` 是源给出的发布时间 (RSS `pubDate` / Atom `published`, 没有再用 `updated`); 历史列表按它新→旧, 没有才回退 `created_at`, 再按 `id`. `created_at` 只是首次写入历史的时间, 同一次拉取里多条可能落在同一秒, 不能当主排序. `ignored_at` 非空表示用户忽略, `read_at` 非空表示用户已读 — 二者都与忽略正交, 不改变 `(feed_id, item_key)` 去重关系. `description` 与 `published_at` 只在首次写入或当时为空时回填, 不随源更新.

历史列表默认只返回未忽略条目, `state=active|ignored|all` 切换忽略视图, `read=unread|read|all` 切换已读视图 (**默认 `all`**, 调用方不传时结果集不变), 两项同时生效. `search` 同时检索标题、番号、正文、链接和 `item_key`. 响应保留 `ignored_at` / `read_at` / `published_at`, 由前端据此展示状态与时间; 日期分段标题与「同番号折叠」都是前端显示层, 不改 API 排序.

跨源列表 `GET /feeds/items` (注册顺序见 [api.md](api.md)): `feed_id` 优先于 `group`; `group` 缺省为全部, 空串只含未分组源, 非空为前缀匹配. 单源列表经由 `GET /feeds/{id}/items`.

按番号关联片库时, 列表先按筛选 / 排序取出当前页 id (不读 `description`), 再只对这些行 JOIN Metadata 返回 `metadata_id`. JOIN 必须用 `number COLLATE NOCASE` 才能使用 `ix_metadata_number` — 对列套 `lower()` 会让 SQLite 在 LIMIT 前做 nested loop. 排序索引是表达式 `coalesce(published_at, created_at), id`, 必须与 ORDER BY 一致. 前端不按番号逐条搜片库 (现有 `GET /metadata?search=` 是模糊检索且会 N+1).

历史操作统一经由 `POST /feeds/{id}/items/batch`, body 为一个 `action` 与一组 FeedItem ID; 阅读器跨源多选时按 `feed_id` 分组多次调用. 输入 ID 先去重, 只处理属于当前 Feed 的行, 不存在或跨 Feed 的计入 `missing`; 前四个 action 在一个 Repository 事务内执行且幂等, `scrape` 在一次任务创建事务中批量入队. `affected` 包含匹配到但状态本来就一致的行.

- `ignore`: 保留历史和去重记录, 不取消已入队 / 运行中 / 已完成的 SCRAPE; 后续拉取不会重新创建或入队.
- `unignore`: 清除忽略状态, 只恢复可见性, 不自动提交 SCRAPE.
- `read` / `unread`: 写 / 清 `read_at`, 幂等, 不改变忽略状态, 不入队 SCRAPE.
- `delete`: 永久删除历史行, 不影响已有 Task / Metadata / Resource; 远程源再次返回相同 `item_key` 时重新视为新条目.
- `scrape`: 按当前 Feed 的 `content_type` / `use_cache` 为有番号条目创建番号级 SCRAPE; 同批次番号大小写不敏感去重, 无番号计入 `skipped`, 手动任务优先级为 `0`. 配置在任务创建时写入 payload, 后续修改 Feed 不影响已入队任务.

阅读器 (`/feeds`) 只消费条目, 源 CRUD 在 `/feeds/sources`; 被忽略条目仍允许手动刮削, 无番号仍可参与批量操作 (由后端计入 `skipped`). FeedItem 没有独立于 batch action 的手动刮削端点.

## 每源刮削配置

- `number_pattern` 一旦设置, **只**采用该正则 (title → description → link; 有捕获组用 group 1), 不回退 `extract_number`; 未设置才用 `extract_number` (命中已知形态则按 `parse_file_info` 重写). 重写与爬虫入参契约见 [crawlers.md](crawlers.md).
- `content_type` 是 `ContentType | None`: 有值原样写入 payload, 空则 `infer_content_type`.
- `use_cache` 与手动 SCRAPE 同语义; 空集 = 强制刷新.

解析经由已下载 bytes + `feedparser.parse`; **禁止把 URL 交给 feedparser** (它会自己发 HTTP).

## OPML 导入

前端 `DOMParser` 抽出 `outline` 的 `xmlUrl` (属性名大小写不敏感), 无 xmlUrl 的祖先 outline 拼成 `group` 伪路径; 导入时可再加一层公共前缀. `auto_enqueue` 默认关, 导入面板可修改. 服务端不解析 OPML. 已存在 URL 在预览里禁用, 409 仍当跳过. 创建会立刻 `poll_one` 追赶, 所以导入是串行的.
