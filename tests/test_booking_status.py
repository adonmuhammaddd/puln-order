import pytest

from app.models.booking import (
    ALLOWED_TRANSITIONS,
    OPEN_STATUSES,
    TERMINAL_STATUSES,
    BookingStatus,
    can_transition,
)

S = BookingStatus


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.DRAFT, S.READY),
        (S.READY, S.DRAFT),
        (S.READY, S.OFFICIAL_FLOW_OPENED),
        (S.OFFICIAL_FLOW_OPENED, S.SUBMITTED_BY_USER),
        (S.OFFICIAL_FLOW_OPENED, S.READY),
        (S.SUBMITTED_BY_USER, S.SUCCESS_PAGE_REVIEWED),
        (S.SUBMITTED_BY_USER, S.AWAITING_EMAIL),
        (S.SUCCESS_PAGE_REVIEWED, S.AWAITING_EMAIL),
        (S.AWAITING_EMAIL, S.CONFIRMED),
        (S.SUBMITTED_BY_USER, S.FAILED),
        (S.AWAITING_EMAIL, S.FAILED),
        (S.DRAFT, S.CANCELLED),
        (S.AWAITING_EMAIL, S.CANCELLED),
    ],
)
def test_allowed_transitions(current: BookingStatus, target: BookingStatus) -> None:
    assert can_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.DRAFT, S.CONFIRMED),
        (S.READY, S.CONFIRMED),
        (S.SUBMITTED_BY_USER, S.CONFIRMED),
        (S.SUCCESS_PAGE_REVIEWED, S.CONFIRMED),
        (S.DRAFT, S.SUBMITTED_BY_USER),
        (S.READY, S.AWAITING_EMAIL),
        (S.DRAFT, S.FAILED),
        (S.READY, S.READY),
    ],
)
def test_forbidden_transitions(current: BookingStatus, target: BookingStatus) -> None:
    assert not can_transition(current, target)


@pytest.mark.parametrize("terminal", [S.CONFIRMED, S.FAILED, S.CANCELLED])
def test_terminal_statuses_have_no_way_out(terminal: BookingStatus) -> None:
    assert ALLOWED_TRANSITIONS[terminal] == frozenset()
    assert terminal in TERMINAL_STATUSES


def test_only_awaiting_email_leads_to_confirmed() -> None:
    sources = {status for status, targets in ALLOWED_TRANSITIONS.items() if S.CONFIRMED in targets}

    assert sources == {S.AWAITING_EMAIL}


def test_every_status_has_a_transition_entry() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(BookingStatus)


def test_open_statuses_are_the_non_terminal_ones() -> None:
    assert frozenset(BookingStatus) - TERMINAL_STATUSES == OPEN_STATUSES
