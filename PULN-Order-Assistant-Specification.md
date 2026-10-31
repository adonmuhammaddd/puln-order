# PULN Order Assistant

## Product Plan & Technical Implementation Specification

**Document version:** 1.0\
**Target runtime:** Python 3.12+\
**Primary environment:** macOS (Apple Silicon / M1)\
**Purpose:** Monitor PULN order availability and coordinate reservation
preparation for up to 10 real customers.

------------------------------------------------------------------------

## 1. Project Overview

PULN Order Assistant is a local Python application that helps a small
group of customers prepare for PULN online-order openings.

The application should:

-   Monitor the public live stock endpoint for a selected order session.
-   Display product stock and pickup-slot availability in a terminal
    dashboard.
-   Notify the operator when a target product or pickup slot becomes
    available or changes.
-   Store up to 10 customer profiles, with each customer's explicit
    consent.
-   Help prepare and track reservation details for each customer.
-   Record reservation outcomes and parse the confirmation page returned
    by PULN.
-   Keep a history of stock snapshots and reservation attempts.

The application is an **order-monitoring and coordination assistant**,
not an automated purchasing bot.

### Explicit scope boundary

The application must not:

-   Bypass, solve, outsource, or automate Cloudflare Turnstile or other
    CAPTCHA challenges.
-   Evade bot detection, rate limits, queues, or other site protections.
-   Spoof browser fingerprints, rotate proxies, or disguise automated
    traffic.
-   Submit mass or parallel reservation requests.
-   Circumvent per-customer, per-account, per-product, or other purchase
    restrictions.
-   Reuse session cookies, Turnstile tokens, or submission tokens
    outside their intended lifecycle.

Reservation submission and any required challenge must remain in the
official PULN flow and under the customer's/operator's direct control.
The app may prepare information, open the official page, and track the
result after the user completes the process.

------------------------------------------------------------------------

## 2. Known PULN Website Observations

The following details were observed during manual inspection. Treat them
as implementation inputs that must be revalidated against each new
session, not as a permanent API contract.

### 2.1 Session URL pattern

An observed session URL:

``` text
https://puln.seatin.id/c/online-order-20261031
```

The corresponding live endpoint:

``` text
https://puln.seatin.id/c/online-order-20261031/live.json
```

The application should generate a candidate session slug from a
configured date, but must verify that the page and endpoint actually
exist. Do not assume every calendar date has a valid session.

### 2.2 Example live JSON response

``` json
{
  "status": "OPEN",
  "accepting": true,
  "opens_at": "2026-10-30T23:00:00.000Z",
  "closes_at": "2026-10-31T08:30:00.000Z",
  "server_now": "2026-10-30T23:07:00.216Z",
  "products": [
    {"id": 102, "stock_remaining": 39, "stock_allocated": 70},
    {"id": 97, "stock_remaining": 31, "stock_allocated": 35},
    {"id": 99, "stock_remaining": 2, "stock_allocated": 75},
    {"id": 98, "stock_remaining": 0, "stock_allocated": 35},
    {"id": 100, "stock_remaining": 0, "stock_allocated": 60},
    {"id": 101, "stock_remaining": 0, "stock_allocated": 25}
  ],
  "slots": [
    {"id": 179, "remaining": 0, "label": "Sabtu, 31 Oktober 2026, 08:00–09:00"},
    {"id": 184, "remaining": 0, "label": "Sabtu, 31 Oktober 2026, 09:00–10:00"},
    {"id": 180, "remaining": 0, "label": "Sabtu, 31 Oktober 2026, 10:00–11:00"},
    {"id": 185, "remaining": 31, "label": "Sabtu, 31 Oktober 2026, 11:00–12:00"},
    {"id": 178, "remaining": 42, "label": "Sabtu, 31 Oktober 2026, 12:00–13:00"},
    {"id": 183, "remaining": 38, "label": "Sabtu, 31 Oktober 2026, 13:00–14:00"}
  ]
}
```

All timestamp fields ending in `Z` are UTC. Convert them to
`Asia/Jakarta` for display.

### 2.3 Target product

The observed product of interest was:

-   Product name: Kataifi Rolls
-   Product ID: `100`
-   Observed maximum: one unit per person

The quantity limit must be configurable and rechecked against the
current official page. Do not hard-code this as a universal rule.

### 2.4 Observed frontend behavior

Manual inspection of the site's frontend JavaScript indicated:

-   The cart appears to be maintained in local frontend state before
    final submission.
-   The frontend reads `live.json` to refresh availability.
-   Polling appeared to vary over time and may be skipped when a tab is
    inactive.
-   The form validates customer name, WhatsApp number, email, pickup
    slot, and Turnstile response.
-   The form includes a `submission_key`; comments in the script suggest
    retry behavior may be associated with that key. This behavior is not
    confirmed and must not be relied on without official documentation.
-   A local-storage key named `bk_customer` was referenced in the
    script, while a manually inspected browser storage entry named
    `bk_network` contained customer details. The discrepancy is
    unexplained.

Do not imitate aggressive polling. Use a conservative, configurable
monitoring interval and stop or back off on errors, HTTP 429, or other
signs of rate limiting.

### 2.5 Observed reservation form

The inspected form had:

``` text
Action: https://puln.seatin.id/c/online-order-20261031/reserve
Method: POST
Encoding: application/x-www-form-urlencoded
```

Observed field names included:

``` text
submission_key
qty_102
qty_97
qty_99
qty_98
qty_100
qty_101
pickup_slot_id
customer_name
whatsapp
email
cf-turnstile-response
```

This is documentation of the inspected page structure only. The
application must not use this information to automate direct reservation
POST requests or to bypass the official user-facing flow.

### 2.6 Observed successful response

A manual test using a different product returned HTTP 200 and an HTML
page with:

-   Page title: `Reservasi berhasil · Puln`
-   Success heading: `Reservasi berhasil`
-   Message indicating the reservation code would be sent to the
    customer's email
-   Product line items and total
-   Customer name, pickup information, and location

The confirmation code was not visible in the returned HTML and was
described as being sent by email.

**Important:** HTTP 200 alone does not prove that a reservation
succeeded. If the operator manually imports or records a result, the app
should inspect the response content and require a recognizable success
indicator. The operator should still verify the confirmation email.

The test response indicated payment at the store by QRIS or debit.
Payment methods and terms may change; do not present this as a
guaranteed current policy.

### 2.7 Sensitive browser data

Browser storage inspection exposed customer personal information and
Turnstile-related values.

Never log, export, or share:

-   Real customer names, phone numbers, or email addresses
    unnecessarily.
-   Session cookies or authorization headers.
-   Turnstile tokens or challenge state.
-   Submission tokens or other temporary secrets.

Do not store Turnstile tokens or browser cookies in the application
database.

------------------------------------------------------------------------

## 3. Functional Requirements

### 3.1 Session management

-   Allow the operator to set the target session date.
-   Generate a candidate URL slug in `YYYYMMDD` format.
-   Allow a custom session URL override.
-   Validate that the live endpoint is reachable and its JSON structure
    is valid.
-   Display the session status, accepting flag, opening time, closing
    time, and server time.
-   Convert UTC timestamps to `Asia/Jakarta` for display.

### 3.2 Product monitoring

-   Fetch and parse the live JSON endpoint.
-   Display product ID, product name (when known), remaining stock, and
    allocated stock.
-   Support configurable target products.
-   Detect stock changes and transitions from zero to positive stock.
-   Store timestamped stock snapshots.
-   Avoid duplicate notifications for the same unchanged state.
-   Handle missing products and unexpected JSON fields safely.

Product names may not be present in the live endpoint. Maintain an
editable local product catalogue and refresh it from the official page
only through permitted, non-invasive inspection.

### 3.3 Pickup-slot monitoring

-   Display slot ID, label, and remaining capacity.
-   Highlight slots with available capacity.
-   Detect changes in slot availability.
-   Store snapshots for historical comparison.
-   Never assume a slot remains available between the last poll and the
    user's action.

### 3.4 Customer profiles

Support up to 10 customer profiles.

Each profile should contain:

-   Display name
-   WhatsApp number
-   Email address
-   Active/inactive flag
-   Optional notes
-   Created and updated timestamps

Requirements:

-   Add, edit, view, deactivate, and delete profiles.
-   Validate required fields and basic email/phone formatting.
-   Require the operator to confirm that each customer has consented to
    the use of their information.
-   Mask phone numbers and email addresses in ordinary dashboard views.
-   Do not print personal information in routine logs.
-   Do not collect passwords, payment-card details, or account
    credentials.

### 3.5 Reservation preparation and tracking

The application may create a reservation preparation record for a
customer:

-   Customer profile
-   Target session
-   Selected product and quantity
-   Preferred pickup slot
-   Notes
-   Current workflow status

Suggested statuses:

  -----------------------------------------------------------------------
  Status                              Meaning
  ----------------------------------- -----------------------------------
  `DRAFT`                             Initial record created

  `READY`                             Details reviewed and ready for the
                                      customer/operator

  `OFFICIAL_FLOW_OPENED`              Official PULN page opened manually

  `SUBMITTED_BY_USER`                 User reports completing the
                                      official submission

  `SUCCESS_PAGE_REVIEWED`             Operator reviewed a success page

  `AWAITING_EMAIL`                    Waiting for the official
                                      confirmation email

  `CONFIRMED`                         Confirmation email/reference
                                      verified

  `FAILED`                            Reservation did not succeed

  `CANCELLED`                         Record cancelled
  -----------------------------------------------------------------------

The application must not transition a booking to `CONFIRMED` solely
because a browser returned HTTP 200. Require an explicit operator action
after confirmation details have been verified.

The app should provide a button or command to open the official session
page. It should not automatically fill and submit the form or solve the
site's challenge.

### 3.6 Notifications

Initial notification channels:

-   Terminal alerts
-   Optional desktop notification on macOS
-   Optional sound notification

Notify on:

-   Session changes to accepting orders.
-   Target product stock becomes available.
-   Target product stock decreases or reaches zero.
-   Pickup slot capacity changes.
-   Monitoring endpoint becomes unavailable or returns malformed data.

Notifications should be deduplicated and configurable.

### 3.7 Monitoring reliability

-   Use a conservative configurable polling interval.
-   Add small random jitter only to avoid synchronized requests, not to
    disguise abusive automation.
-   Respect `Retry-After` when provided.
-   Back off after network errors and HTTP 429/5xx.
-   Stop polling when the session is closed or no longer accepting
    orders, unless the operator explicitly overrides this.
-   Provide a manual refresh command.
-   Log endpoint status and request duration without logging secrets.

------------------------------------------------------------------------

## 4. Non-Functional Requirements

-   Python 3.12 or later.
-   Runs locally on macOS Apple Silicon.
-   SQLite database; no external database server required.
-   Clear separation between API client, business logic, persistence,
    and UI.
-   Type hints for public functions and data models.
-   Pydantic validation for configuration and incoming JSON.
-   Unit tests for parsing, state changes, and booking workflows.
-   Graceful handling of network failures.
-   No credentials or personal data committed to Git.
-   Simple terminal-first user experience.
-   No dependency on a paid cloud service for the core application.

------------------------------------------------------------------------

## 5. Recommended Technology Stack

  Concern                 Recommended library
  ----------------------- ----------------------------------------
  Runtime                 Python 3.12+
  HTTP client             `httpx`
  Configuration           `pydantic-settings`
  Data validation         `pydantic`
  ORM / persistence       `SQLAlchemy`
  Database                SQLite
  Scheduling              `APScheduler` or a simple asyncio loop
  Terminal UI             `Textual` or `Rich`
  Date/time               Python `zoneinfo`
  HTML parsing            `BeautifulSoup4`
  Testing                 `pytest`, `pytest-asyncio`, `respx`
  Environment variables   `python-dotenv`

Keep the first implementation small. Avoid introducing a web server,
frontend framework, Redis, or Docker unless a later requirement
justifies it.

------------------------------------------------------------------------

## 6. Suggested Project Structure

``` text
puln-order-assistant/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── constants.py
│   │
│   ├── clients/
│   │   ├── __init__.py
│   │   └── puln_client.py
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   ├── puln.py
│   │   └── booking.py
│   │
│   ├── repositories/
│   │   ├── __init__.py
│   │   ├── customer_repository.py
│   │   ├── product_repository.py
│   │   ├── snapshot_repository.py
│   │   └── booking_repository.py
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── session_service.py
│   │   ├── stock_monitor.py
│   │   ├── notification_service.py
│   │   ├── customer_service.py
│   │   └── booking_service.py
│   │
│   ├── parsers/
│   │   ├── __init__.py
│   │   └── reservation_response_parser.py
│   │
│   ├── database/
│   │   ├── __init__.py
│   │   ├── engine.py
│   │   └── base.py
│   │
│   └── ui/
│       ├── __init__.py
│       ├── dashboard.py
│       └── commands.py
│
├── tests/
│   ├── test_live_json_parser.py
│   ├── test_stock_monitor.py
│   ├── test_customer_service.py
│   ├── test_booking_service.py
│   └── fixtures/
│       └── live_sample.json
│
├── data/
│   └── .gitkeep
│
├── .env.example
├── .gitignore
├── pyproject.toml
├── README.md
└── CLAUDE.md
```

------------------------------------------------------------------------

## 7. Configuration

Use environment variables with a `.env` file for local configuration.

Example `.env.example`:

``` dotenv
APP_ENV=development
APP_TIMEZONE=Asia/Jakarta

PULN_BASE_URL=https://puln.seatin.id
PULN_SESSION_DATE=2026-10-31
PULN_SESSION_SLUG=
PULN_POLL_INTERVAL_SECONDS=10
PULN_MAX_POLL_INTERVAL_SECONDS=60
PULN_REQUEST_TIMEOUT_SECONDS=10

DATABASE_URL=sqlite:///./data/puln_assistant.db

TARGET_PRODUCT_IDS=100
MAX_CUSTOMERS=10
```

Notes:

-   The example date is historical sample data. Set the actual session
    date before running.
-   The default polling interval is intentionally conservative and
    should be configurable.
-   The application should validate the configured date and URL.
-   Do not put customer information, cookies, or tokens in `.env`.

------------------------------------------------------------------------

## 8. Data Models

Use SQLAlchemy models and Pydantic schemas where appropriate.

### 8.1 Customer

``` text
Customer
--------
id                  INTEGER PRIMARY KEY
display_name        VARCHAR(120) NOT NULL
whatsapp            VARCHAR(40) NOT NULL
email               VARCHAR(255) NOT NULL
consent_confirmed   BOOLEAN NOT NULL DEFAULT FALSE
is_active           BOOLEAN NOT NULL DEFAULT TRUE
notes               TEXT NULL
created_at          DATETIME NOT NULL
updated_at          DATETIME NOT NULL
```

Enforce the configured maximum of 10 active customer profiles in the
service layer.

### 8.2 Product

``` text
Product
-------
id                  INTEGER PRIMARY KEY
puln_product_id     INTEGER UNIQUE NOT NULL
name                VARCHAR(160) NOT NULL
max_quantity        INTEGER NULL
is_target           BOOLEAN NOT NULL DEFAULT FALSE
is_active           BOOLEAN NOT NULL DEFAULT TRUE
updated_at          DATETIME NOT NULL
```

### 8.3 Pickup slot

``` text
PickupSlot
----------
id                  INTEGER PRIMARY KEY
puln_slot_id        INTEGER NOT NULL
session_slug        VARCHAR(80) NOT NULL
label               VARCHAR(255) NOT NULL
last_remaining      INTEGER NOT NULL DEFAULT 0
updated_at          DATETIME NOT NULL
```

A PULN slot ID should be scoped to its session unless the site contract
proves otherwise.

### 8.4 Stock snapshot

``` text
StockSnapshot
-------------
id                  INTEGER PRIMARY KEY
session_slug        VARCHAR(80) NOT NULL
puln_product_id     INTEGER NOT NULL
stock_remaining     INTEGER NOT NULL
stock_allocated     INTEGER NULL
observed_at         DATETIME NOT NULL
```

### 8.5 Slot snapshot

``` text
SlotSnapshot
------------
id                  INTEGER PRIMARY KEY
session_slug        VARCHAR(80) NOT NULL
puln_slot_id        INTEGER NOT NULL
remaining           INTEGER NOT NULL
observed_at         DATETIME NOT NULL
```

### 8.6 Booking

``` text
Booking
-------
id                  INTEGER PRIMARY KEY
customer_id         INTEGER NOT NULL REFERENCES Customer(id)
session_slug        VARCHAR(80) NOT NULL
puln_product_id     INTEGER NOT NULL
quantity            INTEGER NOT NULL
puln_slot_id        INTEGER NULL
status              VARCHAR(40) NOT NULL
operator_notes      TEXT NULL
confirmation_ref    VARCHAR(120) NULL
created_at          DATETIME NOT NULL
updated_at          DATETIME NOT NULL
confirmed_at        DATETIME NULL
```

The booking record is a local tracking record. It is not proof that PULN
accepted a reservation.

------------------------------------------------------------------------

## 9. Core Application Workflows

### 9.1 Start monitoring

1.  Load and validate configuration.
2.  Initialize the SQLite database.
3.  Resolve the target session URL.
4.  Request the live JSON endpoint.
5.  Validate the response schema.
6.  Save the latest product and slot snapshots.
7.  Compare the new state with the previous state.
8.  Send notifications for meaningful changes.
9.  Render the terminal dashboard.
10. Wait for the configured interval, respecting backoff and rate-limit
    responses.

### 9.2 Prepare a customer reservation

1.  Select an existing customer profile.
2.  Select the target session.
3.  Select a product and quantity.
4.  Validate quantity against the locally configured limit.
5.  Select a preferred pickup slot.
6.  Check the latest known stock and slot capacity.
7.  Display a warning that availability can change at any time.
8.  Save the preparation record as `READY`.
9.  Provide an option to open the official PULN session page.
10. The customer/operator completes the official flow manually.
11. The operator records the outcome and verifies the confirmation
    email.
12. Mark the local record `CONFIRMED` only after verification.

### 9.3 Review a reservation response

The response parser may be used to help an operator inspect a locally
supplied HTML response. It should extract:

-   Page title
-   Recognizable success or failure heading
-   Product line items
-   Quantity and displayed total
-   Pickup label
-   Location
-   Any visible reference number, if present

The parser must not assume that an HTTP status code alone means success.
If the page is ambiguous, mark the record for manual review.

------------------------------------------------------------------------

## 10. Example Domain Types

Use typed models for the live endpoint. Adapt optional fields
defensively.

``` python
from datetime import datetime
from pydantic import BaseModel, Field


class LiveProduct(BaseModel):
    id: int
    stock_remaining: int = Field(ge=0)
    stock_allocated: int | None = Field(default=None, ge=0)


class LiveSlot(BaseModel):
    id: int
    remaining: int = Field(ge=0)
    label: str


class LiveStatus(BaseModel):
    status: str
    accepting: bool
    opens_at: datetime | None = None
    closes_at: datetime | None = None
    server_now: datetime | None = None
    products: list[LiveProduct] = []
    slots: list[LiveSlot] = []
```

Use `Field(default_factory=list)` instead of mutable list defaults in
production models:

``` python
from pydantic import BaseModel, Field


class LiveStatus(BaseModel):
    status: str
    accepting: bool
    products: list[LiveProduct] = Field(default_factory=list)
    slots: list[LiveSlot] = Field(default_factory=list)
```

------------------------------------------------------------------------

## 11. Dashboard Requirements

The terminal dashboard should show:

``` text
PULN ORDER ASSISTANT
Session: online-order-YYYYMMDD
Local time: Asia/Jakarta
Session status: OPEN
Accepting orders: YES
Server time: ...

PRODUCTS
ID    PRODUCT             REMAINING    ALLOCATED
100   Kataifi Rolls       0            60
102   Example Product     39           70

PICKUP SLOTS
ID    TIME LABEL                              REMAINING
185   Saturday, 11:00–12:00                  31
178   Saturday, 12:00–13:00                  42

CUSTOMERS
Active profiles: 2 / 10
Prepared bookings: 2
Confirmed bookings: 0

MONITOR
Last successful check: ...
Next check: ...
Last response: HTTP 200
```

The names and numbers above are illustrative only.

Dashboard behavior:

-   Clearly distinguish live data from cached/last-known data.
-   Show the timestamp of the last successful check.
-   Show a warning when data is stale.
-   Mask customer contact details.
-   Do not show secrets or challenge tokens.

------------------------------------------------------------------------

## 12. CLI Commands

Suggested initial commands:

``` bash
python -m app.main monitor
python -m app.main status
python -m app.main customers list
python -m app.main customers add
python -m app.main customers edit 1
python -m app.main customers deactivate 1
python -m app.main bookings list
python -m app.main bookings prepare
python -m app.main bookings show 1
python -m app.main sessions validate
```

Exact CLI syntax can be adjusted to the chosen CLI library. Keep
commands discoverable with `--help`.

------------------------------------------------------------------------

## 13. Error Handling

Handle at least:

-   DNS/network errors
-   Connection timeout
-   HTTP 403
-   HTTP 404 (session may not exist)
-   HTTP 429 (rate limited)
-   HTTP 5xx
-   Invalid JSON
-   Missing or unexpected fields
-   Negative or non-numeric stock values
-   Database unavailable or locked
-   Invalid customer data
-   Duplicate or invalid booking state transitions

For HTTP 429, respect `Retry-After` when available and pause monitoring.
Never attempt to evade the limit.

Do not silently replace invalid live data with zero stock. Preserve the
last known valid snapshot and show that the current data is unavailable.

------------------------------------------------------------------------

## 14. Security and Privacy

-   Keep `.env`, SQLite database files, exports, and logs out of Git.
-   Provide a `.gitignore` for `.env`, `data/*.db`, logs, caches, and
    virtual environments.
-   Store only customer information needed for reservation coordination.
-   Obtain consent from every customer before storing their details.
-   Mask contact details in logs and normal dashboard output.
-   Do not store passwords, payment details, cookies, Turnstile tokens,
    or session credentials.
-   Do not paste real customer data into issue trackers or AI coding
    prompts.
-   Provide a way to delete a customer profile and associated local
    data, subject to booking-history requirements.
-   Restrict database file permissions where practical on macOS.

------------------------------------------------------------------------

## 15. Testing Strategy

### Unit tests

-   Parse a valid live JSON fixture.
-   Handle missing optional fields.
-   Reject invalid stock values.
-   Convert UTC timestamps to `Asia/Jakarta`.
-   Detect stock changes.
-   Detect zero-to-positive stock transitions.
-   Detect slot capacity changes.
-   Deduplicate notifications.
-   Enforce the maximum number of active customers.
-   Validate customer fields.
-   Validate booking status transitions.
-   Parse a known success-page HTML fixture.
-   Treat ambiguous HTML as requiring manual review.

### Integration tests

-   Use mocked HTTP responses; do not repeatedly hit the live PULN
    service in tests.
-   Verify timeout and retry/backoff behavior.
-   Verify HTTP 429 handling.
-   Verify SQLite repository operations.
-   Verify that a malformed response does not erase the last valid
    snapshot.

### Test data

Use synthetic names, phone numbers, emails, and HTML fixtures. Never
commit real customer data or live tokens.

------------------------------------------------------------------------

## 16. Development Milestones

### Phase 1 --- Foundation

-   Initialize project and virtual environment.
-   Add configuration management.
-   Add logging.
-   Create SQLite engine and initial models.
-   Add sample `.env.example`.
-   Add test setup.

**Deliverable:** Application starts and initializes its local database.

### Phase 2 --- Live monitoring

-   Implement session URL builder and override.
-   Implement `httpx` client for the live JSON endpoint.
-   Add Pydantic response validation.
-   Add product and slot snapshots.
-   Add stock-change detection.
-   Add conservative polling and backoff.
-   Add terminal dashboard.

**Deliverable:** Operator can monitor the selected session and see live
stock/slot changes.

### Phase 3 --- Customer management

-   Implement customer CRUD.
-   Enforce maximum of 10 active profiles.
-   Add consent confirmation.
-   Mask personal data in dashboard output.

**Deliverable:** Operator can manage up to 10 consenting customers
locally.

### Phase 4 --- Booking coordination

-   Implement booking preparation records.
-   Add product quantity and pickup-slot validation.
-   Add workflow status transitions.
-   Add official session-page opening action.
-   Add manual outcome recording.

**Deliverable:** Operator can prepare and track each customer's
reservation without automated submission.

### Phase 5 --- Response review and notifications

-   Implement HTML response parser for operator review.
-   Add terminal and optional desktop notifications.
-   Add confirmation-email verification workflow.
-   Add history and reporting.

**Deliverable:** Operator can review outcomes and keep an auditable
local record.

### Phase 6 --- Polish

-   Improve dashboard UX.
-   Add export to CSV if needed.
-   Add database backup instructions.
-   Add README and troubleshooting guide.
-   Run all tests and lint/type checks.

**Deliverable:** Stable local application ready for daily use.

------------------------------------------------------------------------

## 17. Initial Setup

Suggested setup on macOS:

``` bash
mkdir puln-order-assistant
cd puln-order-assistant

python3 --version
python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
```

Install dependencies after creating `pyproject.toml`:

``` bash
pip install httpx pydantic pydantic-settings sqlalchemy \
  apscheduler rich beautifulsoup4 python-dotenv
```

Development dependencies:

``` bash
pip install pytest pytest-asyncio respx ruff mypy
```

Run tests:

``` bash
pytest
```

Run the application:

``` bash
python -m app.main --help
```

------------------------------------------------------------------------

## 18. Acceptance Criteria

The initial release is acceptable when:

1.  The app starts locally on macOS M1 using Python 3.12+.
2.  The operator can configure a PULN session date or custom URL.
3.  The app validates and displays the live endpoint status.
4.  Product stock and pickup-slot availability are displayed accurately
    from the latest valid response.
5.  UTC timestamps are displayed in `Asia/Jakarta`.
6.  Stock and slot changes are saved as historical snapshots.
7.  The app handles network errors and rate limits without aggressive
    retries.
8.  The operator can manage up to 10 consenting customer profiles.
9.  Personal information is masked in ordinary logs and dashboard views.
10. The operator can prepare and track a booking record for each
    customer.
11. The official PULN reservation flow remains a manual user-controlled
    action.
12. The app does not bypass Turnstile, bot detection, queues, rate
    limits, or purchase restrictions.
13. Booking status is not marked confirmed without confirmation
    verification.
14. Automated tests cover the core parsing, monitoring, validation, and
    workflow logic.

------------------------------------------------------------------------

## 19. Instructions for Claude Code

You are implementing the **PULN Order Assistant** described in this
document.

Before writing code:

1.  Inspect the current repository and its existing files.
2.  Check the installed Python version and project environment.
3.  Identify whether the repository already contains code that should be
    preserved.
4.  Compare the current repository with the proposed architecture.
5.  Present a concise implementation plan for **Phase 1 and Phase 2
    only**.
6.  List any assumptions or unresolved questions about the PULN live
    endpoint.
7.  Do not start implementation until the plan has been presented and
    approved.

Implementation rules:

-   Use Python 3.12+ and type hints.
-   Keep modules small and responsibilities clear.
-   Use `httpx`, Pydantic, SQLAlchemy, and SQLite as proposed unless the
    repository has a strong existing reason to use alternatives.
-   Use `Asia/Jakarta` for user-facing date/time display.
-   Treat the observed PULN JSON as an example, not a guaranteed
    permanent schema.
-   Add tests alongside implementation.
-   Use mocked HTTP responses in tests.
-   Do not include real customer personal information in fixtures.
-   Never store or reuse cookies, Turnstile tokens, challenge responses,
    or credentials.
-   Do not implement automated reservation submission, CAPTCHA solving,
    anti-bot evasion, proxy rotation, queue bypassing, or parallel mass
    checkout.
-   Do not claim a reservation is successful based only on HTTP 200.
-   Document all assumptions and any behavior that needs manual
    verification.

For the first task, deliver only the repository assessment and the Phase
1--2 implementation plan. Wait for approval before changing files.
