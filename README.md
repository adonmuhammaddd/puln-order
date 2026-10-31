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

## A session day in short

```bash
uv run python -m app.main sessions validate   # does the session exist and is live.json valid?
uv run python -m app.main monitor --desktop   # leave running in its own terminal tab
uv run python -m app.main bookings prepare    # per customer, before or during the session
uv run python -m app.main bookings open 1     # complete the official form yourself
uv run python -m app.main bookings mark 1 submitted
uv run python -m app.main bookings review 1 ~/Downloads/reservasi.html
uv run python -m app.main bookings mark 1 awaiting-email
uv run python -m app.main bookings confirm 1  # after the confirmation email arrives
uv run python -m app.main report
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

### Customers

```bash
uv run python -m app.main customers add              # interactive, asks for explicit consent
uv run python -m app.main customers list [--active]  # contact details masked
uv run python -m app.main customers show 1 [--reveal]
uv run python -m app.main customers edit 1           # Enter keeps a value, "-" clears notes
uv run python -m app.main customers deactivate 1
uv run python -m app.main customers activate 1
uv run python -m app.main customers delete 1         # type DELETE to confirm
uv run python -m app.main customers delete 1 --anonymize   # keep booking history
```

- Personal data is only entered through prompts, never as command-line flags, so it does not
  land in shell history.
- At most `MAX_CUSTOMERS` (up to 10) active profiles; inactive ones do not count.
- WhatsApp numbers are stored as `+<country><number>`; `08...` becomes `+628...`.
- A WhatsApp number or e-mail can belong to one profile only.
- A customer with booking history cannot be deleted; `--anonymize` erases name, number, e-mail
  and notes, deactivates the profile and keeps the bookings. Anonymized profiles cannot be
  reactivated; add the customer again with fresh consent.
- Logs refer to customers by ID only.

### Bookings

```bash
uv run python -m app.main products limit 100 1        # per-person limit seen on the official page
uv run python -m app.main bookings prepare            # interactive; shows last-known availability
uv run python -m app.main bookings list [--all]
uv run python -m app.main bookings show 1             # details + status history
uv run python -m app.main bookings open 1             # opens the official page in your browser
uv run python -m app.main bookings mark 1 submitted   # after completing the official form yourself
uv run python -m app.main bookings mark 1 awaiting-email
uv run python -m app.main bookings confirm 1          # only after checking the confirmation email
uv run python -m app.main bookings mark 1 failed --note
```

Status flow:

```text
DRAFT -> READY -> OFFICIAL_FLOW_OPENED -> SUBMITTED_BY_USER -> (SUCCESS_PAGE_REVIEWED) -> AWAITING_EMAIL -> CONFIRMED
```

Open bookings can also become `FAILED` or `CANCELLED`; `CONFIRMED`, `FAILED` and `CANCELLED`
are final. `READY` and `OFFICIAL_FLOW_OPENED` can step back for corrections.

- `bookings open` only opens the official page. It never fills or submits the form, and any
  security check is completed by a person in the browser.
- `CONFIRMED` is reachable only through `bookings confirm`, from `AWAITING_EMAIL`, after the
  operator states they checked the official confirmation email. HTTP 200 or a success page alone
  is not enough.
- One open booking per customer, product and session; cancel it before preparing another.
- Quantity is checked against the local limit (`products limit`). Stock and slot capacity are
  last-known values and only produce warnings.
- Every status change is kept in the booking history.

### Reviewing a result page

After submitting in the official page, save the result page from the browser (File > Save Page
As, "Webpage, HTML only") and review it:

```bash
uv run python -m app.main bookings review 1 ~/Downloads/reservasi.html
```

- The outcome is `SUCCESS` only with a recognisable success heading ("Reservasi berhasil"),
  `FAILURE` for a failure heading or title, and `NEEDS_REVIEW` for anything else (challenge
  pages, the order form, conflicting signals). HTTP status is never used.
- Line items, total, pickup, location and any visible reference are read best effort and
  compared with the booking; differences are shown as warnings.
- The file is read only. Scripts and form fields (which may contain tokens) are ignored, the
  customer's name is not extracted, and nothing from the page is stored except a short note.
- A reviewed success page moves the booking to `SUCCESS_PAGE_REVIEWED`, never `CONFIRMED`.
- The parser was written from one observed page and synthetic fixtures; check it against a real
  saved page before relying on the extracted details.

### Notifications

Alerts (session opening/closing, target stock changes, a slot reopening, endpoint down) ring
the terminal bell. On macOS you can also enable a Notification Center banner and a sound:
`NOTIFY_DESKTOP=true`, `NOTIFY_SOUND=true`, or `monitor --desktop --sound` for one run. If
banners do not appear, allow notifications for Script Editor in System Settings.

### Reports

```bash
uv run python -m app.main report            # checks, outcomes, target restocks, bookings
uv run python -m app.main history stock 100 # recorded stock changes of a product
uv run python -m app.main history slot 185  # recorded capacity changes of a slot
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

### Exports

```bash
uv run python -m app.main export bookings [--all] [--with-names]
uv run python -m app.main export stock
uv run python -m app.main export slots
```

Files go to `data/exports/` with owner-only permissions. Booking exports never contain WhatsApp
numbers, e-mail addresses or notes; names appear only with `--with-names`. Cells starting with
`=`, `+`, `-` or `@` are prefixed with `'` so spreadsheets do not run them as formulas.

## Backup and restore

```bash
uv run python -m app.main db backup
```

This writes `data/backups/puln_assistant-YYYYMMDD-HHMMSS.db` using SQLite's online backup API,
so it is safe while `monitor` is running. The backup contains customer data: keep it on
encrypted storage you control (FileVault is enough for the local disk) and never in a shared
or synced folder you do not trust.

To restore, stop every running command, keep a copy of the current file, then copy the backup
over it:

```bash
cp data/puln_assistant.db data/puln_assistant.db.before-restore
cp data/backups/puln_assistant-20261031-133015.db data/puln_assistant.db
chmod 600 data/puln_assistant.db
rm -f data/puln_assistant.db-wal data/puln_assistant.db-shm
uv run python -m app.main db init   # verifies the file opens and adds any newer tables
```

## Troubleshooting

| Symptom | What it means | What to do |
|---|---|---|
| `python3 --version` shows 3.9 | macOS system Python | Use `uv run ...`; uv uses the pinned 3.12 |
| `Invalid configuration: PULN_...` | A value in `.env` failed validation | Fix the named variable; intervals below 5 s are refused |
| `No session configured` | No date, slug or URL set | Set `PULN_SESSION_DATE` (or slug/URL) in `.env` |
| `session not found (HTTP 404)` | The generated slug has no session | Check the date, or copy the real URL into `PULN_SESSION_URL` |
| `access challenged (HTTP 403)` | The site asked for a browser check | Open the session in a browser; the app will not retry or bypass it |
| `unexpected HTTP 302` | Redirect, possibly a queue or moved page | Open the session page in a browser and follow the official flow |
| `rate limited (HTTP 429)` | The site asked us to slow down | Nothing; the monitor waits for `Retry-After`. Consider a larger interval |
| Dashboard shows `LAST KNOWN` | The latest request failed | Data shown is the last valid state; see `Last response` for the reason |
| Dashboard shows `STALE` | No fresh data for 3 intervals | Usually backoff or a slow network; availability may have changed |
| `Live data has new fields` | PULN changed the live.json format | Monitoring continues; report the field names so the model can be updated |
| `database is locked` / database error alert | Another process holds the SQLite file | Monitoring continues without history; close other commands using the database |
| `bookings review` says `NEEDS_REVIEW` | The saved page is not a clear result page | Check the page yourself; save the final result page, not the form or a challenge |
| No macOS banner | Notifications blocked for Script Editor | System Settings > Notifications > Script Editor > allow |
| `customers add` says the limit is reached | Already `MAX_CUSTOMERS` active profiles | Deactivate a customer who is no longer coordinated |

Logs with request outcomes and timings (no personal data) are in
`data/logs/puln_assistant.log`.

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
- `data/exports/`, `data/backups/`: CSV exports and database backups, owner-only

Never commit `.env`, the database, logs, or real customer data.
