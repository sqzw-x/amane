"""从 MediaWiki 的 ZhConversion 表更新内置简繁词典.

上游: https://github.com/wikimedia/mediawiki/blob/master/includes/Languages/Data/ZhConversion.php
默认只下载、解析并打印差异, 不写文件; 确认差异可接受后再加 ``--write``.

策略: 默认 ``merge`` — 旧表独有条目保留, 冲突时以上游为准. 旧词典的繁→简表 (9149 条) 远大于上游
ZH_TO_HANS (4782 条), 旧表含上游之外的来源, 直接替换会大面积丢词; ``replace`` 需要 ``--allow-shrink``.

上游只导出 ZH_TO_HANT / ZH_TO_HANS / ZH_TO_TW / ZH_TO_HK / ZH_TO_CN 五张表.
``SIMPONLY`` / ``TRADONLY`` 上游已不再导出, 保留本地值 (供 ``issimp`` 使用).
上游结构变化 (常量数量或名字不符) 时脚本报错退出, 不做猜测.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import cast

RAW_URL = "https://raw.githubusercontent.com/wikimedia/mediawiki/master/includes/Languages/Data/ZhConversion.php"
DICT_PATH = Path(__file__).resolve().parents[1] / "src" / "amane" / "utils" / "zhcdict.json"

_BLOCKS: dict[str, str] = {
    "ZH_TO_HANT": "zh2Hant",
    "ZH_TO_HANS": "zh2Hans",
    "ZH_TO_TW": "zh2TW",
    "ZH_TO_HK": "zh2HK",
    "ZH_TO_CN": "zh2CN",
}
"""上游常量名 → 本地词典键名."""

_MIN_ENTRIES = 500
"""单表条目数下限; 低于此值视为解析失败, 避免空表或残表覆盖现有数据."""

_BLOCK = re.compile(r"public const (ZH_TO_\w+) = \[(.*?)\n\t\];", re.DOTALL)
_LINE = re.compile(r"^\s*'((?:[^'\\]|\\.)*)'\s*=>\s*'((?:[^'\\]|\\.)*)',?\s*$")


def download(url: str) -> str:
    with urllib.request.urlopen(url) as response:
        return response.read().decode("utf-8")


def parse(php: str) -> dict[str, dict[str, str]]:
    """解析 PHP 常量数组. 每行一个 ``'key' => 'value',``, 其余形态视为结构变化."""
    tables: dict[str, dict[str, str]] = {}
    for name, body in _BLOCK.findall(php):
        table: dict[str, str] = {}
        for line in body.splitlines():
            if not line.strip():
                continue
            match = _LINE.match(line)
            if match is None:
                raise ValueError(f"{name} 含无法解析的行: {line[:80]!r}")
            table[_unescape(match.group(1))] = _unescape(match.group(2))
        tables[name] = table
    if set(tables) != set(_BLOCKS):
        raise ValueError(f"上游常量与预期不符: {sorted(tables)}")
    for name, table in tables.items():
        if len(table) < _MIN_ENTRIES:
            raise ValueError(f"{name} 条目过少 ({len(table)}), 疑似解析失败")
    return tables


def _unescape(s: str) -> str:
    return s.replace("\\'", "'").replace("\\\\", "\\")


def build(
    existing: dict[str, object],
    tables: dict[str, dict[str, str]],
    *,
    policy: str,
    allow_shrink: bool,
) -> dict[str, object]:
    """按策略替换五张映射表, 保留 SIMPONLY / TRADONLY 与未知键."""
    merged = dict(existing)
    for upstream, local in _BLOCKS.items():
        old = cast(dict[str, str], existing[local])
        new = tables[upstream]
        if policy == "merge":
            table = dict(old)
            table.update(new)
        else:
            if len(new) < len(old) and not allow_shrink:
                raise ValueError(f"{local} 条目从 {len(old)} 减到 {len(new)}; 确认后加 --allow-shrink")
            table = new
        merged[local] = table
    return merged


def report(existing: dict[str, object], merged: dict[str, object]) -> None:
    for upstream, local in _BLOCKS.items():
        old = cast(dict[str, str], existing[local])
        new = cast(dict[str, str], merged[local])
        added = new.keys() - old.keys()
        removed = old.keys() - new.keys()
        changed = {word for word in old.keys() & new.keys() if old[word] != new[word]}
        print(f"{upstream:10} {len(old):6} -> {len(new):6}  +{len(added):5} -{len(removed):5} ~{len(changed):5}")  # noqa: T201
        for word in list(added)[:3]:
            print(f"  + {word!r} => {new[word]!r}")  # noqa: T201
        for word in list(removed)[:3]:
            print(f"  - {word!r}")  # noqa: T201
        for word in list(changed)[:3]:
            print(f"  ~ {word!r}: {old[word]!r} -> {new[word]!r}")  # noqa: T201


def main() -> int:
    parser = argparse.ArgumentParser(description="同步 MediaWiki 简繁转换表")
    parser.add_argument("--file", type=Path, help="使用本地 ZhConversion.php, 不下载")
    parser.add_argument("--write", action="store_true", help="确认差异后写回内置词典")
    parser.add_argument("--policy", choices=("merge", "replace"), default="merge", help="默认 merge")
    parser.add_argument("--allow-shrink", action="store_true", help="replace 时允许条目数减少")
    args = parser.parse_args()

    php = args.file.read_text(encoding="utf-8") if args.file else download(RAW_URL)
    existing = cast(dict[str, object], json.loads(DICT_PATH.read_text(encoding="utf-8")))
    merged = build(existing, parse(php), policy=args.policy, allow_shrink=args.allow_shrink)
    report(existing, merged)

    if args.write:
        DICT_PATH.write_text(
            json.dumps(merged, ensure_ascii=False, indent=0, separators=(",", ": "), sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"已写入 {DICT_PATH}")  # noqa: T201
    else:
        print("未写入; 确认差异后加 --write")  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main())
