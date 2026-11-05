# KI-Agent IT-Aufträge

Python-Agent, der passende kleine IT-Freelancer- und Beratungsaufträge findet, bewertet,
in SQLite speichert und die besten neuen Leads per HTML-E-Mail meldet.

> Stand: **Task 05 – End-to-End-Vertical-Slice.** Projektmails werden eingelesen, normalisiert,
> dedupliziert, vorgefiltert, per LLM analysiert, deterministisch bewertet, in SQLite gespeichert
> und als HTML-Tagesreport ausgegeben (Versand im Dry-Run oder per SMTP). Produktive
> Gmail wird nun durch einen rein lesenden API-Adapter unterstützt; für Live-Läufe
> ist eine lokale OAuth-Freigabe nötig. Echte LLM-Aufrufe sind noch nicht im Betrieb erprobt.

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


## Fachliche Kalibrierung: einzelne Projekte gezielt bewerten

Bei vielen eingelesenen Projektalarmen arbeitet der reguläre Crawl die
ausstehenden Projekte chronologisch ab. Für bewusst ausgewählte Testfälle
gibt es zwei separate CLI-Befehle:

```powershell
# Ausstehende Aufträge mit DB-ID anzeigen (ohne API-Aufruf)
python -m src.main pending-leads --limit 20
python -m src.main pending-leads --search "Python" --limit 20
python -m src.main pending-leads --search "Projektmanagement" --limit 20

# Genau einen zuvor ausgewählten Pending-Lead analysieren
python -m src.main analyze-lead --id 42

# Gefahrloser Offline-Test statt Claude
python -m src.main analyze-lead --id 42 --fake-llm
```

Die ID kommt aus der gerade aktiven SQLite-Datenbank
(`AGENT_DATABASE__URL`). Der Einzellauf **liest keine Quellen und
keine E-Mails**, versendet keine E-Mails, fasst keine anderen ausstehenden
Leads an und erzeugt auch keinen automatischen Report. Bereits analysierte,
vorab gefilterte und unbekannte IDs werden **vor** dem LLM-Aufruf abgelehnt.
Jeder Befehl analysiert maximal **einen** Lead; Provider-interne Wiederholungen
werden weiterhin durch `AGENT_LLM__MAX_ATTEMPTS` gesteuert.
Für einen kostengedeckelten Echttest diese Variable auf `1` setzen und
eine separate Testdatenbank verwenden.

Hinweis: `--fake-llm` speichert eine simulierte Bewertung für diesen
Lead. Für eine spätere echte Bewertung deshalb lieber eine Testdatenbank
oder einen noch nicht analysierten Datensatz wählen. Eine Auswahl zeigt
Titel und kurze Beschreibung, die eigentliche Analyse zeigt Klasse,
Score, Zusammenfassung, Passung und geschätzte API-Kosten.

## Zweite Sichtung: fachlich interessant, Angaben klären

Die **bestehende A/B/C/REJECT-Klassifizierung und ihre 100 Punkte bleiben unverändert**.
Um interessante Projekte mit kurzen E-Mail-Ausschreibungen nicht vorschnell aus
den Augen zu verlieren, gibt es zusätzlich eine **rein abgeleitete Klärfallliste**.

Nur Leads mit bereits erfolgreich abgeschlossener KI-Analyse können Klärfälle werden.
Voraussetzungen:

- Klasse REJECT, aber **kein** Hard Fail (nicht fest angestellt, nicht wegen Pflichtzertifikat ausgeschlossen etc.)
- Entweder `technical_fit = high` **oder** `consulting_fit = high`
- Mindestens zwei echte Informationslücken: Budget, Umfang, Kunden-/Vermittlungsart
  oder Zugangshürden
- Ausstehende, gescheiterte und vorgefilterte Leads bleiben ausgeschlossen

Eine Beschreibung mit weniger als 250 Zeichen führt zusätzlich zum Hinweis,
**die Vollständigkeit der Originalausschreibung zu prüfen**. Sie ist kein
Beweis, dass der Originaltext unvollständig ist. Die Quelle wird nicht
automatisch nachgeladen.

Die Klärfälle erscheinen separat im HTML- und Text-Tagesreport, inklusive
Original-Link, **ohne** als A- oder B-Leads zu gelten. Die Auswahl wird bei der
Report-Erstellung aus gespeicherten Analysen berechnet; keine Schemaänderung,
keine neuen API-Aufrufe.

Bereits bewertete Leads aus einer aktiven Datenbank prüfen:

```powershell
python -m src.main review-leads --id 162
python -m src.main review-leads --limit 20
```

Diese Befehle sind nur lesend, benötigen keinen Claude-Key und bearbeiten
weder Gmail noch die Datenbank. Die Klärfallliste ist bewusst konservativ:
Ein hoher, vom LLM erkannter Fit ist ein **Prüfhinweis**, kein Nachweis der
tatsächlichen Lieferfähigkeit. Wirkliche Änderungen der Score-Gewichte und
eine optionale Nachladung vollständiger Projekttexte erfolgen erst nach
weiterer Kalibrierung und Prüfung der Portalzugriffsbedingungen.

## Automatische Recherche statt manueller Projektseitenarbeit (erste Stufe)

Der Agent kann **optional** vor einer kostenpflichtigen LLM-Analyse zusätzliche
Textpassagen zur Ausschreibung über die offizielle **Brave LLM Context API**
recherchieren. Damit ist **kein direktes Crawling** oder automatisches Abrufen
gesperrter Projektportale erforderlich. Die Schnittstelle liefert Suchtreffer,
Quell-URLs und bereits extrahierte Passagen. Sie ist ein Suchdienst und
**garantiert keinen vollständigen Ausschreibungstext**.

Ablauf: Normales Einlesen/Deduplizieren -> Prefilter -> bei kurzer Beschreibung
mit IT-Bezug und noch verfügbarem Recherchebudget eine Suchanfrage ->
**konservativer Titel-/Projekt-ID-Abgleich** -> passende ergänzende
Quellpassagen flüchtig der LLM-Analyse beifügen -> vorhandene Score Engine.
Wenn die Identität nicht hinreichend belegbar ist, nutzt der Agent ausschließlich
die ursprünglichen Informationen. Such- und Anbieterfehler stoppen die
eigentliche Analyse nicht.

**Sicherheit und Datenqualität**
- Default: `enrichment.enabled: false`. Ohne Brave-Key und ohne Opt-in
  ist der bestehende Betrieb einschließlich der Offline-Demo unverändert.
- Maximal `max_requests_per_run: 2` Suchanfragen pro Crawl-Lauf; auch die
  Zahl kostenpflichtiger Claude-Analysen bleibt separat begrenzt.
- Nur der feste Brave-API-Endpunkt wird angesprochen; **keine Requests
  an die in Ergebnissen gefundenen Seiten**. Keine Browser-Imitation,
  keine Login-/Captcha- oder robots.txt-Umgehung.
- Quell-Ausschnitte sind nicht automatisch verifiziert und werden
  **nicht** in der SQLite-Originalbeschreibung gespeichert. Analyse und
  Originalbeschreibung bleiben getrennt; aus extrahierten Daten dürfen
  keine unbelegten Fakten werden.
- Bei mehreren gleich gut passenden Quellen wird bewusst **keine**
  Anreicherung vorgenommen. Die Validierung der Quellenidentität muss
  an realen Projekten weiter kalibriert werden.
- **Quellen-Provenienz und Rechte:** Diese erste Stufe speichert keine
  vollständigen Suchresultate oder Textauszüge. Für dauerhaft gespeicherte
  Recherche-Belege ist vor Ausbau der jeweilige API-Vertrag einschließlich
  zulässiger Speicherung zu prüfen.

**Trefferdiagnose:** Wenn die erste Suche keine passende Quelle liefert,
kann das bedeuten, dass Brave keine Ergebnisse geliefert hat, die Suchquelle
einen zu kurzen Text hat oder die Zuordnung zu streng bzw. mehrdeutig ist.
Mit `--diagnose` zeigt der Agent in derselben Suchabfrage die Anzahl
der Brave-Treffer, ihre Titel und URLs sowie den Ablehnungsgrund. Es gibt
**keine zweite Suchanfrage** und keine zusätzlichen Claude-Kosten:

```powershell
.\.venv\Scripts\python.exe -m src.main enrich-preview --id 162 --diagnose
```

Die Brave-Suche ist nun für deutschsprachige Projekte auf
`country: DE` und `search_lang: de` voreingestellt (über
`AGENT_ENRICHMENT__COUNTRY` und
`AGENT_ENRICHMENT__SEARCH_LANG` änderbar).
Die Ergebniszuordnung bleibt bewusst konservativ: Bei fünf oder mehr
spezifischen Titelbegriffen genügt eine kürzere Fundstellenüberschrift,
wenn mindestens 80 % der Begriffe in Titel und Suchauszügen belegt sind.
Bei unklarer Identität bleibt der Lead unverändert.

**Zuerst Trefferqualität prüfen:** Mit einer bereits gespeicherten Lead-ID lässt
sich eine *einzelne* Brave-Recherche starten, ohne Claude, Gmail, Scoring
oder die Datenbank aufzurufen bzw. zu verändern:

```powershell
$env:AGENT_DATABASE__URL = "sqlite:///data/agent-test.db"
# BRAVE_API_KEY zuvor sicher und separat setzen (nicht ins Terminal-Log kopieren)
.\.venv\Scripts\python.exe -m src.main enrich-preview --id 162
```

Der Befehl macht bei kurzen IT-Leads maximal **eine** Brave-API-Anfrage
(auch für zuvor analysierte Leads). Er zeigt die erkannte Quelle und die
Textpassage, falls der Projektabgleich ausreichend eindeutig ist. Ohne
einen eindeutigen Treffer wird nichts ergänzt. Such-API-Kosten sind
möglich; das Claude-Guthaben wird nicht belastet.

**Aktivierung erst nach eigener Entscheidung:** Ein Brave Search API-Konto
ist erforderlich; es kann mit Kosten verbunden sein und ist unabhängig
vom Anthropic-Guthaben. Den separaten Schlüssel **nur lokal** als
`BRAVE_API_KEY` setzen und **nie** in Chats, Logs oder Git
kopieren. Nach Prüfung der API-Bedingungen und der Abrechnung sind die
Schritte beispielsweise:

```powershell
# Beispiel NUR nach bewusster Aktivierung und in der TESTDATENBANK
$env:AGENT_DATABASE__URL = "sqlite:///data/agent-test.db"
$env:AGENT_ENRICHMENT__ENABLED = "true"
$env:AGENT_ENRICHMENT__MAX_REQUESTS_PER_RUN = "1"
$env:AGENT_LLM__MAX_ATTEMPTS = "1"
# BRAVE_API_KEY separat und sicher in der eigenen Shell setzen
.\.venv\Scripts\python.exe -m src.main crawl --source email --max-llm 1 --no-ack
```

Hinweis: Brave Search API kann nach aktuellem Tarif abgerechnet werden;
auch wenn die Claude-Aufrufe auf eins beschränkt sind, ist eine Brave-Abfrage
eine weitere API-Transaktion. Vor Produktivbetrieb werden Trefferabgleich,
Provenienz, rechtliche Nutzbarkeit und reale Kosten anhand ausgewählter
Testfälle abgenommen.

## Gmail API (rein lesend)

Neben dem unveränderten Offline-Postfach (backend: eml) unterstützt der Agent
nun eine **Gmail-Nur-Lese-Quelle** (backend: gmail). Sie liest das Label
**E01/Projekt-Suchagenten** über die Gmail API, verwendet die vorhandene
SQLite-Deduplizierung und setzt eine **einmalige lokale Google-OAuth-Freigabe**
voraus. Die bereits in ChatGPT verbundene Gmail-App überträgt ihre
Zugangsdaten nicht in das Python-Projekt.

Die Einrichtung inklusive Windows-PowerShell-Befehlen steht in
**[docs/gmail-setup.md](docs/gmail-setup.md)**.

## Plattformübergreifende Dubletten prüfen (nur lesend)

Nach dem ersten Import können mehrere Portale denselben oder ähnlich benannten
Auftrag enthalten. Der Befehl listet Kandidaten zur **manuellen Prüfung** aus der
bestehenden SQLite-Datenbank:



    python -m src.main duplicate-check --limit 20

Er vergleicht **identische Titel** nach vorsichtiger Unicode-, Groß-/Kleinschreibungs-
und Leerzeichen-Normalisierung, aber nur wenn mindestens zwei unterschiedliche
Plattformen vorkommen. Zahlenkennungen mit 7–10 Ziffern im gemeinsamen Titel
(z. B. „1085053“) werden als zusätzlicher Hinweis ausgegeben, **nicht** als
Beweis für dieselbe Ausschreibung.

Auch vier gleichnamige SAP-Projekte können vier unterschiedliche Aufträge sein.
Deshalb werden weder Datensätze automatisch zusammengeführt noch IDs, Scores,
Auswertungsstatus, Quellvorkommen oder die SQLite-Datenbank verändert. Die
reguläre Deduplizierung anhand identischer Quell-IDs oder Projekt-URLs bleibt
unverändert. Ähnliche Titel mit anderen Schreibweisen werden vorerst bewusst
nicht als identisch behandelt. Dieser Prüfbericht ist der erste Schritt vor
einer späteren evidenzbasierten plattformübergreifenden Zusammenführung.

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

1. **Postfach** (`mailbox.py`, `gmail_mailbox.py`): Offline-Verzeichnis mit `.eml`-Dateien
   (`sources.email.mailbox_dir`) oder die Gmail API mit lokal autorisiertem OAuth-Token
   (`backend: gmail`, Scope `gmail.readonly`). Siehe `docs/gmail-setup.md`.
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

### Claude-API: JSON-Schema-Komplexität

Bei der ersten echten Claude-Anfrage meldete die API 400 invalid_request_error:
Das Analyse-Schema überschreitet die Grenze von **16 Union-Parametern** (27 vorhanden).
Dieser Fehler betrifft nicht die API-Berechtigung.

Der Anthropic-Adapter erkennt das Limit nun **vor** dem API-Aufruf. Wenn das
Schema zu komplex für das API-erzwungene JSON-Format ist, wird das **vollständige**
Schema stattdessen als Systemanweisung übermittelt und JSON im Prompt verlangt.
Die anschließende **strikte Pydantic-Prüfung**, die Regeln gegen erfundene Fakten,
die kontrollierten Wiederholungen und die deterministische Bewertung bleiben
aktiv. Einfache Schemas verwenden weiterhin die API-erzwungenen Structured
Outputs. Im Fallback ist JSON **nicht** bereits durch die API garantiert;
ungültige Antworten werden lokal zurückgewiesen.

Für echte Tests den Lauf auf --max-llm 1 und die Zahl der Versuche mit
AGENT_LLM__MAX_ATTEMPTS=1 begrenzen sowie eine **separate Testdatenbank**
(AGENT_DATABASE__URL=sqlite:///data/agent-test.db) verwenden. Der API-Aufruf
kann Kosten verursachen. Der als SUCCESS markierte Crawl kann trotzdem
einzelne LLM-Fehler enthalten; dafür llm_call und llm_failures prüfen.

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
