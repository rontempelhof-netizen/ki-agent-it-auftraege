# Gmail-Anbindung – ausschließlich lesend

Der Agent kann Projektbenachrichtigungen direkt aus dem Label **E01/Projekt-Suchagenten** lesen.
Der vorhandene Parser, SQLite-Deduplizierung und HTML-Report bleiben erhalten.
OAuth-Scope: **https://www.googleapis.com/auth/gmail.readonly**.
Google erlaubt damit das *gesamte* Postfach zu lesen; der Agent selbst beschränkt
sich programmatisch auf das konfigurierte Label. Keine Gmail-Schreiboperationen.

## 1. Lokale Vorbereitung (einmalig)

1. In der [Google Cloud Console](https://console.cloud.google.com/) ein Projekt
   anlegen/auswählen und die **Gmail API** aktivieren.
2. Unter **Google Auth Platform → Branding / Audience / Data Access** den
   OAuth-Zustimmungsbildschirm einrichten. Für private Gmail-Konten als
   **External** und im Testmodus das eigene Konto als Testnutzer eintragen.
   Nur den Scope *gmail.readonly* anfordern.
3. Unter **Google Auth Platform → Clients** einen OAuth-Client vom Typ
   **Desktop-App** erstellen, die JSON-Datei herunterladen und
   ausschließlich **lokal** als **data/gmail-client-secret.json** ablegen.
   Niemals bei GitHub, in einem Chat oder öffentlich ablegen.
4. In PowerShell aus dem Repositoryverzeichnis:

    python -m pip install -e ".[dev,gmail]"
    python -m src.main gmail-auth
    python -m src.main gmail-check

Der Befehl gmail-auth öffnet ausdrücklich den lokalen Browser für die
Google-Freigabe und speichert das Token in **data/gmail-token.json**
(lokal, von Git ignoriert; eingeschränkte Dateirechte unter Linux/macOS).
Normale Crawls öffnen keinen Browser. gmail-check prüft den Label-Zugriff
und liest höchstens eine Nachricht, ohne etwas zu verändern.

**Wichtig:** Im externen Google-OAuth-Testmodus können Refresh-Tokens nach
sieben Tagen ablaufen. Für Dauerbetrieb den Veröffentlichungsstatus und
eventuell erforderliche Prüfungen bei Google berücksichtigen.

## 2. Gmail als Quelle auswählen

Standard ist weiter der Offline-Modus. Für die aktuelle PowerShell-Sitzung:

    $env:AGENT_SOURCES__EMAIL__BACKEND = "gmail"
    $env:AGENT_MAIL__MODE = "dry_run"
    python -m src.main gmail-check
    python -m src.main crawl --source email --fake-llm --max-llm 0 --no-ack

Der Crawl lädt die neuesten **50** E-Mails mit diesem Label. Er schreibt
die erkannten Projekte in die **lokale SQLite-Datenbank**; mit max-llm 0
erfolgt keine externe KI-Anfrage. no-ack verhindert die **interne**
Bestätigung der Mails; Gmail bleibt stets unangetastet.

Grenzen und Label anpassbar über Umgebungsvariablen
**AGENT_SOURCES__EMAIL__GMAIL__MAX_MESSAGES** und
**AGENT_SOURCES__EMAIL__GMAIL__LABEL**.
Für einen größeren einmaligen historischen Import die Grenze vorübergehend
erhöhen. Ohne --send wird kein Report verschickt. Mit --send und
AGENT_MAIL__MODE=dry_run wird ein lokaler .eml-Entwurf erzeugt.

## Sicherheit und Grenzen

- Die ChatGPT-Gmail-Verbindung stellt dem unabhängigen Python-Agenten **keine Tokens** bereit.
- Der Gmail-Adapter verwendet **nur** labels.list, messages.list (nach Label-ID)
  und messages.get (format=raw); kein Archivieren, keine Änderungen des
  Gelesen-Status, kein Löschen, kein Versenden.
- GitHub-Tests nutzen simulierte Mails und benötigen weder echte Nachrichten
  noch Zugangsdaten. Echte Gmail-Crawls erfordern den lokalen OAuth-Schritt.
- Der Adapter lädt je Lauf die neuesten N Mails; SQLite dedupliziert bestätigte
  Nachrichten über die E-Mail-Message-ID (sonst Inhalts-Hash).
- Plattformübergreifende Dublettenkandidaten und unbeaufsichtigter
  Dauerbetrieb sind noch getrennt zu prüfen.
- Token und Clientdatei gehören **niemals** in ein Repository oder ein
  GitHub Actions-Artefakt.

Google-Dokumentation:
[Python Quickstart](https://developers.google.com/workspace/gmail/api/quickstart/python),
[Nachrichten auflisten](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list),
[RAW-Nachrichten abrufen](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/get).
