"""Conservative cross-platform duplicate suggestions: no destructive merges."""

from src.pipeline.duplicate_review import (
    DuplicateEntry,
    find_cross_source_title_matches,
    normalize_title,
)


def _entry(lead_id: int, source: str, source_id: str, title: str) -> DuplicateEntry:
    return DuplicateEntry(lead_id=lead_id, source=source, source_id=source_id, title=title)


def test_same_cross_platform_title_and_reference_gets_review_hint() -> None:
    rows = [
        _entry(1, "freelance.de", "1300023", "1085053 Experte Embedded Software Validation"),
        _entry(2, "freelancermap", "3068400", "1085053  EXPERTE Embedded Software Validation"),
    ]
    groups = find_cross_source_title_matches(rows)
    assert len(groups) == 1
    assert groups[0].reference_hints == ("1085053",)
    assert {entry.source_id for entry in groups[0].entries} == {"1300023", "3068400"}


def test_same_title_four_listings_are_preserved_for_review() -> None:
    title = "SAP Schnittstellenentwickler (m/w/d)"
    rows = [
        _entry(1, "freelance.de", "1297518", title),
        _entry(2, "freelance.de", "1297514", title),
        _entry(3, "freelancermap", "3062679", title),
        _entry(4, "freelancermap", "3062602", title),
    ]
    groups = find_cross_source_title_matches(rows)
    assert len(groups) == 1
    assert len(groups[0].entries) == 4
    assert groups[0].reference_hints == ()
    assert len({entry.source_id for entry in groups[0].entries}) == 4
    assert len(rows) == 4  # The input is not mutated or merged.


def test_only_same_platform_is_not_cross_platform_candidate() -> None:
    title = "Senior Developer"
    assert find_cross_source_title_matches(
        [_entry(1, "freelance.de", "a", title), _entry(2, "freelance.de", "b", title)]
    ) == []


def test_near_titles_are_not_automatically_equated() -> None:
    assert find_cross_source_title_matches(
        [
            _entry(1, "freelancermap", "a", "Senior Java Developer"),
            _entry(2, "freelance.de", "b", "Senior Java / DevOps Developer"),
        ]
    ) == []


def test_case_unicode_and_whitespace_normalized_only() -> None:
    assert normalize_title("  SENIOR  \u212aotlin \n Developer ") == "senior kotlin developer"
    assert normalize_title("Senior Kotlin-Developer") != normalize_title("Senior Kotlin Developer")


def test_reference_groups_sorted_first_without_claiming_a_merge() -> None:
    rows = [
        _entry(1, "one", "x", "SAP Entwickler"),
        _entry(2, "two", "y", "SAP Entwickler"),
        _entry(3, "one", "z", "1085053 Embedded Developer"),
        _entry(4, "two", "w", "1085053 Embedded Developer"),
    ]
    groups = find_cross_source_title_matches(rows)
    assert len(groups) == 2
    assert groups[0].reference_hints == ("1085053",)
    assert groups[1].title == "SAP Entwickler"
