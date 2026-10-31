"""Static values shared across the application."""

APP_NAME = "PULN Order Assistant"
APP_VERSION = "0.1.0"

# The live endpoint is served with ``cache-control: max-age=2``; polling faster gains nothing.
MIN_POLL_INTERVAL_SECONDS = 5

SESSION_SLUG_PREFIX = "online-order-"
LIVE_ENDPOINT_SUFFIX = "/live.json"

# Honest, identifiable client name. Never imitate a browser.
USER_AGENT = f"PULN-Order-Assistant/{APP_VERSION} (personal monitoring tool)"

# Products observed on the official page: (name, observed max quantity per person).
# Seeded only when missing; recheck against the current page for each session.
KNOWN_PRODUCTS: dict[int, tuple[str, int | None]] = {100: ("Kataifi Rolls", 1)}
