"""Operator notifications. Phase 2: terminal bell for alerts; desktop notices come later."""

from rich.console import Console

from app.services.stock_monitor import ChangeEvent, Severity


class TerminalNotifier:
    def __init__(self, console: Console, *, bell: bool) -> None:
        self._console = console
        self._bell = bell

    def notify(self, event: ChangeEvent) -> None:
        if self._bell and event.severity is Severity.ALERT:
            self._console.bell()
