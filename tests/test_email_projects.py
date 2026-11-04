from __future__ import annotations

import pytest

from src.config import EmailSourceProfile, EmailSourceSettings
from src.sources.email.extract import html_to_body, text_to_body
from src.sources.email.projects import canonical_url, match_profile, project_item_id, split_projects

SETTINGS = EmailSourceSettings()
FREELANCERMAP, FREELANCE_DE, GULP = SETTINGS.profiles
GENERIC = SETTINGS.generic_link_texts

PROFILE = EmailSourceProfile(
    name="portal",
    sender_patterns=[r"@portal\.example$"],
    project_url_patterns=[r"^https://portal\.example/p/\d+"],
    project_id_pattern=r"/p/(\d+)",
    footer_markers=["Abbestellen"],
)


@pytest.mark.parametrize(
    ("sender", "expected"),
    [
        ("projektagent@freelancermap.de", "freelancermap"),
        ("noreply@mail.freelancermap.com", "freelancermap"),
        ("PROJEKTALARM@FREELANCE.DE", "freelance.de"),
        ("info@freelancermap.de.evil.example", None),
        ("newsletter@it-jobs-weekly.example", None),
        (None, None),
    ],
)
def test_match_profile_by_sender(sender, expected):
    profile = match_profile(SETTINGS.profiles, sender.lower() if sender else None, "Betreff")
    assert (profile.name if profile else None) == expected


def test_match_profile_requires_subject_pattern_if_configured():
    profile = EmailSourceProfile.model_validate(PROFILE.model_dump() | {"subject_patterns": ["neue Projekte"]})

    assert match_profile([profile], "x@portal.example", "3 NEUE PROJEKTE") is profile
    assert match_profile([profile], "x@portal.example", "Ihre Rechnung") is None


def test_canonical_url_and_item_id():
    url = canonical_url("HTTPS://WWW.Freelancermap.DE/projekt/python-csv-import-2982210?utm_source=x#top", strip_query=True)

    assert url == "https://www.freelancermap.de/projekt/python-csv-import-2982210"
    assert project_item_id(url, FREELANCERMAP) == "2982210"
    assert canonical_url("https://a.example/p?id=5", strip_query=False) == "https://a.example/p?id=5"


def test_item_id_falls_back_to_url_hash():
    url = "https://www.freelancermap.de/projekt/ohne-nummer"

    item_id = project_item_id(url, FREELANCERMAP)

    assert item_id.startswith("url-") and item_id == project_item_id(url, FREELANCERMAP)


def test_link_start_multiple_projects_and_multiple_links_per_project():
    html = """<p>Hallo, neue Projekte:</p>
    <h2><a href="https://portal.example/p/1?utm=a">Projekt Eins</a></h2><p>Beschreibung eins</p>
    <a href="https://portal.example/p/1?utm=b">Details</a> <a href="https://docs.example/lastenheft.pdf">Lastenheft</a>
    <h2><a href="https://portal.example/p/2">Projekt Zwei</a></h2><p>Beschreibung zwei</p>
    <a href="https://portal.example/p/2">Details</a>
    <p>Abbestellen: <a href="https://portal.example/unsubscribe">hier</a></p>"""

    blocks = split_projects(html_to_body(html), PROFILE, "link_start", GENERIC)

    assert [(b.item_id, b.title, b.url) for b in blocks] == [
        ("1", "Projekt Eins", "https://portal.example/p/1"),
        ("2", "Projekt Zwei", "https://portal.example/p/2"),
    ]
    assert "Beschreibung eins" in blocks[0].text and "Beschreibung zwei" not in blocks[0].text
    assert "Abbestellen" not in blocks[1].text and "Hallo" not in blocks[0].text
    assert blocks[0].links == (
        "https://portal.example/p/1?utm=a",
        "https://portal.example/p/1?utm=b",
        "https://docs.example/lastenheft.pdf",
    )
    assert "https://portal.example/unsubscribe" not in blocks[1].links


def test_generic_link_text_falls_back_to_block_text():
    html = '<p><a href="https://portal.example/p/7">Details</a></p><p>Titel aus Text</p>'
    body = html_to_body(html)

    block = split_projects(body, PROFILE, "link_start", GENERIC)[0]

    assert block.title == "Titel aus Text"


def test_link_end_layout_with_separators_and_enumeration():
    text = """Hallo,
wir haben 2 neue Projekte gefunden:
-----
1) Erstes Projekt
Beschreibung A
Link: https://portal.example/p/10
-----
2) Zweites Projekt
Beschreibung B
https://portal.example/p/11?ref=x
Link: https://portal.example/p/11
-----
Abbestellen: https://portal.example/unsubscribe
"""
    profile = PROFILE.model_copy(update={"content_start_markers": FREELANCE_DE.content_start_markers})

    blocks = split_projects(text_to_body(text), profile, "link_end", GENERIC)

    assert [(b.item_id, b.title) for b in blocks] == [("10", "Erstes Projekt"), ("11", "Zweites Projekt")]
    assert blocks[0].text.startswith("1) Erstes Projekt") and "-----" not in blocks[0].text
    assert "Hallo" not in blocks[0].text
    assert "Beschreibung A" not in blocks[1].text
    assert len(blocks[1].links) == 2


def test_same_project_repeated_yields_one_block():
    text = "Projekt X\nhttps://portal.example/p/5\nnochmal: https://portal.example/p/5?utm=1\n"

    blocks = split_projects(text_to_body(text), PROFILE, "link_start", GENERIC)

    assert len(blocks) == 1 and blocks[0].item_id == "5"


def test_no_project_links_returns_empty():
    assert split_projects(text_to_body("Nur https://portal.example/impressum"), PROFILE, "link_start") == []


def test_project_links_in_footer_are_ignored():
    text = "Projekt A\nhttps://portal.example/p/1\nAbbestellen\nÄhnlich: https://portal.example/p/2\n"

    assert [b.item_id for b in split_projects(text_to_body(text), PROFILE, "link_start")] == ["1"]


def test_freelancermap_text_part_title_line_before_url():
    """Text-Fallback: Titelzeile steht direkt vor der URL-Zeile."""
    from src.sources.email.parser import parse_email
    from tests.email_fixtures import FREELANCERMAP_MULTI, load

    text = parse_email(load(FREELANCERMAP_MULTI)).text_body

    blocks = split_projects(text_to_body(text), FREELANCERMAP, FREELANCERMAP.text_layout, GENERIC)

    assert [(b.item_id, b.title) for b in blocks] == [
        ("2981734", "Shopware 6: Fehler im Checkout beheben (Plugin-Konflikt)"),
        ("2982210", "Python-Skript für täglichen CSV-Import in MS SQL"),
        ("2979988", "Senior SAP S/4HANA Berater (m/w/d) – Migration"),
    ]
    assert "Shopware" not in blocks[1].text and "Hallo" not in blocks[0].text
    assert "Sie erhalten diese E-Mail" not in blocks[2].text


def test_freelancermap_current_nproj_links_are_split_and_deduplicated():
    """Anonymisierte Stichprobe: /nproj/ plus Titel- und CTA-Link im selben Digest."""
    html = """
    <h2><a href="https://www.freelancermap.de/nproj/3068473.html?utm_source=mail">Splunk Entwickler</a></h2>
    <p>Ort: Hamburg; Remote</p>
    <a href="https://www.freelancermap.de/nproj/3068473.html?utm_source=cta">Weiter zum Projekt</a>
    <h2><a href="https://www.freelancermap.de/nproj/3068469.html">Dynamics 365 Entwickler</a></h2>
    <p>Ort: Göttingen</p>
    <a href="https://www.freelancermap.de/nproj/3068469.html?tracking=1">Weiter zum Projekt</a>
    <p>Sie erhalten diese Vorschläge auf Basis Ihrer Suche.</p>
    """
    blocks = split_projects(html_to_body(html), FREELANCERMAP, "link_start", GENERIC)

    assert [(b.item_id, b.title) for b in blocks] == [
        ("3068473", "Splunk Entwickler"),
        ("3068469", "Dynamics 365 Entwickler"),
    ]
    assert blocks[0].url == "https://www.freelancermap.de/nproj/3068473.html"
    assert "Hamburg" in blocks[0].text and "Göttingen" not in blocks[0].text
    assert "Sie erhalten diese Vorschläge" not in blocks[-1].text


def test_freelance_de_current_footer_is_excluded():
    html = """
    <p>Ihr Suchagent hat 2 neue Projekte für Sie zusammengestellt:</p>
    <a href="https://www.freelance.de/projekte/projekt-1300010-Android-Developer?utm=x">
        Senior Android Developer
    </a>
    <p>Remote Deutschland</p>
    <a href="https://www.freelance.de/projekte/projekt-1299923-Requirements-Engineer">
        Requirements Engineer
    </a>
    <p>Einstellungen Ihres Suchagenten</p>
    """
    blocks = split_projects(html_to_body(html), FREELANCE_DE, "link_start", GENERIC)
    assert [(b.item_id, b.title) for b in blocks] == [
        ("1300010", "Senior Android Developer"),
        ("1299923", "Requirements Engineer"),
    ]
    assert "Einstellungen Ihres Suchagenten" not in blocks[-1].text


def test_gulp_digest_yields_individual_projects():
    html = """
    <p>Folgende Projekte wurden zu Projektleitung gefunden:</p>
    <h2><a href="https://www.gulp.de/gulp2/g/projekte/talentfinder/6aea24ab482f6840f247c7f1?utm=x">
       1085054 technischer Projektleiter
    </a></h2>
    <p>Beginn 09.11.2026; Marktredwitz</p>
    <h2><a href="https://www.gulp.de/gulp2/g/projekte/agentur/C01359678?utm=x">
       Projektleiter Workday Implementierung
    </a></h2>
    <p>München</p>
    <p>Über Randstad Professional</p>
    """
    blocks = split_projects(html_to_body(html), GULP, "link_start", GENERIC)
    assert [(b.item_id, b.title) for b in blocks] == [
        ("6aea24ab482f6840f247c7f1", "1085054 technischer Projektleiter"),
        ("C01359678", "Projektleiter Workday Implementierung"),
    ]
    assert "Marktredwitz" not in blocks[1].text
    assert "Über Randstad Professional" not in blocks[1].text


def test_gulp_only_matches_project_alert_subject():
    assert match_profile(SETTINGS.profiles, "info.rp@randstad.de", "'Agile Coach': Neue Projekte für Ihre Suche") is GULP
    assert match_profile(SETTINGS.profiles, "info.rp@randstad.de", "Ihre Rechnung") is None
