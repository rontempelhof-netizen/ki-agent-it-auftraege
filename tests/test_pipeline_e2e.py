"""End-to-End: E-Mail-Fixtures -> Connector -> Normalize -> Dedup -> Prefilter -> LLM -> Score -> SQLite -> Report.

Nutzt exakt das Demo-Postfach aus examples/demo (README-Demo ist damit getestet).
"""

from __future__ import annotations

from email import message_from_bytes, policy

from sqlalchemy import func, select

from src.app import generate_report, send_report
from src.domain.enums import LeadClass, ProcessingStatus
from src.storage.database import session_scope
from src.storage.orm import LeadScoreDetailRow, ProcessedEmailRow
from src.storage.repository import LeadRepository
from tests.pipeline_support import EXPECTED, demo_provider, make_env


def test_full_vertical_slice(tmp_path, no_network):
    env = make_env(tmp_path)
    provider = demo_provider()

    summary = env.pipeline(provider).run()

    # --- Lauf-Statistik
    assert summary.status.value == "SUCCESS" and summary.errors == []
    s = summary.stats
    assert (s["mails_read"], s["mail_duplicates"], s["unknown_senders"], s["mails_failed"]) == (6, 1, 1, 1)
    assert (s["items_found"], s["candidates_new"], s["duplicates"]) == (7, 6, 1)
    assert (s["prefiltered"], s["llm_analyses"], s["stored"]) == (1, 5, 6)
    assert (s["class_a"], s["class_b"], s["class_c"], s["class_reject"]) == (1, 2, 1, 2)

    # --- Gespeicherte Leads, Scores und Verarbeitungsstatus
    leads = env.leads()
    assert {sid: (l.lead_class.value, l.score_total, l.processing_status.value) for sid, l in leads.items()} == EXPECTED

    # --- Duplikat und Prefilter-Reject wurden NICHT analysiert (keine LLM-Kosten)
    assert provider.calls == 5
    sent = [r.user for r in provider.requests]
    # Shopware-Projekt steht in zwei Mails, wird aber genau einmal analysiert
    assert sum("Titel: Shopware 6: Fehler im Checkout" in u for u in sent) == 1
    assert not any("Senior Java Entwickler" in u for u in sent)  # Festanstellung: nur Prefilter

    # --- Keine Netzwerkverbindung nötig
    assert no_network == []


def test_persisted_details_per_lead(tmp_path):
    env = make_env(tmp_path)
    env.pipeline(demo_provider()).run()

    leads = env.leads()
    a_lead, sap, java = leads["2981734"], leads["2979988"], leads["2984001"]

    # Quelldaten + Analyse + Nachvollziehbarkeit
    assert a_lead.source == "freelancermap"
    assert a_lead.source_url == "https://www.freelancermap.de/projekt/shopware-6-checkout-bugfix-2981734"
    assert "PayPal-Zahlungen" in a_lead.description
    assert a_lead.analysis is not None and a_lead.analysis.technical_fit.value == "high"
    assert a_lead.prompt_version.startswith("lead-analysis-v1+") and a_lead.llm_model == "fake-model"
    assert a_lead.processed_run_id == 1
    # Score-Breakdown: 9 Kriterien, Summe = Gesamtscore
    with session_scope(env.factory) as session:
        details = session.scalars(select(LeadScoreDetailRow).where(LeadScoreDetailRow.lead_id == a_lead.id)).all()
    assert len(details) == 9 and sum(d.points for d in details) == 93
    # Hard Fails
    assert sap.hard_fail and len(sap.hard_fail_reasons) == 2
    assert java.processing_status == ProcessingStatus.PREFILTERED and java.analysis is None
    assert java.hard_fail_reasons[0].startswith("Prefilter: reine Festanstellung")
    # First/Last Seen: Duplikat aus der Folge-Mail aktualisiert die letzte Sichtung
    assert a_lead.last_seen_at > a_lead.first_seen_at
    with session_scope(env.factory) as session:
        sources = LeadRepository(session).list_sources(a_lead.id)
    assert len(sources) == 1 and sources[0].last_seen_at == a_lead.last_seen_at


def test_second_run_does_not_reanalyze(tmp_path):
    env = make_env(tmp_path)
    provider = demo_provider()
    env.pipeline(provider).run()

    second = env.pipeline(provider).run()

    assert provider.calls == 5  # keine zusätzlichen LLM-Aufrufe
    assert second.stats["mail_duplicates"] == 6 and second.stats.get("candidates_new", 0) == 0
    assert len(env.leads()) == 6


def test_messages_are_acknowledged_after_storage(tmp_path):
    env = make_env(tmp_path)
    env.pipeline(demo_provider()).run()

    with session_scope(env.factory) as session:
        rows = {r.message_key: (r.status, r.item_count) for r in session.scalars(select(ProcessedEmailRow))}
    assert rows["mid:20260930070211.4f2a1c@mailer.freelancermap.de"] == ("processed", 3)
    assert rows["mid:20261001070145.7b3d9e@mailer.freelancermap.de"] == ("processed", 2)
    assert rows["mid:weekly-2026-39@it-jobs-weekly.example"] == ("skipped", 0)
    assert len(rows) == 5  # 6 Dateien, davon 1 erneut zugestellte Kopie


def test_report_contains_a_and_b_only(tmp_path):
    env = make_env(tmp_path)
    summary = env.pipeline(demo_provider()).run()

    output = generate_report(env.settings, env.factory, run_id=summary.run_id, clock=env.clock)
    html = output.html_path.read_text(encoding="utf-8")

    assert output.html_path.exists() and output.html_path.name == "report-run-1.html"
    report = output.report
    assert [(l.lead_class, l.score) for l in report.leads] == [(LeadClass.A, 93), (LeadClass.B, 79), (LeadClass.B, 73)]
    # Reihenfolge im HTML: A vor B, B nach Score absteigend
    positions = [html.index(title) for title in (
        "Shopware 6: Fehler im Checkout beheben",
        "Python-Skript für täglichen CSV-Import",
        "Python-Entwickler (m/w/d) für API-Anbindung",
    )]
    assert positions == sorted(positions)
    # C- und Reject-Leads nicht einzeln, aber gezählt
    for hidden in ("Datenmigration MySQL nach PostgreSQL", "Senior SAP S/4HANA", "Senior Java Entwickler"):
        assert hidden not in html
    assert html.count('class="card lead ') == 3
    assert "C- und Reject-Leads sind gespeichert" in html
    # Kopfstatistik und Warnungen
    assert "Lauf #1" in html and ">6</td>" in html
    assert "unbekannter Absender newsletter@it-jobs-weekly.example" in html
    # Pflichtinhalte je Lead
    for label in ("Auftraggeber", "Quelle", "Kategorie", "Remote/Standort", "Budget", "Geschätzter Aufwand",
                  "Kurzfassung", "Warum passt der Auftrag zu uns?", "Muss-Anforderungen", "Risiken",
                  "Offene Fragen", "Empfohlener nächster Schritt", "Vorbereitete Erstansprache",
                  "Original-Ausschreibung öffnen"):
        assert label in html
    assert "4.000 EUR (Festpreis)" in html and "80–90 EUR/h" in html and "ca. 5 PT" in html
    assert 'href="https://www.freelancermap.de/projekt/shopware-6-checkout-bugfix-2981734"' in html


def test_report_mail_dry_run_writes_eml(tmp_path, no_network):
    env = make_env(tmp_path)
    summary = env.pipeline(demo_provider()).run()
    report = generate_report(env.settings, env.factory, run_id=summary.run_id, clock=env.clock).report

    delivery = send_report(env.settings, report, clock=env.clock)

    assert delivery.mode == "dry_run" and delivery.path.exists()
    message = message_from_bytes(delivery.path.read_bytes(), policy=policy.default)
    assert message["Subject"] == "[IT-Aufträge] IT-Aufträge – Tagesreport: 1 A-, 2 B-Leads (01.10.2026)"
    assert message["To"] == "ich@example.org"
    assert "Shopware 6" in message.get_body(("html",)).get_content()
    assert "[A 93] Shopware 6" in message.get_body(("plain",)).get_content()
    assert delivery.recipients == ["ich@example.org"]
    assert no_network == []


def test_score_details_count_matches_analyzed_leads(tmp_path):
    env = make_env(tmp_path)
    env.pipeline(demo_provider()).run()

    with session_scope(env.factory) as session:
        rows = session.scalar(select(func.count()).select_from(LeadScoreDetailRow))
    assert rows == 5 * 9  # nur LLM-analysierte Leads haben einen Breakdown
