"""Operator notifications for alert events: terminal bell, macOS notification, macOS sound.

Messages describe public stock data only; they never contain customer details. Desktop text is
handed to osascript as an argument, never spliced into the AppleScript source.
"""

import logging
import subprocess
import sys
from collections.abc import Callable, Sequence

from rich.console import Console

from app.constants import APP_NAME
from app.services.stock_monitor import ChangeEvent, Severity

logger = logging.getLogger(__name__)

SOUND_FILE = "/System/Library/Sounds/Glass.aiff"
MAX_MESSAGE_LENGTH = 200
_NOTIFY_SCRIPT = (
    "-e",
    "on run argv",
    "-e",
    "display notification (item 1 of argv) with title (item 2 of argv)",
    "-e",
    "end run",
)

Runner = Callable[[Sequence[str]], None]


def _run_detached(args: Sequence[str]) -> None:
    subprocess.Popen(list(args), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Notifier:
    def __init__(
        self,
        console: Console,
        *,
        bell: bool,
        desktop: bool = False,
        sound: bool = False,
        runner: Runner = _run_detached,
        platform: str = sys.platform,
    ) -> None:
        on_macos = platform == "darwin"
        self._console = console
        self._bell = bell
        self._desktop = desktop and on_macos
        self._sound = sound and on_macos
        self._runner = runner
        if (desktop or sound) and not on_macos:
            logger.info("desktop notifications and sounds are only available on macOS")

    def _try(self, channel: str, args: Sequence[str]) -> bool:
        try:
            self._runner(args)
        except OSError as error:
            logger.warning("%s notification disabled: %s", channel, type(error).__name__)
            return False
        return True

    def notify(self, event: ChangeEvent) -> None:
        if event.severity is not Severity.ALERT:
            return
        if self._bell:
            self._console.bell()
        if self._desktop:
            message = event.message[:MAX_MESSAGE_LENGTH]
            self._desktop = self._try("desktop", ["osascript", *_NOTIFY_SCRIPT, message, APP_NAME])
        if self._sound:
            self._sound = self._try("sound", ["afplay", SOUND_FILE])
