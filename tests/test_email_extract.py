from __future__ import annotations

from src.sources.email.extract import html_to_body, normalize_whitespace, text_to_body


def test_normalize_whitespace():
    raw = "  Zeile  1\t\r\n\r\n\r\n   Zeile 2  \n\n"
    assert normalize_whitespace(raw) == "Zeile 1\n\nZeile 2"


def test_html_text_links_and_offsets():
    html = """<html><head><title>T</title><style>.x{color:red}</style></head><body>
    <p>Hallo &amp; willkommen</p>
    <h2><a href="https://example.org/projekt/a-1">Projekt   A</a></h2>
    <p>Remote&nbsp;| Start: sofort</p>
    <a href="https://example.org/projekt/a-1?x=1">Projekt ansehen</a>
    <a href="mailto:info@example.org">Mail</a>
    <script>track()</script></body></html>"""

    body = html_to_body(html)

    assert "Hallo & willkommen" in body.text
    assert "track()" not in body.text and "color:red" not in body.text
    assert [(link.url, link.text) for link in body.links] == [
        ("https://example.org/projekt/a-1", "Projekt A"),
        ("https://example.org/projekt/a-1?x=1", "Projekt ansehen"),
    ]
    for link in body.links:
        assert body.text[link.offset :].startswith(link.text)


def test_html_table_cells_are_separated():
    body = html_to_body("<table><tr><td>Ort:</td><td>Remote</td></tr><tr><td>Dauer:</td><td>5 Tage</td></tr></table>")
    assert body.text == "Ort: Remote\nDauer: 5 Tage"


def test_html_broken_markup_does_not_fail():
    body = html_to_body('<p>Text <a href="https://example.org/x">offen <b')
    assert "Text" in body.text
    assert body.links[0].url == "https://example.org/x"


def test_text_urls_with_trailing_punctuation():
    body = text_to_body("Siehe https://example.org/projekt/1. Oder (https://example.org/projekt/2), danke")

    assert [link.url for link in body.links] == ["https://example.org/projekt/1", "https://example.org/projekt/2"]
    assert all(body.text[link.offset :].startswith(link.url) for link in body.links)
