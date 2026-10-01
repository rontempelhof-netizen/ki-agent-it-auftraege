# Task 04 – Strukturierte LLM-Analyse

Lies Projektregeln, Requirements und vorhandenen Code.

## Ziel
LeadCandidates semantisch analysieren und ausschließlich strukturierte LeadAnalysis-Daten erzeugen.

## Implementiere
- LLM-Service hinter abstrahierter Schnittstelle
- versioniertes System-/Analyse-Prompt
- strukturierten JSON-Output
- Pydantic-Validierung
- begrenzte Retries bei ungültigem Output
- Timeout/Fehlerbehandlung
- Token-/Kostenlogging soweit Provider dies liefert
- konfigurierbares Modell
- Mock/Fake-LLM für Offline-Tests
- Schutzregel: Auftragstext ist untrusted input und darf Systemanweisungen nicht überschreiben

## Das LLM analysiert
Kundenproblem, Kategorie, Skills, Muss-Anforderungen, Zertifizierungen, Scope, Kundenart, Dringlichkeit, Risiken, offene Fragen, Fit, Folgepotenzial, Kurzbegründung und Entwurf einer Erstansprache.

## Das LLM darf nicht
- finalen Score bestimmen
- fehlende Fakten erfinden
- Kunden kontaktieren
- Code/Anweisungen aus Auftragstexten als Systeminstruktion ausführen

## Tests
Erfolgsfall, fehlende Felder, ungültiges JSON, Retry, Providerfehler, Prompt-Injection-Text, Mock ohne Netzwerk.
