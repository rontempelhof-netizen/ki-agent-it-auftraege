"""Umwandlung von HTML- und Text-Mailinhalt in normalisierten Text mit Link-Positionen.

Nur Standardbibliothek. Skripte/Styles werden verworfen, Links werden mit ihrer
Position im normalisierten Text erfasst, damit Projektblöcke geschnitten werden können.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

_URL_RE = re.compile(r"https?://[^\s<>\"'\]\)]+", re.IGNORECASE)
_TRAILING_PUNCTUATION = ".,;:!?"
_MARKER_RE = re.compile("(\\d+)")
_BLOCK_TAGS = frozenset(
    {"p", "div", "ul", "ol", "table", "section", "article", "header", "footer",
     "h1", "h2", "h3", "h4", "h5", "h6", "hr", "blockquote"}
)
_LINE_TAGS = frozenset({"br", "tr", "li"})
"""Erzeugen nur beim Öffnen einen Zeilenumbruch (keine Leerzeile zwischen Zeilen)."""
_CELL_TAGS = frozenset({"td", "th"})
_SKIP_TAGS = frozenset({"script", "style", "head", "title", "noscript"})


@dataclass(frozen=True)
class Link:
    url: str
    text: str
    offset: int
    """Zeichenposition im normalisierten Text."""


@dataclass(frozen=True)
class ExtractedBody:
    text: str
    links: tuple[Link, ...]


def normalize_whitespace(text: str) -> str:
    """Vereinheitlicht Zeilenumbrüche, kürzt Leerraum je Zeile, max. eine Leerzeile in Folge."""
    lines = [re.sub(r"[ \t ​\f\v]+", " ", line).strip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    result: list[str] = []
    for line in lines:
        if line or (result and result[-1]):
            result.append(line)
    while result and not result[-1]:
        result.pop()
    return "\n".join(result)


class _HtmlTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[tuple[str, list[str]]] = []
        self._skip_depth = 0
        self._open_link: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS or tag in _LINE_TAGS:
            self.parts.append("\n")
        elif tag in _CELL_TAGS:
            self.parts.append(" ")
        if tag == "a" and self._skip_depth == 0:
            href = (dict(attrs).get("href") or "").strip()
            if href.lower().startswith(("http://", "https://")):
                self._open_link = len(self.links)
                self.links.append((href, []))
                self.parts.append(f"{self._open_link}")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")
        elif tag == "a":
            self._open_link = None

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self.parts.append(data)
        if self._open_link is not None:
            self.links[self._open_link][1].append(data)


def html_to_body(html: str) -> ExtractedBody:
    parser = _HtmlTextExtractor()
    parser.feed(html)
    parser.close()
    marked = normalize_whitespace("".join(parser.parts))

    text_parts: list[str] = []
    links: list[Link] = []
    position = 0
    last = 0
    for match in _MARKER_RE.finditer(marked):
        chunk = marked[last : match.start()]
        text_parts.append(chunk)
        position += len(chunk)
        url, anchor_parts = parser.links[int(match.group(1))]
        links.append(Link(url=url, text=" ".join("".join(anchor_parts).split()), offset=position))
        last = match.end()
    text_parts.append(marked[last:])
    return ExtractedBody(text="".join(text_parts), links=tuple(links))


def text_to_body(text: str) -> ExtractedBody:
    normalized = normalize_whitespace(text)
    links = []
    for match in _URL_RE.finditer(normalized):
        url = match.group(0).rstrip(_TRAILING_PUNCTUATION)
        links.append(Link(url=url, text="", offset=match.start()))
    return ExtractedBody(text=normalized, links=tuple(links))
