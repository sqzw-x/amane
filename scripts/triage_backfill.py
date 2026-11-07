#!/usr/bin/env python3
"""补跑分诊: 为漏处理的 issue 重新触发 `.github/workflows/issue-triage.md`.

分诊因模型服务故障或额度耗尽失败时不会自动重试. 本脚本由 `.github/workflows/issue-triage-backfill.yml`
定时执行, 选出近期漏处理的 issue 逐个触发 workflow_dispatch.

约束:
- 漏处理的判据: 带模板写入的 `kind:*` 标签, 没有分诊评论, 也没有维护者介入的痕迹 (维护者的评论、
  `status:*` / `priority:*` 标签). 维护者已经处理过的 issue 不补跑, 避免机器人在其后补发结论.
- 每个 issue 最多尝试 `_MAX_ATTEMPTS` 次, 次数按分诊运行名中的 `#编号` 统计; 运行名格式见 issue-triage.md 的 `run-name`.
- 最近完成的分诊运行全部失败时视为故障尚未恢复, 本轮不触发; 故障本身由分诊的失败上报负责通知.
- 运行于 runner 自带的 python3, 只能使用标准库与 3.12 已有的语法.

Env:
  GH_TOKEN           读取 issue 与运行记录, 触发 workflow_dispatch
  GITHUB_REPOSITORY  由 Actions 注入
  DRY_RUN            为 "true" 时只输出清单, 不触发
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

_WORKFLOW = "issue-triage.lock.yml"
_LOOKBACK = timedelta(days=7)
# issue 创建后的事件触发分诊仍可能在排队或运行, 留出余量再判为漏处理.
_GRACE = timedelta(minutes=30)
_MAX_ATTEMPTS = 3
_MAX_DISPATCH = 5
_BREAKER_RUNS = 5
_MAINTAINERS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
# 分诊评论末尾由 gh-aw 写入的隐藏标记.
_TRIAGE_MARKER = "workflow_id: issue-triage,"
_RUN_NUMBER = re.compile(r"#(\d+)\b")


@dataclass(frozen=True)
class Issue:
    number: int
    created_at: datetime
    labels: frozenset[str]
    author_association: str
    author_is_bot: bool
    assigned: bool


def _gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def _field(obj: object, key: str) -> object:
    return obj.get(key) if isinstance(obj, dict) else None


def _str(obj: object, key: str) -> str:
    value = _field(obj, key)
    return value if isinstance(value, str) else ""


def _list(obj: object, key: str) -> list[object]:
    value = _field(obj, key)
    return value if isinstance(value, list) else []


def parse_issue(raw: object) -> Issue:
    """解析 REST `GET /repos/{repo}/issues` 的单项."""
    return Issue(
        number=int(str(_field(raw, "number"))),
        created_at=datetime.fromisoformat(_str(raw, "created_at")),
        labels=frozenset(_str(label, "name") for label in _list(raw, "labels")),
        author_association=_str(raw, "author_association"),
        author_is_bot=_str(_field(raw, "user"), "type") == "Bot",
        assigned=bool(_list(raw, "assignees")),
    )


def is_candidate(issue: Issue, now: datetime) -> bool:
    """只依据 issue 本身判断; 评论侧的条件见 `handled_in_comments`."""
    if not now - _LOOKBACK <= issue.created_at <= now - _GRACE:
        return False
    if issue.author_is_bot or issue.assigned or issue.author_association in _MAINTAINERS:
        return False
    if not any(label.startswith("kind:") for label in issue.labels):
        return False
    return not any(label.startswith(("status:", "priority:")) for label in issue.labels)


def handled_in_comments(comments: list[object]) -> bool:
    """已有分诊评论, 或维护者已经回复."""
    return any(_TRIAGE_MARKER in _str(c, "body") or _str(c, "author_association") in _MAINTAINERS for c in comments)


def _attempted(run: object) -> bool:
    """只有 issue 事件与手动触发的运行才执行分诊. issue_comment 每条评论都产生一次运行, 未通过前置条件时
    结论仍为 success, 计入会耗尽尝试次数并掩盖故障, 因此一律排除."""
    return _str(run, "event") in ("issues", "workflow_dispatch") and _str(run, "conclusion") in ("success", "failure")


def count_attempts(runs: list[object]) -> dict[int, int]:
    """统计每个 issue 已有的分诊运行次数."""
    attempts: dict[int, int] = {}
    for run in runs:
        if not _attempted(run):
            continue
        match = _RUN_NUMBER.search(_str(run, "displayTitle"))
        if match:
            number = int(match.group(1))
            attempts[number] = attempts.get(number, 0) + 1
    return attempts


def outage_ongoing(runs: list[object]) -> bool:
    """按时间倒序取最近完成且未被跳过的运行, 全部失败时判定故障未恢复."""
    finished = [_str(r, "conclusion") for r in runs if _attempted(r)]
    recent = finished[:_BREAKER_RUNS]
    return len(recent) == _BREAKER_RUNS and all(c == "failure" for c in recent)


def main() -> int:
    repo = os.environ["GITHUB_REPOSITORY"]
    dry_run = os.environ.get("DRY_RUN", "") == "true"
    now = datetime.now(UTC)
    since = (now - _LOOKBACK).strftime("%Y-%m-%dT%H:%M:%SZ")
    # since 按更新时间过滤, 创建时间在 is_candidate 中判断; 结果含 PR, 需要排除.
    raw_issues = json.loads(
        _gh("api", f"repos/{repo}/issues?state=open&since={since}&per_page=100", "--paginate", "--slurp")
    )
    issues = [parse_issue(item) for page in raw_issues for item in page if _field(item, "pull_request") is None]
    candidates = sorted(
        (
            issue
            for issue in issues
            if is_candidate(issue, now)
            and not handled_in_comments(
                json.loads(_gh("api", f"repos/{repo}/issues/{issue.number}/comments?per_page=100"))
            )
        ),
        key=lambda issue: issue.created_at,
    )
    runs = json.loads(
        _gh(
            "run", "list", "--repo", repo, "--workflow", _WORKFLOW, "--limit", "200",
            "--created", f">={since[:10]}", "--json", "displayTitle,conclusion,event",
        )
    )  # fmt: skip
    attempts = count_attempts(runs)
    pending = [issue.number for issue in candidates if attempts.get(issue.number, 0) < _MAX_ATTEMPTS]
    exhausted = [issue.number for issue in candidates if attempts.get(issue.number, 0) >= _MAX_ATTEMPTS]
    sys.stdout.write(f"漏处理: {[i.number for i in candidates]}; 待补跑: {pending}; 已达尝试上限: {exhausted}\n")
    if not pending:
        return 0
    if outage_ongoing(runs):
        sys.stdout.write(f"最近 {_BREAKER_RUNS} 次分诊全部失败, 本轮不触发\n")
        return 0
    for number in pending[:_MAX_DISPATCH]:
        sys.stdout.write(f"触发 #{number}{' (试运行, 未触发)' if dry_run else ''}\n")
        if not dry_run:
            _gh("workflow", "run", _WORKFLOW, "--repo", repo, "-f", f"issue_number={number}", "-f", "dry_run=false")
    return 0


if __name__ == "__main__":
    sys.exit(main())
