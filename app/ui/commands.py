"""CLI command handlers. Each handler returns a process exit code."""

from rich.console import Console
from sqlalchemy import Engine, inspect

from app.config import Settings
from app.database.engine import sqlite_file_path


def run_db_init(settings: Settings, engine: Engine, console: Console) -> int:
    location = sqlite_file_path(settings.database_url) or "in-memory"
    table_count = len(inspect(engine).get_table_names())
    console.print(f"Database ready: {location} ({table_count} tables)", soft_wrap=True)
    return 0
