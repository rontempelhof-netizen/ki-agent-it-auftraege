# Task 05 – End-to-End Vertical Slice und HTML-Report

Lies Projektregeln, Requirements und vorhandenen Code.

## Ziel
Verbinde die bisher implementierten Komponenten zu einem vollständigen PoC-Durchlauf.

## Pipeline
E-Mail-Fixture -> EmailSourceConnector -> Normalize -> Basic Dedup -> Prefilter -> LLM Analysis -> Validate -> Score -> Save -> HTML Report

## Implementiere
- zentrale Pipeline-Orchestrierung
- CrawlRun-Protokollierung
- Jinja2-HTML-Report
- Sortierung A vor B und jeweils nach Score
- C-Leads speichern, aber standardmäßig nicht einzeln reporten
- Kopfstatistik des Laufs
- Original-Links
- Fit-Begründung, Risiken, nächster Schritt, Erstansprache
- Mail-Service mit Test-/Dry-Run-Modus
- CLI-Befehle mindestens `crawl`, `report`, `test-mail`
- End-to-End-Test ohne echte externe Systeme

## Akzeptanzszenario
Eine simulierte Projektmail wird verarbeitet. Mindestens ein geeigneter Lead wird analysiert, deterministisch gescored, in SQLite gespeichert und erscheint in einem gerenderten HTML-Tagesreport. Ein ungeeigneter Treffer wird nachvollziehbar verworfen oder als Reject gespeichert.

## Definition of Done
- alle relevanten Tests grün
- Report lokal renderbar
- README beschreibt Demo-Schritte
- keine echten Portal-/Mail-/LLM-Zugangsdaten für Tests erforderlich
- keine automatische Kundenansprache
