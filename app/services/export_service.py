"""CSV exports and database backups. Files are written owner-only.

Booking exports never contain WhatsApp numbers, e-mail addresses or notes; customer names are
included only when explicitly requested. Cells that a spreadsheet would treat as formulas are
prefixed with an apostrophe.
"""

import csv
import os
import sqlite3
from collections.abc import Iterable, Sequence
from contextlib import closing
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.database.tables import PickupSlot
from app.repositories import (
    booking_repository,
    customer_repository,
    product_repository,
    report_repository,
)
from app.timefmt import format_local

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_STAMP = "%Y%m%d-%H%M%S"


def safe_cell(value: object) -> str:
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(_FORMULA_PREFIXES) else text


def _private_file(path: Path) -> None:
    path.touch(mode=0o600, exist_ok=True)
    os.chmod(path, 0o600)


def _write_csv(path: Path, header: Sequence[str], rows: Iterable[Sequence[object]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    _private_file(path)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows([safe_cell(cell) for cell in row] for row in rows)
    return path


def _stamp(now: datetime, timezone: str) -> str:
    return format_local(now, timezone, _STAMP)


def export_bookings(
    session_factory: sessionmaker[Session],
    *,
    slug: str | None,
    with_names: bool,
    directory: Path,
    now: datetime,
    timezone: str = "Asia/Jakarta",
) -> Path:
    with session_factory() as db:
        bookings = booking_repository.list_bookings(db, session_slug=slug)
        names = product_repository.product_names(db)
        customers = {c.id: c.display_name for c in customer_repository.list_customers(db)}
    header = [
        "booking_id",
        "session",
        "customer_id",
        *(["customer_name"] if with_names else []),
        "product_id",
        "product_name",
        "quantity",
        "slot_id",
        "status",
        "confirmation_ref",
        "created_at",
        "updated_at",
        "confirmed_at",
    ]
    rows = [
        [
            b.id,
            b.session_slug,
            b.customer_id,
            *([customers.get(b.customer_id, "")] if with_names else []),
            b.puln_product_id,
            names.get(b.puln_product_id, ""),
            b.quantity,
            b.puln_slot_id,
            b.status,
            b.confirmation_ref,
            format_local(b.created_at, timezone),
            format_local(b.updated_at, timezone),
            "" if b.confirmed_at is None else format_local(b.confirmed_at, timezone),
        ]
        for b in bookings
    ]
    scope = slug or "all-sessions"
    return _write_csv(directory / f"bookings-{scope}-{_stamp(now, timezone)}.csv", header, rows)


def export_stock(
    session_factory: sessionmaker[Session],
    *,
    slug: str,
    directory: Path,
    now: datetime,
    timezone: str = "Asia/Jakarta",
) -> Path:
    with session_factory() as db:
        snapshots = report_repository.session_stock_snapshots(db, slug)
        names = product_repository.product_names(db)
    header = [
        "observed_at_utc",
        "observed_at_local",
        "product_id",
        "product_name",
        "stock_remaining",
        "stock_allocated",
    ]
    rows = [
        [
            s.observed_at.isoformat(),
            format_local(s.observed_at, timezone),
            s.puln_product_id,
            names.get(s.puln_product_id, ""),
            s.stock_remaining,
            s.stock_allocated,
        ]
        for s in snapshots
    ]
    return _write_csv(directory / f"stock-{slug}-{_stamp(now, timezone)}.csv", header, rows)


def export_slots(
    session_factory: sessionmaker[Session],
    *,
    slug: str,
    directory: Path,
    now: datetime,
    timezone: str = "Asia/Jakarta",
) -> Path:
    with session_factory() as db:
        snapshots = report_repository.session_slot_snapshots(db, slug)
        labels = {
            slot.puln_slot_id: slot.label
            for slot in db.scalars(select(PickupSlot).where(PickupSlot.session_slug == slug))
        }
    header = ["observed_at_utc", "observed_at_local", "slot_id", "label", "remaining"]
    rows = [
        [
            s.observed_at.isoformat(),
            format_local(s.observed_at, timezone),
            s.puln_slot_id,
            labels.get(s.puln_slot_id, ""),
            s.remaining,
        ]
        for s in snapshots
    ]
    return _write_csv(directory / f"slots-{slug}-{_stamp(now, timezone)}.csv", header, rows)


def backup_database(
    db_path: Path, *, directory: Path, now: datetime, timezone: str = "Asia/Jakarta"
) -> Path:
    """Consistent copy via SQLite's online backup API (safe while the monitor is running)."""
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"puln_assistant-{_stamp(now, timezone)}.db"
    _private_file(target)
    with closing(sqlite3.connect(db_path)) as source, closing(sqlite3.connect(target)) as copy:
        source.backup(copy)
    os.chmod(target, 0o600)
    return target
