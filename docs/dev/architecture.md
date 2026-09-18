# 系统架构

> 本文只解释**为什么**这样划分以及**何时会失效**. 字段、签名、目录清单去源码中读.
> 配置系统见 [config.md](config.md), 数据模型见 [data-model.md](data-model.md), 任务流程见 [task-system.md](task-system.md).

## 模块边界

`src/amane/` 顶层包按**输入 → 输出**的处理阶段切分, 而非按层 (controller/service/dao). 新增爬虫站点或任务类型时改动局限在一两个包内.

| 包 | 边界 | 不变量 |
|----|------|--------|
| `parsing/` | 完整路径 / 自由文本 → 番号 + 类型 + 文件相位标记 | 纯函数, 无 I/O 与配置依赖; 路径解析会重写番号 (见 [crawlers.md](crawlers.md)) |
| `crawlers/` | 番号 → `MediaMetadata`; 演员名 → `ActorMetadata` | 无状态; HTTP 与配置在构造期注入; 影片 / 演员分 registry |
| `plugin/` | 第三方来源作者 SDK | 插件只导入这里; 主机不导入 |
| `plugins/` | 来源插件主机 (发现 / 落盘 / Factory) | 作者不导入; 契约见 [plugins.md](plugins.md) |
| `playback/` | 上游反向代理与 HLS 清单改写 | 浏览器只请求本机媒体端点; 码流走独立客户端; 不允许主机实时转码 |
| `aggregate/` | 多源优先级 → `AggregatedMetadata` / `AggregatedActor` | 影片按抓取图波次执行; 演员为档案填空 + 头像优先; 不写 DB |
| `handlers/` | DB Task → 副作用 (写 metadata / 移动文件 / 排队) | 编排层, 不实现解析 / 爬取 / IO 细节 |
| `media/` `organize/` | 元数据 + 路径模板 → 磁盘文件 | 调用方传配置, 自身不读 `HotSettings` 全局 |
| `llm/` | 大模型接入 + 翻译协议 + 译文缓存 | 管线只依赖 `Translator` 协议; provider 映射与 `agent/` 共用, 配置分离 |
| `agent/` | 助理 Agent + Saved Query + 会话 trace | 读为只读 SQL, 写只经封装工具; 与 `llm/` 配置分离, 见 [agent.md](agent.md) |
| `sr/` | 超分二进制封装 | 就地覆盖本地资源文件 |
| `db/` | SQLModel 表 + 异步 Repository | 单一数据源; 启动期自动 `alembic upgrade head` |
| `library/` | 库文件规则与分类 (`LibraryScan.classify`) + CloudDrive 虚拟路径规范 | 扩展名 / 预告片与黑名单正则 / `.amane_trash` / 体积阈值 / `cloud_path`; handlers 与 scheduler 共用, 不归任何一侧 |
| `scheduler/` | 队列消费 / cron / 文件监控 / CloudDrive webhook / RSS 发现 | 与 api 解耦, 经 EventBus 上报; webhook 契约见 [watcher.md](watcher.md) |
| `observability/` | 进程级日志管线 + 单任务 Recorder | 叙事经 structlog; 任务产物落 `{log_dir}/tasks/task-{id}/` |
| `app/` | 进程组合根 (`AppRuntime` / `build_*` / `start_app`) | HTTP 与 CLI / 回放共用; 不依赖 FastAPI; 拥有启停顺序 |
| `api/` | FastAPI 适配 (路由 / WS / `create_app`) | 不持有业务状态与生命周期编排; lifespan 把 `AppSession` 写入 `app.state.runtime`; 约定见 [api.md](api.md) |
| `net/` | curl_cffi WebClient + 按 host 的限速器 | 限速器必须先于 WebClient 构造; HTTP 录制经 `net.recording` 可选绑定 |
| `enums.py` | 跨包枚举 (站点名 / 字段名 / 语言) | 必须留在顶层; 拆进子包会形成循环依赖 |

**导入约定:** 包内模块使用相对导入, 最多三点 (`...`), 再深则改用 `from amane...`. 迁移脚本必须使用绝对导入 — Alembic 按文件路径加载, 无法识别相对导入. 包外 (测试、第三方插件、独立脚本) 从顶层包导入已导出的稳定符号. 插件与主机的导入边界见 [plugins.md](plugins.md).

## 启动编排

入口是 `amane.server` (可编程 uvicorn), lifespan 调用 `start_app`; Docker CMD 与 `just start` 执行 `python -m amane.server`. PyInstaller 使用文件作为运行入口 (`scripts/pyinstaller_entry.py`), 必须使用绝对导入. `just dev` 使用 `uvicorn --reload`, 不启用监督.

顺序非常关键, 颠倒会拿到未初始化或无配置的对象:

```
EventBus → 日志 → 来源插件发现 → 主 DB engine + Repository → r18 只读引擎 (可选)
        → RateLimiters → WebClient → HttpClient → CrawlerFactory
        → ResourceStore → TranslationCache → AgentService → safe_dirs + api_token
        → Handlers → AsyncWorker → CronScheduler → FeedService → WatcherService
```

- **EventBus 必须最先**: 日志 pipeline 把 structlog 事件转发到 WebSocket, 颠倒会丢启动期日志.
- **RateLimiters 在 WebClient 之前**: WebClient 持有漏桶引用, 重建限速器等于重建 WebClient.
- **来源插件在网络栈之前发现**: descriptor 提供来源 URL、多语言能力与默认速率, 配置中的外部来源 ID 须先经当前插件目录校验再构造 `CrawlerFactory`; 目录替换走同一套 `rebuild()`, 见 [plugins.md](plugins.md).
- **CrawlerFactory 缓存爬虫实例**, 只在 `HttpClient` 更换后才需重建.
- **Handlers 在 Worker 之前**: Worker 启动后立即 claim 任务, handler map 必须已就位.
- **PlaybackFactory 在插件目录之后**: 播放源只来自插件, 反代走独立流式客户端, rebuild 时替换并关闭旧客户端.
- **CronScheduler / FeedService / WatcherService 在 Worker 之后**: 三者都会派生任务.

停机时 lifespan `aclose` **先** `EventBus.close_all()` 再停 worker, 否则常驻 WS 会拖死 uvicorn graceful. 重启不是进程内 exec: 服务以退出码 3 退出 (避开 argparse 的 2), 由进程外监督者再次启动 (桌面壳的监督循环, Docker `unless-stopped`). uvicorn 的启动失败码同为 3, `amane.server.main` 将其改写为 4 供监督者区分启动失败与请求重启. Docker 不区分退出码, 容器内两种退出仍会再次启动, `docker stop` 发送 SIGTERM 则 `exit 0`. 仅 `AMANE_SUPERVISED=1` 时重启端点可用, 见 [desktop.md](desktop.md) / [config.md](config.md).

## 跨切面

- **限速** — per-host 漏桶; 优先级见 [crawlers.md](crawlers.md).
- **Resource** — URL → 本地文件, 一等存储非 LRU; 见 [data-model.md](data-model.md).
- **日志** — 三流 + 任务 Recorder; 见 [observability.md](observability.md).
- **事件循环上的磁盘 I/O** — 用户媒体目录与浏览路径 (含 FUSE / NAS) 上的 glob / stat / copy / move 一律经 `@in_thread` 提交线程池, 否则会阻塞健康检查与其它请求; `data_dir` 由进程自己管理, 可同步读写. 细则见 [task-system.md](task-system.md).
