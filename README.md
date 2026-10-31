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
uv run python -m app.main db init
```

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
