from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bump_version.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("bump_version", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bump = _load()


@pytest.mark.parametrize(
    ("version", "message", "tag"),
    [
        ("1.0.1", "release: 1.0.1", "v1.0.1"),
        ("2.0.0", "release: 2.0.0", "v2.0.0"),
        ("1.2.3", "release: 1.2.3", "v1.2.3"),
    ],
)
def test_commit_message_and_tag(version: str, message: str, tag: str) -> None:
    assert bump.commit_message(version) == message
    assert bump.tag_name(version) == tag


@pytest.mark.parametrize(
    ("diff", "untracked", "expected"),
    [
        ("pyproject.toml\n", "", frozenset({"pyproject.toml"})),
        ("pyproject.toml\nuv.lock\n", "tmp.txt\n", frozenset({"pyproject.toml", "uv.lock", "tmp.txt"})),
        ("", "", frozenset()),
        ("  \n", "\n", frozenset()),
    ],
)
def test_parse_name_list(diff: str, untracked: str, expected: frozenset[str]) -> None:
    assert bump.parse_name_list(diff, untracked) == expected


@pytest.mark.parametrize(
    ("changed", "unexpected"),
    [
        (frozenset({"pyproject.toml", "uv.lock"}), frozenset()),
        (frozenset({"pyproject.toml", "CHANGELOG.md"}), frozenset()),
        (frozenset({"pyproject.toml", "src/amane/version.py"}), frozenset({"src/amane/version.py"})),
        (frozenset({"pyproject.toml", "web/openapi.json"}), frozenset()),
        (frozenset({"web/src/client/types.gen.ts", "web/src/client/sdk.gen.ts"}), frozenset()),
        (frozenset({"web/src/client_backup/x.ts"}), frozenset({"web/src/client_backup/x.ts"})),
        (frozenset({"pyproject.toml", "README.md"}), frozenset({"README.md"})),
        (frozenset({"scripts/foo.py"}), frozenset({"scripts/foo.py"})),
        (frozenset(), frozenset()),
    ],
)
def test_extra_paths(changed: frozenset[str], unexpected: frozenset[str]) -> None:
    assert bump.extra_paths(changed) == unexpected


def test_require_clean_ok() -> None:
    bump.require_clean(frozenset())


def test_require_clean_rejects_dirty() -> None:
    with pytest.raises(bump.BumpError):
        bump.require_clean(frozenset({"pyproject.toml"}))


def test_parse_kind_accepts_semver_segments() -> None:
    assert bump._parse_kind("patch") == "patch"
    assert bump._parse_kind("minor") == "minor"
    assert bump._parse_kind("major") == "major"


def test_parse_kind_rejects_unknown() -> None:
    with pytest.raises(bump.BumpError):
        bump._parse_kind("alpha")


def test_allowed_changed_includes_client_prefix() -> None:
    changed = frozenset(
        {
            "CHANGELOG.md",
            "pyproject.toml",
            "web/openapi.json",
            "web/src/client/types.gen.ts",
            "README.md",
        }
    )
    assert bump.allowed_changed(changed) == [
        "CHANGELOG.md",
        "pyproject.toml",
        "web/openapi.json",
        "web/src/client/types.gen.ts",
    ]


def test_allowed_paths_are_the_known_sidecars() -> None:
    assert (
        frozenset(
            {
                "CHANGELOG.md",
                "pyproject.toml",
                "uv.lock",
                "web/openapi.json",
                "web/src/client/",
            }
        )
        == bump.ALLOWED_PATHS
    )


def test_require_prebump_clean_allows_changelog() -> None:
    bump.require_prebump_clean(frozenset())
    bump.require_prebump_clean(frozenset({"CHANGELOG.md"}))


def test_require_prebump_clean_rejects_other_dirty() -> None:
    with pytest.raises(bump.BumpError):
        bump.require_prebump_clean(frozenset({"CHANGELOG.md", "README.md"}))


def test_require_changelog_ok(tmp_path: Path) -> None:
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## v1.2.3\n\n- item\n", encoding="utf-8")
    bump.require_changelog(tmp_path, "1.2.3")
