---
name: amane-pr
description: >-
  Amane PR 规范: 正文组织、跟踪并修复 CI、用 Close/Fix 关联 Issue、squash/rebase
  合入并清理、以及 Alembic migration 的 multi-head 处理.
  Use when creating, updating, or merging a pull request, 开 PR, 更新 PR,
  写 PR 正文, 合并 PR, 处理 CI 失败, or when a PR adds an Alembic migration.
---

# PR 规范

## 工作区

PR 默认用**独立 worktree**, 不在主目录开发, 除非用户明确要求.

```bash
git worktree add ../<repo>-<topic> -b <branch> main
```

分支名用 `<type>/<topic>` (`fix/` `feat/` `docs/`). 收尾见「合入」一节.

## 正文

顺序: Summary → 接口与数据变更 → Implementation → Test plan → Close/Fix 或 Related.

**Summary** 为无序列表, 写本 PR 对外交付的能力或解决的缺陷. 此节需简洁清晰, 高度抽象且提纲挈领, 意在快速告知读者此 PR 「做了什么」

限制每条为**无标点单句**, 简明扼要的描述一个新功能或 bug 修复

严禁:

- 源码标识符 (字段、类、函数、API 参数)
- 对调用链/控制流/状态转移等进行纯粹描述
- 实现细节 (如边界条件、约束等)

这些内容如对审查有帮助可写入 Implementation.

**接口与数据变更** 为表格, 列出本次改动的关键接口与数据结构. 大规模 PR 必写; 小 PR (如内部 bug 修复) 省略整节.

三列: 类别 | 变更 | 说明. 逐类只列实际改动的面 — OpenAPI 端点 (新增 / 删除, 给出方法与路径, 写明个数)、任务类型与任务模型、请求 / 响应字段、数据库模型 (表 / 枚举 / 字段语义)、数据库迁移 (revision 与影响). 未改动的面不写.

**Implementation** 也为无序列表, 写实现方案与契约, 可省略. 格式要求:

- 开头先给一句整体形状 (谁产出、谁确认、谁执行), 让读者拿到心智模型再看细节
- 每条一个要点, 用粗体小标题领起 (`- **存放**: …`), 标题点明该条讲什么
- 单条控制在一行左右: 条件、原因与例外并入同一条, 禁止堆进括号绕成长句
- 条目按读者的理解顺序排列 (产出 → 存放 → 执行 → 边界 → 读取 → 兼容 → 命名), 不按代码结构

**Test plan** 为 check-list

**标题** 是 Summary 的再压缩, 使用约定式提交格式, 提交信息为短句, 要求直击核心

**禁止呈现中间态.** 正文只描述最终实现: 本 PR 开发过程中的增强、修复、重命名与返工一律不写入, 也不作为验收项列出 — 合并后 main 上只有最终形态, 中间态既不可见也无从复现.

两类例外必须写: 相对 base 分支的行为改变 (本 PR 交付的修复本身), 以及范围声明 (未实现项、只写 `Related` 的原因).

## CI

**推送时机**: 不要每提交一次就推一次. 本地先把改动做完, 由用户确认功能或修复已经完全实现之后再推 — 一轮 CI 要几分钟, 逐次推送会把同一套检查反复跑.

推送时按情况压缩本地提交 (按逻辑变更 squash, 保留可分节的粒度), 不要把一个功能的十几次微调原样推上去.

推送之后必须跟踪这一轮 CI, 失败则尝试修复:

- CI 失败通常是测试失败造成的, 这里的界定点是: 如果只需要改测试代码则直接操作, 如果要改业务代码就要先报告给用户获得许可.
- 如果修复需要改动本 PR 的设计方案则需向用户报告.
- 修完按同样的节奏攒着, 等用户确认后再推, 不逐次推送.

PR 的 CI 通过之后再合入, main 上的那一轮 CI 不需要等待 — 那是同一棵树的重复运行. 改动只涉及文档与注释时, 这一轮 CI 同样不必等待. rebase / force-push 会作废上一轮, 这类改动必须重新等这一轮通过.

## Issue

PR 有相关 Issue 时, 按是否彻底解决区分, 写在正文**末尾**:

- **彻底解决** (议题范围小, PR 就是在修这个 bug / 做这个 feature): 简短的在最后用 `Close` / `Fix`, 合并时自动关闭.
- **只是关联、并未彻底解决**: 在最后添加一节 `Related` 中列出它们. 已被 `Close` / `Fix` 的不必写.

示例:

```markdown
<!-- other content -->
## Related

- #56
- #78

Close #12
Fix #34
```

## 合入

用户要求合并时:

- 首选 squash
- 非常简单的 PR 可以用 rebase
- 永远不要用 merge commit 把 PR 合进 main
- 合并用 PR 自身的标题, 不在合并时另指定 message; 要改合并提交的内容就改 PR 标题

合入后, 删除该 PR 的 worktree 与本地分支, 同步 main; 若是从本仓库分支创建的, 还要删除远程分支.

### 堆叠式 PR

一个需求涉及多个领域的改动、且预期规模较大时, 放进单个 PR 不合适; 按领域各自开向 main, 又会因彼此依赖而冲突. 此时用堆叠式 PR: 改动按依赖关系分成多层, 上层 PR 的目标分支是下层分支.

`gh stack submit` 会在 GitHub 上创建 Stack 对象, `init` / `add` 只维护本地跟踪. **CI 依据这个对象**: `ci.yaml` 的 `pull_request.branches` 按 base 过滤, 仅手工把 base 改成下层分支 (`gh pr edit --base`) 时整条栈不产生任何检查; 进入 Stack 之后, base 不是 main 也照常运行.

加入已有分支: 先用 `git` 创建分支, 再 `gh stack checkout <当前栈顶>` 后 `gh stack add <分支>` —— 当前分支不在栈里时 `add` 报 `not part of a stack`. 一次性成栈用 `gh stack init <自下而上依次列出>`, 已有分支一并纳入. 没有 TTY 时 `submit` 等价 `--auto`: 新建的 PR 处于草稿状态, 需要可评审时加 `--open` (交互式编辑器内默认为可评审).

堆叠内的 PR 不能逐个 `gh pr merge` (GitHub 拒绝); 用 `gh stack merge [<PR 号>] --yes --squash` 一次合入, 整批原子, 每个 PR 各留一个 squash 提交. `gh stack` 的本地跟踪在 rebase / force-push 之后可能失效 (报 `not part of a stack`), 此时改用 PR 号.

自下而上合入: 下层合入后上层 PR 的 base 自动改指 main; 未自动改指时, 删除已合入的远程分支即可. 随后把上层的剩余提交重基到新 main (`git rebase --onto origin/main <下层旧 tip>`), 并按 Alembic 一节与 `just generate` 重新核对迁移与生成物.

## Alembic

PR 含数据库 schema 变更 / 新 migration 时, **正文最前面** (各节之前) 固定放:

```
> ❗ **此 PR 包含 DB migration**
```

当存在多个并行的 PR 都创建了 migration 时, 表面上它们合并都不会造成冲突;
但每个 PR 都基于 main 的最后一个 revision, 各自把自己当成唯一后继, 所以直接合入会使得 Alembic 图分叉 (multiple heads).
因此合并这类 PR 之前:

1. 把 `origin/main` merge (或 rebase) 进该 PR 分支.
2. `uv run alembic heads` — 必须只剩一个 head.
3. 若多个 head: 不要 `alembic merge`. 把本 PR 里最早那条 revision 的 `down_revision` 改成当前 main 的 head, 排成一条链. 禁止手写新的 revision ID.
4. 推上去, 再等 CI, 再 squash.

连续合多个带这条警告的 PR 时, 每合一个都要重新做 1–3.
