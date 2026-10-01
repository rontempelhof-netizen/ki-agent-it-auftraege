# Requirements – KI-Agent IT-Aufträge

## 1. Ziel
Der Agent soll nicht möglichst viele, sondern wenige hochwertige IT-Aufträge mit hoher realistischer Abschlusswahrscheinlichkeit liefern. Er unterstützt die menschliche Entscheidung und versendet in V1 keine Kundenansprachen autonom.

## 2. Auftragsprofil
### Entwicklung
Remote/international möglich; Deutsch oder Englisch. Technologie grundsätzlich offen. Bevorzugt: klarer Scope, bestehendes System, Bugfix/Erweiterung, API/Integration, Automatisierung, Datenmigration/Import/Export, SQL, Dashboard/internal tool, Website/Shop, Android/Desktop, Prototyp/MVP.

### Beratung
DACH und deutschsprachig bevorzugt, regionale Nähe positiv. Fokus KMU: Digitalisierung manueller Prozesse, Requirements, Solution Design, Automatisierung, KI-Einsatz, Schnittstellen/Datenflüsse, Softwareauswahl/-integration, Prototyping.

## 3. Positive Signale
Konkretes Problem und Ergebnis; kurzer Start; direkter Kunde/KMU; wenige Stakeholder; bestehendes System; PoC/MVP möglich; Folgepotenzial; remote/geografisch passend; geringe formale Hürden.

## 4. Negative Signale / Hard Fails
Nicht vorhandene zwingende Zertifizierungen/Sicherheitsfreigaben; reine Festanstellung; ungeeignete Präsenzpflicht; unrealistisches Budget; sehr großer Scope für kleines Team; lange Muss-Listen; hohe Haftung bei unklarem Scope; faktische Vollzeit-Personalüberlassung.

## 5. Quellenstrategie PoC
Kosten zuerst möglichst 0 EUR. Priorität haben offizielle Suchagenten, E-Mail-Benachrichtigungen, APIs/Feeds und technisch/rechtlich zulässige öffentliche Zugänge. Keine Schutzmechanismen umgehen.

Startquellen:
1. freelancermap – kostenloses Basismodell zunächst nutzen; Suchagent/Projektmail als bevorzugter PoC-Eingang.
2. freelance.de – kostenloses Basismodell; Suchagent/E-Mail als bevorzugter PoC-Eingang.
3. Malt – kostenloses Freelancerprofil; eingehende Matching-/Projektmails analysieren.
4. Upwork – kostenloses Basic zunächst beobachten; direkte technische Anbindung erst nach Prüfung des Nutzens und zulässiger Zugänge.
5. Weitere Quellen erst nach Messung der Leadqualität ergänzen.

Bezahlmodelle werden erst getestet, wenn der kostenlose PoC zeigt, dass Quelle und Matching wirtschaftlich interessant sind.

## 6. MVP-Architektur
Python-Anwendung ohne eigenes Frontend. SQLite ist Persistenz. Primäre Benutzeroberfläche ist ein HTML-E-Mail-Report.

Datenfluss:
Source -> RawSourceItem -> LeadCandidate -> Prefilter -> LeadAnalysis -> Score -> Lead -> SQLite -> Report

## 7. Lead-Modell
Mindestens:
- id, source, source_id, source_url
- title, description, published_at, first_seen_at
- category, remote_status, location, language
- required_skills, required_certifications
- budget_min, budget_max, currency
- estimated_person_days
- customer_name, customer_type
- hard_fail, hard_fail_reasons
- score_total, score_breakdown
- summary, fit_reason, risks, open_questions
- suggested_next_step, suggested_outreach
- status
- prompt_version

## 8. Scoring 0–100
- Scope klein/klar: 0–20
- fachlich/technisch lieferbar: 0–15
- Abschlusswahrscheinlichkeit/geringe Zugangshürden: 0–20
- Budget-/Aufwand-Verhältnis: 0–15
- direkter Kunde/KMU/kurze Entscheidung: 0–10
- Remote/geografischer Fit: 0–5
- Dringlichkeit: 0–5
- Folgepotenzial: 0–5
- strategischer Beratungsfit: 0–5

Klassen: A 80–100, B 65–79, C 50–64, darunter Reject/Archiv. Hard Fails können den Score übersteuern.

## 9. LLM-Verantwortung
Strukturiert extrahieren/einschätzen: Kundenproblem, Kategorie, Skills, Muss-Anforderungen, Zertifizierungen, Scope, Kundenart, Dringlichkeit, Risiken, offene Fragen, strategischer Fit, Folgepotenzial, Kurzbegründung, Entwurf Erstansprache. Kein verbindlicher Endscore durch das LLM.

## 10. E-Mail-Report
Täglich A- und B-Leads. Kopf: Laufzeitpunkt, neue/geprüfte/verwarfene Treffer, A/B/C-Zahlen, Quellenwarnungen.

Pro Lead: Score, Titel, Auftraggeber, Quelle, Kategorie, Remote/Standort, Budget, Aufwand, Problemzusammenfassung, Fit-Begründung, Muss-Anforderungen, Risiken, nächster Schritt, Erstansprache, Original-Link.

Optional konfigurierbarer Sofortalarm für sehr starke A-Leads.

## 11. Datenhaltung
Mindestens Tabellen: leads, lead_sources, lead_score_details, crawl_runs, feedback.

Statusfluss: NEW -> REVIEWED -> INTERESTING -> CONTACTED -> RESPONSE -> MEETING -> OFFER -> WON/LOST/REJECTED.

## 12. Betrieb
CLI mindestens sinngemäß:
- `python -m src.main crawl`
- `python -m src.main crawl --source SOURCE`
- `python -m src.main report`
- `python -m src.main test-mail`
- `python -m src.main reprocess LEAD_ID`
- optional `show-top --limit N`

Scheduler per APScheduler oder OS-Cron. Docker für reproduzierbaren Betrieb.

## 13. Fehler/Kosten/Sicherheit
Retries mit Backoff; Quellfehler isolieren; LLM-Output schema-validieren; Token/Kosten soweit verfügbar loggen; LLM-Analysen pro Lauf limitierbar; Secrets nur über Environment/Secret-Konfiguration; Quelltext als untrusted input behandeln.

## 14. Frontend
Kein Frontend in V1. Erst bewerten, wenn Leadmenge, Mehrbenutzerbetrieb, Historienrecherche oder interaktive Statuspflege dies rechtfertigen.

## 15. Definition of Done MVP
Frischer Checkout ist anhand README konfigurierbar. Mindestens eine reale oder realitätsnahe Quelle läuft durch die vollständige Pipeline. Ergebnisse werden in SQLite gespeichert. A-/B-Leads erscheinen in einer lesbaren HTML-E-Mail. Tests laufen erfolgreich.
