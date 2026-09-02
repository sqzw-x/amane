# Amane

![](https://img.shields.io/badge/License-GPLv3-blue.svg)

AI 时代的私人影库

## 功能特性

- **自动监控** — 实时监控磁盘文件变化并自动刮削
- **本地优先** — 持久化存储已获取数据, 构建个人影片仓库
- **多源择优** — 聚合多个数据源的元数据, 逐字段择优
- **目录管理** — 自定义命名规则, 整理本地文件结构
- **图片增强** — 内置超分工具优化低清海报图
- **AI 智能助理** — 自然语言检索片库、批量整理、发起刮削
- **Web 界面** — 海报墙、任务队列、结构化日志、可视化设置

## 安装

- [桌面应用 (macOS / Windows)](https://github.com/sqzw-x/amane/releases) — 下载即用
- [Docker](https://sqzw-x.github.io/amane/user/#docker) — 服务端部署

更多说明见 [用户文档](https://sqzw-x.github.io/amane/).

## 参与开发

前置依赖: [uv](https://github.com/astral-sh/uv), [pnpm](https://pnpm.io/), [just](https://github.com/casey/just)

```bash
just setup   # 同步依赖
just dev     # 启动 API 与前端
```

开发文档见 [docs/dev/index.md](docs/dev/index.md).

## 社区生态

可与本项目集成的外部工具或插件:

| 类型 | 标签 |
|------|------|
| 社区项目 | [`eco:project`](https://github.com/sqzw-x/amane/issues?q=is%3Aissue+label%3Aeco%3Aproject) |

这些项目由社区开发维护, 请自行评估其功能与安全性, 除公共 API 外不提供任何支持与保证.

## 相关项目

- [yoshiko2/Movie_Data_Capture](https://github.com/yoshiko2/Movie_Data_Capture)
- [moyy996/AVDC](https://github.com/moyy996/AVDC)
