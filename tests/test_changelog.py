from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "changelog.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("changelog", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cl = _load()

SAMPLE = """# Changelog

前言不算节.

## [Unreleased]

- 还没发

## v0.5.0 - 2026-08-27

### ✨ 新功能

- **媒体库文件过滤** (#13)

## v0.5.10

- patch ten

## v0.4.2

- **演员刮削** (#10)
"""


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("0.5.0", "0.5.0"),
        ("v0.5.0", "0.5.0"),
        ("V0.5.0", "0.5.0"),
        ("  v1.2.3  ", "1.2.3"),
    ],
)
def test_normalize_version(raw: str, want: str) -> None:
    assert cl.normalize_version(raw) == want


@pytest.mark.parametrize(
    ("title", "want"),
    [
        ("v0.5.0", "0.5.0"),
        ("0.5.0", "0.5.0"),
        ("v0.5.0 - 2026-08-27", "0.5.0"),
        ("[0.5.0] - 2026-08-27", "0.5.0"),
        ("[Unreleased]", None),
        ("Unreleased", None),
        ("v0.5.10", "0.5.10"),
    ],
)
def test_heading_version(title: str, want: str | None) -> None:
    assert cl.heading_version(title) == want


def test_extract_section_matches_version_not_latest() -> None:
    notes = cl.extract_section(SAMPLE, "v0.4.2")
    assert notes.startswith("## v0.4.2\n")
    assert "演员刮削" in notes
    assert "0.5.0" not in notes


def test_extract_section_ignores_unreleased_and_does_not_prefix_match() -> None:
    notes = cl.extract_section(SAMPLE, "0.5.0")
    assert "媒体库文件过滤" in notes
    assert "patch ten" not in notes
    assert "还没发" not in notes


def test_extract_section_distinguishes_0_5_10() -> None:
    notes = cl.extract_section(SAMPLE, "0.5.10")
    assert "patch ten" in notes
    assert "媒体库文件过滤" not in notes


@pytest.mark.parametrize("version", ["0.9.9", ""])
def test_extract_section_missing(version: str) -> None:
    with pytest.raises(cl.ChangelogError):
        cl.extract_section(SAMPLE, version)


def test_extract_section_empty() -> None:
    text = "# Changelog\n\n## v1.0.0\n\n\n## v0.9.0\n\n- x\n"
    with pytest.raises(cl.ChangelogError):
        cl.extract_section(text, "1.0.0")


def test_extract_file_and_cli(tmp_path: Path) -> None:
    path = tmp_path / "CHANGELOG.md"
    path.write_text(SAMPLE, encoding="utf-8")
    assert "媒体库文件过滤" in cl.extract_file(path, "0.5.0")
    assert cl.main(["extract", "0.5.0", "--file", str(path)]) == 0


def test_extract_file_missing(tmp_path: Path) -> None:
    missing = tmp_path / "CHANGELOG.md"
    with pytest.raises(cl.ChangelogError):
        cl.extract_file(missing, "0.5.0")
    assert cl.main(["extract", "0.5.0", "--file", str(missing)]) == 1


def test_extract_crlf() -> None:
    text = "# Changelog\r\n\r\n## v1.2.3\r\n\r\n- **fix** (#1)\r\n"
    notes = cl.extract_section(text, "1.2.3")
    assert "**fix** (#1)" in notes
