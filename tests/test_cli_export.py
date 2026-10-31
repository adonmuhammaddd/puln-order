import stat
from pathlib import Path

import httpx
import pytest

from app.main import main
from tests.test_cli_monitoring import JSON, SAMPLE


@pytest.fixture(autouse=True)
def session_with_data(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("PULN_SESSION_DATE", "2026-10-31")
    main(
        ["status"],
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=SAMPLE, headers=JSON)),
    )
    capsys.readouterr()


def written_path(output: str) -> Path:
    line = next(line for line in output.splitlines() if line.startswith("Wrote "))
    return Path(line.removeprefix("Wrote ").strip())


@pytest.mark.parametrize("kind", ["bookings", "stock", "slots"])
def test_export_writes_private_csv_in_data_exports(
    kind: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["export", kind]) == 0

    path = written_path(capsys.readouterr().out)
    assert path.parent == Path("data/exports")
    assert path.name.startswith(f"{kind}-online-order-20261031-")
    assert stat.S_IMODE((tmp_path / path).stat().st_mode) == 0o600


def test_export_bookings_with_names_warns(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["export", "bookings", "--with-names"]) == 0

    assert "customer names" in capsys.readouterr().out


def test_db_backup(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["db", "backup"]) == 0

    path = written_path(capsys.readouterr().out)
    assert path.parent == Path("data/backups")
    assert (tmp_path / path).stat().st_size > 0


def test_db_backup_rejects_memory_database(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite://")

    assert main(["db", "backup"]) == 2
    assert "in-memory" in capsys.readouterr().err
