# AGENTS.md

本文件为各种 Coding Agent 提供引导.
**绝对禁止此文件长度超过 200 行.**

## 项目定位

**Amane** — 影片元数据管理服务. 监控媒体目录, 多源刮削, 与 Emby/Jellyfin 集成, 作为流媒体服务的元数据补充.

技术栈: FastAPI · React + Mantine · SQLite (SQLModel + Alembic) · Docker · PyInstaller + 原生桌面 APP (swift / .NET AOT)

## 代码知识库

位置: `docs/dev/` — 导航见 [`docs/dev/index.md`](docs/dev/index.md).
**优先读文档**, 根据文档按需读源码.

### 维护

修改代码后必须同步更新对应文档. 遵循以下规则:

1. **分层组织**: 单个文档简短、主题集中; 形成树结构逐级索引, 根为 `index.md`.
2. **不重复源码**: 只写无法从源码直接读出的信息 (边界、顺序、契约、取舍、踩坑). 禁止罗列字段、复制签名、抄注释.
3. **只写当前状态 (禁止旧状态)**: 文档只描述**现在**是什么、为什么. **禁止**迁移动机、原实现、对照表述
   (「从 A 改成 B」「取代旧 X」「等价旧 Y」「以前是…」「修成了…」). 读者无需了解历史沿革.
   例外仅限**仍影响当前行为**的踩坑/易回归点, 且只写「当前要注意什么」, 不写演进史.
4. **精准引用**: 必要时用路径+行号, 如 `src/amane/handlers/scrape.py:100-105`.
5. **正交性**: 同一内容只出现在一个文档; 交叉引用用相对链接.
6. **AGENTS.md 保持简洁**: 只留最重要信息, 细节在 `docs/dev/`.
7. **版本化**: 每个文档头部 `提交: <sha>` 指向**上次实质修改该文档的提交**; 改文档时同步更新.

### 内容标准

一个信息是否写文档, 看其"引力中心" (改动时会牵连什么):

| 信息类型 | 引力中心 | 归属 |
| --------- | --------- | ------ |
| "为什么这行/这个函数这样写" | 单个代码点, 与代码完全同步 | **注释 / docstring** |
| "为什么这些东西这样组织/协作" | 跨文件的边界、顺序、契约、取舍 | **`docs/dev/`** |
| 可机械读出的事实 (字段、签名、清单、正则模式) | — | **都不写**, 让代码自注释 |

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
发版前更新 `CHANGELOG.md`, 只用 `just bump`, 不要手改版本或手打 tag

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
- **禁止玩具测试**. 禁止为提高覆盖率而创建无意义的测试, 必须测试实际逻辑
- **必须包含边界/非法用例**. 不能只包含合法输入用例, 必须充分测试非法输入下的健壮性, 以及报错异常信息的明确性
