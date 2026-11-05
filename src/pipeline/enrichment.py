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
    # Mindestens drei aussagekräftige Wörter in der gefundenen Überschrift,
    # vier im Gesamttext sowie >=75 % Titelwortabdeckung.
    if overlap_title < 3 or overlap_all < 4 or coverage < 0.75:
        return 0.0, "Titel/Anforderungen nicht eindeutig genug"
    return coverage, f"Titelabgleich {overlap_title} Wörter, Gesamtdeckung {coverage:.0%}"


class BraveContextEnricher:
    """Brave LLM Context API (Schlüssel nur aus Umgebung, Resultate flüchtig)."""

    def __init__(self, settings: EnrichmentSettings) -> None:
        self._settings = settings
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
            return None

        matches: list[tuple[float, EnrichmentMatch]] = []
        for source in generic[: self._settings.max_results]:
            if not isinstance(source, dict):
                continue
            url = source.get("url")
            title = source.get("title")
            snippets = source.get("snippets")
            if not isinstance(url, str) or not isinstance(title, str) or not isinstance(snippets, list):
                continue
            joined = "\n".join(snippet for snippet in snippets if isinstance(snippet, str)).strip()
            if len(joined) < self._settings.min_snippet_chars:
                continue
            strength, evidence = _match_strength(candidate, title, joined, url)
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
        if not matches:
            return None
        # Zwei gleich gute Treffer können verschiedene Aufträge sein.
        # Kein willkürliches Mischen von Daten konkurrierender Projekte.
        if len(matches) > 1 and matches[0][0] - matches[1][0] < 0.15:
            return None
        return matches[0][1]


def build_enricher(settings: EnrichmentSettings) -> Enricher | None:
    if not settings.enabled:
        return None
    return BraveContextEnricher(settings)
