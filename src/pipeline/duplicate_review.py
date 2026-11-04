"""Read-only evidence for possible cross-platform duplicate project listings.

Equal titles are a review signal, not proof of identical assignments. The
function never alters leads, source references, processing status or scores.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

# Typical 7-10 digit external posting references such as "1085053".
# These are *hints*, not a global identity or evidence for auto-merging.
_REFERENCE = re.compile(r"(?<!\d)\d{7,10}(?!\d)")


@dataclass(frozen=True, slots=True)
class DuplicateEntry:
    lead_id: int
    source: str
    source_id: str
    title: str
    source_url: str | None = None


@dataclass(frozen=True, slots=True)
class DuplicateGroup:
    title: str
    entries: tuple[DuplicateEntry, ...]
    reference_hints: tuple[str, ...]

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(sorted({entry.source for entry in self.entries}))


def normalize_title(title: str) -> str:
    """Conservative title normalization: Unicode, case and whitespace only."""
    return " ".join(unicodedata.normalize("NFKC", title).casefold().split())


def find_cross_source_title_matches(entries: Iterable[DuplicateEntry]) -> list[DuplicateGroup]:
    """Return review groups with identical normalized titles on >=2 sources.

    Retain multiple different posting IDs even within one platform: the title
    alone is insufficient to determine which entries describe the same job.
    """
    by_title: dict[str, list[DuplicateEntry]] = defaultdict(list)
    for entry in entries:
        key = normalize_title(entry.title)
        if key:
            by_title[key].append(entry)

    candidates: list[DuplicateGroup] = []
    for key, matches in by_title.items():
        if len(matches) < 2 or len({entry.source for entry in matches}) < 2:
            continue
        matches.sort(key=lambda entry: (entry.source, entry.source_id, entry.lead_id))
        candidates.append(
            DuplicateGroup(
                title=matches[0].title,
                entries=tuple(matches),
                reference_hints=tuple(sorted(set(_REFERENCE.findall(key)))),
            )
        )

    # Reference hints first, then largest groups, followed by stable title sort.
    return sorted(
        candidates,
        key=lambda group: (not bool(group.reference_hints), -len(group.entries), normalize_title(group.title)),
    )
