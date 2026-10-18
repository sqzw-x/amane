---
emoji: "📋"
on:
  # 模糊调度: 编译时自动产生一定偏差, 避免整点高峰.
  schedule: daily around 10:00 utc+8
  # 附加约束: 距上次启动过 agent 的运行不足 22 小时则跳过启动.
  # 同日重复由 skip-if-match 负责, 此处只阻止手动补跑后当日再自动运行.
  cooldown: 22h
  # 每日上限: 当日已存在 open 的日报时跳过.
  skip-if-match: 'is:issue is:open label:report in:title "[日报]" created:>=2026-10-19'
  # 数量门槛: 起始线之后未收录的条目不足 min 条时, 整个运行在
  # pre-activation 阶段终止, 不启动 runner, 不启动 agent, 待累计达到门槛.
  # 查询必须使用编译期字面量: 该检查排在 on.steps 之前, 无法取得本次运行计算的值.
  # 起始线同时决定日报的覆盖范围与门槛的计数范围, 两者必须一致.
  # 2026-10-11 为首版起始线, 用于把近期积压纳入覆盖范围以便调优.
  # 存量收录完毕后删除 created 条件, 即退回全部未收录口径.
  skip-if-no-match:
    query: "is:issue is:open created:>=2026-10-11 -label:digest:done"
    min: 10
  workflow_dispatch:
    inputs:
      dry_run:
        description: "试运行 (只在日志里预览日报正文, 不开 issue, 不打标签)"
        required: false
        type: boolean
        default: true
      since:
        description: "覆盖起始线 (YYYY-MM-DD). 留空则用默认起始线"
        required: false
        type: string
      label_filter:
        description: "只处理带该标签的 issue. 留空则处理全部"
        required: false
        type: string
  reaction: none
# 手动触发时每次运行独占; 定时触发时同一时刻只会有一个运行在跑.
concurrency:
  job-discriminator: ${{ github.run_id }}
permissions:
  contents: read
  issues: read
engine:
  id: copilot
  model: deepseek/deepseek-v4.1-flash?effort=high
  env:
    COPILOT_PROVIDER_BASE_URL: ${{ secrets.LLM_BASE_URL }}
    COPILOT_MODEL: deepseek/deepseek-v4.1-flash
    COPILOT_PROVIDER_API_KEY: ${{ secrets.CF_GATEWAY_TOKEN }}
    COPILOT_PROVIDER_TYPE: openai
    COPILOT_PROVIDER_MAX_PROMPT_TOKENS: "200000"
    COPILOT_PROVIDER_MAX_OUTPUT_TOKENS: "32000"
sandbox:
  agent:
    model-fallback: false
models:
  default-ai-credits-pricing:
    input: 0.30
    output: 1.20
    cache_read: 0.006
    cache_write: 0.00001
# 只做归类与整理, 不做分析. 预算低于分诊流程 (15 AIC / 20 轮).
max-ai-credits: 8
max-turns: 15
timeout-minutes: 10
# 清单由 mcp-scripts 经 gh 取得, 不启用 GitHub MCP 只读工具:
# 检索, 分页与标签过滤留在脚本内, agent 只承担分类与判断.
mcp-scripts:
  # 待处理清单: 起始线之后创建, 尚未收录, 且仍为 open 的 issue.
  pending-issues:
    description: "列出起始线之后创建、未收录的 open issue 清单"
    inputs:
      since:
        type: string
        default: "2026-10-11"
        description: "起始线 (YYYY-MM-DD)"
      label_filter:
        type: string
        default: ""
        description: "只列出带该标签的 issue, 留空表示不过滤"
    # 清单规模可达百条, 默认 60 秒不足.
    timeout: 120
    run: |
      set -euo pipefail
      if [ -n "${INPUT_LABEL_FILTER}" ]; then
        query="is:issue is:open created:>=${INPUT_SINCE} -label:digest:done label:${INPUT_LABEL_FILTER}"
      else
        query="is:issue is:open created:>=${INPUT_SINCE} -label:digest:done"
      fi
      gh issue list --repo "$GH_AW_REPO" --limit 100 --state open \
        --search "$query" \
        --json number,createdAt,updatedAt,labels,comments,title \
        -q '.[] | [(.number|tostring), (.createdAt[0:10]), (.updatedAt[0:10]), ([.labels[].name]|join(",")), (.comments|length|tostring), (.title|gsub("[\n\t]";" "))] | join("\t")'
      echo "---"
      echo "查询窗口: 创建时间 >= ${INPUT_SINCE}"
      if [ -n "${INPUT_LABEL_FILTER}" ]; then
        echo "附加过滤: label:${INPUT_LABEL_FILTER}"
      fi
    env:
      GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
      GH_AW_REPO: ${{ github.repository }}
  # 补充信息候选: 带 status:needs-info 的 open issue. 不返回 bot 评论,
  # 因此是否存在回复由数据本身确定, 无需再查评论接口.
  needs-info-candidates:
    description: "列出带 status:needs-info 的 open issue 及其最近人类评论"
    inputs:
      since:
        type: string
        default: "2026-10-11"
        description: "起始线 (YYYY-MM-DD)"
    # 逐条读取评论, 耗时随条目数增长.
    timeout: 180
    run: |
      set -euo pipefail
      query="is:issue is:open created:>=${INPUT_SINCE} label:status:needs-info"
      for n in $(gh issue list --repo "$GH_AW_REPO" --limit 60 --state open \
        --search "$query" --json number -q '.[].number'); do
        meta=$(gh issue view "$n" --repo "$GH_AW_REPO" \
          --json createdAt,updatedAt,labels,title \
          -q '[.createdAt[0:10], .updatedAt[0:10], ([.labels[].name]|join(",")), (.title|gsub("[\n\t]";" "))] | join("\t")')
        echo "#${n}	${meta}"
        gh api "repos/${GH_AW_REPO}/issues/${n}/comments?per_page=100" \
          -q '[.[] | select(.user.type != "Bot")] | .[-3:] | .[] | "  评论 " + (.created_at[0:10]) + " by " + .user.login + ": " + (.body | gsub("[\n\t]";" ") | .[0:300])'
      done
      echo "---"
      echo "查询窗口: 创建时间 >= ${INPUT_SINCE}, 标签 status:needs-info"
    env:
      GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
      GH_AW_REPO: ${{ github.repository }}

tools:
  bash: ["cat", "ls", "head", "tail", "wc"]
  # NOTE: mcp-scripts 属于根级字段, 不是 tools 的子字段.
safe-outputs:
  # 手动触发时默认试运行; 定时触发时 inputs 为空, 即正常落地.
  staged: ${{ inputs.dry_run }}
  report-failure-as-issue: false
  create-issue:
    title-prefix: "[日报] "
    labels: [report]
    max: 1
    # 只留最新一期: 出新的就关掉旧的并反向链接, 邮箱与列表里不堆积历史日报.
    close-older-issues: true
  # 收录过的条目打上 digest:done, 下次不再进清单.
  # target 必须是 "*": 默认值 "triggering" 要求触发项是 issue, 而本流程由定时触发.
  add-labels:
    allowed: ["digest:done"]
    target: "*"
    max: 60
---

# 日报

把清单中issue分入下列分组, 生成一份日报. 用于提醒维护者未处理事项.

## 数据来源

清单由两个工具取得, 名称必须带 `mcpscripts-` 前缀:

1. `mcpscripts-pending-issues`: 传入 `since=${{ inputs.since }}` 与 `label_filter=${{ inputs.label_filter }}`.
   参数为空时工具使用默认起始线并处理全部标签. 每行格式:
   `编号 <TAB> 创建日 <TAB> 最后更新日 <TAB> 标签 <TAB> 评论数 <TAB> 标题`.
2. `mcpscripts-needs-info-candidates`: 传 `since` 同上. 返回带 `status:needs-info` 的 issue, 以及其中人类评论
   (bot 评论已由工具剔除)的日期, 作者与正文前 300 字.

不读取 issue 正文, 评论, 代码与文档: 判断所需信息均在返回值内.

工具报未知名称时执行 `mcpscripts --help` 确认可用名称, 禁止改名重试; 两个工具均失败时终止并说明原因, 禁止改用其他方式推演清单.

## 分组

全部清单条目都必须归入下列四组之一并写入日报, 顺序即输出顺序; 无内容的分组整节省略.

**第一组 需重新判断** —— 候选取自 `needs-info-candidates`.

- 判据: 标注 `status:needs-info` 之后, 作者本人或其他普通用户是否就分诊提出的问题给出新信息, 新步骤, 新日志或明确答复.
- 不构成补充: 催促, 顶帖, 情绪表达, 与问题无关的内容, 复述原问题而无新内容, 维护者(`sqzw-x`)的追问与说明.
- 只判断是否回应分诊提出的问题, 不判断补充是否充分, 是否正确, 以及是否足以动手实现.
- 无法确定时保留在组内.
- 无人类评论, 或人类评论均早于分诊回复时, 不属于该组.
- 每条写作 `- #编号 - 用户已在 <日期> 补充信息`.

**第二组 待处理的 Bug** —— `kind:bug`, 且无 `status:needs-info` 与结论性状态; 每条只写 `- #编号`, 不深究内容.

**第三组 功能请求** —— `kind:feature` 或 `kind:enhancement`, 且无 `status:needs-info` 与结论性状态; 每条只写 `- #编号`. 按优先级分小标题: `### 高优先级`(`priority:critical` / `priority:high`), `### 中低优先级`(`priority:medium` / `priority:low`), `### 未定级`(无 priority 标签).

**第四组 其他** —— 未归入上述分组的其余 issue 全部归入本组, 每条只写 `- #编号`. 可按状态或主题自行细分小标题.

## 报告格式

首段为概览: 运行日期, 起始范围, 处理总数

禁止与上期日报对比或描述趋势. 各条措辞直接陈述事实, 不添加客套与总结.

组不编号, 标题后括号注明数量

组内条目为无序列表

- `- #编号`
- `- #编号 - 说明`

`#编号` 会自动渲染为文本为 issue 标题的超链接, 因此禁止复述标题. 可省略说明只写编号.

## 打标签

日报创建成功后, 为全部清单条目添加 `digest:done`, 以表明已分析

方法: 每个 issue 一次 `add_labels` 调用

清单非空时必须全部打标签: 打标签只在创建日报时执行, 漏标会导致重复分析
