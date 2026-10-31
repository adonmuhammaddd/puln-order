from io import StringIO

import pytest
from rich.console import Console

from app.services.notification_service import TerminalNotifier
from app.services.stock_monitor import ChangeEvent, EventKind, Severity

ALERT = ChangeEvent(EventKind.PRODUCT_RESTOCKED, Severity.ALERT, "Kataifi Rolls (#100) 0 -> 3")
INFO = ChangeEvent(EventKind.SLOT_CHANGED, Severity.INFO, "Slot 185: 31 -> 30")


def terminal() -> tuple[Console, StringIO]:
    output = StringIO()
    return Console(file=output, force_terminal=True), output


def test_alert_rings_terminal_bell() -> None:
    console, output = terminal()

    TerminalNotifier(console, bell=True).notify(ALERT)

    assert "\a" in output.getvalue()


@pytest.mark.parametrize(("event", "bell"), [(INFO, True), (ALERT, False)])
def test_no_bell_for_info_or_when_disabled(event: ChangeEvent, bell: bool) -> None:
    console, output = terminal()

    TerminalNotifier(console, bell=bell).notify(event)

    assert "\a" not in output.getvalue()
