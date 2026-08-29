"""演员用例发现: 双料站只读 actor/, 无该目录时不回退站点根."""

from pathlib import Path

from .driven import discover_actor_cases


def _write_toml(path: Path, site: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'site = "{site}"\n', encoding="utf-8")


def test_discover_actor_skips_dual_role_film_root(tmp_path: Path) -> None:
    _write_toml(tmp_path / "theporndb" / "ssis497.toml", "theporndb")
    _write_toml(tmp_path / "javdb" / "ssis497.toml", "javdb")
    _write_toml(tmp_path / "javdb" / "actor" / "miru.toml", "javdb")
    _write_toml(tmp_path / "minnano" / "aika.toml", "minnano")

    ids = [
        c[0]
        for c in discover_actor_cases(
            lambda site: site in {"theporndb", "javdb", "minnano"},
            is_dual=lambda site: site in {"theporndb", "javdb"},
            cases_dir=tmp_path,
        )
    ]
    assert ids == ["javdb/actor/miru", "minnano/aika"]
