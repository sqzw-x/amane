---
emoji: "⚠️"
on:
  schedule: daily
  workflow_dispatch:
permissions:
  contents: read
  issues: write
safe-outputs:
  report-failure-as-issue:
    enabled: true
    categories: [inference_access_error, ai_credits_rate_limit_error, max_ai_credits_exceeded, activation_failed, agent_startup_failure, engine_driver_failure]
    group: true
    close-older-key: same-day
    labels: [bot, ci]
    title-prefix: "CI 分诊失败汇总"
tools:
  github:
    list-issues:
      state: open
      labels: [status:needs-info, ci-error]
    add-comment: true
---

# CI 分诊失败汇总

查询最近一周主分诊与补跑中的系统级失败 (模型服务故障、预算超限、启动失败), 汇总为一条子 Issue, 并关联到受影响的 Issue.

如果最近 24 小时内无此类失败, 不创建新 Issue, 直接退出.

规则:
- 只汇总系统级失败, 不包括 agent 无法完成任务的情况 (agent 主动 report_incomplete)
- 每个受影响 Issue 打上 `ci-error` 标签, 并在评论中说明
- 报告中列出失败类型、受影响 Issue 编号、时间范围
