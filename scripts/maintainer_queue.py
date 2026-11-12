#!/usr/bin/env python3
"""维护者待办: 把需要维护者处理的 open issue 与 PR 汇总到一个 issue 的正文.

由 `.github/workflows/maintainer-queue.yml` 定时执行, 不调用模型: 分组只依据标签、评论顺序与分诊评论中的
固定行首, 每次从当前状态全量计算, 不在 issue 上留下「已收录」之类的标记.

约束:
- 分组依据分诊评论的两个契约: gh-aw 写入的隐藏标记 `_TRIAGE_MARKER`, 以及「需维护者决定:」行首
  (格式见 `.github/workflows/issue-triage.md` 的「评论写法」). 两处任一改动都要同步本脚本.
- 待办 issue 由 `_QUEUE_LABEL` 与正文中的 `_QUEUE_MARKER` 识别, 每次运行原地编辑正文; 找不到 open 的待办 issue
  时新建. 上次通知过的条目记录在正文末尾的隐藏注释里, 只有 `NOTIFY=true` 的运行才对比并评论新增条目,
  其余运行只刷新正文.
- 运行于 runner 自带的 python3, 只能使用标准库与 3.12 已有的语法.

Env:
  GH_TOKEN           读写 issue
  GITHUB_REPOSITORY  由 Actions 注入
  MAINTAINERS        逗号分隔的维护者 login; 这些账号的评论视为「已处理」, 其 PR 不列入待评审
  NOTIFY             为 "true" 时对新增条目发表评论
  DRY_RUN            为 "true" 时只输出正文, 不写入
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

_TRIAGE_MARKER = "workflow_id: issue-triage,"
_DECISION_PREFIX = "需维护者决定:"
_QUEUE_MARKER = "<!-- maintainer-queue -->"
_STATE_PATTERN = re.compile(r"<!-- maintainer-queue-state: (\{.*?\}) -->")
_QUEUE_TITLE = "维护者待办"
_QUEUE_LABEL = "report"
_MAINTAINER_ROLES = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
# 漏分诊的判定窗口与 scripts/triage_backfill.py 的选择条件一致: 该分组列出的是补跑本应处理的 issue.
# 分诊在 issue 创建后通常几分钟内完成, 超过 _TRIAGE_GRACE 仍无分诊评论视为漏处理.
_TRIAGE_GRACE = timedelta(hours=1)
_TRIAGE_LOOKBACK = timedelta(days=7)
# 已答复的用法问题在用户沉默该时长后列为可关闭; stale 工作流要 21 天才关闭.
_QUESTION_QUIET = timedelta(days=3)
_SNIPPET_CHARS = 80
_BACKLOG_SHOWN = 10

_ISSUES_QUERY = """\
query($owner: String!, $repo: String!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    issues(first: 50, after: $cursor, states: OPEN, orderBy: {field: CREATED_AT, direction: ASC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title createdAt authorAssociation body
        author { __typename login }
        labels(first: 20) { nodes { name } }
        assignees(first: 1) { totalCount }
        reactionGroups { content reactors { totalCount } }
        comments(last: 30) { nodes { createdAt authorAssociation isMinimized body author { __typename login } } }
      }
    }
  }
}
"""
_PULLS_QUERY = """\
query($owner: String!, $repo: String!) {
  repository(owner: $owner, name: $repo) {
    pullRequests(first: 50, states: OPEN, orderBy: {field: CREATED_AT, direction: ASC}) {
      nodes { number createdAt isDraft author { login } }
    }
  }\n}
"""

Actor = Literal["user", "handled", "ignored"]


@dataclass(frozen=True)
class Comment:
    created_at: datetime
    login: str
    role: str
    is_bot: bool
    minimized: bool
    body: str

    @property
    def is_triage(self) -> bool:
        return self.is_bot and _TRIAGE_MARKER in self.body


@dataclass(frozen=True)
class Issue:
    number: int
    created_at: datetime
    author: str
    author_role: str
    author_is_bot: bool
    labels: frozenset[str]
    assigned: bool
    thumbs_up: int
    comments: list[Comment] = field(default_factory=list)

    def kind(self, prefix: str) -> bool:
        return any(label.startswith(prefix) for label in self.labels)


@dataclass(frozen=True)
class Entry:
    number: int
    note: str = ""
    # 长文本 (用户回复摘要、待决问题) 另起一行: GitHub 把列表项里的 issue 引用展开为标题, 同行接续的文本与标题难以区分.
    detail: str = ""

    def render(self) -> str:
        line = f"- #{self.number}" + (f" · {self.note}" if self.note else "")
        # 续行缩进两格, 仍属同一列表项; issue 正文中的单个换行渲染为换行.
        return line + (f"\n  {self.detail}" if self.detail else "")


@dataclass
class Queue:
    waiting: list[Entry] = field(default_factory=list)
    decisions: list[Entry] = field(default_factory=list)
    closable: list[Entry] = field(default_factory=list)
    bugs: list[Entry] = field(default_factory=list)
    untriaged: list[Entry] = field(default_factory=list)
    pulls: list[Entry] = field(default_factory=list)
    backlog: list[Entry] = field(default_factory=list)
    backlog_total: int = 0
    needs_info_total: int = 0

    def notify_keys(self) -> dict[str, list[int]]:
        """需要维护者动手的分组; 新增条目据此通知."""
        return {
            "waiting": [e.number for e in self.waiting],
            "decisions": [e.number for e in self.decisions],
            "bugs": [e.number for e in self.bugs],
            "untriaged": [e.number for e in self.untriaged],
            "pulls": [e.number for e in self.pulls],
        }


def _gh(*args: str, stdin: str | None = None) -> str:
    proc = subprocess.run(["gh", *args], capture_output=True, text=True, input=stdin)
    if proc.returncode != 0:
        sys.stderr.write(f"gh {args[0]} failed: {proc.stderr[:500]}\n")
        proc.check_returncode()
    return proc.stdout


def _field(obj: object, key: str) -> object:
    return obj.get(key) if isinstance(obj, dict) else None


def _str(obj: object, key: str) -> str:
    value = _field(obj, key)
    return value if isinstance(value, str) else ""


def _nodes(obj: object, key: str) -> list[object]:
    value = _field(_field(obj, key), "nodes")
    return value if isinstance(value, list) else []


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def parse_issue(raw: object) -> Issue:
    author = _field(raw, "author")
    thumbs = next(
        (
            _field(_field(g, "reactors"), "totalCount")
            for g in _list(raw, "reactionGroups")
            if _str(g, "content") == "THUMBS_UP"
        ),
        0,
    )
    comments = [
        Comment(
            created_at=_time(_str(c, "createdAt")),
            login=_str(_field(c, "author"), "login"),
            role=_str(c, "authorAssociation"),
            is_bot=_str(_field(c, "author"), "__typename") == "Bot",
            minimized=_field(c, "isMinimized") is True,
            body=_str(c, "body"),
        )
        for c in _nodes(raw, "comments")
    ]
    return Issue(
        number=int(str(_field(raw, "number"))),
        created_at=_time(_str(raw, "createdAt")),
        author=_str(author, "login"),
        author_role=_str(raw, "authorAssociation"),
        author_is_bot=_str(author, "__typename") == "Bot",
        labels=frozenset(_str(n, "name") for n in _nodes(raw, "labels")),
        assigned=(_field(_field(raw, "assignees"), "totalCount") or 0) != 0,
        thumbs_up=thumbs if isinstance(thumbs, int) else 0,
        comments=comments,
    )


def _list(obj: object, key: str) -> list[object]:
    value = _field(obj, key)
    return value if isinstance(value, list) else []


def snippet(body: str) -> str:
    """评论摘要: 去掉引用、图片与 HTML 注释, 压缩空白后截断."""
    lines = [line for line in body.splitlines() if not line.lstrip().startswith(">")]
    text = re.sub(r"<!--.*?-->", "", "\n".join(lines), flags=re.DOTALL)
    text = re.sub(r"<img[^>]*>|!\[[^\]]*\]\([^)]*\)", "[图片]", text)
    text = re.sub(r"https://github\.com/user-attachments/\S+", "[附件]", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= _SNIPPET_CHARS else text[: _SNIPPET_CHARS - 1] + "…"


def classify(issue: Issue, maintainers: frozenset[str], now: datetime) -> tuple[str, Entry] | None:
    """返回 (分组, 条目); 不需要维护者关注的 issue 返回 None. 每个 issue 只进入一个分组, 按下列顺序判定."""
    if issue.author_is_bot or _QUEUE_LABEL in issue.labels:
        return None

    def actor(c: Comment) -> Actor:
        if c.is_triage or c.login in maintainers or c.role in _MAINTAINER_ROLES:
            return "handled"
        return "ignored" if c.is_bot else "user"

    acted = [(actor(c), c) for c in issue.comments if actor(c) != "ignored"]
    maintainer_replied = any(c.login in maintainers or c.role in _MAINTAINER_ROLES for _, c in acted)
    triaged = any(c.is_triage for c in issue.comments)
    labels = issue.labels

    # 用户在最后一次处理 (分诊或维护者回复) 之后发言: 轮到维护者.
    if acted and acted[-1][0] == "user":
        last = acted[-1][1]
        return "waiting", Entry(issue.number, detail=f"{last.login} {last.created_at:%m-%d}: {snippet(last.body)}")

    latest_triage = next((c for c in reversed(issue.comments) if c.is_triage and not c.minimized), None)
    if latest_triage is not None and not any(
        c.created_at > latest_triage.created_at for a, c in acted if a == "handled" and not c.is_triage
    ):
        question = next(
            (line.strip() for line in latest_triage.body.splitlines() if line.strip().startswith(_DECISION_PREFIX)),
            None,
        )
        if question is not None:
            return "decisions", Entry(issue.number, detail=question.removeprefix(_DECISION_PREFIX).strip())

    if "status:duplicate" in labels:
        return "closable", Entry(issue.number, "已判为重复")
    if (
        "kind:question" in labels
        and acted
        and acted[-1][0] == "handled"
        and "status:needs-info" not in labels
        and now - acted[-1][1].created_at >= _QUESTION_QUIET
    ):
        return "closable", Entry(issue.number, f"已答复, 用户 {(now - acted[-1][1].created_at).days} 天未回复")

    if "status:needs-info" in labels:
        return "needs_info", Entry(issue.number)

    if issue.author_role in _MAINTAINER_ROLES or issue.assigned:
        return None
    labeled_by_hand = issue.kind("status:") or issue.kind("priority:")
    age = now - issue.created_at
    if (
        not triaged
        and not maintainer_replied
        and not labeled_by_hand
        and issue.kind("kind:")
        and _TRIAGE_GRACE <= age <= _TRIAGE_LOOKBACK
    ):
        return "untriaged", Entry(issue.number, f"创建于 {issue.created_at:%m-%d}")

    settled = maintainer_replied or "status:triaged" in labels
    if "kind:bug" in labels and not settled:
        high = next((p for p in ("priority:critical", "priority:high") if p in labels), "")
        return "bugs", Entry(issue.number, high)
    if (issue.kind("kind:enhancement") or issue.kind("kind:feature")) and not settled:
        return "backlog", Entry(issue.number, f"👍 {issue.thumbs_up}" if issue.thumbs_up else "")
    return None


def build_queue(issues: list[Issue], pulls: list[object], maintainers: frozenset[str], now: datetime) -> Queue:
    queue = Queue()
    backlog: list[tuple[int, Entry]] = []
    sinks: dict[str, Callable[[Entry], None]] = {
        "waiting": queue.waiting.append,
        "decisions": queue.decisions.append,
        "closable": queue.closable.append,
        "bugs": queue.bugs.append,
        "untriaged": queue.untriaged.append,
    }
    thumbs = {issue.number: issue.thumbs_up for issue in issues}
    for issue in issues:
        result = classify(issue, maintainers, now)
        if result is None:
            continue
        group, entry = result
        if group == "needs_info":
            queue.needs_info_total += 1
        elif group == "backlog":
            backlog.append((thumbs[entry.number], entry))
        else:
            sinks[group](entry)
    backlog.sort(key=lambda item: (-item[0], item[1].number))
    queue.backlog_total = len(backlog)
    queue.backlog = [entry for _, entry in backlog[:_BACKLOG_SHOWN]]
    for pr in pulls:
        if _field(pr, "isDraft") is True or _str(_field(pr, "author"), "login") in maintainers:
            continue
        queue.pulls.append(Entry(int(str(_field(pr, "number"))), _str(_field(pr, "author"), "login")))
    return queue


def _section(title: str, hint: str, entries: list[Entry]) -> str:
    if not entries:
        return ""
    body = "\n".join(entry.render() for entry in entries)
    return f"## {title} ({len(entries)})\n\n{hint}\n\n{body}\n\n"


def render(queue: Queue, now: datetime, state: dict[str, list[int]]) -> str:
    sections = [
        _section("等你回复", "用户在最后一次分诊或维护者回复之后又发了言.", queue.waiting),
        _section("需你决定", "分诊无法自行判断, 已整理为选择题.", queue.decisions),
        _section("可以关闭", "已判为重复, 或用法问题已答复且用户沉默.", queue.closable),
        _section("待确认的 bug", "分诊已通过, 维护者尚未回复或标记 `status:triaged`.", queue.bugs),
        _section(
            "漏分诊", "近 7 天创建、超过 1 小时仍无分诊评论. 补跑每 6 小时重试, 每个 issue 至多 3 次.", queue.untriaged
        ),
        _section("待评审的 PR", "非维护者提交、非草稿.", queue.pulls),
    ]
    backlog = ""
    if queue.backlog:
        backlog = (
            f"## 未评估的功能请求 ({queue.backlog_total})\n\n按 👍 数排列, 只列前 {len(queue.backlog)} 个.\n\n"
            + "\n".join(entry.render() for entry in queue.backlog)
            + "\n\n"
        )
    summary = "无待办事项." if not any(sections) and not backlog else ""
    footer = (
        f"等待用户补充信息的 issue 有 {queue.needs_info_total} 个, 不需要处理.\n\n"
        f"{_QUEUE_MARKER}\n<!-- maintainer-queue-state: {json.dumps(state, separators=(',', ':'))} -->\n"
    )
    header = f"更新于 {now:%Y-%m-%d %H:%M} UTC. 本 issue 由工作流自动维护, 请勿手动编辑.\n\n"
    return header + summary + "".join(sections) + backlog + footer


def new_items(previous: dict[str, list[int]], current: dict[str, list[int]]) -> dict[str, list[int]]:
    return {group: [n for n in numbers if n not in previous.get(group, [])] for group, numbers in current.items()}


_GROUP_NAMES = {
    "waiting": "等你回复",
    "decisions": "需你决定",
    "bugs": "待确认的 bug",
    "untriaged": "漏分诊",
    "pulls": "待评审的 PR",
}


def render_notice(added: dict[str, list[int]]) -> str:
    lines = [f"- {_GROUP_NAMES[g]}: " + " ".join(f"#{n}" for n in numbers) for g, numbers in added.items() if numbers]
    return "自上次通知以来新增:\n\n" + "\n".join(lines)


def _graphql(query: str, **variables: str) -> object:
    args = ["api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        args += ["-f", f"{key}={value}"]
    return json.loads(_gh(*args))


def fetch(repo: str) -> tuple[list[Issue], list[object]]:
    owner, name = repo.split("/", 1)
    issues: list[Issue] = []
    cursor = ""
    while True:
        variables = {"owner": owner, "repo": name} | ({"cursor": cursor} if cursor else {})
        page = _field(_field(_field(_graphql(_ISSUES_QUERY, **variables), "data"), "repository"), "issues")
        issues += [parse_issue(node) for node in _list(page, "nodes")]
        info = _field(page, "pageInfo")
        if _field(info, "hasNextPage") is not True:
            break
        cursor = _str(info, "endCursor")
    pulls_data = _graphql(_PULLS_QUERY, owner=owner, repo=name)
    pulls = _nodes(_field(_field(pulls_data, "data"), "repository"), "pullRequests")
    return issues, pulls


def find_queue_issue(repo: str) -> tuple[int, str] | None:
    found = json.loads(
        _gh("issue", "list", "--repo", repo, "--state", "open", "--label", _QUEUE_LABEL, "--json", "number,body")
    )
    for item in found if isinstance(found, list) else []:
        if _QUEUE_MARKER in _str(item, "body"):
            return int(str(_field(item, "number"))), _str(item, "body")
    return None


def read_state(body: str) -> dict[str, list[int]]:
    match = _STATE_PATTERN.search(body)
    if match is None:
        return {}
    raw = json.loads(match.group(1))
    if not isinstance(raw, dict):
        return {}
    return {str(k): [n for n in v if isinstance(n, int)] for k, v in raw.items() if isinstance(v, list)}


def main() -> int:
    repo = os.environ["GITHUB_REPOSITORY"]
    maintainers = frozenset(m.strip() for m in os.environ.get("MAINTAINERS", "").split(",") if m.strip())
    notify = os.environ.get("NOTIFY", "") == "true"
    dry_run = os.environ.get("DRY_RUN", "") == "true"
    now = datetime.now(UTC)

    issues, pulls = fetch(repo)
    queue = build_queue(issues, pulls, maintainers, now)
    existing = find_queue_issue(repo)
    current = queue.notify_keys()
    if existing is None or _STATE_PATTERN.search(existing[1]) is None:
        # 首次运行以当前清单为基线, 首次通知只列出此后新增的条目.
        state, added = current, {}
    else:
        previous = read_state(existing[1])
        added = new_items(previous, current) if notify else {}
        # 只有通知运行才推进已通知状态, 其余运行沿用上次的记录.
        state = current if notify else previous
    body = render(queue, now, state)

    sys.stdout.write(body)
    if dry_run:
        if any(added.values()):
            sys.stdout.write("\n---\n" + render_notice(added) + "\n")
        return 0
    if existing is None:
        url = _gh(
            "issue",
            "create",
            "--repo",
            repo,
            "--title",
            _QUEUE_TITLE,
            "--label",
            _QUEUE_LABEL,
            "--body-file",
            "-",
            stdin=body,
        )
        number = int(url.strip().rsplit("/", 1)[-1])
        return 0
    number = existing[0]
    # 先评论再推进正文中的状态: 中途失败时下次重复通知, 而不是漏掉通知.
    if any(added.values()):
        _gh("issue", "comment", str(number), "--repo", repo, "--body-file", "-", stdin=render_notice(added))
    _gh("issue", "edit", str(number), "--repo", repo, "--body-file", "-", stdin=body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
