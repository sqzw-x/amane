# 开发文档

> 本目录是**索引**, 不是手册: 目标是让 Agent 用最少的阅读量定位到一两个具体文件, 定位之后仍去读那些文件.
> 只记录跨文件的边界、顺序、契约、取舍与踩坑. 字段、签名、枚举去源码或 `web/openapi.json`; 单文件内部的原因与约束写在该文件的注释里. 归属与判据见 [AGENTS.md](../../AGENTS.md)「内容归属」.
> 用语见 [writing.md](writing.md).

阅读顺序:

1. [architecture.md](architecture.md) — 模块边界与启动编排
2. [config.md](config.md) — Cold / Hot 与进程内 rebuild
3. [data-model.md](data-model.md) — 所有权与多源聚合
4. [task-system.md](task-system.md) — 任务边界与 Worker

| 主题 | 文档 |
|---------|------|
| 注释 / 文档 / 提交说明 / 对话 | [writing.md](writing.md) (最高优先级) |
| 爬虫 / 采集 fixture | [crawlers.md](crawlers.md) (含番号入参) · [crawler-testing.md](crawler-testing.md) |
| 来源插件 (影片刮削 / 播放) | [plugins.md](plugins.md) · [crawlers.md](crawlers.md) |
| 站点覆盖 / 默认路由 | [content-routes.md](content-routes.md) |
| API 端点 | [api.md](api.md) |
| 前端文件索引 (路由 → 文件) | [frontend.md](frontend.md) |
| 表结构 / 迁移 | [database.md](database.md) |
| 翻译 / LLM | [llm.md](llm.md) |
| 助理 (首页对话) | [agent.md](agent.md) |
| 排障 / 回放刮削 | [observability.md](observability.md) |
| RSS/Atom 远程发现 | [feeds.md](feeds.md) |
| 文件发现 / CloudDrive webhook | [watcher.md](watcher.md) |
| 桌面菜单栏 / 托盘 / 打包 | [desktop.md](desktop.md) |
| Android 壳 (WebView / APK) | [android.md](android.md) |
| 后端测试 | [testing.md](testing.md) |

同一事实只出现在一个文档; 其它位置用相对链接.
