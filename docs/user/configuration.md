# 配置指南

Amane 的配置分为两层:

- **环境变量** — 进程启动时读取, 修改后需重启
- **应用配置** — Web 界面「设置」页面, 实时生效

## 环境变量

| 键 | Docker 默认 | 桌面默认 | 说明 |
| ---------- | -------- | -------- | ------ |
| `AMANE_HOST` | `0.0.0.0` | `127.0.0.1` | 监听地址; 桌面默认只接受本机连接 |
| `AMANE_PORT` | `8000` | `18000` | API 监听端口 |
| `AMANE_DATA_DIR` | `/data` | 系统应用数据目录 | 数据库、资源与配置的存放位置; 修改后不会迁移已有数据 |
| `AMANE_LOG_DIR` | `/data/logs` | 数据目录下的 `logs` | 日志输出位置 |
| `AMANE_TOKEN` | (自动生成) | (自动生成) | API 访问令牌; `off` 关闭鉴权 |
| `AMANE_SAFE_DIRS` | (自动推导) | `ALLOW_ALL` | 文件浏览器与库路径的边界目录, 逗号分隔; `ALLOW_ALL` 关闭校验 |

桌面版会读取固定位置的配置文件以设置环境变量, 文件位于:

- macOS `~/Library/Application Support/Amane/desktop.env`
- Windows `%LOCALAPPDATA%\Amane\desktop.env`

可点击菜单中的「打开设置文件」进行编辑, 需重启服务器才能生效.

### 数据目录结构

```
data/
├── amane.db          SQLite 数据库
├── token             API Token (自动生成)
├── translations.db   LLM 翻译缓存
├── resources/        下载的图片、预告片等资源
├── agent/            AI 助理会话数据
├── plugins/          已安装的插件
├── tools/            超分工具二进制
└── watermarks/       可选: 自定义封面角标 PNG (见下方)
```

`watermarks/` 自定义 (覆盖内置水印样式):

| 文件名 | 对应水印 |
| -------- | ---------- |
| `subtitle.png` | 中字 |
| `uncensored.png` | 无码 |
| `cracked.png` | 破解 |
| `leaked.png` | 流出 |
| `4k.png` / `8k.png` | 4K / 8K |
| `1080p.png` 等 | 其它清晰度 (无内置图标) |

## 应用配置

应用配置通过 Web 界面「设置」页面实时修改, 无需重启.

### 刮削配置 (`scraping`)

见 [刮削指南](scraping.md#刮削设置).

### 水印 (`watermark`)

整理时添加到封面/海报:

- **启用水印**: 是否添加
- **水印大小**: 相对图片高度的比例. 封面和海报通常同高, 角标会一样大
- **水印位置**: 中字 / 无码 / 破解 / 流出 / 清晰度各自选四角之一. 同角按顺序向内叠

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

- **轮询模式**: 在 NAS/NFS/Docker Desktop/WSL2 等场景下使用轮询替代原生事件. 只作用于文件监控为「本地文件」的库; CD2 webhook 见 [媒体库管理](libraries.md)
- **防抖窗口**: 文件变动后等待的时间, 避免重复触发; CD2 webhook 目录 create 后的子树扫描也使用该窗口
- **媒体扩展名**: 监控的文件类型白名单

### AI 助理 (`agent`)

- **API 类型**: 支持 OpenAI Chat / OpenAI Response / Anthropic
- **API 密钥**: 对应提供商的密钥
- **模型**: 使用的模型名称
- **思考强度**: 推理深度 (off / minimal / low / medium / high / xhigh)

### LLM 翻译 (`llm`)

- **启用**: 是否启用 LLM 翻译
- **翻译字段**: 需要翻译的字段 (title / plot)
- **API 类型**: 支持 OpenAI Chat / OpenAI Response / Anthropic
- **API 配置**: 端点和密钥
- **自定义提示词**: 替换内置系统提示词的指令部分; 留空使用内置
- **字段提示词**: 逐字段覆盖内置字段说明; 留空使用内置

自定义提示词与字段提示词均可用 `{target_lang}` 引用目标语言名 (简体中文 / 繁體中文 / 日本語 / English). 单条上限 2000 字符. 提示词变化后缓存的旧译文自动失效, 详见 [刮削指南](scraping.md).

### 图像超分 (`sr`)

- **启用**: 刮削时是否自动超分低清图片
- **尺寸阈值**: 最长边小于此值才超分
- **预设**: waifu-photo-2x (默认) 或 realesr-photo-4x

### r18 数据源 (`r18`)

r18.dev 提供 PostgreSQL dump, 需要自备 PostgreSQL 实例:

- **DSN**: 超级用户连接串 (需 CREATEDB/CREATEROLE 权限)
- **下载地址**: dump 归档下载 URL
- **数据库名**: 目标数据库名
