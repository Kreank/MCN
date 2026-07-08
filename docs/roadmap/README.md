# MCN Leitstand — Roadmap & Implementierungsplan

Diese Roadmap leitet aus der **vollständigen Hero-CRM-Bedienungsanleitung**
(221 Artikel, 13 Kategorien, 952 Screenshots) einen strukturtreuen
Implementierungsplan für das neue Angular-„Leitstand"-Frontend ab. Ziel: das
neue Frontend **spiegelt Hero's Aufbau**, damit umstellungsscheue Kollegen sich
wiederfinden — auf MCN's eigenständiger, KI-first, GoBD-/audit-gehärteter Basis.

## Lesereihenfolge

1. **[00 – Informationsarchitektur](00-informationsarchitektur.md)** — Grundstein:
   Ziel-Navigation, Hero→Leitstand-Mapping, Seiten-Konventionen,
   Querschnittsprinzipien (KI-first, No-Delete/Audit, Auth), Phasierung.
   **Hier anfangen.**
2. Danach die Sektions-Dokumente `01`–`14` (Nummer = Ziel-Navigationspunkt).

## Sektions-Index

| Doc | Bereich | Hero-Entsprechung | Status Umsetzung |
|---|---|---|---|
| [01](01-uebersicht.md) | Übersicht (Dashboard-Landing) | Übersicht/Dashboard | Platzhalter |
| [02](02-kontakte.md) | Kontakte | Kontakte | Liste ✅, Detail offen |
| [03](03-liegenschaften.md) | Liegenschaften | Objektadressen (gehoben) | Liste ✅, Detail offen |
| [04](04-vorgaenge-projekte.md) | Vorgänge/Projekte | Projekte | Platzhalter |
| [05](05-dokumente.md) | Dokumente | Dokumente (größter Bereich) | Platzhalter |
| [06](06-planung.md) | Planung | Planung | — |
| [07](07-aufgaben.md) | Aufgaben | Aufgaben | — |
| [08](08-artikel-leistungen.md) | Artikel & Leistungen | Artikelstamm | — |
| [09](09-buchhaltung.md) | Buchhaltung | Buchhaltung | — |
| [10](10-auswertungen.md) | Auswertungen | Auswertungen/Dashboards | — |
| [11](11-wartungsvertraege.md) | Wartung | Wartungsverträge | — |
| [12](12-mitarbeiter.md) | Mitarbeiter | Mitarbeiterverwaltung | — |
| [13](13-firmeneinstellungen.md) | Einstellungen | Firmeneinstellungen | — |
| [14](14-persoenliche-daten.md) | Mein Profil | Persönliche Daten | — |

## Abdeckungsnachweis (alle 221 Hero-Dateien zugeordnet)

| Hero-Kategorie | Dateien | Ziel-Sektion(en) |
|---|---:|---|
| Erste Schritte | 2 | 00 (IA), 13/14 (Login/Setup) |
| Kontakte | 13 | 02 |
| Projekte | 23 | 04 |
| Dokumente | 81 | 05 |
| Aufgaben | 8 | 07 |
| Planung | 13 | 06 |
| Artikelstamm | 11 | 08 |
| Buchhaltung | 15 | 09 |
| Auswertungen Dashboards | 8 | 10 (+01) |
| Wartungsverträge und Aufträge | 1 | 11 |
| Mitarbeiterverwaltung | 20 | 12 |
| Firmeneinstellungen | 19 | 13 |
| Persönliche Daten | 7 | 14 |
| **Summe** | **221** | — |

Jedes Sektions-Doc listet im Abschnitt „Abgedeckte Hero-Quelldateien" seine
konkreten Dateien auf.

## Zentrale DB-Befunde (beim Schreiben gegen `db/migrations` verifiziert)

Die Roadmap wurde gegen das reale Schema geerdet. Wichtigste Lücken/Entscheidungen:

- **Aufgaben (`07`):** Es existiert **keine** `workflow.task`-Tabelle → neue
  Hand-SQL-Migration nötig (Schutzstandard erben).
- **Vorgänge (`04`):** MCN trennt Hero's „Projekt" in `workflow.project` (Akte/
  Cockpit) und `workflow.service_case` (**Vorgang** = reicher Statusautomat, speist
  die Pipeline). Fehlend: individuelle Felder, Datenerfassungsbogen, Beteiligte.
- **Dokumente (`05`):** MCN trennt strukturierten Beleg (`invoicing.quote/invoice`
  + Positionen/Kalkulation) vom gerenderten `content.document` (PDF, Versionen).
  `quote.status` kennt echtes **ABGELEHNT** (statt Hero's Projekt-Archiv-Weg).
- **Artikel/Lager (`08`):** Hero's **Lagerverwaltung hat kein DB-Fundament** —
  Beschluss B-26 (Migration 0028) untersagt Bestandsführung → Grundsatzentscheidung.
- **Buchhaltung (`09`):** `invoicing.dunning_level` seedet nur **3** Mahnstufen,
  Hero braucht 6; Buchungskonto/Kostenstelle/RE-GS-Belegkreise/Export-Historie fehlen.
- **Einstellungen (`13`):** Rechtematrix `security.role/role_permission` (0026)
  **existiert** und ist App-seitig durchzusetzen (Voraussetzung aller Schreib-UIs);
  company_profile/branch/gewerk/email_template fehlen als Tabellen.
- **Mitarbeiter (`12`) / Profil (`14`):** `security.app_user` ist minimal;
  Arbeitsvertrag/Urlaubsbudget/Steuer-/Bankdaten haben **kein Zuhause** →
  eigenes HR-Fachschema empfohlen. Signatur/Sprache/Mailserver-OAuth fehlen.

## Grundsatz-Entscheidungen

Entschieden (User, 2026-07-08):
1. ✅ Nav-Begriffe **„Projekte"/„Dokumente"** (Hero, Wiedererkennung) — im
   Leitstand bereits umgestellt.
2. ✅ **Liegenschaften als eigener Nav-Punkt** (zusätzlich im Kontakt verlinkt).
3. ✅ **Löschen unter GoBD:** erstellte **Rechnungen nicht löschbar** (nur Storno);
   **Projekte nicht löschbar** (nur verschieben/archivieren). Leitlinie für alle
   „Löschen"-Übersetzungen.
4. ✅ **Lagerverwaltung** vorerst **weglassen** (kommt später sicher dazu → dann
   DB-Schema nötig, siehe `08`).

Noch offen:
- Wartung: eigenes `maintenance`-Schema und eigener Nav-Punkt vs. Unterbereich
  von Projekten (siehe `11`).
- DSGVO-Endlöschung von Kontakten (Anonymisierung) vs. GoBD-Aufbewahrung — Detail
  in `02`.

## Herkunft

Erstellt via Multi-Agenten-Orchestrierung: 14 Recherche-Agenten (Sonnet) haben
die Hero-Artikel je Kategorie zu Feature-Specs verdichtet; 11 Autoren-Agenten
(Opus) plus der Orchestrator haben daraus die Sektions-Dokumente geschrieben und
gegen `db/migrations` geerdet. Rohspecs liegen projekt-intern (nicht eingecheckt).
