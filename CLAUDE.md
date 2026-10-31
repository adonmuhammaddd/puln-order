# CLAUDE.md

PULN Order Assistant: a local Python monitoring and reservation-coordination tool.
Source of truth for requirements: `PULN-Order-Assistant-Specification.md`.

## Hard boundaries (never implement)

- Automated reservation POSTs, form auto-fill/submit, or parallel/mass requests.
- Turnstile/CAPTCHA solving, bot-detection evasion, browser spoofing, proxy rotation.
- Storing or reusing cookies, Turnstile tokens, `submission_key`, or credentials.
- Marking a booking `CONFIRMED` without explicit operator verification (HTTP 200 is not proof).

## Commands

```bash
uv run pytest                 # tests (HTTP is always mocked; never hit live PULN in tests)
uv run ruff check . && uv run ruff format --check .
uv run mypy                   # strict
uv run python -m app.main --help
```

The system `python3` on this Mac is 3.9.6. Always use `uv run` (pinned 3.12 via `.python-version`).

## Architecture

- `app/config.py`: pydantic-settings `Settings`; `load_settings(env_file=None)` in tests.
- `app/database/`: `base.py` (`UTCDateTime` stores aware UTC, rejects naive), `engine.py`
  (SQLite only, WAL + foreign keys + busy timeout, file chmod 600), `tables.py` (ORM tables).
- `app/models/`: pydantic/domain types (no ORM).
- `app/logging_setup.py`: file-only logging with a redaction filter (safety net, not a licence
  to log personal data).
- `app/ui/commands.py`: CLI handlers return exit codes; `app/main.py` builds the argparse CLI.
- Schema changes: no Alembic yet; `create_all` only adds missing tables. Changing an existing
  column needs a migration plan first.

## Conventions

- Time: store and compare in UTC (`app.clock.utc_now`); convert to `Asia/Jakarta` only for display.
- Tests: synthetic data only; `tests/conftest.py` chdirs to `tmp_path` and clears settings env vars.
- Dependencies are deliberately minimal; justify any new one.
