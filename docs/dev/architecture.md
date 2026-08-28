# 系统架构

> 提交: `ec165d2`
>
> 本文只解释**为什么**这样划分以及**何时会失效**. 字段、签名、目录清单去源码中读.
> 配置系统见 [config.md](config.md), 数据模型见 [data-model.md](data-model.md), 任务流程见 [task-system.md](task-system.md).

## 模块边界

`src/amane/` 顶层包按**输入 → 输出**的处理阶段切分, 而非按层 (controller/service/dao). 这样新增爬虫站点或新增任务类型时, 改动局限在一两个包内.

| 包 | 边界 | 不变量 |
|----|------|--------|
| `parsing/` | 完整路径 / 自由文本 → 番号 + ContentType + 文件相位标记 | 纯函数, 无 I/O, 无配置依赖 |
| `crawlers/` | 番号 → `MediaMetadata`; 演员名 → `ActorMetadata` | 无状态; HTTP 与配置构造期注入; 影片/演员分 registry |
| `plugin/` | 第三方来源作者 SDK（再导出契约类型） | 插件只进口这里; 主机不进口 |
| `plugins/` | 来源插件主机（发现 / 落盘 / Factory） | 作者不进口; 契约见 [plugins.md](plugins.md) |
| `aggregate/` | 多源优先级 → `AggregatedMetadata` / `AggregatedActor` | 影片走抓取图波次; 演员为档案填空 + 头像优先; 不写 DB |
| `handlers/` | DB Task → 副作用 (写 metadata / 移动文件 / 排队) | 编排层, 不实现解析/爬取/IO 细节 |
| `media/` `organize/` | 元数据 + 路径模板 → 磁盘文件 | 调用方传配置, 自身不读 `HotSettings` 全局 |
| `llm/` | LLM 后端 + 翻译协议 + 译文缓存 | 管线只依赖协议, 不耦合具体 SDK |
| `agent/` | 助理 Agent (产品面 Amane) + Saved Query + 会话 trace | 读=任意只读 SQL; 写=封装工具 + pydantic-ai 渐进披露 (Capability); 与 `llm/` 配置分离 |
| `sr/` | 超分二进制封装 | Docker 用镜像内 patched waifu2x (有 ICD 走 GPU, 否则 ``-g -1`` / process_cpu). 桌面仍下上游 zip. realesrgan 无 CPU. |
| `db/` | SQLModel 表 + 异步 Repository (按聚合 mixin 拆分) | 单一数据源; 启动期自动 `alembic upgrade head` |
| `scheduler/` | 队列消费 / cron / 文件监控 / RSS 发现 | 与 api 解耦, 通过 EventBus 上报 |
| `observability/` | 进程级日志管线 + 单任务 Recorder | 叙事走 structlog; 任务产物落 `{log_dir}/tasks/task-{id}/` |
| `app/` | 进程组合根 (`AppRuntime` / `build_*` / `start_app`) | HTTP 与 CLI/回放共用; 不依赖 FastAPI; 拥有启停顺序 |
| `api/` | FastAPI 适配 (路由 / WS / `create_app`) | 不持有业务状态与生命周期编排; lifespan 只把 `AppSession` 挂到 `app.state.runtime`. 路由约定见 [api.md](api.md) |
| `net/` | curl_cffi WebClient + 限速器 | 速率限制按 host, 优先级见 `RateLimiters.from_config`; HTTP 录制经 `net.recording` 可选绑定 |
| `enums.py` | 跨包枚举 (站点名 / 字段名 / 语言) | 顶层避免循环依赖 — 不要把它拆进任何子包 |

**导入约定:** 优先从顶层包导入已导出的稳定符号 (如 `from amane.config import HotSettings`). 顶层 `__init__.py` 未导出的实现细节可从子模块导入 (如 `from amane.config.manager import LANG_METADATA_FIELD_SET`); 新增公开 API 时应补到顶层导出. 第三方来源插件只从 `amane.plugin` 进口; 主机用 `amane.plugins.*`.

## 启动编排

入口: `src/amane/server.py` 构造可编程 uvicorn (`timeout_graceful_shutdown=5`), lifespan 挂 `start_app`. Docker CMD / `just start` / 桌面 entry 都走这里; `just dev` 仍用 `uvicorn --reload`, 不设监督.

顺序非常关键, 颠倒会拿到未初始化或无配置的对象:

```
EventBus → 日志 → 来源插件发现 → 主 DB engine + Repository → r18 只读引擎 (可选)
        → RateLimiters → WebClient → HttpClient → CrawlerFactory
        → ResourceStore → TranslationCache → AgentService → safe_dirs + api_token
        → Handlers → AsyncWorker → CronScheduler → FeedService → WatcherService
```

要点:

- **EventBus 必须最先**, 因为日志 pipeline 会把 structlog 事件转发到 WebSocket. 颠倒会丢启动期日志.
- **RateLimiters 在 WebClient 之前**: 限速器是 host → leaky bucket 的查表, WebClient 持有引用, 重建限速器 = 重建 WebClient.
- **来源插件在网络栈之前发现**: descriptor 提供来源 URL、多语言能力和默认速率；配置中的外部来源 ID 要先经过当前插件目录校验，再构造 `CrawlerFactory`. 安装 / 卸载 / 重新扫描会在进程内替换该目录并走同一套 `rebuild()`（排空旧 Worker），不重启进程；见 [plugins.md](plugins.md).
- **CrawlerFactory 缓存爬虫实例**. 配置在构造函数注入并立即合并 (`_resolve_config`). 重建工厂只在 `HttpClient` 换了之后才有必要.
- **Handlers 在 Worker 之前**, Worker 启动后会立即开始 claim 任务 — handler map 必须就位.
- **CronScheduler / FeedService / WatcherService 在 Worker 之后**: 三者都会派生/触发任务, worker 须已就绪. Feed 间隔在源上, 见 [feeds.md](feeds.md).

停机时 lifespan `aclose` **先** `EventBus.close_all()` 再停 worker: 常驻 WS 否则会拖死 uvicorn graceful. 重启不是进程内 exec — 服务以退出码 3 退出 (避开 argparse 的 2), 由进程外拉起 (桌面监督循环, Docker `unless-stopped`). Docker 不区分退出码: 容器内 `exit 3` 仍会拉起; `docker stop` 走 SIGTERM → `exit 0`. 仅 `AMANE_SUPERVISED=1` 时重启端点可用, 见 [desktop.md](desktop.md) / [config.md](config.md).

## 跨切面

- **限速** — per-host 漏桶; 优先级与实现见 [crawlers.md](crawlers.md).
- **Resource** — URL → 本地文件, 一等存储非 LRU; 见 [data-model.md](data-model.md).
- **日志** — 三流 + 任务 Recorder; 见 [observability.md](observability.md).
