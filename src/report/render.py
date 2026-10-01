"""Darstellung des Tagesreports als HTML (Jinja2) und als Textalternative für E-Mails."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from src.report.builder import DailyReport

TEMPLATE_DIR = Path(__file__).parent / "templates"


def _formatter(timezone: str):
    zone = ZoneInfo(timezone)

    def format_datetime(value: datetime) -> str:
        return value.astimezone(zone).strftime("%d.%m.%Y %H:%M")

    return format_datetime


def _environment(timezone: str) -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        # Auftragstexte sind untrusted input: HTML immer escapen.
        autoescape=select_autoescape(["html"], default=True),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["dt"] = _formatter(timezone)
    return env


def render_html(report: DailyReport) -> str:
    stats = defaultdict(int, report.stats)
    return _environment(report.timezone).get_template("daily_report.html").render(report=report, s=stats)


def render_text(report: DailyReport) -> str:
    s = defaultdict(int, report.stats)
    c = report.class_counts
    lines = [
        report.title,
        f"Lauf #{report.run_id}, gestartet {_formatter(report.timezone)(report.run_started_at)}, Status {report.run_status}",
        "",
        f"Gelesene Mails: {s['mails_read']} | Neue Kandidaten: {s['candidates_new']} | "
        f"Duplikate: {s['duplicates']} | Prefilter-Rejects: {s['prefiltered']} | LLM-Analysen: {s['llm_analyses']}",
        f"A: {c['A']} | B: {c['B']} | C: {c['C']} | Reject: {c['REJECT']} | ausstehend: {s['pending_total']}",
    ]
    for problem in report.errors + report.warnings:
        lines.append(f"! {problem}")
    for lead in report.leads:
        lines += [
            "",
            f"[{lead.lead_class.value} {lead.score}] {lead.title}",
            f"  {lead.customer} · {lead.source} · {lead.category} · {lead.work_mode}",
            f"  Budget: {lead.budget} · Aufwand: {lead.effort}",
            f"  {lead.summary or ''}",
            f"  Warum passend: {lead.fit_reason or '–'}",
            f"  Nächster Schritt: {lead.next_step or '–'}",
            f"  Link: {lead.url or '–'}",
        ]
    if not report.leads:
        lines += ["", "Keine neuen A- oder B-Leads in diesem Lauf."]
    return "\n".join(lines) + "\n"
