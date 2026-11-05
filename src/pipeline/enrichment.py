"""Optionale automatische Ausschreibungsanreicherung aus einer Such-API.

WICHTIG:
- Keine direkten Abrufe von Projektportalen: nur der dokumentierte Brave-API-Endpunkt.
- Antwortausschnitte werden ausschließlich flüchtig an die aktuelle LLM-Analyse
  weitergereicht, nicht als Datenbankspalte oder Suchresultat gespeichert.
- Strenge, deterministische Identitätsprüfung. Bei Unsicherheit keine Anreicherung.
- Keine Umgehung von Captchas, Logins, robots.txt oder Scraping-Verboten.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from src.config import EnrichmentSettings
from src.domain.models import LeadCandidate

BRAVE_CONTEXT_URL = "https://api.search.brave.com/res/v1/llm/context"
_STOPWORDS = frozenset({
    "remote", "deutschland", "m", "w", "d", "senior", "junior",
    "gesucht", "projekt", "freiberuflich", "freelance", "entwickler",
    "engineer", "developer", "und", "oder", "fuer", "für", "with",
    "the", "job", "vollzeit", "teilzeit",
})
_IT_HINTS = re.compile(
    r"\b(?:it|ki|ai|llm|rag|python|sql|data|backend|frontend|fullstack|software|"
    r"digitalisierung|automatisierung|api|erp|sap|workday|devops|cloud|"
    r"business analyst|datenmigration|anwendungsentwicklung|web|angular)\b",
    re.IGNORECASE,
)
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True)
class EnrichmentMatch:
    url: str
    title: str
    text: str
    evidence: str
    mirror_urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class SearchCandidateDiagnostic:
    """Suchtreffer ohne Volltext oder Zugangsdaten; nur für die lokale Diagnose."""

    title: str
    url: str
    score: float
    reason: str


@dataclass(frozen=True)
class SearchDiagnostics:
    query: str
    total_results: int
    checked_results: int
    outcome: str
    candidates: tuple[SearchCandidateDiagnostic, ...]


class Enricher(Protocol):
    def should_search(self, candidate: LeadCandidate) -> bool: ...
    def enrich(self, candidate: LeadCandidate) -> EnrichmentMatch | None: ...


def _terms(text: str) -> set[str]:
    return {
        t.casefold()
        for t in _TOKEN.findall(text)
        if len(t) >= 4 and t.casefold() not in _STOPWORDS and not t.isdigit()
    }


def _valid_https_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def _match_strength(candidate: LeadCandidate, title: str, snippets: str, url: str) -> tuple[float, str]:
    """Source-ID oder mehrere charakteristische Titelwörter müssen übereinstimmen.

    Ein Suchtreffer allein ist keine Identitätsbestätigung: Projekt-/Jobtitel sind
    oft generisch. Einzelne Übereinstimmungen werden bewusst verworfen.
    """
    if not _valid_https_url(url):
        return 0.0, "ungültige URL"
    source_id = candidate.source_id
    query_terms = _terms(candidate.title)
    title_terms = _terms(title)
    # Plattform-IDs sind nicht global eindeutig. Auch bei gleicher
    # Nummer muss mindestens ein fachlicher Titelabgleich vorliegen,
    # wenn der Treffer auf einer anderen Domain veröffentlicht ist.
    original_host = urlsplit(candidate.source_url or "").hostname
    matched_host = urlsplit(url).hostname
    same_origin = bool(original_host and original_host == matched_host)
    if source_id.isdigit() and len(source_id) >= 7 and (
        source_id in url or source_id in title
    ) and (same_origin or len(query_terms & title_terms) >= 2):
        return 1.0, "Projektkennung mit zusätzlichem Quellabgleich"


    if len(query_terms) < 4:
        return 0.0, "Quelltitel zu unspezifisch"
    overlap_title = len(query_terms & title_terms)
    overlap_all = len(query_terms & (title_terms | _terms(snippets)))
    coverage = overlap_all / len(query_terms)
    # Bei mindestens fünf charakteristischen Titelbegriffen reichen zwei
    # Titelübereinstimmungen, wenn insgesamt mindestens vier Begriffe und
    # mindestens 80 % des Quelltitels (inkl. Snippets) wiederzufinden sind.
    # Bei kürzeren Titeln bleiben drei Treffer im gefundenen Titel Pflicht.
    title_minimum = 2 if len(query_terms) >= 5 and coverage >= 0.8 else 3
    if overlap_title < title_minimum or overlap_all < 4 or coverage < 0.75:
        return 0.0, (
            f"Abgleich unzureichend: Titel {overlap_title}/{title_minimum}, "
            f"gesamt {overlap_all}/{len(query_terms)} ({coverage:.0%})"
        )
    return coverage, f"Titelabgleich {overlap_title} Wörter, Gesamtdeckung {coverage:.0%}"


# Strong title agreement is useful for detecting syndicated *copies*.
# Generic role names must never be de-duplicated solely by a score tie.
_RATE_FACT_RE = re.compile(
    r"\b(\d{2,4})(?:[,.]\d{1,2})?\s*(?:€|eur|euro)\s*(?:/|pro|je)\s*"
    r"(h|std\.?|stunde|tag|pt)\b",
    re.IGNORECASE,
)


def _ordered_terms(title: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            w.casefold() for w in _TOKEN.findall(title)
            if len(w) >= 4 and w.casefold() not in _STOPWORDS and not w.isdigit()
        )
    )


def _mirror_title(candidate: LeadCandidate, title: str) -> bool:
    """Require a very distinctive title, in identical characteristic-word order.

    A portal may suffix "auf www.freelancermap.de"; allow at most one
    additional significant word. A generic Python job title is insufficient.
    """
    core = _ordered_terms(candidate.title)
    found = _ordered_terms(title)
    if len(core) < 5 or len(found) < len(core) or len(found) - len(core) > 1:
        return False
    positions = iter(found)
    return all(any(value == word for value in positions) for word in core)


def _rate_facts(text: str) -> set[tuple[int, str]]:
    """Only compare explicit rates with identical rate units; no guesswork."""
    normalized: set[tuple[int, str]] = set()
    for amount, unit in _RATE_FACT_RE.findall(text):
        kind = "hour" if unit.lower().startswith(("h", "std")) else "day"
        normalized.add((int(amount), kind))
    return normalized


def _mirrors_do_not_conflict(matches: list[EnrichmentMatch]) -> bool:
    """Reject obviously conflicting rate facts; never merge disparate snippets."""
    rates = [_rate_facts(m.text) for m in matches]
    for i, left in enumerate(rates):
        for right in rates[i + 1:]:
            if left and right and left.isdisjoint(right):
                return False
    return True


def _preferred_source_key(candidate: LeadCandidate, match: EnrichmentMatch) -> tuple[int, int, int, str]:
    """Prefer original host, then a monitored project portal, then richer text."""
    host = urlsplit(match.url).hostname or ""
    original_host = urlsplit(candidate.source_url or "").hostname or ""
    trusted_portal_suffixes = ("freelancermap.de", "freelance.de", "gulp.de")
    is_portal = any(host == d or host.endswith("." + d) for d in trusted_portal_suffixes)
    return (int(bool(original_host and host == original_host)), int(is_portal), len(match.text), match.url)


class BraveContextEnricher:
    """Brave LLM Context API (Schlüssel nur aus Umgebung, Resultate flüchtig)."""

    def __init__(self, settings: EnrichmentSettings) -> None:
        self._settings = settings
        self.last_diagnostics: SearchDiagnostics | None = None
        self._api_key = os.getenv("BRAVE_API_KEY", "").strip()
        if not self._api_key:
            raise ValueError(
                "Automatische Recherche aktiviert, aber BRAVE_API_KEY fehlt. "
                "Brave API-Key nur lokal als Umgebungsvariable hinterlegen."
            )

    def should_search(self, candidate: LeadCandidate) -> bool:
        return (
            len(candidate.description.strip()) <= self._settings.max_original_chars
            and bool(_IT_HINTS.search(f"{candidate.title}\n{candidate.description}"))
        )

    def enrich(self, candidate: LeadCandidate) -> EnrichmentMatch | None:
        """Genau eine Suchanfrage, maximal wenige Resultate, kein direktes Scraping."""
        self.last_diagnostics = None
        # Kein exakter Gesamt-Titel: Erstveröffentlicher formulieren häufig
        # anders als die Plattformen, die ihre Projektalarme verschicken.
        search_terms: list[str] = []
        for word in _TOKEN.findall(candidate.title):
            normalized = word.casefold()
            if len(normalized) >= 4 and normalized not in _STOPWORDS and normalized not in search_terms:
                search_terms.append(normalized)
        search_query = " ".join(search_terms[:8])[:180] + " Projekt Freelancer"
        query = urlencode({
            "q": search_query,
            "country": self._settings.country,
            "search_lang": self._settings.search_lang,
            "maximum_number_of_tokens": self._settings.search_token_budget,
        })
        request = Request(
            f"{BRAVE_CONTEXT_URL}?{query}",
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": self._api_key,
                "User-Agent": "IT-Auftragsagent/0.1 (Brave API integration)",
            },
        )
        with urlopen(request, timeout=self._settings.timeout_seconds) as response:
            # Bound the API response before parsing, even if a provider misbehaves.
            payload = response.read(self._settings.max_response_bytes + 1)
        if len(payload) > self._settings.max_response_bytes:
            raise ValueError("Suchantwort übersteigt das konfigurierte Größenlimit.")
        data = json.loads(payload)
        generic = data.get("grounding", {}).get("generic", [])
        if not isinstance(generic, list):
            generic = []

        matches: list[tuple[float, EnrichmentMatch]] = []
        diagnostics: list[SearchCandidateDiagnostic] = []
        for source in generic[: self._settings.max_results]:
            if not isinstance(source, dict):
                diagnostics.append(SearchCandidateDiagnostic("", "", 0.0, "Ungültiger Antwort-Eintrag"))
                continue
            url = source.get("url")
            title = source.get("title")
            snippets = source.get("snippets")
            if not isinstance(url, str) or not isinstance(title, str) or not isinstance(snippets, list):
                diagnostics.append(SearchCandidateDiagnostic("", "", 0.0, "Titel, URL oder Snippets fehlen"))
                continue
            joined = "\n".join(snippet for snippet in snippets if isinstance(snippet, str)).strip()
            if len(joined) < self._settings.min_snippet_chars:
                diagnostics.append(SearchCandidateDiagnostic(
                    title[:250], url[:500], 0.0, f"Textauszug zu kurz ({len(joined)} Zeichen)"
                ))
                continue
            strength, evidence = _match_strength(candidate, title, joined, url)
            diagnostics.append(SearchCandidateDiagnostic(title[:250], url[:500], strength, evidence))
            if strength <= 0:
                continue
            matches.append((
                strength,
                EnrichmentMatch(
                    url=url,
                    title=title[:250],
                    text=joined[: self._settings.max_enriched_chars],
                    evidence=evidence,
                ),
            ))
        matches.sort(key=lambda item: item[0], reverse=True)
        result: EnrichmentMatch | None = None
        if not generic:
            outcome = "keine Suchtreffer"
        elif not matches:
            outcome = "keine hinreichend passenden Treffer"
        else:
            # Only results near the top score can cause ambiguity.
            competing = [match for strength, match in matches if matches[0][0] - strength < 0.15]
            if len(competing) == 1:
                result = matches[0][1]
                outcome = "eindeutiger Treffer"
            elif (
                all(_mirror_title(candidate, match.title) for match in competing)
                and _mirrors_do_not_conflict(competing)
            ):
                # Search hits from syndication sites are not independent proof.
                # Select ONE source and never blend text snippets or inferred facts.
                primary = max(competing, key=lambda m: _preferred_source_key(candidate, m))
                mirrors = tuple(sorted({m.url for m in competing if m.url != primary.url}))
                result = EnrichmentMatch(
                    url=primary.url,
                    title=primary.title,
                    text=primary.text,
                    evidence=(
                        f"{primary.evidence}; {len(competing)} sehr ähnliche "
                        "Veröffentlichungen, nur eine Quelle verwendet"
                    ),
                    mirror_urls=mirrors,
                )
                outcome = "wahrscheinliche Mehrfachveröffentlichung"
            else:
                outcome = "mehrdeutige Zuordnung"
        self.last_diagnostics = SearchDiagnostics(
            query=search_query,
            total_results=len(generic),
            checked_results=len(diagnostics),
            outcome=outcome,
            candidates=tuple(diagnostics),
        )
        return result


def build_enricher(settings: EnrichmentSettings) -> Enricher | None:
    if not settings.enabled:
        return None
    return BraveContextEnricher(settings)
