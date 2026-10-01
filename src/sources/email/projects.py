"""Zuordnung einer Mail zu einem Parserprofil und Zerlegung in Projektblöcke.

Ein Projekt wird über seinen Projektlink erkannt (``project_url_patterns``).
Mehrere Links auf dasselbe Projekt (Titel-Link, "Projekt ansehen", Tracking-
Parameter) werden über die Projekt-ID zusammengefasst. Der Text zwischen den
Projektlinks bildet den jeweiligen Projektblock (Layout ``link_start``/``link_end``).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from src.config import EmailSourceProfile, LinkLayout
from src.sources.email.extract import ExtractedBody, Link

_ENUMERATION_RE = re.compile(r"^\s*(?:\d{1,2}[.)]|[-*•–])\s+")
_SEPARATOR_LINE_RE = re.compile(r"^[\s\-=_*~#·•—–]{3,}$")
_URL_ONLY_LINE_RE = re.compile(r"^(?:[\w ÄÖÜäöüß-]{0,40}:\s*)?https?://\S+$", re.IGNORECASE)
MAX_TITLE_LENGTH = 300


@dataclass(frozen=True)
class ProjectBlock:
    item_id: str
    url: str
    title: str | None
    text: str
    links: tuple[str, ...]
    """Alle URLs innerhalb des Blocks (inkl. Nicht-Projektlinks), eindeutig."""


def match_profile(
    profiles: Sequence[EmailSourceProfile], sender_address: str | None, subject: str | None
) -> EmailSourceProfile | None:
    """Erstes Profil, dessen Absender- (und ggf. Betreff-)Muster passt."""
    if not sender_address:
        return None
    for profile in profiles:
        if not any(p.search(sender_address) for p in profile.sender_patterns):
            continue
        if profile.subject_patterns and not any(p.search(subject or "") for p in profile.subject_patterns):
            continue
        return profile
    return None


def canonical_url(url: str, strip_query: bool) -> str:
    parts = urlsplit(url.strip())
    query = "" if strip_query else parts.query
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, query, ""))


def project_item_id(url: str, profile: EmailSourceProfile) -> str:
    if profile.project_id_pattern is not None:
        match = profile.project_id_pattern.search(url)
        if match:
            return match.group(1) if match.groups() else match.group(0)
    return "url-" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def _content_bounds(text: str, profile: EmailSourceProfile) -> tuple[int, int]:
    start = 0
    for marker in profile.content_start_markers:
        if match := marker.search(text):
            start = max(start, match.end())
    end = len(text)
    for marker in profile.footer_markers:
        if (match := marker.search(text, start)) is not None:
            end = min(end, _line_start(text, match.start()))
    return start, end


def _line_start(text: str, offset: int) -> int:
    return text.rfind("\n", 0, offset) + 1


def _line_end(text: str, offset: int) -> int:
    position = text.find("\n", offset)
    return len(text) if position == -1 else position


def _clean_block_text(text: str) -> str:
    lines = [line for line in text.split("\n") if not _SEPARATOR_LINE_RE.match(line)]
    return "\n".join(lines).strip()


def _title_from_text(text: str, generic_texts: frozenset[str]) -> str | None:
    for line in text.split("\n"):
        candidate = line.strip()
        if (
            candidate
            and candidate.casefold() not in generic_texts
            and not _URL_ONLY_LINE_RE.match(candidate)
            and not _SEPARATOR_LINE_RE.match(candidate)
        ):
            return _clean_title(candidate)
    return None


def _block_start(text: str, link: Link, lower_bound: int) -> int:
    """Blockanfang für Layout ``link_start``.

    HTML: Zeile des (Titel-)Links. Reiner Text: Steht die URL allein in ihrer Zeile,
    gehört die unmittelbar vorangehende Textzeile als Titel zum Block.
    """
    line_start = _line_start(text, link.offset)
    if link.text:
        return max(lower_bound, line_start)
    line = text[line_start : _line_end(text, link.offset)].strip()
    if line_start > lower_bound and _URL_ONLY_LINE_RE.match(line):
        previous_start = _line_start(text, line_start - 1)
        previous = text[previous_start : line_start - 1].strip()
        if previous and previous_start >= lower_bound and not _URL_ONLY_LINE_RE.match(previous):
            return previous_start
    return max(lower_bound, line_start)


def _clean_title(title: str) -> str | None:
    cleaned = _ENUMERATION_RE.sub("", title).strip()
    return cleaned[:MAX_TITLE_LENGTH] or None


def _title_from_links(links: Iterable[Link], generic_texts: frozenset[str]) -> str | None:
    for link in links:
        text = link.text.strip()
        if text and text.casefold() not in generic_texts and not text.lower().startswith("http"):
            return _clean_title(text)
    return None


def split_projects(
    body: ExtractedBody,
    profile: EmailSourceProfile,
    layout: LinkLayout,
    generic_link_texts: Iterable[str] = (),
) -> list[ProjectBlock]:
    """Zerlegt einen Mailinhalt in Projektblöcke. Leere Liste, wenn kein Projektlink erkannt wird."""
    text = body.text
    start, end = _content_bounds(text, profile)
    generic = frozenset(t.casefold() for t in generic_link_texts)

    grouped: dict[str, list[Link]] = {}
    urls: dict[str, str] = {}
    for link in body.links:
        if not start <= link.offset < end:
            continue
        url = canonical_url(link.url, profile.strip_url_query)
        if not any(p.search(url) for p in profile.project_url_patterns):
            continue
        item_id = project_item_id(url, profile)
        grouped.setdefault(item_id, []).append(link)
        urls.setdefault(item_id, url)
    if not grouped:
        return []

    if layout == "link_start":
        order = sorted(grouped, key=lambda i: grouped[i][0].offset)
        bounds: list[int] = []
        for item_id in order:
            lower = bounds[-1] + 1 if bounds else start
            bounds.append(_block_start(text, grouped[item_id][0], lower))
        bounds.append(end)
        segments = [(bounds[k], bounds[k + 1]) for k in range(len(order))]
    else:
        order = sorted(grouped, key=lambda i: grouped[i][-1].offset)
        ends = [min(end, _line_end(text, grouped[i][-1].offset)) for i in order]
        segments = [(start if k == 0 else ends[k - 1], ends[k]) for k in range(len(order))]

    blocks: list[ProjectBlock] = []
    for item_id, (seg_start, seg_end) in zip(order, segments, strict=True):
        segment = text[seg_start:seg_end]
        block_links = tuple(dict.fromkeys(link.url for link in body.links if seg_start <= link.offset < seg_end))
        title = _title_from_links(grouped[item_id], generic) if layout == "link_start" else None
        blocks.append(
            ProjectBlock(
                item_id=item_id,
                url=urls[item_id],
                title=title or _title_from_text(_clean_block_text(segment), generic),
                text=_clean_block_text(segment),
                links=block_links,
            )
        )
    return blocks
