# LLM 能力

> 入口: `src/amane/llm/`. 本文记录 LLM 接入层、dspy 边界, 以及翻译在刮削管线中的位置.
> 配置分层见 [config.md](config.md), 刮削管线见 [task-system.md](task-system.md).

## 端口抽象

翻译经 pydantic-ai 接入上游: `llm/model.py::build_model` 按 `ApiType` (chat / response / anthropic) 构造 `Model`, 与助理共用同一套 provider 映射, 但两边的凭据与配置各自独立 (见 [agent.md](agent.md)).

- **单轮调用不使用 Agent**: 请求经 `pydantic_ai.direct.model_request` 发出并取 `ModelResponse.text` — 该属性只拼接文本部分, 推理模型写在 `ThinkingPart` 的思维链天然排除; 写进正文的 `<think>` 块由翻译器剥离.
- **传输客户端归属**: 翻译路径自建 `httpx.AsyncClient` (超时 60 秒, 代理取 `network.proxy`) 交给 provider; 助理不传, 由 pydantic-ai 构造默认客户端.
- **重试由 SDK 按其默认策略承担**: Amane 不配置重试次数与退避. SDK 重试结束后仍失败, 或正文为空时, 翻译器返回 `None` 且不抛出 — 调用方保留原值.
- **限速在翻译器内**: `AsyncLimiter` 按 `llm.rate_limit` 包住一次翻译调用 (其内部重试由 SDK 承担).
- **`Translator` 协议**是管线唯一依赖的翻译面, `ScrapeHandler` 仅依赖此协议, 测试可用结构化替身实现.

### dspy 边界

翻译不使用 dspy: 它依赖 `dspy.configure` **全局状态**, 与 DI / `AppRuntime.rebuild` 冲突, 并引入 litellm / pandas / numpy 使 Docker 部署偏重; 其价值在 prompt 优化、签名化结构抽取与带 metric 的编译式评估, 翻译用不到. 需要上述能力时再作为独立调用路径接入.

## 翻译嵌入点

接入位置在 `ScrapeHandler.handle`: `aggregate()` 之后、`upsert_metadata()` 之前. 翻译是对 `AggregatedMetadata` 文本字段的**就地变换**, 与 image materialize 并列为聚合后的后处理步骤.

- **机会主义降级**: translator 为 `None` (未配置) 或单字段翻译抛异常时保留原值、不阻断刮削 — 与资源物化同款策略.
- **缓存语义不变**: `Metadata.raw` 仍存**源语言**站点快照; 译文是派生值, 写入 `Metadata` 标量字段.
- **独立限速**: LLM 端点用自己的 `AsyncLimiter`, 与站点 host 限速器隔离.
- **覆盖字段**: 由 `llm.translate_fields` 控制, 当前仅文本标量. 扩展到 tags / series 等须在 `_translate_metadata` 显式列出 (项目禁反射).

## 自定义提示词

`llm.system_prompt` 与 `llm.field_prompts` 覆盖内置提示词, 组装规则集中在 `translator.build_system_prompt`:

- **三段拼接**: system 提示词 = 指令 + 字段说明 + 输出约束. `system_prompt` 只替换指令, `field_prompts[field]` 只替换该字段的说明; 未配置、空串与纯空白等价, 一律回退内置; 无内置说明的字段不追加空段.
- **输出约束不在配置面内**: 「只输出译文本身」恒由 Amane 追加 — 译文写入标量字段, 附加解释会污染元数据.
- **占位符**: 两处都支持 `{target_lang}`, 其余花括号按字面保留 (提示词因此可以包含 JSON 示例).
- **长度上限**: 单条提示词上限 `config.manager.PROMPT_MAX_LENGTH`, 超长在配置校验阶段拒绝.

## 译文缓存

翻译接在 `aggregate()` 下游, 输入是从 `raw` 重建的**源语言** metadata; 译文从不回写 `raw`. 无缓存时, **全缓存命中 + 配置不变的重刮仍会逐次重新翻译** — 既烧 token, 又因 `temperature>0` 让译文在多次重刮间漂移. `TranslationCache` (`llm/cache.py`) 补上这一层:

- **键 = `(源文本 sha256, 目标语言, 字段, system 提示词 sha256)`**. 不含 number (翻译输出只取决于文本, 系列共用简介天然去重), 含 field (不同字段用不同说明), 含 system 提示词指纹 (实际发给模型的 system 内容变化即失效); 不含 model / temperature.
- **独立 SQLite 文件** (`data_dir/translations.db`), **不纳入主库、不经由 Alembic**: 纯缓存, 仅 `CREATE TABLE IF NOT EXISTS`, 可安全直接删除并在下次自动重建. 现有表的列集合与当前 schema 不符时整表 `DROP` 重建.
- **会话级注入**: `start_app` 创建 → `AppRuntime.translation_cache` → 穿过 `build_handlers` / `build_translator`; 与 `ResourceStore` 同属不随热重载重建的对象, `rebuild()` 复用同一实例 (改 LLM 配置不丢缓存).
- **只缓存 LLM 路径**: 中文简繁 (zhconv) 廉价且确定, 不进缓存; 模型返回空也不写, 下次重试.

刮削 `use_cache` 的 `trans` 档控制是否读此缓存 (仍回写), 与 `metadata` 档的分工见 [task-system.md](task-system.md); 前端「强制刮削」发 `use_cache=["trans"]`, 即重爬元数据、源文本不变则零 token. 换模型后想重译可直接删除 `translations.db` (model 不在键里); 修改提示词不需要删缓存 (指纹已在键中).

## 语言判定与简繁

判定逻辑在 `src/amane/utils/language.py`, 服务于「是否需要翻译」的决策, 非通用语言识别:

- **句子级前提**: 标题 / 简介必含假名 → 「含假名」即判日文; 纯汉字日文只出现在词级 (标签 / 人名), 不在翻译范围.
- **简繁不经由 LLM**: 简繁是字形差异而非语言差异, 中文文本一律交 `zhconv.convert` (幂等); 共用字转换后等于原文则返回 `None`, 调用方保留原值 — **无需检测**文本是简还是繁.
- 仅日↔中、英↔中等**跨语系**才调 LLM (`needs_llm_translation`).

> 踩坑: `is_ascii_only` 的字符类必须含方 / 花括号 `[]{}` — `[HD]` / `[4K]` 在番号标题中极常见, 漏了会把英文标题误判为非英文而触发无谓翻译.
