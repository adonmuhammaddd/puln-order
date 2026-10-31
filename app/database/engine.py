"""SQLite engine creation and idempotent schema initialisation."""

import os
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.database import tables  # noqa: F401  (registers tables on Base.metadata)
from app.database.base import Base

_BUSY_TIMEOUT_MS = 5_000


def sqlite_file_path(database_url: str) -> Path | None:
    """Return the database file for a SQLite URL, or None for an in-memory database."""
    database = make_url(database_url).database
    if not database or database == ":memory:":
        return None
    return Path(database)


def _set_sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


def create_db_engine(database_url: str) -> Engine:
    if make_url(database_url).get_backend_name() != "sqlite":
        raise ValueError("Only SQLite databases are supported")
    path = sqlite_file_path(database_url)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(database_url)
    event.listen(engine, "connect", _set_sqlite_pragmas)
    return engine


def _restrict_permissions(path: Path) -> None:
    for candidate in (path, path.with_name(f"{path.name}-wal"), path.with_name(f"{path.name}-shm")):
        if candidate.exists():
            os.chmod(candidate, 0o600)


def init_db(database_url: str) -> Engine:
    """Create missing tables and restrict the database file to the current user."""
    engine = create_db_engine(database_url)
    Base.metadata.create_all(engine)
    path = sqlite_file_path(database_url)
    if path is not None:
        _restrict_permissions(path)
    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
