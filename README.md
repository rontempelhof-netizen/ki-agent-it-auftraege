# KI-Agent IT-Aufträge

Python-Agent, der passende kleine IT-Freelancer- und Beratungsaufträge findet, bewertet,
in SQLite speichert und die besten neuen Leads per HTML-E-Mail meldet.

> Stand: **Task 02 – Lead-Modell, Persistenz und Scoring.** Vorhanden sind Konfiguration,
> Logging, CLI-Grundgerüst, Domänenmodelle, SQLite-Persistenz mit Repository-Schicht,
> deterministische Hard-Fail-Regeln, Score Engine und A/B/C/REJECT-Klassifizierung.
> Quellen, LLM-Analyse und Report folgen in den nächsten Tasks (`tasks/`).

## Voraussetzungen

- Python 3.12 oder neuer
- optional: Docker

## Setup

```bash
git clone <repo-url> ki-agent-it-auftraege
cd ki-agent-it-auftraege

python -m venv .venv
# Linux/macOS:
source .venv/bin/activate
# Windows (PowerShell):
.venv\Scripts\Activate.ps1

pip install -e ".[dev]"

# optional: lokale Overrides/Secrets
cp .env.example .env
```

## Start

Alle Befehle aus dem Projektverzeichnis ausführen:

```bash
python -m src.main --help          # Übersicht
python -m src.main show-config     # wirksame Konfiguration als JSON
python -m src.main init-db         # SQLite-Datenbank anlegen (idempotent)
python -m src.main --config pfad/zu/settings.yaml show-config
```

Standardmäßig wird die Datenbank unter `data/agent.db` angelegt (`data/` ist git-ignoriert).

## Konfiguration

Die Konfiguration ist Pydantic-basiert (`src/config.py`). Priorität, höchste zuerst:

1. Environment-Variablen mit Präfix `AGENT_`, Verschachtelung über `__`
   (z. B. `AGENT_DATABASE__URL`, `AGENT_LOGGING__LEVEL`)
2. `.env`-Datei im Projektverzeichnis
3. YAML-Datei `config/settings.yaml` (alternativ `--config PATH` oder `AGENT_CONFIG_FILE`)
4. Defaults im Code

| Schlüssel          | Env-Variable             | Default                    |
|--------------------|--------------------------|----------------------------|
| `environment`      | `AGENT_ENVIRONMENT`      | `development`              |
| `database.url`     | `AGENT_DATABASE__URL`    | `sqlite:///data/agent.db`  |
| `database.echo`    | `AGENT_DATABASE__ECHO`   | `false`                    |
| `logging.level`    | `AGENT_LOGGING__LEVEL`   | `INFO`                     |
| `logging.format`   | `AGENT_LOGGING__FORMAT`  | `json` (`json` \| `text`)  |
| `scoring.*`        | z. B. `AGENT_SCORING__THRESHOLDS__A` | siehe `config/settings.yaml` |

Secrets gehören ausschließlich in `.env` bzw. Environment-Variablen, niemals in
`config/settings.yaml` oder ins Repository. `.env.example` enthält nur Platzhalter.

## Datenmodell

Datenfluss: `RawSourceItem -> LeadCandidate -> LeadAnalysis -> ScoreResult -> Lead`
(Pydantic, `src/domain/models.py`). Unbekannte Informationen bleiben `None` bzw. leere Liste.

- `LeadAnalysis` enthält nur Merkmale (z. B. `scope_clarity`, `technical_fit`,
  `customer_type`), **keinen Score**; zusätzliche Felder werden abgelehnt.
- `merge_source_facts(candidate, analysis)` überträgt strukturierte Quelldaten
  (Budget, Remote-Status, Ort, Sprache, Kunde) in die Analyse; Quelldaten haben Vorrang.
  Gescored wird immer die zusammengeführte Analyse.
- Statusfluss (`src/domain/status.py`): `NEW -> REVIEWED -> INTERESTING -> CONTACTED ->
  RESPONSE -> MEETING -> OFFER -> WON/LOST`; `REJECTED` aus jedem offenen Status,
  `LOST` ab `CONTACTED`. Ungültige Übergänge werfen `InvalidStatusTransitionError`.

SQLite-Tabellen (`src/storage/orm.py`): `leads`, `lead_sources` (Vorkommen eines Leads je
Quelle), `lead_score_details` (ein Eintrag je Kriterium), `crawl_runs`, `feedback`, `app_meta`.
Zugriff ausschließlich über `src/storage/repository.py`.

### Migrationsfähigkeit

Das Schema ist so angelegt, dass später ein Migrationswerkzeug wie Alembic ergänzt werden
kann, ohne die Architektur umzubauen:

- Alle Tabellen hängen an einer gemeinsamen Metadata (`src.storage.base.Base.metadata`)
  und werden in einem Modul definiert (`src.storage.orm`) → `target_metadata = Base.metadata`.
- Feste Namenskonvention für Primär-/Fremdschlüssel, Unique-Constraints und Indizes
  (z. B. `uq_leads_source_source_id`), nötig für Autogenerate und SQLite-Batch-Migrationen.
- Enums werden als Strings gespeichert (keine nativen Enum-/CHECK-Typen); neue Werte
  brauchen keine Migration. Zeitpunkte werden als UTC gespeichert (`UTCDateTime`).
- Schemaanlage nur in `init_db()` (`src/storage/database.py`). Aktuell `create_all` +
  Schema-Version in `app_meta`; `create_all` legt nur fehlende Tabellen an und ändert
  keine bestehenden. Mit Alembic würde nur diese Funktion durch `alembic upgrade head` ersetzt.

## Scoring

Der Score (0–100) wird deterministisch in Python berechnet (`src/scoring/`); das LLM
liefert nur Merkmale.

| Kriterium | Max | Grundlage |
|---|---|---|
| `scope` | 20 | 50 % Umfang (Personentage bzw. `scope_size`), 50 % Klarheit |
| `deliverability` | 15 | `technical_fit` |
| `win_probability` | 20 | `entry_barrier` (invers), Abzug 25 % bei > 8 Muss-Anforderungen |
| `budget_effort` | 15 | impliziter Tagessatz in EUR (≥ 800 / ≥ 600 / ≥ 300) bzw. Festpreis im Erstauftragsrahmen |
| `direct_customer` | 10 | 70 % Kundenart, 30 % Entscheidungskomplexität (invers) |
| `remote_fit` | 5 | Arbeitsmodus × Region |
| `urgency` / `follow_up` / `consulting_fit` | je 5 | jeweilige Einschätzung |

- Stufen: `high` = 100 %, `medium` = 50 %, `low` = 0 % der Punkte; **unbekannt** =
  `unknown_fraction` (Standard 40 %). Punkte je Kriterium werden kaufmännisch gerundet,
  der Gesamtscore ist exakt die Summe der Aufschlüsselung. Jedes Kriterium hat eine Begründung.
- Klassen: **A** ≥ 80, **B** ≥ 65, **C** ≥ 50, sonst **REJECT** (Grenzen inklusive).
- **Hard Fails** setzen die Klasse immer auf REJECT; der berechnete Score bleibt zur
  Nachvollziehbarkeit erhalten. Regeln: fehlende Pflicht-Zertifizierung,
  Sicherheitsüberprüfung, Festanstellung, Personalüberlassung, Präsenzpflicht außerhalb
  erlaubter Regionen, Tagessatz < 300 EUR, > 60 PT bzw. `very_large`, > 15 Muss-Anforderungen,
  hohe Haftung bei unklarem Scope. Fehlende Informationen lösen nie einen Hard Fail aus.
- Gewichte, Schwellen, Grenzwerte und Wechselkurse sind in `config/settings.yaml`
  (Abschnitt `scoring`) konfigurierbar; die Gewichte müssen 100 ergeben.

## Logging

Strukturiertes Logging nach `stderr`: im Format `json` eine JSON-Zeile pro Eintrag,
im Format `text` eine lesbare Zeile mit angehängten `key=value`-Feldern.

## Tests

```bash
pytest
```

Die Tests laufen vollständig offline und verwenden temporäre Verzeichnisse/Datenbanken.

## Docker

```bash
docker build -t ki-agent-it-auftraege .
docker run --rm -v "$(pwd)/data:/app/data" ki-agent-it-auftraege init-db
docker run --rm --env-file .env ki-agent-it-auftraege show-config
```

Die SQLite-Datei liegt im Container unter `/app/data` und sollte per Volume persistiert werden.

## Projektstruktur

```
config/            YAML-Konfiguration
docs/              Anforderungen
src/
  main.py          CLI-Einstiegspunkt (python -m src.main)
  config.py        Pydantic-Settings (YAML + .env + Environment)
  logging_setup.py strukturiertes Logging
  domain/          Pydantic-Modelle, Enums, Statusfluss
  scoring/         Hard Fails, Budget/Tagessatz, Score Engine, Klassifizierung
  storage/         Base/Namenskonvention, ORM-Tabellen, Engine/Sessions, Repositories
tasks/             Umsetzungsschritte
tests/             pytest-Tests
  fixtures/        realistische Leads mit handgerechneten Score-Erwartungen
```
