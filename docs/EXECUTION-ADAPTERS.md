# Execution Adapter POC

This branch introduces two separate adapters behind the existing booking/monitoring foundation.

## Backend track

`PulnReadOnlyBackendAdapter` wraps the existing `PulnClient` and combines the official session
page check with the validated public `live.json` response. It is intentionally read-only.
The repository currently has no verified public reservation API contract. Do not infer that an
observed browser form endpoint is an authorised integration. A reservation-writing adapter can
be added only after PULN documents or explicitly authorises the integration, including its
request schema, authentication, idempotency, rate limits, and error semantics.

## Browser track

`PlaywrightBrowserAdapter` launches a visible Chromium window in a fresh, non-persistent context,
opens the official PULN page, and returns only coarse page metadata (URL, title, HTTP status, and
page stage). The operator remains in control of the browser and completes the official flow.
The adapter does not fill or submit forms, click checkout controls, solve Turnstile, inspect or
persist cookies, read challenge tokens, or automate multiple checkouts.

Install the optional browser dependency:

```bash
uv sync --extra browser
uv run playwright install chromium
```

The browser adapter is a library-level POC in this branch; CLI wiring and end-to-end tests are
follow-up work. Browser tests should continue to use synthetic page copy and must not hit PULN.

## Shared orchestration direction

Both tracks should report typed outcomes to a future `OrderOrchestrator`. The orchestrator owns
booking state transitions and audit history; adapters do not write directly to the database.
A booking becomes `CONFIRMED` only after the official confirmation email is independently
verified, as enforced by the existing `BookingService`.

## Known unknowns

- Whether PULN offers an official reservation API or partner integration.
- Whether PULN permits browser automation beyond opening/observing the official page.
- The current session's product limits, stock, slots, and endpoint behavior; these must be
  revalidated for every session.
