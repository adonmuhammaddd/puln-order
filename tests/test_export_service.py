import csv
import sqlite3
import stat
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.database.engine import create_session_factory, init_db
from app.models.customer import CustomerInput
from app.models.puln import parse_live_status
from app.repositories import product_repository, snapshot_repository
from app.services.booking_service import BookingService, PreparationRequest
from app.services.customer_service import CustomerService
from app.services.export_service import (
    backup_database,
    export_bookings,
    export_slots,
    export_stock,
    safe_cell,
)

SLUG = "online-order-20261031"
NOW = datetime(2026, 10, 31, 6, 30, 15, tzinfo=UTC)
SAMPLE = parse_live_status((Path(__file__).parent / "fixtures" / "live_sample.json").read_bytes())


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "puln.db"


@pytest.fixture
def session_factory(db_path: Path) -> sessionmaker[Session]:
    factory = create_session_factory(init_db(f"sqlite:///{db_path}"))
    with factory() as db:
        product_repository.seed_known_products(db)
        snapshot_repository.record_live_state(db, SLUG, SAMPLE, NOW)
        db.commit()
    customer = CustomerService(factory, max_active=10).create(
        CustomerInput(
            display_name="=HYPERLINK(evil)",
            whatsapp="081200000001",
            email="customer@example.test",
            notes="secret note 0812",
        ),
        consent_confirmed=True,
    )
    BookingService(factory, session_slug=SLUG).prepare(
        PreparationRequest(
            customer_id=customer.id, product_id=100, quantity=1, slot_id=185, notes="call first"
        ),
        ready=True,
    )
    return factory


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


@pytest.mark.parametrize(
    ("value", "expected"),
    [("=1+1", "'=1+1"), ("+62812", "'+62812"), ("-5", "'-5"), ("@SUM", "'@SUM"), ("ok", "ok")],
)
def test_safe_cell_neutralises_formulas(value: str, expected: str) -> None:
    assert safe_cell(value) == expected


def test_bookings_export_has_no_contact_details_or_notes(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    path = export_bookings(
        session_factory, slug=SLUG, with_names=False, directory=tmp_path / "exports", now=NOW
    )

    content = path.read_text()
    assert "example.test" not in content
    assert "0812" not in content
    assert "call first" not in content
    assert "secret note" not in content
    assert "HYPERLINK" not in content
    rows = read_rows(path)
    assert rows[0]["product_name"] == "Kataifi Rolls"
    assert rows[0]["status"] == "READY"
    assert rows[0]["created_at"].endswith("WIB")
    assert "customer_name" not in rows[0]


def test_bookings_export_with_names_is_formula_safe(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    path = export_bookings(session_factory, slug=SLUG, with_names=True, directory=tmp_path, now=NOW)

    assert read_rows(path)[0]["customer_name"] == "'=HYPERLINK(evil)"


def test_export_files_are_private_and_timestamped(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    path = export_bookings(
        session_factory, slug=SLUG, with_names=False, directory=tmp_path, now=NOW
    )

    assert path.name == "bookings-online-order-20261031-20261031-133015.csv"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_stock_export(session_factory: sessionmaker[Session], tmp_path: Path) -> None:
    path = export_stock(session_factory, slug=SLUG, directory=tmp_path, now=NOW)

    rows = read_rows(path)
    assert len(rows) == 6
    kataifi = next(row for row in rows if row["product_id"] == "100")
    assert kataifi["product_name"] == "Kataifi Rolls"
    assert kataifi["stock_remaining"] == "0"
    assert kataifi["observed_at_utc"] == "2026-10-31T06:30:15+00:00"


def test_slot_export(session_factory: sessionmaker[Session], tmp_path: Path) -> None:
    path = export_slots(session_factory, slug=SLUG, directory=tmp_path, now=NOW)

    rows = read_rows(path)
    assert {row["slot_id"] for row in rows} == {"179", "184", "180", "185", "178", "183"}
    assert next(r for r in rows if r["slot_id"] == "185")["remaining"] == "31"


def test_backup_copies_database_privately(
    session_factory: sessionmaker[Session], db_path: Path, tmp_path: Path
) -> None:
    backup = backup_database(db_path, directory=tmp_path / "backups", now=NOW)

    assert backup.name == "puln_assistant-20261031-133015.db"
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    with closing(sqlite3.connect(backup)) as copy:
        assert copy.execute("select count(*) from bookings").fetchone() == (1,)
        assert copy.execute("select count(*) from stock_snapshots").fetchone() == (6,)


def test_backup_requires_existing_database(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        backup_database(tmp_path / "missing.db", directory=tmp_path, now=NOW)
