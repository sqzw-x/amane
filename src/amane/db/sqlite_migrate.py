"""SQLite 迁移安全辅助: WAL 一致备份 + 事务性 DDL 升级.

备份使用 sqlite3 在线 backup API (含 WAL), 禁止只 cp 主文件.
事务性 DDL 仅作用于迁移连接 (autocommit=False), 不影响业务连接.
"""

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
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Connection, Engine

logger = structlog.get_logger()

_DEFAULT_KEEP = 5


def register_sqlite_datetime_adapters() -> None:
    """将 date/datetime 转为字符串后再交给 sqlite3.

    SQLite 无日期类型. Python 3.12 起内置转换已弃用: 目前发出警告, 移除后直接传入 datetime 将失败.
    SQLAlchemy 的 DateTime 列会自行转换; 迁移脚本中的裸 SQL 不会. 字符串格式与 Python 原先默认一致, 以便读取既有数据.
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
    """启用 Python 3.12+ 非遗留事务模式, 使 DDL 可随事务回滚."""
    dbapi_connection.autocommit = False


def backup_sqlite_database(
    db_path: Path,
    *,
    dest_dir: Path | None = None,
    keep: int = _DEFAULT_KEEP,
    label: str | None = None,
) -> Path | None:
    """用 online backup API 生成一致快照.

    库文件不存在或大小为 0 时返回 None (无需备份).
    ``label`` 写入文件名 (通常为当前 alembic revision).
    """
    if not db_path.is_file() or db_path.stat().st_size == 0:
        return None

    dest_dir = dest_dir if dest_dir is not None else db_path.parent
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe_label = (label or "unknown").replace("/", "_")
    bak_path = dest_dir / f"{db_path.name}.pre-migrate-{safe_label}-{stamp}.bak"

    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(bak_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    pruned = prune_migrate_backups(db_path, dest_dir=dest_dir, keep=keep)
    logger.info(
        "sqlite pre-migrate backup created",
        backup=str(bak_path),
        source=str(db_path),
        pruned=pruned,
    )
    return bak_path


def prune_migrate_backups(db_path: Path, *, dest_dir: Path, keep: int) -> int:
    """按修改时间保留最新 keep 份 ``{db}.pre-migrate-*.bak``, 返回删除数量."""
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
    """读取库内 alembic head; 无 version 表或空库返回 None."""
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
    """解析 Alembic head; 以 versions 目录 mtime 为缓存键, 同路径追加 revision 后失效."""
    cfg = Config()
    cfg.set_main_option("script_location", script_location)
    return frozenset(ScriptDirectory.from_config(cfg).get_heads())


def needs_upgrade(db_path: Path, *, script_location: Path | None = None) -> bool:
    """当前 revision 是否落后于脚本 head."""
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
    return current != head_revs


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
    """升级到 head; 若需要升级且 ``backup`` 则先做 WAL 安全备份.

    返回备份路径 (未备份则为 None).
    迁移连接启用事务性 DDL: 单次 revision 内失败应回滚 DDL + version.
    """
    script_location = script_location or migrations_dir()
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    if not needs_upgrade(db_path, script_location=script_location):
        logger.debug("sqlite schema already at head", path=str(db_path))
        return None

    bak: Path | None = None
    if backup:
        bak = backup_sqlite_database(
            db_path,
            dest_dir=db_path.parent,
            keep=backup_keep,
            label=current_revision(db_path) or "empty",
        )

    cfg = Config()
    cfg.set_main_option("script_location", str(script_location))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    if connection is not None:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "head")
        return bak

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
