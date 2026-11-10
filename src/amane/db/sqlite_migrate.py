"""WAL 模式下禁止只 cp 主文件; 事务性 DDL 仅作用于迁移连接, 不影响业务连接."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path

import structlog
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Connection, Engine

logger = structlog.get_logger()

_DEFAULT_KEEP = 5


def register_sqlite_datetime_adapters() -> None:
    """裸 SQL 绑定 date/datetime 前须转为字符串; SQLAlchemy DateTime 列会自行转换.
    格式与 Python 默认一致 (``YYYY-MM-DD[ HH:MM:SS]``), 否则读不出既有数据.
    """
    sqlite3.register_adapter(date, _adapt_date)
    sqlite3.register_adapter(datetime, _adapt_datetime)


def _adapt_date(val: date) -> str:
    return val.isoformat()


def _adapt_datetime(val: datetime) -> str:
    return val.isoformat(" ")


register_sqlite_datetime_adapters()


def migrations_dir() -> Path:
    return Path(__file__).parent / "migrations"


def enable_sqlite_transactional_ddl(dbapi_connection: sqlite3.Connection) -> None:
    """Python 3.12+ 非遗留事务模式, 使 DDL 可随事务回滚."""
    dbapi_connection.autocommit = False


def backup_sqlite_database(
    db_path: Path,
    *,
    dest_dir: Path | None = None,
    keep: int = _DEFAULT_KEEP,
    label: str | None = None,
) -> Path | None:
    """online backup API 生成含 WAL 的一致快照. 库文件不存在或大小为 0 时返回 None."""
    if not db_path.is_file() or db_path.stat().st_size == 0:
        return None

    dest_dir = dest_dir if dest_dir is not None else db_path.parent
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe_label = (label or "unknown").replace("/", "_")
    bak_path = dest_dir / f"{db_path.name}.pre-migrate-{safe_label}-{stamp}.bak"

    # 在线 backup, 含 WAL; 禁止只复制主文件.
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(bak_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    # 排空超出 keep 的旧备份
    pruned = prune_migrate_backups(db_path, dest_dir=dest_dir, keep=keep)
    logger.info(
        "sqlite pre-migrate backup created",
        backup=str(bak_path),
        source=str(db_path),
        pruned=pruned,
    )
    return bak_path


def prune_migrate_backups(db_path: Path, *, dest_dir: Path, keep: int) -> int:
    """按修改时间只保留最新 keep 份 ``{db}.pre-migrate-*.bak``."""
    if keep < 1:
        raise ValueError("keep must be >= 1")
    prefix = f"{db_path.name}.pre-migrate-"
    backups = sorted(
        (p for p in dest_dir.iterdir() if p.is_file() and p.name.startswith(prefix) and p.suffix == ".bak"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    removed = 0
    for old in backups[keep:]:
        old.unlink(missing_ok=True)
        removed += 1
    return removed


def current_revision(db_path: Path) -> str | None:
    """无 version 表或空库返回 None."""
    if not db_path.is_file() or db_path.stat().st_size == 0:
        return None
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            heads = context.get_current_heads()
            if not heads:
                return None
            if len(heads) == 1:
                return heads[0]
            return ",".join(sorted(heads))
    finally:
        engine.dispose()


@lru_cache(maxsize=8)
def _alembic_head_revs(script_location: str, versions_mtime: float) -> frozenset[str]:
    """以 versions 目录 mtime 为缓存键; 同路径追加 revision 后须失效."""
    cfg = Config()
    cfg.set_main_option("script_location", script_location)
    return frozenset(ScriptDirectory.from_config(cfg).get_heads())


def _unknown_revisions(script_location: Path, current: set[str]) -> list[str]:
    """当前 revision 中不存在于脚本树的部分; 缺失项说明库由更新版本写过."""
    cfg = Config()
    cfg.set_main_option("script_location", str(script_location))
    script = ScriptDirectory.from_config(cfg)
    unknown: list[str] = []
    for rev in sorted(current):
        try:
            script.get_revision(rev)
        except CommandError:
            unknown.append(rev)
    return unknown


def needs_upgrade(db_path: Path, *, script_location: Path | None = None) -> bool:
    """当前 revision 是否落后于脚本 head. 空库视为需要升级.

    库中 revision 不在脚本树内时抛 ``RuntimeError``: 库版本高于当前代码, 不能备份或升级.
    """
    if not db_path.is_file() or db_path.stat().st_size == 0:
        return True

    script_location = script_location or migrations_dir()
    versions = script_location / "versions"
    versions_mtime = versions.stat().st_mtime if versions.is_dir() else 0.0
    head_revs = set(_alembic_head_revs(str(script_location), versions_mtime))

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            current = set(context.get_current_heads())
    finally:
        engine.dispose()

    if current == head_revs:
        return False
    unknown = _unknown_revisions(script_location, current)
    if unknown:
        raise RuntimeError(
            f"数据库 {db_path} 的 alembic revision {', '.join(unknown)} 不在当前代码的迁移脚本中; "
            "库版本高于当前代码, 未创建备份, 也未执行升级; 请升级程序, 或从已有备份恢复"
        )
    return True


def _attach_transactional_ddl(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection: sqlite3.Connection, _connection_record: object) -> None:
        enable_sqlite_transactional_ddl(dbapi_connection)


def upgrade_sqlite_database(
    db_path: Path,
    *,
    script_location: Path | None = None,
    backup: bool = True,
    backup_keep: int = _DEFAULT_KEEP,
    connection: Connection | None = None,
) -> Path | None:
    """落后 head 且 ``backup`` 时先做 WAL 安全备份. 单次 revision 失败须回滚 DDL 与 version.

    库中 revision 不在脚本树内时抛 ``RuntimeError``, 不写备份.
    """
    script_location = script_location or migrations_dir()
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    if not needs_upgrade(db_path, script_location=script_location):
        logger.debug("sqlite schema already at head", path=str(db_path))
        return None

    # 升级前备份
    bak: Path | None = None
    if backup:
        bak = backup_sqlite_database(
            db_path,
            dest_dir=db_path.parent,
            keep=backup_keep,
            label=current_revision(db_path) or "empty",
        )

    # 应用迁移
    cfg = Config()
    cfg.set_main_option("script_location", str(script_location))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    if connection is not None:
        # 调用方已持有连接, 不另开引擎
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "head")
        return bak

    # 自建连接须启用事务性 DDL
    engine = create_engine(f"sqlite:///{db_path}")
    _attach_transactional_ddl(engine)
    try:
        with engine.connect() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "head")
            conn.commit()
    finally:
        engine.dispose()

    logger.info("sqlite migrations applied", path=str(db_path), backup=str(bak) if bak else None)
    return bak
