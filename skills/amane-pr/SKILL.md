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

顺序: Summary → Implementation → Test plan → Close/Fix 或 Related.

**Summary** 为无序列表, 写本 PR 对外交付的能力或解决的缺陷. 此节需简洁清晰, 高度抽象且提纲挈领, 意在快速告知读者此 PR 「做了什么」

限制每条为**无标点单句**, 简明扼要的描述一个新功能或 bug 修复

严禁:

- 源码标识符 (字段、类、函数、API 参数)
- 对调用链/控制流/状态转移等进行纯粹描述
- 实现细节 (如边界条件、约束等)

这些内容如对审查有帮助可写入 Implementation.

**Implementation** 也为无序列表, 写实现方案与契约, 可省略

**Test plan** 为 check-list

**标题** 是 Summary 的再压缩, 使用约定式提交格式, 提交信息为短句, 要求直击核心

## CI

**推送时机**: 不要每提交一次就推一次. 本地先把改动做完, 由用户确认功能或修复已经完全实现之后再推 — 一轮 CI 要几分钟, 逐次推送会把同一套检查反复跑.

推送时按情况压缩本地提交 (按逻辑变更 squash, 保留可分节的粒度), 不要把一个功能的十几次微调原样推上去.

推送之后必须跟踪这一轮 CI, 失败则尝试修复:

- CI 失败通常是测试失败造成的, 这里的界定点是: 如果只需要改测试代码则直接操作, 如果要改业务代码就要先报告给用户获得许可.
- 如果修复需要改动本 PR 的设计方案则需向用户报告.
- 修完按同样的节奏攒着, 等用户确认后再推, 不逐次推送.

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

合入后, 删除该 PR 的 worktree 与本地分支, 同步 main; 若是从本仓库分支创建的, 还要删除远程分支.

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
