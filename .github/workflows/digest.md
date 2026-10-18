---
emoji: "📋"
on:
  # 模糊调度: 编译时按文件路径散列成具体时刻, 避开整点高峰.
  schedule: daily around 10:00 utc+8
  # 兜底防线: 距上次真正启动过 agent 的 run 不足 22 小时则跳过.
  # 日切由下面的 skip-if-match 负责, 这里只防"手动补跑后当天又自动跑".
  cooldown: 22h
  # 最高每天一次: 今天已出过日报就不再出.
  skip-if-match: 'is:issue is:open label:report in:title "[日报]" created:>=2026-10-19'
  # 绝对数量门槛: 起始线之后还没进过日报的条目不足 min 条时, 整条 run 在
  # pre-activation 阶段跳过, 不启动 runner, 不起 agent. 积压到够数再出.
  # 查询必须是编译期字面量: 该检查排在 on.steps 之前, 取不到本次运行算出的值.
  # 起始线同时决定日报的覆盖范围与门槛的计数范围, 两者必须一致.
  # 2026-10-11 是首版起始线, 目的是把最近的积压拉进覆盖范围以便调优;
  # 等存量都收录干净后, 删掉 created 条件即可退回"全量未收录"口径.
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
# 手动触发时每次 Run 独立; 定时触发时同一时刻只会有一个 run 在跑.
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
# 清单由 mcp-scripts 用 gh CLI 确定性取出, 不另开 GitHub MCP 只读工具:
# 检索分页与标签过滤的细节留在脚本里, agent 只负责分类与判断.
mcp-scripts:
  # 待处理清单: 起始线之后创建, 尚未进过日报, 且仍在 open 的 issue.
  pending-issues:
    description: "列出起始线之后创建、未进过日报的 open issue 清单"
    inputs:
      since:
        type: string
        default: "2026-10-11"
        description: "起始线 (YYYY-MM-DD)"
      label_filter:
        type: string
        default: ""
        description: "只列出带该标签的 issue, 留空表示不过滤"
    run: |
      set -euo pipefail
      query="is:issue is:open created:>=${INPUT_SINCE} -label:digest:done"
      if [ -n "${INPUT_LABEL_FILTER}" ]; then
        query="${query} label:${INPUT_LABEL_FILTER}"
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
  # 补充信息候选: 带 status:needs-info 的 open issue, 附最近 3 条人类评论.
  # bot 评论不返回, 因此"有没有回复"由数据本身回答, 不需要再查评论接口.
  needs-info-candidates:
    description: "列出带 status:needs-info 的 open issue 及其最近人类评论"
    inputs:
      since:
        type: string
        default: "2026-10-11"
        description: "起始线 (YYYY-MM-DD)"
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

你是维护者的收件箱整理员. 这条流程唯一的作用是**提醒维护者: 还有哪些可以做、但他还没做的事**.

不是分析报告, 不是进度汇报, 不要评价任何 issue 的质量, 不要给实现建议, 不要预估工作量.

## 你要看的东西

本次运行的清单由两个自定义工具给出, 不要自己去搜索 issue.

工具名带 `mcpscripts-` 前缀, 缺了这个前缀会被判为未知工具:

1. 调用 `mcpscripts-pending-issues`, 参数 `since` 传 `${{ inputs.since }}`, `label_filter` 传 `${{ inputs.label_filter }}`.
   两个参数传空值时, 工具会回落到默认起始线, 并处理全部标签.
   返回每行一条: `编号 <TAB> 创建日 <TAB> 最后更新日 <TAB> 标签 <TAB> 评论数 <TAB> 标题`.
2. 调用 `mcpscripts-needs-info-candidates`, `since` 同上.

若某个工具报未知工具, 先执行 `mcpscripts --help` 看实际可用的名称, 不要换着名字反复重试. 两个工具都失败时直接停下, 在结论里说明原因, 不要改用别的方式去猜清单.
   返回带 `status:needs-info` 的 issue, 以及其中**人类评论**(bot 评论已被工具过滤掉)的日期, 作者和正文前 300 字.

除这两个工具外不要读 issue 正文, 不要读评论, 不要看代码和文档. 需要判断的信息都已经在返回结果里.

## 分组规则

只做一件事: 把清单里的条目分到下面四组, 顺序即输出顺序. 没有内容的分组整节省略. 不属于任何一组的条目不写进日报, 但**照常打标签**(见末尾).

### 第一组: 需要你重新判断

候选来自 `needs-info-candidates`. 这一组是日报的重点.

判断方式: 该 issue 标注 `status:needs-info` 之后, **issue 作者本人**(或另一位普通用户)是否又做了**形式上的补充** —— 即是否针对分诊提出的问题给出了新信息, 新步骤, 新日志或明确答复.
以下不算补充, 剔除: 单纯的催促, 顶帖, "我也遇到", "同问", 情绪表达, 与问题无关的闲聊, 只有一次复述原问题而没有新内容的回复, 以及**维护者自己**(`sqzw-x`)的追问与说明.

- 只判断"是否在回应分诊提出的问题", **不判断补充是否充分, 是否正确, 是否能让人直接动手做**. 那是维护者自己的判断.
- 拿不准时**保留**在组内 —— 这一组的成本是维护者多看一眼, 漏掉的成本是一个 issue 永远停摆.
- 没有人类评论, 或人类评论都早于分诊回复, 则不属于本组.

每条的写法: `- #编号 用户已在 <日期> 补充信息, 需要重新判断是否可接手`, 换行后缩进一句该 issue 讲的是什么(从标题提炼, 不超过 20 字).

### 第二组: 待处理的 Bug

`kind:bug` 且不带 `status:needs-info`. 这些是已判定有效但还没人动手的缺陷.

只列出来并说明, 不要深究内容, 不要复现, 不要判断严重程度. 每行: `- #编号 一句话概括`.

### 第三组: 功能请求

`kind:feature` 或 `kind:enhancement`, 且不带 `status:needs-info`.

按标签分小标题, 仅用于整理, **不要提醒维护者去做**:

- `### 高优先级` —— `priority:critical` / `priority:high`
- `### 中低优先级` —— `priority:medium` / `priority:low`
- `### 未定级` —— 无 priority 标签

### 第四组: 其他

其余条目, 包括 `kind:question` / `eco:project` / `kind:other` / 没有 kind 标签的. 用无序列表简单提及, 每条一行, 不展开.

### 不写进日报的

- `status:duplicate` / `status:invalid` / `status:wontfix` / `status:stale` —— 已有结论, 不是待办.
- 分配给他人的 issue.

## 日报正文

开头一个"概览": 本次覆盖的起始线和运行日期, 本次收录多少条, 其中需要重新判断多少条, 各组各多少条.

**不要对比上次日报**, 不要写趋势, 不要写"较上期增加/减少".

然后按上面四组的顺序排列. 分组内用无序列表, 每条以 `#编号` 开头. 措辞直白, 像给同事留便条, 不要客套, 不要总结陈词.

## 收尾: 打标签

日报创建成功后, 给**所有进入清单的条目**(包括没写进正文的那些, 以及被剔除出第一组的)逐个打上 `digest:done`.

- 每个 issue 一次 `add_labels` 调用, 编号用清单里的 `#编号`.
- 语义是"维护者已经知道它了", 不是"已经处理完了". 所以打标签与是否值得写进正文无关.
- 漏打的条目会在下期日报里重复出现, 所以这一步必须做完, 不要挑着打.

## 没有可提醒的条目时

清单为空, 或清单里所有条目都属于"不写进日报的"类别时: **仍然创建日报**, 只写概览, 加一句"起始线以来无待办条目". 不要调用 `noop`.

日报必须每期都出, 因为 `digest:done` 只在创建日报时才落地. 若这一期不出日报, 条目就会一直留在清单里, 数量门槛被永久抬高; 而"清单里没有值得提醒的条目"这个结论本身, 也已经由维护者看过一眼了.
