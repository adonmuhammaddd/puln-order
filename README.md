# PULN Order Assistant

Local terminal tool that monitors PULN online-order availability and helps an operator
coordinate reservations for up to 10 consenting customers.

It is **not** a purchasing bot. Reservations, and any Turnstile/CAPTCHA challenge, always stay
in the official PULN page under the customer's or operator's direct control.

Full product specification: [PULN-Order-Assistant-Specification.md](PULN-Order-Assistant-Specification.md).

## Requirements

- macOS (Apple Silicon tested), Python 3.12+
- [uv](https://docs.astral.sh/uv/)

The macOS system `python3` may be 3.9; always run through `uv run` so the pinned 3.12 is used.

## Setup

```bash
uv sync
cp .env.example .env   # then set PULN_SESSION_DATE to the real session date
uv run python -m app.main db init
```

## Usage

```bash
uv run python -m app.main --help
uv run python -m app.main sessions validate     # one check of the session page + live.json
uv run python -m app.main status                # manual refresh: one request, print dashboard
uv run python -m app.main monitor               # live dashboard until the session closes
uv run python -m app.main monitor --keep-running --no-bell
uv run python -m app.main products list
uv run python -m app.main products name 102 "Product name from the official page"
uv run python -m app.main db init
```

### Session selection

Precedence: `PULN_SESSION_URL` > `PULN_SESSION_SLUG` > `PULN_SESSION_DATE` (becomes
`online-order-YYYYMMDD`). A generated slug is only a guess; run `sessions validate` first.

### Monitoring behaviour

- Polls every `PULN_POLL_INTERVAL_SECONDS` (minimum 5) with about 10% jitter.
- Before `opens_at` it polls at the maximum interval and wakes up at opening time.
- Network errors, 5xx and malformed data back off exponentially up to the maximum interval.
- HTTP 429 pauses for at least `Retry-After`.
- HTTP 403, 404 and redirects stop monitoring; open the session in a browser instead.
- Stops when the session stops accepting orders, unless `--keep-running` is given.
- Invalid data never replaces the last valid state; the dashboard marks it `LAST KNOWN`.
- Stock and slot history is written only when a value changes.
- The terminal bell rings for alerts: session opening/closing, target product stock changes,
  a slot reopening, and the endpoint becoming unavailable. Disable with `--no-bell` or
  `NOTIFY_BELL=false`.

The live endpoint has no product names. Names come from the local catalogue (`products name`);
unknown products show as `Product #<id>`.

## Development

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy
```

## Local data

Everything stays on this machine under `data/` (git-ignored):

- `data/puln_assistant.db`: SQLite database, permissions restricted to the current user
- `data/logs/puln_assistant.log`: rotating log; e-mail addresses, phone numbers and tokens are redacted

Never commit `.env`, the database, logs, or real customer data.
