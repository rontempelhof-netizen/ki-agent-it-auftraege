# Task 02 – Lead-Modell, Persistenz und Scoring

Lies `CLAUDE.md`, `docs/requirements.md` und vorhandenen Code.

## Ziel
Implementiere den fachlichen Kern ohne LLM und ohne externe Quelle.

## Implementiere
- Pydantic-Modelle für RawSourceItem, LeadCandidate, LeadAnalysis und Lead
- SQLAlchemy-Persistenz für Leads, Quellen, Score-Details, Crawl-Runs und Feedback
- Repository-Schicht
- deterministische Hard-Fail-Regeln
- deterministische Score Engine gemäß Requirements
- A/B/C/Reject-Klassifizierung
- Statusmodell
- realistische Test-Fixtures

## Tests
Mindestens:
- vollständiger Lead
- fehlende optionale Daten
- Hard Fail
- bekannte Score-Testfälle
- Grenzen 49/50/64/65/79/80/100
- Speichern und Laden aus SQLite
- Score-Breakdown ergibt nachvollziehbar Gesamtwert

## Nicht implementieren
LLM, echte Portale, Mailversand, Frontend.

Alle Tests selbst ausführen und Ergebnis dokumentieren.
