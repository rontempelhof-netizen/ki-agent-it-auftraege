from __future__ import annotations

import pytest

from src.config import PrefilterSettings, ScoringSettings
from src.domain.enums import BudgetType, RemoteStatus
from src.domain.models import RawSourceItem
from src.pipeline.normalize import NormalizationError, extract_budget, extract_remote_status, normalize
from src.pipeline.prefilter import Prefilter
from tests.fixtures.leads import SEEN_AT, make_candidate


def raw(**fields: object) -> RawSourceItem:
    data: dict[str, object] = {"source": "freelancermap", "source_item_id": "2981734", "received_at": SEEN_AT,
                               "title": "Titel", "body_text": "Text"}
    data.update(fields)
    return RawSourceItem.model_validate(data)


# --- Normalisierung ---------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Stundensatz: 80–90 €/h", (80, 90, "EUR", BudgetType.HOURLY)),
        ("Stundensatz 95 EUR/Std.", (95, 95, "EUR", BudgetType.HOURLY)),
        ("Tagessatz: 650 € pro Tag", (650, 650, "EUR", BudgetType.DAILY)),
        ("Budget: 4.000 € (Festpreis)", (4000, 4000, "EUR", BudgetType.FIXED)),
        ("Festpreis: 12.500,50 EUR", (12500.5, 12500.5, "EUR", BudgetType.FIXED)),
        ("Budget nach Absprache", (None, None, None, None)),
        ("Kosten ca. 5.000 € insgesamt", (None, None, None, None)),  # mehrdeutig -> LLM
        ("Stundensatz: auf Anfrage", (None, None, None, None)),
        ("Rate: 60 $/h", (None, None, None, None)),  # nur eindeutige EUR-Angaben
    ],
)
def test_extract_budget(text, expected):
    assert extract_budget(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Remote | Start: ab sofort", RemoteStatus.REMOTE),
        ("Walldorf (vor Ort) | 12 Monate", RemoteStatus.ONSITE),
        ("München (hybrid, 1 Tag/Woche vor Ort)", RemoteStatus.HYBRID),
        ("Remote, 2 Tage pro Woche vor Ort", RemoteStatus.HYBRID),
        ("100 % remote, Kickoff vor Ort", RemoteStatus.REMOTE),
        ("Start im November", None),
    ],
)
def test_extract_remote_status(text, expected):
    assert extract_remote_status(text) == expected


def test_normalize_maps_fields():
    item = raw(
        source_url="https://www.freelancermap.de/projekt/x-2981734",
        title="  Shopware 6: Fehler   im Checkout ",
        body_text="Shopware 6: Fehler im Checkout\r\n\r\n\r\nRemote | Budget: 4.000 € (Festpreis)",
    )

    candidate = normalize(item, SEEN_AT)

    assert candidate.source_id == "2981734" and candidate.source_url.endswith("x-2981734")
    assert candidate.title == "Shopware 6: Fehler im Checkout"
    assert candidate.description == "Shopware 6: Fehler im Checkout\n\nRemote | Budget: 4.000 € (Festpreis)"
    assert candidate.first_seen_at == SEEN_AT and candidate.published_at is None
    assert candidate.remote_status == RemoteStatus.REMOTE
    assert (candidate.budget_min, candidate.budget_type) == (4000, BudgetType.FIXED)
    assert candidate.customer_name is None and candidate.location is None  # nichts erfunden


def test_normalize_title_fallback_and_html_body():
    item = raw(title=None, body_text=None, body_html="<p>Erste Zeile</p><p>Mehr Text</p>")

    candidate = normalize(item, SEEN_AT)

    assert candidate.title == "Erste Zeile" and "Mehr Text" in candidate.description


def test_normalize_source_id_fallback_is_stable():
    a = normalize(raw(source_item_id=None, source_url="https://x.example/p/1"), SEEN_AT)
    b = normalize(raw(source_item_id=None, source_url="https://x.example/p/1"), SEEN_AT)

    assert a.source_id == b.source_id and a.source_id.startswith("h-")


def test_normalize_without_title_and_text_fails():
    with pytest.raises(NormalizationError):
        normalize(raw(title=None, body_text=None, body_html="<p>   </p><script>x()</script>"), SEEN_AT)


# --- Prefilter --------------------------------------------------------------

PREFILTER = Prefilter(PrefilterSettings(), ScoringSettings())


def check(title: str = "Projekt", description: str = "", **fields: object):
    return PREFILTER.check(make_candidate(title=title, description=description, **fields))


@pytest.mark.parametrize(
    ("title", "description", "reason"),
    [
        ("Senior Java Entwickler (m/w/d) – Festanstellung", "", "reine Festanstellung"),
        ("Backend-Entwickler", "Wir bieten eine unbefristete Anstellung in Vollzeit.", "reine Festanstellung"),
        ("Developer", "This is a permanent position in Berlin.", "reine Festanstellung"),
        ("SAP-Berater", "Einsatz im Rahmen einer Arbeitnehmerüberlassung.", "Arbeitnehmerüberlassung"),
        ("SAP-Berater", "Vertragsform: ANÜ", "Arbeitnehmerüberlassung"),
    ],
)
def test_prefilter_rejects_reliable_cases(title, description, reason):
    result = check(title, description)

    assert result.rejected and reason in result.reasons[0]


def test_prefilter_rejects_explicit_rate_below_floor():
    result = check(budget_min=35, budget_max=35, currency="EUR", budget_type=BudgetType.HOURLY)

    assert result.rejected and "280 EUR/Tag unter Untergrenze 300" in result.reasons[0]


@pytest.mark.parametrize(
    ("title", "description", "fields"),
    [
        ("Python-Projekt", "Freiberuflich, keine Festanstellung.", {}),
        ("Python-Projekt", "Projekt ohne Arbeitnehmerüberlassung, Werkvertrag.", {}),
        ("Testmanager", "Zwingend: ISTQB Advanced und Sicherheitsüberprüfung Ü2.", {}),  # -> LLM
        ("SAP S/4HANA", "12 Monate Vollzeit vor Ort, Sicherheitsüberprüfung von Vorteil.", {}),  # -> LLM
        ("Python", "Satz 95 €/h", {"budget_min": 95, "currency": "EUR", "budget_type": BudgetType.HOURLY}),
        ("Shop", "Festpreis 500 €", {"budget_min": 500, "currency": "EUR", "budget_type": BudgetType.FIXED}),
    ],
)
def test_prefilter_leaves_uncertain_cases_to_llm(title, description, fields):
    assert not check(title, description, **fields).rejected


def test_prefilter_custom_patterns_and_disable():
    custom = Prefilter(PrefilterSettings(exclude_patterns=[r"\bblockchain\b"]), ScoringSettings())
    candidate = make_candidate(title="Blockchain-Wallet bauen", description="")

    assert custom.check(candidate).rejected
    assert not Prefilter(PrefilterSettings(enabled=False), ScoringSettings()).check(
        make_candidate(title="Festanstellung", description="")
    ).rejected
