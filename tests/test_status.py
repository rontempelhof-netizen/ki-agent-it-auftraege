from __future__ import annotations

import pytest

from src.domain.enums import LeadStatus as S
from src.domain.status import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATUSES,
    InvalidStatusTransitionError,
    can_transition,
    ensure_transition,
)


def test_every_status_has_transition_rules():
    assert set(ALLOWED_TRANSITIONS) == set(S)


def test_happy_path_to_won():
    path = [S.NEW, S.REVIEWED, S.INTERESTING, S.CONTACTED, S.RESPONSE, S.MEETING, S.OFFER, S.WON]
    for current, target in zip(path, path[1:]):
        ensure_transition(current, target)


def test_terminal_statuses():
    assert TERMINAL_STATUSES == {S.WON, S.LOST, S.REJECTED}


@pytest.mark.parametrize("status", [s for s in S if s not in {S.WON, S.LOST, S.REJECTED}])
def test_rejected_reachable_from_every_open_status(status):
    assert can_transition(status, S.REJECTED)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.NEW, S.CONTACTED),      # Prüfung übersprungen
        (S.NEW, S.WON),
        (S.REVIEWED, S.NEW),       # kein Rückschritt
        (S.INTERESTING, S.LOST),   # LOST erst nach Kontakt
        (S.WON, S.LOST),           # Endzustand
        (S.REJECTED, S.NEW),
        (S.NEW, S.NEW),            # kein Selbstübergang
    ],
)
def test_invalid_transitions(current, target):
    assert not can_transition(current, target)
    with pytest.raises(InvalidStatusTransitionError):
        ensure_transition(current, target)


def test_meeting_can_be_skipped_after_response():
    assert can_transition(S.RESPONSE, S.OFFER)
