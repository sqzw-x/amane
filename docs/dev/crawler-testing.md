# 爬虫测试

> 提交: `6b932a5`
>
> 运行器: `tests/crawlers/test_crawlers.py` (影片) / `test_actor_crawlers.py` (演员). TOML 字段语法以解析器源码为准; 本文只写约定与仍会影响采集的坑.
> 爬虫架构见 [crawlers.md](crawlers.md).

## 为何数据驱动

新增用例只丢 TOML + 真实响应, 不动 Python. 真实 HTML/JSON 原样保存 (禁止编造、禁止裁剪); 同一爬虫多场景各自独立 TOML.

## Fixture 仓库

用例在独立仓库 `amane-testdata`; `just fixtures` 按 `tests/.fixtures-rev` 钉住的 sha 检出到 `tests/crawlers/cases/` (gitignored). `just test` 与 `just test-cov` 会先执行该依赖, `just check` 通过 `test-cov` 间接执行, 不单独声明. CI 经 `FIXTURES_DEPLOY_KEY` 走 SSH.

直接运行 `uv run pytest` 不会同步 fixture; fixture 获取失败时依赖 `cases/` 的用例 skip, **不会失败**. 刷新: testdata 仓库重新采集并提交, 再推进指针.

## 采集

**IP 重于工具.** 住宅 IP 下 curl 常能过; 机房 IP 下所有工具都可能被拦.

| 方法 | 适用 |
|------|------|
| curl (`-L` + cookie jar) | API / 低反爬 |
| 项目 `WebClient` (curl_cffi) | curl 过不了的中等反爬 |
| 无头浏览器 (`get_rendered`) | JS 渲染 (成功率低) |
| 真浏览器 DevTools → Copy Response | CF 盾 (往往是唯一可靠途径) |

每个 TOML **顶部注释必须记录**采集命令 (curl / `uv run python -c` / 浏览器步骤), 方便刷新. 一个 TOML 一个场景, 不要塞多 case.

布局 `cases/{site}/`: 纯演员站 TOML 在站点根; 同名也在影片 registry 的站, 影片在根、演员只放 `actor/` (影片 runner 忽略 `actor/` 段; 演员 runner 不回退根目录). 查询键: 影片 `number`, 演员 `name`. 覆盖 `fetch()` 的站只写 `[fetch]`. `[fetch].config` / `[search].config` / `[scrape].config` 注入 `SiteConfig` (theporndb `api_token` 等).

命名: 影片 `{番号小写}_{变体}.toml`; 演员 `{名字或场景}_{变体}.toml`. 响应与 TOML 同目录, `.html`→`get_text`, `.json`→`post_json`.

## Ground truth

`[scrape.expected]` / `[fetch.expected]` 每个字段默认 `==` 全等 (含 list 顺序与完整 URL). 宽松断言 (`field_contains` 等) **仅特殊情况**, 且必须在该键旁注释为何不能全等 (如签名 CDN). `responses[].url_contains` 是 mock 路由键, 不是 ground truth.

每个站点的 `[search]` **必须有空结果用例** (`expected_none = true`): `_search` 对不存在的番号须返回 `None`, 否则筛选 bug 会被首页/推广链接掩盖.

## 仍会影响解析的坑

- **`url_contains` 子串匹配**: 多条都命中时**第一条生效**. 更具体的模式放前面.
- **`live` 不能替代 mock**: `@pytest.mark.live` 在 CI 跳过; 回归必须可重复.
- **DMM 分类页结构不同**: Mono/DVD 仍是旧版 table (`/mono/dvd/.../cid={short}/`); monthly 已改 div (XPath 失效); digital / Fanza TV 走 GraphQL; rental 404. 全页 `//dt` 会把 monthly 双栏镜像翻倍 — 解析须先 `#multi-column`、再 `#single-column`, 最后才回退 table.
- **Wikipedia 演员页**: 条目引用里常出现「年齢認証」等词, 不能走 `get_html` 启发式 (会误判 `age_verification`); 维基正文用 `get_text`.

许多站点长期 CF / DNS / 关站, 不要为「测得到」而编造 fixture. 采集失败就 skip, 并在 TOML 注释里记下当时用的方法.

## 何时写 Python 测试

数据驱动只覆盖「输入 → 输出」解析. 下列放 `tests/crawlers/test_<site>.py`:

- 跨多个详情页的依赖 (如 official 路由转发)
- 副作用 (改 `partial_result`、调 fallback)
- 错误恢复 (5xx 重试、限速)
