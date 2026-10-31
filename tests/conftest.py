from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.config import Settings
from app.database.engine import create_session_factory, init_db


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep tests independent of the developer's real .env file and shell variables."""
    monkeypatch.chdir(tmp_path)
    for field_name in Settings.model_fields:
        monkeypatch.delenv(field_name.upper(), raising=False)


@pytest.fixture
def db_session() -> Iterator[Session]:
    session_factory = create_session_factory(init_db("sqlite://"))
    with session_factory() as session:
        yield session
