# LLM 能力

> 入口: `src/amane/llm/`. 本文记录 LLM 端口、dspy 边界, 以及翻译在刮削管线中的位置.
> 配置分层见 [config.md](config.md), 刮削管线见 [task-system.md](task-system.md).

## 端口抽象

`src/amane/llm/` 暴露两层协议, 管线只依赖协议, 不耦合具体 SDK:

- `LLMBackend` — 原始 chat 能力 (`ask`), 无业务语义. 翻译以外的未来用途 (分类/抽取) 复用同一后端.
- `Translator` — 翻译业务能力 (`translate`). `ScrapeHandler` 仅依赖此协议.

当前后端是 OpenAI 兼容实现 (`OpenAIBackend`, 复用已有 `openai` 依赖). 翻译只需一次 chat completion, 管线只依赖协议; 新增后端时管线不必修改.

### dspy 边界

翻译不使用 dspy. dspy 依赖 `dspy.configure` **全局状态**, 与 DI / `AppRuntime.rebuild` 冲突, 须用 `dspy.context` 逐调用绕开; 并引入 litellm / pandas / numpy, 对 Docker 部署偏重. 其价值在 prompt 优化 (MIPRO)、签名化结构抽取与带 metric 的编译式评估, 翻译用不到. 需要上述能力时再作为协议后端接入.

## 翻译嵌入点

接入位置在 `ScrapeHandler.handle`: `aggregate()` 之后、`upsert_metadata()` 之前 (`src/amane/handlers/scrape.py`).
翻译是对 `AggregatedMetadata` 文本字段的**就地变换**, 与 image materialize 并列为聚合后的后处理步骤.

关键约定:

- **机会主义降级**: translator 为 `None` (未配置) 或单字段翻译抛异常时, 保留原值、不阻断刮削 —— 与资源物化同款策略.
- **缓存语义不变**: `Metadata.raw` 仍存**源语言**站点快照; 译文是派生值, 写入 `Metadata` 标量字段.
- **独立限速**: LLM 端点用自己的 `AsyncLimiter` (`llm.rate_limit`), 与站点 host 限速器隔离.
- **覆盖字段**: 由 `llm.translate_fields` 控制, 当前仅文本标量 title/plot. 扩展到 tags/series 等需在 `_translate_metadata` 显式列出 (项目禁反射, 不用 getattr 动态分发).

## 译文缓存

翻译接在 `aggregate()` 下游, 输入是从 `raw` 重建的**源语言** metadata; 译文从不回写 `raw`.
若无缓存, **全缓存命中 + 配置不变的重刮仍会逐次重新翻译** —— 既烧 token, 又因 `temperature>0`
让译文在多次重刮间漂移. 这与项目"逐站复用、只补未成功站"的增量哲学相悖.

`TranslationCache` (`src/amane/llm/cache.py`) 补上这一层:

- **键 = `(源文本 sha256, 目标语言, 字段)`**. 不含 number —— 翻译输出只取决于文本, 跨番号天然去重
  (系列共用简介/相同标语只译一次). 含 field —— 因不同字段用不同提示词 (`translator._FIELD_HINT`),
  输出与字段相关. 不含 model/temperature.
- **独立 SQLite 文件** (`data_dir/translations.db`), **不纳入主 `amane.db`、不经由 Alembic**: 纯缓存, 仅
  `CREATE TABLE IF NOT EXISTS`, 可安全直接删除, 下次自动重建. 故意绕开 [database.md](database.md) 的迁移体系.
- **会话级注入**: `start_app` 创建 → `AppRuntime.translation_cache` → 穿过 `build_handlers`/`build_translator`.
  与 `ResourceStore` 同属"不随热重载重建"的对象; `rebuild()` 复用同一实例 (改 LLM 配置不丢缓存).
- **只缓存 LLM 路径**: 中文简繁 (zhconv) 廉价且确定, 不进缓存; 后端返回空也不写, 下次重试.

刮削 `use_cache` 的 `trans` 档控制是否读此缓存 (仍回写); 与 `metadata` 档的分工见 [task-system.md](task-system.md). 前端「强制刮削」发 `use_cache=["trans"]` — 重爬元数据、源文本不变则零 token.

> 换模型后想重译: model 不在键里, 旧译文仍会命中. 直接删除 `translations.db` 即可强制全部重译.


## 语言判定与简繁

判定逻辑在 `src/amane/utils/language.py`, 服务于"是否需要翻译"的决策, 非通用语言识别:

- **句子级前提**: 标题/简介必含假名 → "含假名" (`has_kana`) 即判日文. 纯汉字日文只出现在词级 (标签/人名), 不在翻译范围.
- **简繁不经由 LLM**: 简繁是字形差异而非语言差异. 中文文本一律交 `zhconv.convert` (幂等); 共用字 (简繁同形) 转换后等于原文 → 返回 `None`, 调用方保留原值. **无需检测**文本是简还是繁, 转换一次即可.
- 仅日↔中、英↔中等**跨语系**才调 LLM (`needs_llm_translation`).

> 踩坑: `is_ascii_only` 的字符类必须含方/花括号 `[]{}` —— `[HD]`/`[4K]` 在番号标题中极常见, 漏了会把英文标题误判为非英文而触发无谓翻译.
