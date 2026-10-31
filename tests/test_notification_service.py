from collections.abc import Sequence
from io import StringIO

import pytest
from rich.console import Console

from app.services.notification_service import SOUND_FILE, Notifier
from app.services.stock_monitor import ChangeEvent, EventKind, Severity

ALERT = ChangeEvent(EventKind.PRODUCT_RESTOCKED, Severity.ALERT, "Kataifi Rolls (#100) 0 -> 3")
INFO = ChangeEvent(EventKind.SLOT_CHANGED, Severity.INFO, "Slot 185: 31 -> 30")


class Recorder:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.fail = fail

    def __call__(self, args: Sequence[str]) -> None:
        self.calls.append(list(args))
        if self.fail:
            raise OSError("osascript not found")


def terminal() -> tuple[Console, StringIO]:
    output = StringIO()
    return Console(file=output, force_terminal=True), output


def notifier(
    *,
    bell: bool = False,
    desktop: bool = False,
    sound: bool = False,
    runner: Recorder | None = None,
    platform: str = "darwin",
) -> tuple[Notifier, StringIO, Recorder]:
    console, output = terminal()
    recorder = runner or Recorder()
    return (
        Notifier(
            console, bell=bell, desktop=desktop, sound=sound, runner=recorder, platform=platform
        ),
        output,
        recorder,
    )


def test_alert_rings_terminal_bell() -> None:
    notify, output, _ = notifier(bell=True)

    notify.notify(ALERT)

    assert "\a" in output.getvalue()


@pytest.mark.parametrize(("event", "bell"), [(INFO, True), (ALERT, False)])
def test_no_bell_for_info_or_when_disabled(event: ChangeEvent, bell: bool) -> None:
    notify, output, _ = notifier(bell=bell)

    notify.notify(event)

    assert "\a" not in output.getvalue()


def test_desktop_notification_passes_message_as_argument() -> None:
    notify, _, runner = notifier(desktop=True)

    notify.notify(ALERT)

    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call[0] == "osascript"
    assert call[-2:] == [ALERT.message, "PULN Order Assistant"]
    assert all(ALERT.message not in part for part in call[:-2])


def test_desktop_message_with_quotes_stays_an_argument() -> None:
    hostile = ChangeEvent(
        EventKind.SLOT_OPENED, Severity.ALERT, 'Slot "x" & do shell script "rm -rf ~"'
    )
    notify, _, runner = notifier(desktop=True)

    notify.notify(hostile)

    script_parts = runner.calls[0][:-2]
    assert all("rm -rf" not in part for part in script_parts)


def test_long_messages_are_truncated() -> None:
    long = ChangeEvent(EventKind.SLOT_OPENED, Severity.ALERT, "x" * 500)
    notify, _, runner = notifier(desktop=True)

    notify.notify(long)

    assert len(runner.calls[0][-2]) <= 200


def test_sound_plays_system_sound() -> None:
    notify, _, runner = notifier(sound=True)

    notify.notify(ALERT)

    assert runner.calls == [["afplay", SOUND_FILE]]


def test_info_events_trigger_nothing() -> None:
    notify, _, runner = notifier(desktop=True, sound=True)

    notify.notify(INFO)

    assert runner.calls == []


def test_desktop_and_sound_are_skipped_outside_macos() -> None:
    notify, _, runner = notifier(desktop=True, sound=True, platform="linux")

    notify.notify(ALERT)

    assert runner.calls == []


def test_failing_desktop_command_is_disabled_after_first_error() -> None:
    notify, _, runner = notifier(desktop=True, runner=Recorder(fail=True))

    notify.notify(ALERT)
    notify.notify(ALERT)

    assert len(runner.calls) == 1
