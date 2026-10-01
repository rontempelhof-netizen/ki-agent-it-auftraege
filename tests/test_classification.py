from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.config import ClassThresholds
from src.domain.enums import LeadClass
from src.scoring.classification import classify

DEFAULT = ClassThresholds()


@pytest.mark.parametrize(
    ("total", "expected"),
    [
        (0, LeadClass.REJECT),
        (49, LeadClass.REJECT),
        (50, LeadClass.C),
        (64, LeadClass.C),
        (65, LeadClass.B),
        (79, LeadClass.B),
        (80, LeadClass.A),
        (100, LeadClass.A),
    ],
)
def test_default_class_boundaries(total, expected):
    assert classify(total, DEFAULT) == expected


@pytest.mark.parametrize("total", [0, 49, 50, 65, 80, 100])
def test_hard_fail_always_rejects(total):
    assert classify(total, DEFAULT, hard_fail=True) == LeadClass.REJECT


@pytest.mark.parametrize("total", [-1, 101])
def test_out_of_range_score_raises(total):
    with pytest.raises(ValueError):
        classify(total, DEFAULT)


def test_custom_thresholds():
    thresholds = ClassThresholds(a=90, b=70, c=40)

    assert [classify(t, thresholds) for t in (39, 40, 69, 70, 89, 90)] == [
        LeadClass.REJECT, LeadClass.C, LeadClass.C, LeadClass.B, LeadClass.B, LeadClass.A,
    ]


@pytest.mark.parametrize("values", [{"a": 65, "b": 65, "c": 50}, {"a": 80, "b": 40, "c": 50}, {"a": 101}])
def test_invalid_thresholds_rejected(values):
    with pytest.raises(ValidationError):
        ClassThresholds(**values)
