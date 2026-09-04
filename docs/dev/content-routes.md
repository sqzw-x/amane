# 内容路由与站点特性

> 默认 `content_routes` 的取舍、各源覆盖与站点特例. 资格真值 / `field_priority` 编译见 [config.md](config.md); 建图见 [task-system.md](task-system.md). 成员与顺序以 `src/amane/config/manager.py` `_DEFAULT_CONTENT_ROUTES` 为准, 本文不列出默认表.

站点可被搜索命中, 不构成纳入默认路由的条件. 默认表与新爬虫的取舍见下文各节.

## 原则

- 类型专属源靠前, 综合索引垫后.
- 厂牌站若对不匹配的番号仍发 HTTP, 不纳入默认表 (用户可按前缀自行加).
- 未配置即立即返回 `None` 的源 (theporndb 无 token、r18dev 无 PG、official 前缀未命中) 可以垫后: `fetch` 立刻 `None`.
- 会把无关番号拼成「看起来像详情页」的站不纳入默认表.

iqqtv 作为有碼中文标题源时: 加入 **censored** 路由, 再在 `field_priority.title` 里提前. 默认不放入有碼, 否则每部有碼片都会请求中文站.

## 类型取舍

| 类型 | 意图 | 不纳入默认表 |
|------|------|----------|
| censored | 中文索引 → FANZA 权威图文 → 稳定镜像 → 可空返回的官网 / r18 | 单厂牌 API (Prestige 等对每个候选 SKU 都请求); iqqtv (见上) |
| uncensored | 有独立无碼区的索引 + 无碼专站 | kin8 (从任意番号抽数字拼详情, `HEYZO-3363` 会命中 kin8 的 3363) |
| fc2 | javdb 分类 + 专用索引 + 官方电子市场 + BT 垫后 | fc2club (跳转镜像不稳定) |
| chinese | 有國產区的中文站第一, 综合/BT 兜底 | javdb 当第一源 (它没有国产分区, 见下) |
| amateur | MGS 第一; FANZA 有素人频道; javbus 首页能见到 `300MIUM-*` | MGS 不纳入有碼默认, 否则 MIDV 会请求 MGS |
| western | TPDB 第一 (需 token 才真正请求); 有歐美分类的索引垫后 | javbus 欧美域 (见下, 目录空); 不实现该域爬虫 |
| hentai | getchu 对路径关键词分类的里番第一; DMM 动画/同人; javdb 兜底 | getchu 商品是数字 id 不是 JAV 番号, 仅因路径分类而纳入此链 |

## 综合索引

**javdb.com** — 导航分有碼 / 無碼 / 歐美 / FC2 / 動漫, **没有国产分区**. 片商下拉会置顶「麻豆傳媒映畫」, 热搜也有「麻豆」, 个别国产厂当日本片商收录, 不是独立目录. 搜索带 `locale=zh`. 几乎所有类型的默认成员. 默认 `use_proxy=True`; 部分网络会命中版权地域拦截页, 欧美列表新鲜度在这类网络上核不准.

独立层, 不是 FANZA 的无差别镜像:

- 中文 `current-title` + 隐藏日文 `origin-title` (爬虫只取中文标题, origin 未用)
- 演員栏: `<a>名</a><strong class="symbol female|male">`, 标记在名字之后, 同 `span.value` 内男女并存. 两边都进入 `actors` 并带性别.
- 社区评分, 不是 FANZA 店评
- 封面/样品在 `jdbstatic.com` (通常是重编码后的柜台图)
- 片商名跟官网英文商标 (`S1 NO.1 STYLE`), 不跟 DMM 日文メーカー名

**javbus.com** — 有碼首页、無碼 `/uncensored` (Caribbean / 1pondo 风格日期番号). 有碼首页会混素人号 (`300MIUM-*`). 搜索回退带 `parent=ce` (有碼); 无碼主要靠 `/{number}` 直达. 不允许放入欧美默认路由.

欧美入口指向 `javbus.org` (不是 `javbus.hair`). `javbus.org` GET 正文可以是字面 `404`; 浏览器能打开壳页, 关公告后没有影片网格. `javbus.hair` 证书无效、HTTP 空响应. **欧美目录不可用.**

有碼详情 `extrafanart` 热链 `pics.dmm.co.jp`; 标签含「DMM獨家」; 发行日与 DMM 配信日相同; 片商用 DMM 日文名. 封面缩略图位于 javbus `/pics/`. **不解析 plot.** 无碼是另一套目录.

**freejavbt.com** — 显式分有碼 / 無碼 / 歐美 / FC2, 首页还有「國產」「成人動畫」. 覆盖最宽的 BT 向索引, 元数据质量一般, 适合垫后.

**avsox.click** — 日本无码情报站. 同源: AVMOO=有碼 (`avmoo.shop`)、AVSOX=无碼、AVHEAT=欧美 (`avheat.shop`). 项目只接入 AVSOX. Vue SPA, 中文入口 `/cn`; 壳页 `#javu-site-index` 没有影片 DOM. 元数据经由 POST `/javu/data/api/search` 与 `/javu/data/api/getMovie` (Yii CSRF: 先 GET `/cn/` 取 `csrf-token`, body 是 JSON 数组). 详情 URL `/{lang}/movies/{movieId}`. 默认 `lang=cn`. 搜索 API 对 `HEYZO-3607` / `HEYZO3607` 都能命中, 返回的 `movieFanHao` 带短横线; 选取时先大小写精确匹配, 再忽略短横线/空格, **不**把下划线当成短横线 (`010115_001` 与 `010115-001` 是两部). 对不上不取第一条.

**jav321.com** — 标题带 dmm, 导航分 AV / 素人. 属于 DMM 目录镜像; 有碼与素人垫后. 无无碼 / FC2 / 欧美入口. 日文标签即メーカー / 配信開始日 / ジャンル.

**javlibrary.com** — 常命中 Cloudflare 等待页. 不纳入默认表.

**airav.io** — 中文「女優 av 查詢」, 主导航是日本 AV / 發行商 / 女優, 不是国产分区. 中文标题补强; 国产路由里垫在 iqqtv 后面.

**iqqtv** (`iqq5.xyz` 会跳转到 `iqqk4.quest` 一类轮换域) — 导航有日本 / **國產** / 無碼 / 歐美 / 卡通 / 漫畫. 國產区是爱豆傳媒、杏吧傳媒等, 不是 MD 号为主. 国产路由第一源. 爬虫带 `/cn|/jp` 语言前缀.

## 类型专属 / 官方

**mgstage.com** (`adc=1` 通过年龄墙) — Prestige 集团素人站. 首页同时有 `ABF-*` (Prestige 有碼号, 番号分类会判成 censored) 和 `300MIUM-*` / `893NYN-*`. 另有 SOD 厂牌入口. 素人路由第一源.

**dmm.co.jp / FANZA** — ビデオ、**素人** (`/amateur/`)、アニメ、同人ゲーム. 有碼权威源, 高清图. 素人第二源. 里番可垫. 不是 FC2 / 欧美 / 国产站.

**adult.contents.fc2.com** — 官方 FC2 电子市场, 商品 ID 纯数字 (`/article/4961587/`). 元数据偏卖家自填, 不如索引站完整, 但是第一方. FC2 路由里置于 javdb / fc2ppvdb 之后.

**fc2ppvdb.com** — FC2 专用索引. 可能被 Cloudflare Access denied. 爬虫按数字拼 `/articles/{id}`. 仍纳入 FC2 默认 (专用站); 拦截后回落到后续源.

**fc2club.top** — 打开后跳转 `ww547.fc2club.top`. 镜像不稳定, 不纳入默认表.

**getchu.com** — 美少女游戏 / 同人 / 动画周边, 商品是数字 id. 里番类型靠路径关键词 (`getchu`/`里番`) 分类, 所以是里番第一源. HTTP 首页偏慢, 会跳转到 https.

**kin8tengoku.com** — 「金髪洋物専門 無修正」. 从任意番号抽数字拼 `/moviepages/{id}/`, 会误匹配其它番号. 不纳入默认表.

**theporndb.net** — 未登录跳转 `/login`. GraphQL 无 token 时影片/演员爬虫都直接 `None`. 欧美路由第一. uncensored fixture 经由 `?type=Scene`. 演员经由 `searchPerformer`, 精确匹配 name/aliases, 不回退首条; `deleted` 且有 `merged_into_id` 则 `findPerformer` 跟随, 无合并目标则丢弃; 头像按宽×高排序; `urls` 写入 `provider_ids` (iafd / twitter 等, 跳过本站链接); `disambiguation` 填 `tagline`. 与影片共用 `site_config.api_token`. stash-box 响应中的 `death_date`、出道/引退年、发色/瞳色、`ethnicity`、`breast_type`、纹身穿孔不写入列.

**official** — Will/Outvision 官网集群. 前缀对不上不发 HTTP, 可以垫在有碼末尾.

**r18dev** — 未配 PG 直接 skip. 有碼末尾. dump 图片路径补全后是 DMM CDN, 见 [crawlers.md](crawlers.md).

**prestige / faleno / dahlia / giga** — 单厂牌. Prestige 对每个候选 SKU 发送 API 请求. 不纳入默认表.

**xcity.jp** — 与 DMM 重叠, 年龄墙严格. 不纳入默认表.

## 官网与 FANZA

厂牌文案是一手, FANZA 是零售总目录, 官网是厂牌营销页. 两套都不是从对方爬来的, 也不是超集.

制作委员会写包装文案 (日文标题、女优、类型、时长), 同时送到:

1. **厂牌官网** (Will/Outvision CMS). 营销页: 日文标题、女优、ジャンル、発売日、自家 CDN 摄影. 只有横版封面, 无竖版海报; 常见缺口: 导演、剧情、评分、sample gallery.
2. **FANZA (DMM)** 最大数字分发柜台. 同一段日文标题会再出现, 另叠零售层: 配信開始日 (通常早于発売日)、用户评分、独占/4K 柜台标签、竖版包装封面、`pics.dmm.co.jp` 样品、预告、plot. 导演也在柜台页.

同一番号的日期是 SKU 分层: DMM/javbus 用配信開始日, official 用発売日, javdb 自选一个零售日, 三者不必相等. javdb 隐藏的 `origin-title` 可能带 FANZA/mono 碟片特典后缀 (官网作品页标题没有), 所以 javdb「原标题」来自零售目录.

FANZA 覆盖几乎全部有碼厂, 图片可热链, 字段齐全 (含 plot). 官网按前缀路由数十个域, 且缺少 plot / 海报 / 导演. **权威图文采用 DMM; 官网在需要厂牌摄影或発売日时具有独立价值.**

## 索引站来源关系

**javbus** = FANZA 目录映射 + 磁力 (见上). **jav321 / r18dev / 多数 javlibrary 类站** 是同一 FANZA 目录的镜像或离线快照.

**javdb** 见综合索引独立层. 中文站 (iqqtv / airav) 有自己的剧情翻译, 底本仍是日文柜台文案.

综合索引 javdb / javbus **不解析 plot**. 长简介来自柜台、中文站、专用源 (DMM / r18dev / jav321 / MGS / Prestige / iqqtv / airav / FC2 / theporndb).

## 覆盖缺口

**javdb 国产.** 没有分区. 麻豆是置顶片商 + 热搜词. 爱豆 / 杏吧不会作为分区出现. 国产路由以 iqqtv 为第一源, javdb 只当「搜得到就用」的兜底.

**javdb 欧美.** 导航和排行 `t=western` 仍在, 但欧美不是主库存; 编号 (studio.YY.MM.DD) 与 JAV 番号不同. 地域拦截时无法核列表. **欧美默认第一源为 theporndb** (Stash-box). javbus 欧美域空, 不实现该域爬虫. AVSOX 家族的 AVHEAT (`avheat.shop`) 是未接入的欧美索引, 优先级仍低于 TPDB.
