# 配置指南

Amane 的配置分为两层:

- **环境变量** — 通过 Docker 环境变量或启动参数设置, 修改后需重启
- **应用配置** — 通过 Web 界面「设置」页面实时修改, 无需重启

## 环境变量

环境变量控制服务的运行环境, 通常在首次部署时设定, 之后很少改动.

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `AMANE_DATA_DIR` | `./data` | 数据库、配置文件、资源的存储位置 |
| `AMANE_LOG_DIR` | `./logs` | 日志输出位置 |
| `AMANE_TOKEN` | (自动生成) | API 访问令牌. 设为 `off` 可关闭鉴权 |
| `AMANE_SAFE_DIRS` | (自动推导) | 文件浏览器 / 库路径允许访问的目录, 逗号分隔. `ALLOW_ALL` 关闭校验 |
| `AMANE_LOG_LEVEL` | `INFO` | 日志级别: DEBUG / INFO / WARNING / ERROR / CRITICAL |

### 数据目录结构

```
data/
├── amane.db          SQLite 数据库
├── token             API Token (自动生成)
├── translations.db   LLM 翻译缓存
├── resources/        下载的图片、预告片等资源
├── agent/            AI 助理会话数据
├── plugins/          已安装的插件
└── tools/            超分工具二进制
```

## 应用配置

应用配置通过 Web 界面「设置」页面实时修改, 无需重启.

### 刮削配置 (`scraping`)

控制影片元数据刮削行为, 详细说明见 [刮削指南](scraping.md). 主要配置项:

- **下载资源类型**: 刮削时自动下载的资源类型 (详见 [刮削指南](scraping.md#_3))
- **内容路由**: 按内容类型配置数据源优先级顺序 (见 [内容路由](scraping.md#_3))
- **字段优先**: 特定字段的来源站点优先级
- **站点配置**: 按站点设置代理、Cookie、API Token、限速等

### 演员刮削 (`actor_scraping`)

控制演员元数据的抓取行为, 详细说明见 [刮削指南 - 演员刮削](scraping.md#_11).

### 网络配置 (`network`)

- **代理**: SOCKS/HTTP 代理 URL (如 `socks5://127.0.0.1:7890`)
- **超时**: HTTP 请求超时时间
- **重试**: 请求失败时的最大重试次数
- **限速**: 全局和按域名的请求速率限制

### 任务引擎 (`worker`)

- **并发数**: 最大并发任务执行数 (默认 10)

### 文件监控 (`watcher`)

- **轮询模式**: 在 NAS/NFS/Docker Desktop/WSL2 等场景下使用轮询替代原生事件
- **防抖窗口**: 文件变动后等待的时间, 避免重复触发
- **媒体扩展名**: 监控的文件类型白名单

### AI 助理 (`agent`)

- **API 类型**: 支持 OpenAI Chat / OpenAI Response / Anthropic
- **API 密钥**: 对应提供商的密钥
- **模型**: 使用的模型名称
- **思考强度**: 推理深度 (off / minimal / low / medium / high / xhigh)

### LLM 翻译 (`llm`)

- **启用**: 是否启用 LLM 翻译
- **翻译字段**: 需要翻译的字段 (title / plot)
- **API 配置**: OpenAI 兼容端点和密钥

### 图像超分 (`sr`)

- **启用**: 刮削时是否自动超分低清图片
- **尺寸阈值**: 最长边小于此值才超分
- **预设**: waifu-photo-2x (默认) 或 realesr-photo-4x

### r18 数据源 (`r18`)

r18.dev 提供 PostgreSQL dump, 需要自备 PostgreSQL 实例:

- **DSN**: 超级用户连接串 (需 CREATEDB/CREATEROLE 权限)
- **下载地址**: dump 归档下载 URL
- **数据库名**: 目标数据库名
