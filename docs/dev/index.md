# 开发文档

> 提交: `5214796`
>
> 只写跨文件的边界、顺序、契约、取舍与踩坑. 字段、签名、枚举去源码或 `web/openapi.json`.

新人按此顺序建立全局认知:

1. [architecture.md](architecture.md) — 模块边界与启动编排
2. [config.md](config.md) — Cold / Hot 与热重载
3. [data-model.md](data-model.md) — 所有权与多源聚合
4. [task-system.md](task-system.md) — 任务边界与 Worker

| 要做的事 | 文档 |
|---------|------|
| 加爬虫 / 采集 fixture | [crawlers.md](crawlers.md) · [crawler-testing.md](crawler-testing.md) |
| 改来源插件主机契约 | [plugins.md](plugins.md) · [crawlers.md](crawlers.md) |
| 查站点覆盖 / 改默认路由 | [content-routes.md](content-routes.md) |
| 加 API 端点 | [api.md](api.md) |
| 改前端 / Schema 表单 | [frontend.md](frontend.md) |
| 改表结构 / 迁移 | [database.md](database.md) |
| 翻译 / LLM | [llm.md](llm.md) |
| 助理 (首页对话) | [agent.md](agent.md) |
| 排障 / 回放刮削 | [observability.md](observability.md) |
| RSS/Atom 远程发现 | [feeds.md](feeds.md) |
| 桌面菜单栏 / 托盘 / 打包 | [desktop.md](desktop.md) |
| 加后端测试 | [testing.md](testing.md) |

同一事实只出现在一个文档; 其它位置用相对链接.
