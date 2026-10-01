"""Statusfluss eines Leads (docs/requirements.md, Abschnitt 11).

NEW -> REVIEWED -> INTERESTING -> CONTACTED -> RESPONSE -> MEETING -> OFFER -> WON/LOST/REJECTED

Zusätzlich: REJECTED ist aus jedem offenen Status erreichbar, LOST ab CONTACTED.
Nach einer Antwort darf das Meeting übersprungen werden (RESPONSE -> OFFER).
WON, LOST und REJECTED sind Endzustände.
"""

from __future__ import annotations

from collections.abc import Mapping

from src.domain.enums import LeadStatus

S = LeadStatus

ALLOWED_TRANSITIONS: Mapping[LeadStatus, frozenset[LeadStatus]] = {
    S.NEW: frozenset({S.REVIEWED, S.REJECTED}),
    S.REVIEWED: frozenset({S.INTERESTING, S.REJECTED}),
    S.INTERESTING: frozenset({S.CONTACTED, S.REJECTED}),
    S.CONTACTED: frozenset({S.RESPONSE, S.LOST, S.REJECTED}),
    S.RESPONSE: frozenset({S.MEETING, S.OFFER, S.LOST, S.REJECTED}),
    S.MEETING: frozenset({S.OFFER, S.LOST, S.REJECTED}),
    S.OFFER: frozenset({S.WON, S.LOST, S.REJECTED}),
    S.WON: frozenset(),
    S.LOST: frozenset(),
    S.REJECTED: frozenset(),
}

TERMINAL_STATUSES = frozenset(status for status, targets in ALLOWED_TRANSITIONS.items() if not targets)


class InvalidStatusTransitionError(ValueError):
    def __init__(self, current: LeadStatus, target: LeadStatus) -> None:
        super().__init__(f"Statuswechsel {current} -> {target} ist nicht erlaubt")
        self.current = current
        self.target = target


def can_transition(current: LeadStatus, target: LeadStatus) -> bool:
    return target in ALLOWED_TRANSITIONS[current]


def ensure_transition(current: LeadStatus, target: LeadStatus) -> None:
    if not can_transition(current, target):
        raise InvalidStatusTransitionError(current, target)
