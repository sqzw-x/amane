# 开发路线图

## 后端

- 数据库与数据模型
  - [ ] 反思: 导演和演员分开是否有意义, 感觉可以将相关概念合并为 person, 加一个 role 字段
- 任务系统
  - [x] 任务后继图基础设施: TaskLink、`TaskResult.followups`、Worker 完成阶段统一派生子任务. 设计见 [plan/task-graph.md](plan/task-graph.md)
    - [x] 现有 fan-out (REFRESH/RESCRAPE→SCRAPE, SCRAPE→ACTOR_SCRAPE) 迁入图
    - [x] 任务树视图 (前端嵌套树 + `children` API)
    - [ ] 静态后继声明 (Feed 刮削成功后挂 UserTag 等)
    - [ ] 自动整理等库级流水依赖此图, 不要在 Handler 里继续链式 create_task
- 助理 Agent
- 刮削与爬虫
  - [ ] 实现 use_browser, 考虑通过 HttpClient 封装 curl 和浏览器的差异
    - 低优先级; 采集方法见 [crawler-testing.md](dev/crawler-testing.md)
- 定时任务
- 资源管理
- [ ] RSS Feed: 刮削成功后按来源自动为入库元数据添加 UserTag (任务后继设计见 [task-graph.md](plan/task-graph.md))

## 前端

- UX
- 媒体库
- 元数据页
- 定时任务页
- 设置页
- 日志页

## mac app

## Windows app

## 未来方向

- 通过 Emby API 同步演员人物信息到媒体服务器
- 应用内换包 / 自动安装 (当前只做版本检查)
- bot 集成 (tg/onebot/discord/...)
- *javdb 集成 (订阅/自动推送/刮削)
- 插件系统(扩展系统)
  - [x] 影片元数据来源插件 API、数据目录 drop-in 发现和统一 Factory/聚合接入
  - [x] 可扩展来源 ID、descriptor、多语言能力和插件独立配置模型
  - [x] 插件配置 API 与独立插件页配置表单
  - [x] 播放源插件与主机媒体端点
  - [ ] 插件版本快照、回放兼容性和更精确的密钥字段声明
  - [ ] 允许其他 Amane 实例作为刮削源
  - [ ] 评估不可信插件的进程隔离方案
  - 当前边界与实现契约见 [plugins.md](dev/plugins.md)

标注 `*` 的功能存在风险, 需做成可插拔, 且不在开源版本中包含 (换句话说, 基本上是做成另一个独立项目, 但是可以方便的集成到 Amane 中).
