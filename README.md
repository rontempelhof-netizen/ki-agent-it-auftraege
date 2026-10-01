# KI-Agent IT-Aufträge

Python-Agent, der passende kleine IT-Freelancer- und Beratungsaufträge findet, bewertet,
in SQLite speichert und die besten neuen Leads per HTML-E-Mail meldet.

> Stand: **Task 05 – End-to-End-Vertical-Slice.** Projektmails werden eingelesen, normalisiert,
> dedupliziert, vorgefiltert, per LLM analysiert, deterministisch bewertet, in SQLite gespeichert
> und als HTML-Tagesreport ausgegeben (Versand im Dry-Run oder per SMTP). Produktive
> IMAP-/Gmail-Anbindung und echte LLM-Aufrufe sind konfigurierbar, aber noch nicht im Betrieb erprobt.

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
python -m src.main --help                  # Übersicht
python -m src.main show-config             # wirksame Konfiguration als JSON
python -m src.main init-db                 # SQLite-Datenbank anlegen (idempotent)

python -m src.main crawl                   # Quellen lesen -> analysieren -> speichern -> Report
python -m src.main crawl --source email    # nur eine Quelle (Connector-Name)
python -m src.main crawl --fake-llm        # ohne API-Kosten (Fake-LLM)
python -m src.main crawl --max-llm 5       # LLM-Analysen in diesem Lauf begrenzen
python -m src.main crawl --no-ack          # Mails nicht als verarbeitet markieren (wiederholbar)
python -m src.main crawl --send            # Report zusätzlich versenden (gemäß mail.mode)
python -m src.main crawl --no-report       # keinen HTML-Report erzeugen

python -m src.main report                  # Report des letzten Laufs als HTML-Datei
python -m src.main report --run-id 3 --output report.html --send
python -m src.main test-mail --to ich@example.org   # Test-Mail (Standard: Dry-Run)
```

Exit-Codes: `0` Erfolg, `1` teilweise erfolgreich (z. B. eine Quelle fehlerhaft), `2` Fehler.
Standardmäßig liegen Datenbank, Reports und Dry-Run-Mails unter `data/` (git-ignoriert).

## Demo (offline, ohne API-Key)

Das Beispielpostfach `examples/demo/inbox` enthält realistische Projektmails (freelancermap,
freelance.de, eine erneut zugestellte Mail, ein bereits bekanntes Projekt, eine Festanstellung,
einen unbekannten Absender und eine defekte Mail). Das Fake-LLM liefert dazu skriptgesteuerte
Antworten aus `examples/demo/fake_llm_responses.json`; die Scores berechnet die echte Score Engine.

```bash
python -m src.main --config config/demo.yaml crawl --send
# Lauf #1: SUCCESS | Mails 6 | neu 6 | Duplikate 1 | Prefilter 1 | LLM 5 | A 1 B 2 C 1 Reject 2 | ausstehend 0
# Report: data/demo/reports/report-run-1.html      <- im Browser öffnen
# Mail (Dry-Run) abgelegt: data/demo/outbox/....eml – nichts versendet

python -m src.main --config config/demo.yaml crawl   # zweiter Lauf: nichts Neues, keine LLM-Aufrufe
```

Zurücksetzen: Ordner `data/demo` löschen. Der End-to-End-Test (`tests/test_pipeline_e2e.py`)
verwendet genau diese Demo-Daten.

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
| `llm.provider`     | `AGENT_LLM__PROVIDER`    | `anthropic` (`fake` = offline) |
| `llm.model`        | `AGENT_LLM__MODEL`       | `claude-opus-5-5`          |
| API-Key            | `ANTHROPIC_API_KEY`      | – (nur Environment/`.env`) |

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

## Quellen: E-Mail-Connector

Alle Connectoren implementieren `src.sources.base.SourceConnector` (`fetch()` →
`FetchResult`, optional `acknowledge()`); `safe_fetch()` kapselt Fehler, damit eine
defekte Quelle andere nicht stoppt. Connectoren liefern nur `RawSourceItem`s; sie
normalisieren, bewerten und speichern nichts.

Der `EmailSourceConnector` (`src/sources/email/`) verarbeitet Projektbenachrichtigungen:

1. **Postfach** (`mailbox.py`): derzeit offline ein Verzeichnis mit `.eml`-Dateien
   (`sources.email.mailbox_dir`, Standard `data/inbox`). Eine IMAP/Gmail-Anbindung
   implementiert später dasselbe `Mailbox`-Protokoll.
2. **Parsen** (`parser.py`): Absender, Betreff, Datum, Text- und HTML-Teil; defekte Header,
   unbekannte Zeichensätze oder abgeschnittene Multipart-Mails werden als Warnung gemeldet.
3. **Deduplizierung**: über Message-ID (sonst Inhalts-Hash) innerhalb eines Laufs und über
   Läufe hinweg via `SeenMessageStore` (SQLite: Tabelle `processed_emails`). Nachrichten
   gelten erst nach `acknowledge()` als verarbeitet, also nach erfolgreicher Weiterverarbeitung.
4. **Quellzuordnung** über Parserprofile in `config/settings.yaml` (`sources.email.profiles`):
   Absender-/Betreffmuster, Muster für Projekt-URLs und Projekt-ID, Layout
   (`link_start`: Block beginnt mit dem Projektlink, `link_end`: Block endet mit ihm),
   Marker für Inhaltsbeginn und Footer. Mitgeliefert: `freelancermap`, `freelance.de`.
5. **Projektblöcke**: Jede erkannte Projekt-URL ergibt einen `RawSourceItem`; mehrere Links
   auf dasselbe Projekt (Titel, Button, Tracking-Parameter) werden zusammengefasst.
   Ohne erkennbare Projektlinks wird die ganze Mail als ein Eintrag übernommen.
6. **Unbekannte Absender**: `unknown_sender_policy: skip` (Standard, mit Warnung) oder
   `generic` (ganze Mail als Eintrag mit Quelle `email_unknown`).

Mailinhalte sind untrusted input und werden unverändert als Daten weitergegeben.

## LLM-Analyse

Das LLM extrahiert und schätzt ausschließlich **Merkmale** (`LeadAnalysis`); der Score wird
danach deterministisch in Python berechnet. Code in `src/llm/`:

- `provider.py`: anbieterneutraler Vertrag `LLMProvider.complete(LLMRequest) -> LLMResponse`
  mit eigener Fehlerhierarchie (`retryable` je Fehlerart) und optionalen Token-/Kostenangaben.
- `anthropic_provider.py`: Adapter für die Claude API (einziger Ort mit SDK-Abhängigkeit,
  wird erst bei `provider: anthropic` geladen). Nutzt Structured Outputs
  (`output_config.format`) und `effort`. Ein server-seitiger Refusal-Fallback auf ein anderes
  Modell ist optional (`llm.refusal_fallback`, Standard `false` für reproduzierbare Ergebnisse).
- `fake.py`: `FakeLLMProvider` für Tests und Demos – offline, ohne API-Key.
- `prompts.py`: versionierter System-Prompt (`PROMPT_VERSION` + Hash des Profils),
  Nutzernachricht mit `<quelldaten>`/`<auftragstext>`-Delimitern, striktes JSON-Schema
  (abgeleitet aus `LeadAnalysis`, alle Felder Pflicht, Werte nullable, keine Zusatzfelder).
- `validation.py`: strikte Pydantic-Validierung; Antworten mit Entscheidungsfeldern
  (`score_total`, `classification`, …) sind ungültig. **Grounding:** Budget, Währung,
  Zertifizierungen, Aufwand und Sicherheitsüberprüfung, die nicht im Quelltext belegt sind,
  werden verworfen (None) und als `grounding_issues` gemeldet.
- `service.py`: `LeadAnalysisService.analyze(candidate) -> AnalysisResult` mit
  Prompt-Version, Provider, tatsächlichem Modell, Versuchen und Usage/Kosten.

**Retry** nur bei technischen Fehlern (Timeout, Rate Limit, Verbindungs-/Serverfehler,
abgeschnittene Antwort; mit Backoff) und strukturell ungültigen Antworten (kein JSON,
Schemafehler, Entscheidungsfelder; mit Korrekturhinweis ohne Antwort-Echo). Eine gültige
Analyse wird nie wiederholt; Ablehnungen und Auth-/Requestfehler ebenfalls nicht.

**Sicherheit:** Auftragstexte sind untrusted input. Systemregeln stehen nur im System-Prompt;
Delimiter-Tags im Auftragstext werden neutralisiert; an das LLM gehen nur Titel, Beschreibung
und strukturierte Quelldaten (keine Mail-Metadaten, keine URLs). Logs enthalten nur Metadaten
(Modell, Prompt-Version, Tokens, Kosten), keine Inhalte. `Lead` speichert `prompt_version`
und `llm_model`.

Ohne API-Key lokal arbeiten: `AGENT_LLM__PROVIDER=fake`.

## Pipeline

`src/pipeline/runner.py` orchestriert alle Schritte; die Komponenten kennen sich nicht und werden
in `src/app.py` aus der Konfiguration zusammengesetzt.

```
Lauf starten (crawl_runs)
 1. ausstehende Analysen früherer Läufe (pending_analysis, älteste zuerst, im LLM-Limit)
 2. je Connector: safe_fetch()  -> Fehler der Quelle = Laufeintrag, andere Quellen laufen weiter
 3. je RawSourceItem:
      Normalize      -> LeadCandidate (eindeutige Budget-/Remote-Angaben deterministisch)
      Basic Dedup    -> bekannte Quelle+Source-ID oder URL: nur Sichtung (last_seen), KEIN LLM
      Prefilter      -> deterministische Ausschlüsse, gespeichert als REJECT/prefiltered, KEIN LLM
      LLM-Limit      -> darüber: als pending_analysis speichern (geht nicht verloren)
      LLM Analysis   -> LeadAnalysisService (Validierung, Grounding)
      Score          -> merge_source_facts + ScoreEngine (deterministisch)
      SQLite         -> eigene Transaktion je Lead
 4. acknowledge(): nur Nachrichten, deren Leads alle gespeichert wurden
Lauf abschließen (Status SUCCESS/PARTIAL/FAILED, Zähler, Warnungen, Fehler)
```

- **LLM-Fehler:** Der Kandidat wird als `pending_analysis` mit Fehlertext gespeichert und im
  nächsten Lauf erneut analysiert; nach `pipeline.max_analysis_failures` Versuchen `analysis_failed`.
- **Datenbankfehler** bei einem Lead: nur dieser Lead fehlt, seine Mail wird nicht bestätigt und
  im nächsten Lauf erneut gelesen; bereits gespeicherte Leads derselben Mail sind dann Duplikate.
- **Gespeichert je Lead:** Quelldaten, Analyse (JSON), Score und Breakdown (`lead_score_details`),
  Hard-Fail-Gründe, Prompt-Version, LLM-Modell, Verarbeitungsstatus und -fehler, Lauf-ID,
  First Seen / Last Seen (auch je Quellvorkommen in `lead_sources`).

### Prefilter: was ohne LLM zuverlässig geht

| Regel | Ohne LLM zuverlässig? |
|---|---|
| Reine Festanstellung ("Festanstellung", "unbefristete Anstellung", "permanent position") | ja, mit Verneinungsprüfung ("keine Festanstellung" wird nicht gefiltert) |
| Arbeitnehmerüberlassung ("Arbeitnehmerüberlassung", "ANÜ") | ja, mit Verneinungsprüfung |
| Ausdrücklicher Stunden-/Tagessatz in EUR unter `scoring.hard_fail.min_day_rate_eur` | ja, nur bei eindeutigem Muster wie "35 €/h" |
| Eigene Ausschlussmuster (`prefilter.exclude_patterns`) | ja (vom Nutzer verantwortet) |
| Pflichtzertifizierungen, Sicherheitsüberprüfung | **nein** – Muss vs. "von Vorteil" braucht Kontext → LLM + Hard Fail |
| Projektumfang, Präsenzpflicht/Region, Haftung, Länge der Muss-Liste | **nein** → LLM + Hard Fail |
| Festpreis ohne Aufwandsangabe | **nein** (Tagessatz nicht bestimmbar) → Scoring |

### Report und Versand

Der Report (`src/report/`, Jinja2 mit HTML-Autoescape) enthält Lauf-ID und Zeitpunkt, gelesene
Mails, neue Kandidaten, Duplikate, Prefilter-Rejects, LLM-Analysen, A/B/C/Reject-Zahlen,
ausstehende Analysen, geschätzte LLM-Kosten sowie Quellenfehler und Warnungen. Je A-/B-Lead:
Score, Titel, Auftraggeber, Quelle, Kategorie, Remote/Standort, Budget, Aufwand, Kurzfassung,
Fit-Begründung, Muss-Anforderungen, Risiken, offene Fragen, nächster Schritt, Erstansprache
(nur Entwurf) und Original-Link (nur http/https). A vor B, innerhalb der Klasse nach Score
absteigend; C und Reject werden gespeichert, aber nur gezählt (`report.detail_classes`).

Versand (`src/notify/mailer.py`): `mail.mode: dry_run` (Standard) legt die Mail nur als `.eml` in
`mail.outbox_dir` ab. `smtp` versendet an `mail.recipients` (nur eigene Adressen); das Passwort
ausschließlich per `AGENT_MAIL__SMTP_PASSWORD`. Es werden nie Kunden kontaktiert.

## Logging

Strukturiertes Logging nach `stderr`: im Format `json` eine JSON-Zeile pro Eintrag,
im Format `text` eine lesbare Zeile mit angehängten `key=value`-Feldern.

## Tests

```bash
pytest
```

Die Tests laufen vollständig offline (Netzzugriffe sind in Tests blockiert, kein API-Key nötig)
und verwenden temporäre Verzeichnisse/Datenbanken.

Bestehende Datenbanken aus älteren Schemaständen werden von `init-db` erkannt
(`SchemaMismatchError`); bis zur Einführung von Migrationen die Datei `data/agent.db` neu anlegen.

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
  sources/         Connector-Vertrag; email/: Postfach, Parser, Projekt-Splitting, Connector
  llm/             Provider-Vertrag, Anthropic-Adapter, Fake, Prompt, Validierung, Service
  pipeline/        Normalisierung, Prefilter, Orchestrierung (runner.py)
  report/          Report-Ansichtsmodell, Jinja2-Template, HTML/Text-Rendering
  notify/          Mail-Versand (Dry-Run/SMTP)
  app.py           Verdrahtung der Komponenten aus der Konfiguration
examples/demo/     Demo-Postfach und Fake-LLM-Antworten (auch vom E2E-Test genutzt)
  storage/         Base/Namenskonvention, ORM-Tabellen, Engine/Sessions, Repositories
tasks/             Umsetzungsschritte
tests/             pytest-Tests
  fixtures/        realistische Leads mit handgerechneten Score-Erwartungen
  fixtures/emails/ .eml-Fixtures (freelancermap, freelance.de, unbekannt, Duplikat, defekt)
```
