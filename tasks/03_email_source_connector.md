# Task 03 – E-Mail als Source Connector

Lies Projektregeln, Requirements und vorhandenen Code.

## Ziel
Projektbenachrichtigungen aus E-Mails in die bestehende Source-Pipeline überführen. Für den PoC ist E-Mail ein vollwertiger Source Connector.

## Implementiere
- generisches Connector-Interface
- EmailSourceConnector
- Verarbeitung von Text- und HTML-Mailinhalt
- Extraktion von Absender, Betreff, Zeit, Body und Projektlinks
- Unterstützung mehrerer Projekte in einer Mail, soweit strukturell erkennbar
- Deduplizierung eingelesener Nachrichten
- Zuordnung einer Source anhand konfigurierbarer Absender/Patterns
- Fixtures/Parserprofile für freelancermap und freelance.de, ohne echte Zugangsdaten
- unbekannte Quellen robust behandeln

## Wichtig
Der erste Task benötigt noch keinen produktiven Gmail/IMAP-Zugang. Verwende lokale `.eml`- oder strukturierte Fixtures, damit alles offline testbar ist. Die produktive Mailbox-Anbindung wird separat konfigurierbar ergänzt.

## Tests
- freelancermap-Beispielmail
- freelance.de-Beispielmail
- HTML-Mail
- Text-Mail
- mehrere Links/Projekte
- unbekannter Absender
- doppelte Mail
- defekte/unvollständige Mail

Keine Kundenkommunikation versenden.
