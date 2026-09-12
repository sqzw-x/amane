#!/usr/bin/env python3
"""parley —— 两个 Agent 通过文件系统契约轮流讨论的协议工具.

工具承担三件事: 生成暂存文件名、生成契约文件名、以原子替换发布本轮.
讨论内容一律由 Agent 自己的文件工具写入, 工具不解析也不校验 Markdown 正文.
这样分层是为了与 Agent 框架无关: 任何能执行命令并读写文件的 Agent 都能参与,
工具不需要知道对方是哪个 CLI 或哪个 harness.

只使用标准库, 不依赖任何 Agent 框架与第三方包.

依赖的不变量, 修改时不可破坏:

- 契约名 `NN-<role>.md` 与暂存名 `.staging/NN-<role>.<token>.md` 不相交, 由两层彼此
  独立的机制保障: 暂存名多一个 token 段; 暂存文件位于 `.staging/` 子目录, 而 `wait`
  与 `status` 不递归子目录. 任一层单独成立即可避免误匹配.
- 轮次只由已发布的契约文件推导, 与暂存文件无关. 由此 `wait` 的重复调用得到同一目标.
- 发布使用 `Path.replace`, 其原子性依赖暂存目录是工作目录的子目录, 即源与目标位于同一
  文件系统. 对端因此只需判断文件存在且非空, 不需要判断对方是否写完.
- 已发布的回合文件不可覆盖. 轮次只在发布成功后推进, 目标已存在的检查用于阻断绕过正常
  流程的写入.
- 退出状态恒为 0, 输出恒为单个 JSON 信封. 唯一调用方是 Agent, 退出状态不携带可用信息.
"""

from __future__ import annotations

import argparse
import contextlib
import fnmatch
import json
import os
import re
import secrets
import signal
import sys
import time
import traceback
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = 1

ROLES = ("author", "critic")
PEER = {"author": "critic", "critic": "author"}

TOPIC_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
CONTRACT_RE = re.compile(r"^([0-9]{2})-(author|critic)\.md$")
STAGING_RE = re.compile(r"^([0-9]{2})-(author|critic)\.([0-9a-f]{8})\.md$")

DEFAULT_MAX_ROUNDS = 10
DEFAULT_TIMEOUT = 1800.0
DEFAULT_POLL = 0.5
DEFAULT_STABLE_MS = 0

_interrupted = False

ROLE_LABEL = {"author": "主理人", "critic": "批评家"}


def role_label(role: str | None) -> str:
    return ROLE_LABEL.get(role or "", str(role))


# ---------------------------------------------------------------- 输出信封


def emit(
    action: str,
    ok: bool,
    message: str,
    data: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """所有子命令的唯一输出形式. 进程退出状态恒为 0."""
    payload = {
        "ok": ok,
        "action": action,
        "message": message,
        "data": data if data is not None else {},
        "error": error,
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    sys.stdout.flush()


def fail(action: str, error: str, message: str, data: dict[str, Any] | None = None) -> int:
    emit(action, False, message, data, error)
    return 0


# ---------------------------------------------------------------- 工作目录


def find_project_root(start: Path) -> Path:
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return current


def read_manifest(workspace: Path) -> dict[str, Any] | None:
    manifest_path = workspace / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    if not isinstance(raw, dict):
        return None
    max_rounds = raw.get("max_rounds")
    if not isinstance(max_rounds, int) or not 1 <= max_rounds <= 99:
        return None
    for key in ("subject", "artifact"):
        if not isinstance(raw.get(key), str) or not raw[key].strip():
            return None
    return raw


def open_workspace(directory: str, action: str) -> tuple[Path | None, dict[str, Any] | None, int]:
    """返回 (workspace, manifest, 0). 失败时后两项为 None 且已输出信封."""
    workspace = Path(directory).expanduser().resolve()
    if not workspace.is_dir():
        return (
            None,
            None,
            fail(
                action,
                "no_workspace",
                f"工作目录不存在: {workspace}。请先运行 init 创建主题目录。",
                {"dir": str(workspace)},
            ),
        )
    manifest = read_manifest(workspace)
    if manifest is None:
        return (
            None,
            None,
            fail(
                action,
                "no_workspace",
                f"工作目录缺少合法的 manifest.json: {workspace}。请先运行 init 创建主题目录。",
                {"dir": str(workspace)},
            ),
        )
    return workspace, manifest, 0


def contract_path(workspace: Path, round_number: int, role: str) -> Path:
    return workspace / f"{round_number:02d}-{role}.md"


def published_rounds(workspace: Path, role: str) -> list[int]:
    rounds: list[int] = []
    for entry in workspace.iterdir():
        if entry.is_symlink() or not entry.is_file():
            continue
        matched = CONTRACT_RE.match(entry.name)
        if matched and matched.group(2) == role:
            rounds.append(int(matched.group(1)))
    return sorted(rounds)


def latest_round(workspace: Path, role: str) -> int:
    rounds = published_rounds(workspace, role)
    return rounds[-1] if rounds else 0


def current_turn(workspace: Path, max_rounds: int) -> dict[str, Any]:
    """按已发布的契约文件推导当前该谁行动. 不解析文件内容.

    批评家先落盘: 一轮由一次批评家评审与一次主理人回应组成, 因此无人落盘时轮到批评家第 1 轮.
    """
    author_latest = latest_round(workspace, "author")
    critic_latest = latest_round(workspace, "critic")

    if critic_latest <= author_latest:
        role, round_number = "critic", critic_latest + 1
    else:
        role, round_number = "author", author_latest + 1

    if round_number > max_rounds:
        return {
            "role": None,
            "round": round_number,
            "limit_reached": True,
            "author_latest": author_latest,
            "critic_latest": critic_latest,
        }

    peer_round: int | None = None
    peer_path: str | None = None
    if role == "critic":
        # 第 1 轮面对的是产物本身, 由 artifact 指出, 没有上一回合文件.
        if round_number >= 2:
            peer_round = round_number - 1
    else:
        peer_round = round_number
    if peer_round is not None:
        candidate = contract_path(workspace, peer_round, PEER[role])
        if candidate.is_file():
            peer_path = str(candidate)

    return {
        "role": role,
        "round": round_number,
        "limit_reached": False,
        "author_latest": author_latest,
        "critic_latest": critic_latest,
        "target_name": f"{round_number:02d}-{role}.md",
        "target_path": str(contract_path(workspace, round_number, role)),
        "staging_glob": f".staging/{round_number:02d}-{role}.*.md",
        "peer_path": peer_path,
        "peer_round": peer_round,
    }


def describe_turn(turn: dict[str, Any]) -> str:
    if turn.get("limit_reached"):
        return f"已达轮次上限，没有下一轮（下一轮本应是第 {turn['round']} 轮）"
    if turn["role"] is None:
        return "讨论已结束"
    return f"轮到{role_label(turn['role'])}第 {turn['round']} 轮"


# ---------------------------------------------------------------- init


def cmd_init(args: argparse.Namespace) -> int:
    action = "init"
    topic = args.topic
    if not TOPIC_RE.match(topic):
        return fail(
            action,
            "usage_error",
            "主题标识非法：只允许小写字母、数字与连字符，以字母或数字开头，长度不超过 64。",
            {"topic": topic},
        )

    if args.max_rounds < 1 or args.max_rounds > 99:
        return fail(action, "usage_error", "轮次上限必须位于 1 到 99 之间。", {"max_rounds": args.max_rounds})

    subject = args.subject.strip()
    artifact = args.artifact.strip()
    if not subject:
        return fail(action, "usage_error", "主题说明不能为空。", {})
    if not artifact:
        return fail(action, "usage_error", "被评审对象不能为空。", {})

    root = Path(args.root).expanduser().resolve() if args.root else find_project_root(Path.cwd()) / ".parley"
    workspace = root / topic
    staging_dir = workspace / ".staging"

    if workspace.exists() and not workspace.is_dir():
        return fail(action, "usage_error", f"目标路径已被非目录文件占用: {workspace}。", {"dir": str(workspace)})

    if workspace.is_dir():
        existing = read_manifest(workspace)
        published = sorted(published_rounds(workspace, "author") + published_rounds(workspace, "critic"))
        if existing is not None and not args.force:
            return emit_ready(action, workspace, staging_dir, existing, reused=True)
        if published:
            return fail(
                action,
                "usage_error",
                f"工作目录内已有 {len(published)} 份已发布的回合文件，拒绝重建。如需保留原讨论，请改用其他主题标识。",
                {"dir": str(workspace), "published_rounds": published},
            )
        stale = [entry.name for entry in workspace.iterdir() if entry.name != "manifest.json"]
        if stale and not args.force:
            return fail(
                action,
                "usage_error",
                f"工作目录内存在非骨架文件，拒绝覆盖: {', '.join(sorted(stale))}。"
                f"确认这些文件无用后可加 --force 重建。",
                {"dir": str(workspace), "unexpected": sorted(stale)},
            )

    staging_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "protocol_version": PROTOCOL_VERSION,
        "topic": topic,
        "subject": subject,
        "artifact": artifact,
        "max_rounds": args.max_rounds,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (workspace / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return emit_ready(action, workspace, staging_dir, manifest, reused=False)


def emit_ready(action: str, workspace: Path, staging_dir: Path, manifest: dict[str, Any], reused: bool) -> int:
    verb = "已就绪" if reused else "已创建"
    emit(
        action,
        True,
        f"主题 {manifest['topic']} 的工作目录{verb}: {workspace}。"
        f"被评审对象: {manifest['artifact']}。轮次上限 {manifest['max_rounds']}。"
        f"讨论由批评家第 1 轮开始, 主理人运行 wait --for critic 等待其评审。",
        {
            "dir": str(workspace),
            "topic": manifest["topic"],
            "subject": manifest["subject"],
            "artifact": manifest["artifact"],
            "max_rounds": manifest["max_rounds"],
            "staging_dir": str(staging_dir),
            "manifest": str(workspace / "manifest.json"),
            "reused": reused,
        },
    )
    return 0


# ---------------------------------------------------------------- next


def cmd_next(args: argparse.Namespace) -> int:
    action = "next"
    workspace, manifest, code = open_workspace(args.dir, action)
    if workspace is None or manifest is None:
        return code

    role = args.role
    max_rounds = manifest["max_rounds"]
    turn = current_turn(workspace, max_rounds)

    if turn.get("limit_reached"):
        last_peer = contract_path(workspace, turn["critic_latest"], "critic")
        return fail(
            action,
            "round_limit",
            f"已达轮次上限 {max_rounds}，没有下一轮可用。请读取最后一份批评家文件并收敛分歧，然后输出最终结论。",
            {
                "role": role,
                "round": turn["round"],
                "max_rounds": max_rounds,
                "last_peer_path": str(last_peer) if last_peer.is_file() else None,
            },
        )

    if turn["role"] != role:
        waiting_for = "批评家" if turn["role"] == "critic" else "主理人"
        return fail(
            action,
            "not_your_turn",
            f"尚未轮到{role_label(role)}：{describe_turn(turn)}。请先运行 wait --for {turn['role']} 等待对方。",
            {
                "role": role,
                "turn_role": turn["role"],
                "turn_round": turn["round"],
                "wait_for": turn["role"],
                "peer_path": turn["peer_path"],
                "waiting_for": waiting_for,
            },
        )

    round_number = turn["round"]
    token = secrets.token_hex(4)
    staging_path = workspace / ".staging" / f"{round_number:02d}-{role}.{token}.md"
    staging_path.parent.mkdir(parents=True, exist_ok=True)

    label = role_label(role)
    if role == "critic" and round_number == 1:
        instruction = f"本轮评审对象是产物本身: {manifest['artifact']}。"
    elif turn["peer_path"]:
        instruction = f"先读取对方回合 {turn['peer_path']}。"
    else:
        instruction = f"先读取 {manifest['artifact']}。"

    emit(
        action,
        True,
        f"请把第 {round_number} 轮{label}回合写入 {staging_path}，完成后运行 publish --file 加上该路径。{instruction}",
        {
            "role": role,
            "round": round_number,
            "staging_path": str(staging_path),
            "target_name": turn["target_name"],
            "target_path": turn["target_path"],
            "peer_path": turn["peer_path"],
            "peer_round": turn["peer_round"],
            "subject": manifest["subject"],
            "artifact": manifest["artifact"],
            "max_rounds": max_rounds,
        },
    )
    return 0


# ---------------------------------------------------------------- publish


def cmd_publish(args: argparse.Namespace) -> int:
    action = "publish"
    raw = Path(args.file).expanduser()
    staging_path = (raw if raw.is_absolute() else Path.cwd() / raw).resolve()

    matched = STAGING_RE.match(staging_path.name)
    if staging_path.parent.name != ".staging" or matched is None:
        return fail(
            action,
            "invalid_staging",
            f"暂存路径不符合约定: {staging_path}。"
            f"暂存文件必须位于工作目录的 .staging/ 下，且文件名为 NN-<角色>.<8 位十六进制>.md。"
            f"请使用 next 返回的路径，不要自行构造。",
            {"file": str(staging_path)},
        )

    workspace = staging_path.parent.parent
    manifest = read_manifest(workspace)
    if manifest is None:
        return fail(
            action,
            "no_workspace",
            f"暂存文件所在的工作目录缺少合法的 manifest.json: {workspace}。",
            {"dir": str(workspace), "file": str(staging_path)},
        )

    if staging_path.is_symlink() or not staging_path.is_file():
        return fail(
            action,
            "staging_missing",
            f"暂存文件不存在: {staging_path}。请重新运行 next 取得路径，并确认内容已写入该文件。",
            {"file": str(staging_path)},
        )

    size = staging_path.stat().st_size
    if size == 0:
        return fail(
            action,
            "staging_missing",
            f"暂存文件为空: {staging_path}。请先写入回合内容再发布。",
            {"file": str(staging_path), "bytes": 0},
        )

    role = matched.group(2)
    round_number = int(matched.group(1))
    max_rounds = manifest["max_rounds"]
    turn = current_turn(workspace, max_rounds)

    if turn.get("limit_reached") or turn["role"] != role or turn["round"] != round_number:
        return fail(
            action,
            "not_your_turn",
            f"本轮不是{role_label(role)}第 {round_number} 轮："
            f"{describe_turn(turn)}。请重新运行 next 取得当前轮次的暂存路径。",
            {
                "role": role,
                "round": round_number,
                "turn_role": turn["role"],
                "turn_round": turn["round"],
            },
        )

    target = contract_path(workspace, round_number, role)
    if target.exists():
        return fail(
            action,
            "already_published",
            f"契约文件已存在，不允许覆盖: {target}。已发布的回合不可修改；如需修正请发布下一轮。",
            {"path": str(target), "role": role, "round": round_number},
        )

    try:
        with open(staging_path, "rb+") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        staging_path.replace(target)
    except OSError as exc:
        return fail(
            action,
            "internal_error",
            f"发布失败: {exc}。",
            {"file": str(staging_path), "target": str(target), "detail": repr(exc)},
        )

    cleaned: list[str] = []
    for sibling in sorted(staging_path.parent.glob(f"{round_number:02d}-{role}.*.md")):
        with contextlib.suppress(OSError):
            sibling.unlink()
            cleaned.append(str(sibling))

    label = role_label(role)
    nxt = current_turn(workspace, max_rounds)
    if nxt.get("limit_reached"):
        tail = f"这是第 {max_rounds} 轮，讨论已达上限。请读取对方文件并输出最终结论。"
    else:
        tail = f"下一步运行 wait --for {nxt['role']} 等待{role_label(nxt['role'])}。"

    emit(
        action,
        True,
        f"第 {round_number} 轮{label}回合已发布: {target}。{tail}",
        {
            "role": role,
            "round": round_number,
            "path": str(target),
            "bytes": size,
            "cleaned_staging": cleaned,
            "next_role": nxt["role"],
            "next_round": nxt["round"],
        },
    )
    return 0


# ---------------------------------------------------------------- wait


def staging_ready(path: Path, stable_ms: float, tracker: dict[str, Any]) -> bool:
    """判定候选文件是否已完整落盘.

    stable_ms 为 0 时只需存在、是常规文件、非符号链接且非空.
    大于 0 时额外要求 (大小, mtime, inode) 三元组在该窗口内保持不变,
    用于兼容绕开 publish、由写入方直接落盘的场景.
    """
    if path.is_symlink() or not path.is_file():
        return False
    try:
        stat = path.stat()
    except OSError:
        return False
    if stat.st_size == 0:
        return False
    if stable_ms <= 0:
        return True

    key = str(path)
    triple = (stat.st_size, stat.st_mtime_ns, stat.st_ino)
    record = tracker.get(key)
    now = time.monotonic()
    if record is None or record[0] != triple:
        tracker[key] = (triple, now)
        return False
    return (now - record[1]) * 1000.0 >= stable_ms


def cmd_wait(args: argparse.Namespace) -> int:
    action = "wait"
    workspace, manifest, code = open_workspace(args.dir, action)
    if workspace is None or manifest is None:
        return code

    if args.for_role and args.match:
        return fail(action, "usage_error", "--for 与 --match 互斥，只能提供其中一个。", {})
    if not args.for_role and not args.match:
        return fail(action, "usage_error", "必须提供 --for 或 --match 之一。", {})

    max_rounds = manifest["max_rounds"]
    expected_path: Path | None = None
    expected_role: str | None = None
    expected_round: int | None = None

    if args.for_role:
        role = args.for_role
        author_latest = latest_round(workspace, "author")
        critic_latest = latest_round(workspace, "critic")
        if role == "critic":
            round_number = author_latest + 1
            if round_number > max_rounds:
                last_critic = contract_path(workspace, critic_latest, "critic")
                return fail(
                    action,
                    "round_limit",
                    f"已达轮次上限 {max_rounds}，不会再有批评家回合。请读取最后一份批评家文件并输出最终结论。",
                    {
                        "for": role,
                        "round": round_number,
                        "max_rounds": max_rounds,
                        "last_peer_path": str(last_critic) if last_critic.is_file() else None,
                    },
                )
        else:
            round_number = critic_latest
            if round_number < 1:
                return fail(
                    action,
                    "no_pending_turn",
                    "批评家尚未发布第 1 轮评审，此刻没有可等待的主理人回合。"
                    "请由批评家运行 next --role critic 评审产物。",
                    {"for": role, "author_latest": author_latest, "critic_latest": critic_latest},
                )

        expected_role = role
        expected_round = round_number
        expected_path = contract_path(workspace, round_number, role)

    patterns: list[str] = list(args.match or [])
    stable_ms = float(args.stable)
    timeout = float(args.timeout)
    poll = float(args.poll)
    if poll <= 0:
        return fail(action, "usage_error", "轮询间隔必须大于 0。", {"poll": poll})
    if stable_ms < 0:
        return fail(action, "usage_error", "稳定判定窗口不能为负数。", {"stable": stable_ms})
    if timeout < 0:
        return fail(action, "usage_error", "超时上限不能为负数，0 表示无限等待。", {"timeout": timeout})

    tracker: dict[str, Any] = {}
    started = time.monotonic()
    deadline = None if timeout == 0 else started + timeout

    while True:
        if _interrupted:
            return fail(
                action,
                "interrupted",
                "等待被信号中断。",
                {"waited_ms": int((time.monotonic() - started) * 1000)},
            )

        hit: Path | None = None
        if expected_path is not None:
            if staging_ready(expected_path, stable_ms, tracker):
                hit = expected_path
        else:
            for entry in sorted(workspace.iterdir(), key=lambda item: item.name):
                if entry.is_symlink() or not entry.is_file():
                    continue
                if any(fnmatch.fnmatchcase(entry.name, pattern) for pattern in patterns) and staging_ready(
                    entry, stable_ms, tracker
                ):
                    hit = entry
                    break

        if hit is not None:
            waited_ms = int((time.monotonic() - started) * 1000)
            if expected_role is not None:
                detail = f"请读取该文件，逐条回应后运行 next --role {PEER[expected_role]} 提交下一轮。"
                message = f"{role_label(expected_role)}第 {expected_round} 轮已落盘: {hit}。{detail}"
            else:
                message = f"命中文件 {hit}。"
            emit(
                action,
                True,
                message,
                {
                    "role": expected_role,
                    "round": expected_round,
                    "path": str(hit),
                    "bytes": hit.stat().st_size,
                    "waited_ms": waited_ms,
                    "match": patterns or None,
                },
            )
            return 0

        if deadline is not None and time.monotonic() >= deadline:
            target_label = (
                f"{role_label(expected_role)}第 {expected_round} 轮" if expected_role else f"匹配 {patterns} 的文件"
            )
            suffix = f"（{expected_path}）" if expected_path else ""
            return fail(
                action,
                "timeout",
                f"等待超时：{int(timeout)} 秒内未出现 {target_label}{suffix}。对方可能已中断，请把当前状态交给人处理。",
                {
                    "role": expected_role,
                    "round": expected_round,
                    "path": str(expected_path) if expected_path else None,
                    "match": patterns or None,
                    "waited_ms": int((time.monotonic() - started) * 1000),
                    "timeout": timeout,
                },
            )

        time.sleep(poll)


# ---------------------------------------------------------------- status


def cmd_status(args: argparse.Namespace) -> int:
    action = "status"
    workspace, manifest, code = open_workspace(args.dir, action)
    if workspace is None or manifest is None:
        return code

    max_rounds = manifest["max_rounds"]
    published: list[dict[str, Any]] = []
    for role in ROLES:
        for round_number in published_rounds(workspace, role):
            path = contract_path(workspace, round_number, role)
            stat = path.stat()
            published.append(
                {
                    "round": round_number,
                    "role": role,
                    "path": str(path),
                    "bytes": stat.st_size,
                    "mtime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stat.st_mtime)),
                }
            )
    published.sort(key=lambda item: (item["round"], item["role"]))

    turn = current_turn(workspace, max_rounds)
    turn_view: dict[str, Any] | None = None
    if not turn.get("limit_reached") and turn["role"] is not None:
        turn_view = {
            "role": turn["role"],
            "round": turn["round"],
            "target_name": turn["target_name"],
            "target_path": turn["target_path"],
            "staging_glob": turn["staging_glob"],
            "peer_path": turn["peer_path"],
            "peer_round": turn["peer_round"],
        }

    staging_dir = workspace / ".staging"
    orphans: list[str] = []
    active_prefix = f"{turn['round']:02d}-{turn['role']}." if turn_view is not None else None
    if staging_dir.is_dir():
        for entry in sorted(staging_dir.iterdir()):
            if entry.is_file() and not entry.is_symlink() and entry.name.endswith(".md"):
                if active_prefix is not None and entry.name.startswith(active_prefix):
                    continue
                orphans.append(str(entry))

    emit(
        action,
        True,
        f"主题 {manifest['topic']}：{manifest['subject']}。已发布 {len(published)} 份回合文件，{describe_turn(turn)}。",
        {
            "topic": manifest["topic"],
            "subject": manifest["subject"],
            "artifact": manifest["artifact"],
            "dir": str(workspace),
            "max_rounds": max_rounds,
            "published": published,
            "turn": turn_view,
            "limit_reached": bool(turn.get("limit_reached")),
            "orphan_staging": orphans,
        },
    )
    return 0


# ---------------------------------------------------------------- 入口


class UsageError(Exception):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        raise UsageError(message)


def build_parser() -> Parser:
    parser = Parser(prog="parley", add_help=False, description="两个 Agent 轮流讨论的协议工具")
    parser.add_argument("-h", "--help", action="store_true")
    subparsers = parser.add_subparsers(dest="command")

    init = subparsers.add_parser("init", add_help=False)
    init.add_argument("topic")
    init.add_argument("--subject", required=True)
    init.add_argument("--artifact", required=True)
    init.add_argument("--root", default=None)
    init.add_argument("--max-rounds", dest="max_rounds", type=int, default=DEFAULT_MAX_ROUNDS)
    init.add_argument("--force", action="store_true")
    init.add_argument("-h", "--help", action="store_true")
    init.set_defaults(handler=cmd_init)

    nxt = subparsers.add_parser("next", add_help=False)
    nxt.add_argument("--dir", required=True)
    nxt.add_argument("--role", choices=ROLES, required=True)
    nxt.add_argument("-h", "--help", action="store_true")
    nxt.set_defaults(handler=cmd_next)

    publish = subparsers.add_parser("publish", add_help=False)
    publish.add_argument("--file", required=True)
    publish.add_argument("-h", "--help", action="store_true")
    publish.set_defaults(handler=cmd_publish)

    wait = subparsers.add_parser("wait", add_help=False)
    wait.add_argument("--dir", required=True)
    wait.add_argument("--for", dest="for_role", choices=ROLES, default=None)
    wait.add_argument("--match", action="append", default=None)
    wait.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    wait.add_argument("--poll", type=float, default=DEFAULT_POLL)
    wait.add_argument("--stable", type=float, default=DEFAULT_STABLE_MS)
    wait.add_argument("-h", "--help", action="store_true")
    wait.set_defaults(handler=cmd_wait)

    status = subparsers.add_parser("status", add_help=False)
    status.add_argument("--dir", required=True)
    status.add_argument("-h", "--help", action="store_true")
    status.set_defaults(handler=cmd_status)

    return parser


def install_signal_handlers() -> None:
    def handler(signum: int, frame: object) -> None:
        global _interrupted
        _interrupted = True

    for name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, name):
            # 括号形式在所有受支持的 Python 版本上合法; 3.14 起免括号也可, 但不必依赖.
            with contextlib.suppress(ValueError, OSError):
                signal.signal(getattr(signal, name), handler)


def main(argv: list[str] | None = None) -> int:
    install_signal_handlers()
    parser = build_parser()
    arguments = sys.argv[1:] if argv is None else argv

    if not arguments or arguments[0] in ("-h", "--help"):
        emit(
            "help",
            True,
            "parley 通过文件系统契约协调两个 Agent 轮流讨论。可用子命令: init, next, publish, wait, status。",
            {"usage": parser.format_help()},
        )
        return 0

    try:
        args = parser.parse_args(arguments)
    except UsageError as exc:
        return fail("usage_error", "usage_error", f"参数错误: {exc}", {"argv": arguments})

    if getattr(args, "help", False) or getattr(args, "handler", None) is None:
        emit(
            "help",
            True,
            "parley 通过文件系统契约协调两个 Agent 轮流讨论。可用子命令: init, next, publish, wait, status。",
            {"usage": parser.format_help()},
        )
        return 0

    try:
        return int(args.handler(args))
    # 顶层兜底: 无论发生什么, stdout 都必须是一个合法信封.
    except Exception as exc:
        emit(
            args.command,
            False,
            f"工具内部错误: {exc}。这是缺陷，请把 data.detail 一并反馈。",
            {"detail": traceback.format_exc()},
            "internal_error",
        )
        return 0


if __name__ == "__main__":
    sys.exit(main())
