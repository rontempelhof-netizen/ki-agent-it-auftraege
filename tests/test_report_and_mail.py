from __future__ import annotations

from datetime import UTC, datetime
from email import message_from_bytes, policy

import pytest

from src.config import MailSettings, ReportSettings, ScoringSettings
from src.domain.enums import BudgetType, CrawlRunStatus, LeadClass
from src.domain.models import CrawlRun, Lead, merge_source_facts
from src.notify.mailer import DryRunMailer, MailError, OutgoingMail, SmtpMailer, create_mailer
from src.report.builder import build_report, format_budget, safe_url
from src.report.render import render_html, render_text
from src.scoring.engine import ScoreEngine
from tests.fixtures.leads import (
    CSV_IMPORT_79,
    LEGACY_PHP_50,
    PERFECT_SMALL_PROJECT,
    SAP_STAFF_LEASING,
    SEEN_AT,
    SHOPWARE_BUGFIX,
    make_candidate,
)

NOW = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
ENGINE = ScoreEngine(ScoringSettings())
RUN = CrawlRun(id=7, started_at=NOW, finished_at=NOW, status=CrawlRunStatus.SUCCESS,
               stats={"mails_read": 3, "candidates_new": 4, "duplicates": 1, "prefiltered": 1, "llm_analyses": 3},
               warnings=["email: unbekannter Absender x@y.example, übersprungen"])


def lead(case, lead_id: int, **candidate_fields: object) -> Lead:
    candidate = make_candidate(source_id=str(lead_id), **candidate_fields)
    merged = merge_source_facts(candidate, case.analysis)
    built = Lead.build(candidate, merged, ENGINE.score(merged))
    return built.model_copy(update={"id": lead_id})


def test_builder_orders_a_before_b_and_hides_c_reject():
    leads = [
        lead(LEGACY_PHP_50, 1, title="Legacy PHP (C)"),
        lead(CSV_IMPORT_79, 2, title="CSV-Import (B)"),
        lead(SHOPWARE_BUGFIX, 3, title="Shopware (A 93)"),
        lead(SAP_STAFF_LEASING, 4, title="SAP (Reject)"),
        lead(PERFECT_SMALL_PROJECT, 5, title="Perfekt (A 100)"),
    ]

    report = build_report(RUN, leads, ReportSettings(), generated_at=NOW)

    assert [(l.title, l.score) for l in report.leads] == [("Perfekt (A 100)", 100), ("Shopware (A 93)", 93), ("CSV-Import (B)", 79)]
    assert report.class_counts == {"A": 2, "B": 1, "C": 1, "REJECT": 1, "pending": 0}
    assert report.subject == "IT-Aufträge – Tagesreport: 2 A-, 1 B-Leads (01.10.2026)"


def test_detail_classes_are_configurable():
    leads = [lead(LEGACY_PHP_50, 1), lead(SHOPWARE_BUGFIX, 2)]

    report = build_report(RUN, leads, ReportSettings(detail_classes=[LeadClass.A, LeadClass.B, LeadClass.C]), NOW)

    assert [l.lead_class for l in report.leads] == [LeadClass.A, LeadClass.C]


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"budget_min": 80, "budget_max": 90, "currency": "EUR", "budget_type": BudgetType.HOURLY}, "80–90 EUR/h"),
        ({"budget_min": 4000, "budget_max": 4000, "currency": "EUR", "budget_type": BudgetType.FIXED}, "4.000 EUR (Festpreis)"),
        ({"budget_min": 650.5, "currency": "EUR", "budget_type": BudgetType.DAILY}, "ab 650,50 EUR/Tag"),
        ({"budget_max": 95, "currency": "EUR", "budget_type": BudgetType.HOURLY}, "bis 95 EUR/h"),
        ({"budget_min": 500}, "ab 500 (Währung unbekannt)"),
        ({}, "unbekannt"),
    ],
)
def test_format_budget(fields, expected):
    assert format_budget(Lead(source="x", source_id="1", title="t", first_seen_at=SEEN_AT, **fields)) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.freelancermap.de/projekt/x", "https://www.freelancermap.de/projekt/x"),
        ("http://example.org", "http://example.org"),
        ("javascript:alert(1)", None),
        ("data:text/html;base64,PHNjcmlwdD4=", None),
        (None, None),
    ],
)
def test_safe_url(url, expected):
    assert safe_url(url) == expected


def test_untrusted_content_is_escaped_in_html():
    evil = lead(SHOPWARE_BUGFIX, 1, title='<script>alert("x")</script> Shop',
                source_url="javascript:alert(document.cookie)")
    evil = evil.model_copy(update={"suggested_outreach": "<img src=x onerror=alert(1)>"})

    html = render_html(build_report(RUN, [evil], ReportSettings(), NOW))

    assert "<script>alert" not in html and "&lt;script&gt;" in html
    assert "<img src=x" not in html and "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "javascript:" not in html and "Kein Original-Link vorhanden" in html


def test_report_header_stats_warnings_and_empty_state():
    html = render_html(build_report(RUN, [lead(LEGACY_PHP_50, 1)], ReportSettings(), NOW))

    assert "Lauf #7" in html and "01.10.2026 10:00" in html  # Europe/Berlin
    assert "Keine neuen A- oder B-Leads in diesem Lauf." in html
    assert "unbekannter Absender x@y.example" in html
    for label in ("Gelesene Mails", "Neue Kandidaten", "Duplikate", "Prefilter-Rejects", "LLM-Analysen"):
        assert label in html
    assert "C- und Reject-Leads sind gespeichert" in html


def test_text_version():
    text = render_text(build_report(RUN, [lead(SHOPWARE_BUGFIX, 1, title="Shopware-Bugfix")], ReportSettings(), NOW))

    assert "[A 93] Shopware-Bugfix" in text and "Gelesene Mails: 3" in text and "! email: unbekannter" in text


# --- Mailer ---------------------------------------------------------------

MAIL = OutgoingMail(subject="Tagesreport", html="<p>Hallo</p>", text="Hallo")


def test_dry_run_writes_eml_and_sends_nothing(tmp_path, no_network):
    settings = MailSettings(outbox_dir=tmp_path / "outbox", recipients=["ich@example.org"], sender="agent@example.org")

    result = create_mailer(settings).send(MAIL, now=NOW)

    assert isinstance(create_mailer(settings), DryRunMailer)
    message = message_from_bytes(result.path.read_bytes(), policy=policy.default)
    assert message["Subject"] == "[IT-Aufträge] Tagesreport" and message["To"] == "ich@example.org"
    assert message.get_body(("html",)).get_content().strip() == "<p>Hallo</p>"
    assert no_network == []


class FakeSMTP:
    instances: list[FakeSMTP] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host, self.port, self.timeout = host, port, timeout
        self.calls: list[str] = []
        self.sent = None
        FakeSMTP.instances.append(self)

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, *args: object) -> None:
        self.calls.append("quit")

    def starttls(self) -> None:
        self.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        self.calls.append(f"login:{user}:{password}")

    def send_message(self, message) -> None:
        self.sent = message


def test_smtp_mailer_with_mocked_smtp(monkeypatch):
    monkeypatch.setattr("src.notify.mailer.smtplib.SMTP", FakeSMTP)
    settings = MailSettings(mode="smtp", smtp_host="smtp.example.org", smtp_username="u", smtp_password="geheim",
                            recipients=["ich@example.org"], sender="agent@example.org")

    result = create_mailer(settings).send(MAIL, now=NOW)

    smtp = FakeSMTP.instances[-1]
    assert (smtp.host, smtp.port) == ("smtp.example.org", 587)
    assert smtp.calls == ["starttls", "login:u:geheim", "quit"]
    assert smtp.sent["To"] == "ich@example.org" and result.mode == "smtp"


def test_smtp_requires_host_and_recipients():
    with pytest.raises(MailError, match="smtp_host"):
        SmtpMailer(MailSettings(mode="smtp", recipients=["a@example.org"]), ["a@example.org"])
    with pytest.raises(MailError, match="Empfänger"):
        create_mailer(MailSettings(mode="smtp", smtp_host="smtp.example.org"))


def test_smtp_password_is_secret():
    settings = MailSettings(smtp_password="geheim")

    assert "geheim" not in settings.model_dump_json() and "geheim" not in repr(settings)
