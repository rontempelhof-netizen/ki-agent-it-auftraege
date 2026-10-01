# Task 01 – Projektgrundgerüst

Lies `CLAUDE.md` und `docs/requirements.md` vollständig.

## Ziel
Erstelle eine lokal lauffähige, saubere Python-Basis für den KI-Agenten.

## Implementiere
- Python-3.12+-Projekt mit `pyproject.toml`
- `src/`, `tests/`, `config/`
- Pydantic-basierte Konfiguration
- SQLite + SQLAlchemy Initialisierung
- strukturiertes Logging
- `.env.example` und `.gitignore`
- pytest-Konfiguration
- Dockerfile
- README mit Setup, Start und Test
- minimale CLI, die Start/Config/DB-Initialisierung demonstriert

## Noch nicht implementieren
- Portalzugriffe
- E-Mail-Ingestion
- LLM
- Scoring-Geschäftslogik
- Web-Frontend/FastAPI

## Akzeptanzkriterien
1. Frischer Checkout lässt sich nach README installieren.
2. Anwendung startet lokal.
3. SQLite-Datenbank kann initialisiert werden.
4. YAML/Environment-Konfiguration funktioniert.
5. Tests laufen erfolgreich.
6. Keine Secrets im Repository.

Führe Tests selbst aus und dokumentiere Ergebnis und offene Punkte.
