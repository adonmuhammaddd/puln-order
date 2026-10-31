import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

from app.main import main

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_help_lists_commands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])

    assert exc_info.value.code == 0
    assert "db" in capsys.readouterr().out


def test_missing_command_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main([])

    assert exc_info.value.code == 2


def test_db_init_creates_configured_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "store" / "puln.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    exit_code = main(["db", "init"])

    assert exit_code == 0
    assert "customers" in inspect(create_engine(f"sqlite:///{db_path}")).get_table_names()
    assert "Database ready" in capsys.readouterr().out


def test_default_database_lives_in_data_directory(tmp_path: Path) -> None:
    assert main(["db", "init"]) == 0

    assert (tmp_path / "data" / "puln_assistant.db").exists()
    assert (tmp_path / "data" / "logs").is_dir()


def test_invalid_configuration_is_reported_without_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PULN_POLL_INTERVAL_SECONDS", "1")

    exit_code = main(["db", "init"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "PULN_POLL_INTERVAL_SECONDS" in captured.err
    assert "Traceback" not in captured.err


def test_module_entry_point_runs(tmp_path: Path) -> None:
    env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}

    result = subprocess.run(
        [sys.executable, "-m", "app.main", "--help"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "PULN Order Assistant" in result.stdout
