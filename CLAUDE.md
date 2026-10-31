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
- `app/models/puln.py`: strict pydantic models for live.json (`model_validate_json`, strict;
  unknown fields tolerated and reported via `unknown_fields`).
- `app/clients/puln_client.py`: read-only GET client returning a typed `FetchResult` union, never
  raising for HTTP/network problems. No redirects, cookie jar cleared after every request.
- `app/services/poll_policy.py`: pure timing rules (`decide_next`). `stock_monitor.py`: pure
  diffing + `HealthTracker`/`DriftTracker` dedup. `monitor_service.py`: orchestration with
  injected clock/rng/sleep. `session_service.py`: URL resolution.
- `app/models/customer.py`: normalise/validate name, WhatsApp (E.164), e-mail, notes; masking.
  `app/services/customer_service.py`: limits, consent, duplicates, delete/anonymize; raises
  `CustomerError` subclasses whose messages are safe to show. Never log personal data.
- `app/ui/prompts.py` + `customer_commands.py`: personal data only via prompts (not flags).
  CLI tests feed answers by monkeypatching `builtins.input`.
- `app/models/booking.py`: `ALLOWED_TRANSITIONS` is the single source of status rules;
  `BookingService.transition` refuses CONFIRMED, only `confirm(email_verified=True)` sets it.
  Every change writes a `BookingStatusChange` row. `bookings open` uses `Runtime.open_url`
  (tests inject a fake; `main(open_url=...)`).
- `app/repositories/`: module functions taking a `Session`; they flush, callers commit.
- `app/ui/dashboard.py`: pure render (`render_dashboard`), tested with `Console(record=True)`.
- `app/ui/commands.py`: CLI handlers return exit codes; `app/main.py` builds the argparse CLI and
  accepts `transport=` so CLI tests run on `httpx.MockTransport`.
- Schema changes: no Alembic yet; `create_all` only adds missing tables. Changing an existing
  column needs a migration plan first.

## PULN endpoint facts (observed 2026-10-31, revalidate per session)

- live.json sends `cache-control: public, max-age=2`; slots disappear once their time passes;
  stock can go back up (0 -> positive) mid-session. Absent is not zero.

## Conventions

- Time: store and compare in UTC (`app.clock.utc_now`); convert to `Asia/Jakarta` only for display.
- Tests: synthetic data only; `tests/conftest.py` chdirs to `tmp_path` and clears settings env vars.
- Dependencies are deliberately minimal; justify any new one.
