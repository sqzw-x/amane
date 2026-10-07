# 项目用词规范

> 只登记用词: 顶层概念的两层定名、同一个词在不同位置的义项分工、明确禁止的说法.
> 源码里的符号与字段不在此登记, 照现有注释与文案写即可; 遣词造句见 [writing.md](writing.md).
> 用法: 引入新概念、写用户文案、给概念改名时查阅, 不要求通读.

列的含义: 开发中文用于注释、`docs/dev/`、提交说明与 PR; 用户中文用于 i18n 与 `docs/user/`, 不出现实现词 (Agent / SQL / facet / Worker / Provider 等). `—` 表示该层不使用此词.

## 顶层概念

跨模块的一等实体:

| 概念 | 开发中文 | 用户中文 |
|------|---------|---------|
| `Metadata` | 番号条目 | 影片 |
| `MediaFile` | 媒体文件 | 媒体文件 |
| `Resource` | 资源 | 资源 |
| `Library` | 媒体库 | 媒体库 |
| `Actor` | 演员 | 演员 |
| `Studio` | 片商 | 制作商 |
| `Feed` / `FeedItem` | 订阅源 / 条目 | 订阅源 / 历史条目 |
| `Schedule` / `Task` | 定时任务 / 任务 | 定时任务 / 任务 |
| `TaskLink` | 后继边 | 子任务 |
| `AgentSession` | 会话 | 会话 |
| `SavedQuery` | 查询预设 | 查询预设 |
| `facet` / `FacetKind` | 分类 / 分类种类 | 分类 |
| `agent` | 助理 | 智能体 |
| `.amane_trash` | 回收目录 | 回收站 |

## 动作与任务

项目自己的动作, 以及 `TaskType` 的取值:

| 动作 | 开发中文 | 用户中文 |
|------|---------|---------|
| `SCRAPE` | 刮削 | 刮削 |
| `ORGANIZE` | 整理 | 整理 |
| `REFRESH` | 扫描 | 扫描 |
| `CLEANUP` | 清理 | 清理 |
| `SCAN_INVALID` | 无效扫描 | 扫描无效文件 |
| `DELETE` | 删除 | 删除 |
| `RESCRAPE` | 重新刮削 | 重新刮削 |
| `UPSCALE` | 超分 | 超分 |
| `R18_IMPORT` | 导入 | R18 导入 |
| `classify` | 归类 | — |
| `register_media_file` | 文件注册 | 入库 |
| `rebuild` | 重建 | — |
| 回收 | `Resource` 的淘汰 | — |

## 易歧义

同一个词按位置分工, 不得互换:

| 中文词 | 只允许指 | 不允许指 |
|--------|---------|---------|
| 条目 | `FeedItem`、列表项、目录项 | `Metadata` (写番号条目或影片) |
| 分类 | `facet` 实体与分类页 | `ContentType` (内容类型)、`classify` (归类) |
| 类型 | `ContentType`、`SavedQuery.entity`、表单枚举 | 泛指的类别 (必须带限定词)、`FacetKind` (分类) |
| 来源 | 刮削来源 (`source`)、清单来源 (`InventorySource`) | `Feed` (订阅源)、日志 `source` 字段 |
| 站点 | 内置来源 (`SiteName`) | 插件来源 (写「来源」) |
| 回收 | `Resource` 的淘汰、回收目录 | 文件判废 (写「无效」) |
| 落盘 | `ORGANIZE` 写入库路径 | 一般的持久化 (写写入磁盘、落库) |
| 扫描 | `REFRESH` (库文件增删) | 插件重新发现 (写重新加载)、界面数据刷新 (写刷新) |
| 入库 | `MediaFile` 登记 | 写入数据库 (写落库) |
| 库 | `Library` (媒体库) | 数据库 (写 DB 或数据库) |
| 中字 | `has_subtitle` (影片带中文字幕) | 字幕文件 (`subtitle`) |
| 元数据 | `Metadata` 携带的数据本身 (元数据缓存) | 片库页 (`meta`, 用户层写「片库」) |
| 简介 | 演员档案文本 (`overview`) | 影片剧情 (`plot`) |
| 关联文件 | `connections` / `has_files` / `file_count` 三处同词 | 其它列表 |
| 上游 | LLM 上游、上游接口、上游 CDN | 播放链路的用户文案 (写「播放来源」) |
| 厂商 | 组件与包的发行方 (WebView 厂商包) | `Studio` (写制作商) |
| 提供者 | 插件返回的来源对象 (`provider`) | 上游模型服务 (用户层写「供应商」) |
| 能力 | 来源声明的能力 (`SourceCapability`) | 助理的工具组 (`Capability`, 写工具组) |
| 黑名单 | 文件黑名单正则、清理清单的命中原因 | `FacetRuleAction.BLOCK` (写剔除)、`field_blacklist` (写字段排除) |

## 禁用

| 禁止 | 使用 |
|------|------|
| 垃圾文件 | 无效文件 |
| 补刮 | 重新刮削 |
| 清晰度 | 分辨率 |
| 内容路由 | 类型路由 |
| 已空目录 | 空目录; 清单删完才会空的写「将变空」 |
| 档案站 / 档案源 | 资料来源 / 头像来源 |
| 入口 (指库外的链接文件) | 链接 (页面入口、webhook 入口等义项保留) |
| 对话文案里的 Agent / SQL | 智能体 / 查询预设 (查询预设页的 SQL 保留) |

## 豁免与约定

以下义项允许保留, 不必改写:

- **分辨率**: 文件名检测的档位 (`definition`) 与图像的像素量同词.
- **刷新**: 界面数据刷新与 `updated_at` 刷新, 不写「扫描」.
- **子任务**: 用户层既指 `TaskLink` 的父子关系, 也覆盖 `child_count` / `link_key` 的展示.
- **类型**: `ContentType` 可简称类型; 泛指的类别必须带限定词.
- **马赛克标记**: 值取 有码 / 无码标记 / 破解 / 流出, 与 `ContentType` 正交.
- **档案**: 用户层的「演员档案」指 `Actor` 的人物数据; 开发层的「档案」指 `CrawlerProfile` 的声明性事实.
- **拉丁词不译**: Token、SQL、WebView、NFO、STRM 等, 以及事件名与日志字段.
