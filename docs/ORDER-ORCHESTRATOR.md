# Order Orchestrator (POC)

The orchestrator coordinates local READY bookings and read-only PULN availability. It does not
reserve stock, submit a form, or interact with Turnstile.

## Responsibilities

- Rebuild an in-memory FIFO queue from READY bookings in the configured session.
- Enforce a configurable pending queue limit (default 10).
- Avoid duplicate booking IDs in the same queue.
- Inspect the public session page and validated live status once per evaluation.
- Report one typed outcome per queued booking.

The database remains the source of truth. After a process restart, call
`enqueue_ready_bookings()` to reconstruct the in-memory working queue.

## Outcomes

- `READY_FOR_OPERATOR`: latest live data reports accepting=true and sufficient stock/slot capacity.
- `WAITING_FOR_OPENING`: the session is not accepting orders.
- `OUT_OF_STOCK`: latest stock is below the booking quantity.
- `SLOT_FULL`: the selected slot is missing or has insufficient capacity.
- `SESSION_UNAVAILABLE`: the official session page could not be reached.
- `LIVE_DATA_UNAVAILABLE`: live data is not a validated LiveOk response, or a queue record is stale.
- `PRODUCT_UNAVAILABLE`: the product is absent from live data.

These are point-in-time assessments, not reservations or guarantees. An operator must complete
the official PULN flow. Ambiguous submissions must be reviewed rather than retried. Only
BookingService.confirm after verification of the official confirmation email can mark a booking
CONFIRMED.

## Example

```python
refresh = orchestrator.enqueue_ready_bookings()
results = orchestrator.evaluate_queue()
for result in results:
    print(result.booking_id, result.outcome, result.detail)
```
