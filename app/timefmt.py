"""Display helpers: all stored times are UTC, all displayed times are local."""

from datetime import datetime
from zoneinfo import ZoneInfo


def to_local(value: datetime, timezone: str) -> datetime:
    return value.astimezone(ZoneInfo(timezone))


def format_local(value: datetime | None, timezone: str, fmt: str = "%Y-%m-%d %H:%M:%S %Z") -> str:
    if value is None:
        return "-"
    return to_local(value, timezone).strftime(fmt)
