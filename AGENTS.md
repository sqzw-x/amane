# AGENTS.md

本文件为各种 Coding Agent 提供引导.
**绝对禁止此文件长度超过 200 行.**

## 用语规范

**最高优先级.** 凡需语言表达的输出 (注释、开发文档、提交说明、Pull Request、与维护者的对话) 必须遵守 [`docs/dev/writing.md`](docs/dev/writing.md). 与本文其它条款冲突时以该文档为准.

## 项目定位

**Amane** — 影片元数据管理服务. 监控媒体目录, 多源刮削, 与 Emby/Jellyfin 集成, 作为流媒体服务的元数据补充.

技术栈: FastAPI · React + Mantine · SQLite (SQLModel + Alembic) · Docker · PyInstaller + 原生桌面 APP (swift / .NET AOT) · Android 壳 (Kotlin WebView)

## Issue 与 Pull Request

外部贡献必须遵守 [`AI_POLICY.md`](AI_POLICY.md). 开 Issue 须经网页端模板; 禁止经 GitHub CLI 或 REST API 创建无模板 Issue. PR 须经人工 review, 并按该政策披露 AI 使用程度.

## 远程操作

**所有影响远程仓库的变更必须经用户明确批准后才能执行.** 例如 push、合并 PR、回复 Issue、发表评论.

## 代码知识库

位置: `docs/dev/` — 导航见 [`docs/dev/index.md`](docs/dev/index.md).
**优先读文档**, 根据文档按需读源码.

### 文档的用途

`docs/dev/` 只服务 Coding Agent, 不服务人类读者或终端用户. 它的唯一作用是**索引**: 用最少的阅读量把 Agent 送到一两个具体文件, 送到之后仍要读那些文件.
因此文档不求自足, 不承载实现细节; 一份文档的价值等于它替 Agent 省下的源码阅读量.

### 内容归属

一条信息写在哪里, 看它的影响半径:

| 信息 | 归属 |
|------|------|
| 源码本身已经说明的事实 (字段、签名、枚举、默认值、清单、正则、目录树、命令用法) | **都不写**, 让代码自注释 |
| 单个文件内部的实现原因与约束 | **该文件的注释 / docstring** |
| 跨模块的边界、顺序、契约、取舍 (模块间联动、前后端联动、启动顺序、持久化格式、仍生效的禁止事项) | **`docs/dev/`** |

判据: 删掉这条信息, Agent 会不会因此多读文件或读错文件? 不会就删.

### 维护

修改代码后必须同步更新对应文档. 遵循以下规则:

1. **先精简再加**: 每次改动都顺手压缩所在段落, 新信息优先并入既有句子. 文档长度只允许持平或变短, 除非确实新增了跨模块契约.
2. **分层组织**: 单个文档简短、主题集中; 形成树结构逐级索引, 根为 `index.md`.
3. **不重复源码**: 只写无法从源码直接读出的信息 (边界、顺序、契约、取舍、踩坑). 禁止罗列字段、复制签名、抄注释、粘贴可机械读出的源码片段.
4. **只写当前状态**: 文档只描述现在是什么、为什么, 以及仍生效的禁止事项.
   **禁止**背景铺陈与迁移动机 (「从 A 改成 B」「以前是…」). 禁止事项的写法见 [writing.md](docs/dev/writing.md).
5. **不举例**: 禁止用示例、场景枚举或对照说明代替规则本身.
6. **精准引用**: 指向文件用仓库相对路径加符号名, 如 `src/amane/handlers/scrape.py::ScrapeHandler.handle`. 不写行号 — 行号随改动失效.
7. **正交性**: 同一内容只出现在一个文档; 交叉引用用相对链接.
8. **不新增文档**: 新主题优先进既有文档; 确需新建时同步登记到 `index.md`.
9. **AGENTS.md 保持简洁**: 只留最重要信息, 细节在 `docs/dev/`.

## 开发命令

前置条件: uv, pnpm, [just](https://github.com/casey/just)

Python 用 uv, 前端用 pnpm; **对外任务入口是根目录 Justfile** (`just --list`). 不要用根 `package.json` 编排.

```bash
just setup      # 同步依赖 + 拉取测试 fixture + 安装 prek hooks
just sync       # 仅同步 Python + web 依赖

just dev        # 并行启动 API (:8000) + Vite web

just build      # 导出 OpenAPI + 构建前端
just generate   # 导出 openapi + 生成 TS client (alias: just api)
just icons      # 从 assets/logo.svg 生成 favicon / ico / icns

just test       # pytest
just test-cov   # pytest + 覆盖率报告
just check      # lint + format + typecheck + web check + test
just fix        # ruff / oxfmt / oxlint 自动修复
just all        # generate → fix → check → build

just bump patch|minor|major  # 发版: 升版本 + generate + 提交 + tag (不 push)
just bump-dry patch          # 预览下一版本
```

formatter/linter 不通过时优先使用 `just fix` 自动修复, 不要手动改
发版前更新 `CHANGELOG.md`, 只用 `just bump`, 不要手改版本或手打 tag; Android APP 的版本与 tag 独立, 见 [android.md](docs/dev/android.md)

## 开发规范

### 通用

- 及时提交. 开发过程保持细粒度提交, 每个逻辑变更独立提交, 避免积压为单个巨大提交
- 同步更新知识库. 修改架构、API 或工作流时必须同步更新或增减文档
- 进度同步. 当需求来自 roadmap/todo 注释等, 在完成后移除相关内容

### Python

- **类型安全第一**. 所有函数/类/方法必须具有完备的类型标注, 使用 Pydantic model 而不是 dict 进行数据传递
- **禁止内部导入**. 所有导入一律放文件顶部 (`if TYPE_CHECKING:` 除外), 如果出现循环导入, 反思结构设计
- **禁止反射**. 禁止使用 `hasattr/getattr` (动态字段访问除外), 类型检查报错就修类型标注, 不要使用 `getattr` 绕过
- **禁止全局变量**. 使用 FastAPI 依赖注入, 通过构造函数或参数传递
- **数据库迁移必须用工具**. 必须通过 `uv run alembic revision [--autogenerate] -m "描述"` 创建, **绝对禁止手写 revision ID** 手动创建迁移文件

### TypeScript

- **绝对类型安全**. 禁止使用 as 类型断言 (必要情况需说明), **绝对禁止**使用 any (仅必要时使用 unknown)
- **静态优先**. 能在类型检查阶段完成的不要依赖运行时检查. 善用类型计算解决问题

### 测试

- **表测试优先**. 能使用表测试就不要创建多个测试函数/类等
- **复用fixture**. 创建 fixture 前先检查是否已有
- **禁止玩具测试**. 禁止为提高覆盖率而创建无意义的测试, 必须测试实际逻辑. 详见 `docs/dev/testing.md`
- **必须包含边界/非法用例**. 不能只包含合法输入用例, 必须充分测试非法输入下的健壮性, 以及报错异常信息的明确性
