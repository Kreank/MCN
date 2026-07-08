# 12 — Mitarbeiter (Hero: Mitarbeiterverwaltung)

## Zweck & Hero-Entsprechung

Diese Sektion bildet Hero's **Mitarbeiterverwaltung** 1:1 als eigenen
Top-Level-Bereich des Leitstands ab: Mitarbeiter-Accounts (anlegen,
aktivieren/deaktivieren, Passwort-Reset), Rollen/Berechtigungen, versionierte
Arbeitsverträge (Wochenarbeitszeiten, Urlaubsanspruch), Lohngruppen/Kostensätze,
Zeiterfassung (erfassen/bestätigen, Kategorien, Pausen, Stundenausgleich,
Export) sowie Urlaub/Abwesenheiten (Anträge, Genehmigung, Budget, Auswertung).
Sie ist der zentrale Personal-/HR-Bereich und speist Kalkulation (Lohngruppe →
`pricing`), Planung (Abwesenheit → Plantafel) und Soll-/Ist-Berechnung
(Vertrag → Zeiterfassung). Die **Rechtematrix** (`security`) ist Voraussetzung
für nahezu alle Schreib-UIs dieser Sektion. Querschnitts-Prinzipien (KI-first,
No-Delete/Audit, `business_transaction`, WCAG) gelten wie in
`00-informationsarchitektur.md` beschrieben und werden hier nicht wiederholt.

**Abgedeckte Hero-Quelldateien** (alle relativ zu `Mitarbeiterverwaltung\`):
- `Wo kann ich meine Mitarbeiter verwalten\Wo kann ich meine Mitarbeiter verwalten.txt`
- `Wie lege ich neue Mitarbeiter in HERO an\Wie lege ich neue Mitarbeiter in HERO an.txt`
- `Wie kann ich die Daten meiner Mitarbeiter ändern\Wie kann ich die Daten meiner Mitarbeiter ändern.txt`
- `Mitarbeiter aktivieren und deaktivieren\Mitarbeiter aktivieren und deaktivieren.txt`
- `Wie lösche ich einen Mitarbeiter\Wie lösche ich einen Mitarbeiter.txt`
- `Wie kann ich das Passwort meiner Mitarbeiter zurücksetzen\Wie kann ich das Passwort meiner Mitarbeiter zurücksetzen.txt`
- `Verträge anlegen und verwalten\Verträge anlegen und verwalten.txt`
- `Verschiedene Wochenarbeitszeiten eintragen\Verschiedene Wochenarbeitszeiten eintragen.txt`
- `Wie kann ich Lohngruppen erstellen  verwalten\Wie kann ich Lohngruppen erstellen  verwalten.txt`
- `Individuelle Kategorien in der Zeiterfassung\Individuelle Kategorien in der Zeiterfassung.txt`
- `Pausenverwaltung mit HERO\Pausenverwaltung mit HERO.txt`
- `Zeiten im Web eintragen und bestätigen\Zeiten im Web eintragen und bestätigen.txt`
- `Wie kann ich mir alle Arbeitszeiten anzeigen lassen\Wie kann ich mir alle Arbeitszeiten anzeigen lassen.txt`
- `Wie kann ich eine Stundenliste erstellen\Wie kann ich eine Stundenliste erstellen.txt`
- `Stundenausgleich bei einer 4-Tage Woche\Stundenausgleich bei einer 4-Tage Woche.txt`
- `Abwesenheitsanträge verwalten und einreichen\Abwesenheitsanträge verwalten und einreichen.txt`
- `Auswertung Abwesenheiten\Auswertung Abwesenheiten.txt`
- `Wie kann ich Abwesenheitsanträge exportieren\Wie kann ich Abwesenheitsanträge exportieren.txt`
- `Kann ich Rest-Urlaub für meine Mitarbeiterinnen eintragen und in das Folgejahr übernehmen\Kann ich Rest-Urlaub für meine Mitarbeiterinnen eintragen und in das Folgejahr übernehmen.txt`
- `Wo sehe ich meine verbleibenden Urlaubstage\Wo sehe ich meine verbleibenden Urlaubstage.txt`

## Ziel-Navigation & Routen

Sidebar-Punkt **Mitarbeiter** (Hero: „Mitarbeiterverwaltung"), Phase-4-Bereich
(siehe `00`). Spiegelt Hero's Unterpunkt-Reihenfolge. Angular-Routen:

```
/mitarbeiter                         Mitarbeiterliste (Tabs Aktiv/Inaktiv), [+Mitarbeiter], Export
/mitarbeiter/neu                     Anlage-Dialog/Wizard (Allgemein / Berechtigung / Signatur)
/mitarbeiter/:id                     Detail-„Mappe", Tabs (Hero-Reihenfolge):
    …/uebersicht                       Unter-Tabs: Persönliches · Berechtigungen · Mailserver ·
                                       Vertrag · Lohngruppe · Steuerdaten · Bankdaten
    …/urlaub-abwesenheiten             Urlaubsbudget-Kacheln, Resturlaub ermitteln, Anträge, Jahr-Dropdown
    …/zeiterfassung                    Zeitraum-Filter Heute/Woche/Monat/Jahr, Einträge
    …/stundenausgleich
    …/dokumente                        Upload (Kategorie, Beschreibung)
/mitarbeiter/zeiterfassung           Globale Zeiterfassungs-Übersicht (alle MA, Filter, Mitarbeiterfilter)
    …/stundenliste                     Export-Dialog (MA/Zeitraum/Excel)
    …/stundenausgleich                 Wochenauswahl, [+Stundenausgleich]
    …/tagesansicht  |  …/einzelansicht Bestätigen/Korrigieren/Ablehnen
/mitarbeiter/zeitkategorien          Einstellungsseite (Liste + Dialog, Status Aktiv/Inaktiv)
/mitarbeiter/pausenverwaltung        Einstellungsseite (3 Regeltypen)
/mitarbeiter/lohngruppen             Liste + Dialog (Name, Selbstkosten, Gesamtkosten)
/mitarbeiter/abwesenheiten           Übersicht (Liste, [+Neuer Antrag], „Derzeit Abwesend", Export)
    …/auswertung                       Dashboard (Filter Beginn/Ende)
```

Self-Service (eigene Person, außerhalb der Admin-Mitarbeiterverwaltung, gehört
perspektivisch zu Sektion `14 Mein Profil` — hier nur als Gegenstück benannt):
- `/meine-daten/urlaub-abwesenheiten` — eigenes Urlaubsbudget, eigene Anträge
- `/meine-daten/zeiterfassung` — eigene Zeiten (Arbeits-/Pausenzeit-Dialog)

## Screens & Komponenten

### Mitarbeiterliste (`/mitarbeiter`)
- **UI-Typ & Aufbau:** Ressourcen-Liste (shared, siehe `00`). Tabs
  **[Aktiv]/[Inaktiv]**, Suche/Filter, `[+Mitarbeiter]` oben rechts, Zeilen mit
  Name/Rolle/Niederlassung. Zeilen-Aktionen im **Drei-Punkte-Menü**:
  [Bearbeiten], [Deaktivieren]/[Reaktivieren], [Passwort zurücksetzen].
  Toolbar-Aktion `[Export]` (CSV/Excel — deckt Hero „Abwesenheitsanträge
  exportieren" ab, das im Hero unter Mitarbeiter→Export sitzt).
- **Zustände:** Laden/Leer/Fehler wie shared; sichtbar nur mit Leserecht
  „Mitarbeiter" (`security`); Aktionsmenü nur mit Schreibrecht.
- **Wiederverwendung:** Ressourcen-Liste, Statuswechsel-Steuer (Aktiv/Inaktiv).
  Neu: Tab-Segment Aktiv/Inaktiv.

### Mitarbeiter anlegen (`/mitarbeiter/neu`)
- **UI-Typ & Aufbau:** Mehrstufiger Anlege-Dialog (shared), Hero's 3 Reiter:
  **[Allgemein]** (Profilbild, Anrede, Vor-/Nachname, Telefon/Mobil/Fax, E-Mail,
  Initial-Passwort, „im Urlaub"-Flag), **[Berechtigung]** (Account-Typ
  Standard-Nutzer/App-Nutzer, Benutzerrechte aus Rechtematrix),
  **[Signatur]** (Signatur-Text). Abschluss `[Speichern]`.
- **Zustände:** Sichtbar nur mit Recht „Mitarbeiter anlegen". Hero-Lizenzlimit
  entfällt (kein SaaS-Lizenzmodell, siehe Offene Punkte).
- **Wiederverwendung:** Anlege-Dialog. Neu: Rechte-/Rollen-Auswahlkomponente
  (Rechtematrix-Editor), wiederverwendbar in Sektion `13`.

### Mitarbeiter-Detail-„Mappe" (`/mitarbeiter/:id`)
- **UI-Typ & Aufbau:** Detail-„Mappe" (shared: Kopf + Tab-Leiste + Kacheln +
  Logbuch). Kopf mit Foto/Name/Status/Aktionen. Prägend (12 Hero-Screenshots) —
  siehe unten. Tab **Übersicht** mit Unter-Tabs:
  - *Persönliches:* Anrede, Vor-/Nachname, Geburtsdatum, Telefonnummern,
    Adresse, Signatur, Anzeigesprache → `identity`.
  - *Berechtigungen:* Niederlassung, Benutzerrechte, Account-Typ → `security`.
  - *Mailserver:* Absender-E-Mail → `security`/Profil.
  - *Vertrag:* eigenes Feature (s. u.).
  - *Lohngruppe:* Zuordnung einer Lohngruppe → `pricing`.
  - *Steuerdaten:* Steuerklasse, Familienstand, Kirchenzugehörigkeit,
    Versicherungen → OPEN (Personal-Teilschema).
  - *Bankdaten:* IBAN → OPEN (Personal-Teilschema).
  Weitere Tabs: **Urlaub und Abwesenheiten**, **Zeiterfassung**,
  **Stundenausgleich**, **Dokumente** (`[+Hochladen]` → Datei, Kategorie,
  Beschreibung → `content`).
- **Zustände:** Tab-/Feld-Sichtbarkeit rollenabhängig (Steuer-/Bankdaten nur mit
  Personal-Recht). Speichern je Unterbereich.
- **Wiederverwendung:** Detail-„Mappe", Logbuch/Feed, Dokumenten-Upload.

### Vertrag (Unter-Tab Vertrag)
- **UI-Typ & Aufbau:** Formular in der Mappe. Felder bis Bearbeiten gesperrt.
  **Vertragsbeginn** Pflicht (muss in der Zukunft liegen), **Vertragsende**
  optional (leer = unbefristet). Pro Wochentag geplante **Soll-Stunden** (statt
  pauschal 5×8h). **Urlaubstage im Jahr** (initiales Urlaubsbudget). Buttons
  `[+ Neuer Vertrag]`, `[Bearbeiten]`, `[Vertrag kündigen]`.
- **Fachregeln (Berechnungslogik, verbindlich):** Folgevertrag wird automatisch
  aktiv bei Erreichen des Startdatums; danach unveränderlich; davor frei
  editier-/löschbar. `[Vertrag kündigen]` setzt Ende auf heute. Arbeitszeit-
  änderung erfordert **neuen Vertrag** (kein rückwirkendes Überschreiben).
  Feiertage/0-Std-Tage = 0 Sollzeit, bei Urlaub/Krankheit ignoriert; ganzer
  Fehltag → Soll 0, halber → Soll halbiert; „Überstundenausgleich" ändert Soll
  nicht; Urlaubsanspruch/Monat nach Anzahl Vertragsarbeitstage.
- **Zustände:** Historische Verträge read-only (Versionierung). Nur mit
  Personal-Recht editierbar.
- **Wiederverwendung:** Anlege-/Bearbeiten-Dialog, Statuswechsel-Steuer
  (Kündigen mit `status_reason`). Neu: Wochentag-Sollzeit-Raster.

### Urlaub und Abwesenheiten (Tab in der Mappe)
- **UI-Typ & Aufbau:** Kacheln + Tabelle. Jahres-Dropdown oben links (Folgejahr
  ab 1. Oktober wählbar). Kachel Urlaubsbudget des Jahres; mittlere Kachel
  „Resturlaub aus (Jahr) ermitteln" (Kreis-Pfeile-Icon) berechnet/überträgt
  automatisch. `[Bearbeiten]` (Stift) → Dialog mit Jahr-Auswahl + Feld
  „Manuelle Anpassung" (auch negativ, z. B. Sonderurlaub). Tabelle geplanter/
  genehmigter Anträge.
- **Zustände:** Budget pro Jahr einzeln gespeichert; direkte Änderung nur für
  aktuelles Jahr, Vorjahre nur über Resturlaub-Funktion. Bekannte Lücke:
  unterjähriger Vertragsbeginn/-ende noch nicht automatisch berücksichtigt.
- **Wiederverwendung:** Dashboard-Kachel, Bearbeiten-Dialog.

### Zeiterfassung (Tab in der Mappe + global `/mitarbeiter/zeiterfassung`)
- **UI-Typ & Aufbau (Tab):** Zeitraum-Filter [Heute]/[Diese Woche]/[Dieser
  Monat]/[Dieses Jahr] + Start-/Enddatum; Liste erfasster Zeiten.
- **UI-Typ & Aufbau (global):** Ressourcen-Liste aller Mitarbeiter mit
  Filterbereich (Start-/Enddatum, Schnellfilter täglich/wöchentlich/monatlich/
  jährlich, Mitarbeiter-Namensfilter). Klick auf Namen → Mitarbeiter-Mappe.
  Toolbar `[Stundenliste]` und Unterreiter `[Stundenausgleich]`,
  `[Tagesansicht]`/`[Einzelansicht]`.
- **Bestätigungsansicht:** Umschalter Tagesansicht/Einzelansicht; ganzer Tag
  oder Einzeleintrag **bestätigen / korrigieren / ablehnen**. Einzelansicht mit
  Excel-Export.
- **Zustände:** Bestätigen/Ablehnen nur mit Recht „Zeiten freigeben".
- **Wiederverwendung:** Ressourcen-Liste, Statuswechsel-Steuer (bestätigt/
  abgelehnt mit Begründung), Export-Menü.

### Zeiteintrag-Dialog (Self-Service + Korrektur)
- **UI-Typ & Aufbau:** Editor-Fenster. `[+Arbeitszeit hinzufügen]` (Projekt,
  Zeitabschnitte, Kommentar), `[+Pausenzeit hinzufügen]`; grafische Anzeige
  Pause/Arbeitstag; automatische Pausen-/Arbeitszeitberechnung. Mehrere Einträge
  → ein Tageseintrag.
- **Validierung:** Endzeit nicht vor Startzeit; keine Überschneidungen; erster
  Eintrag darf keine Pause sein; fehlende Start-/Endzeiten erkannt.
- **Wiederverwendung:** Anlege-/Bearbeiten-Dialog. Neu: Zeitachsen-Visualisierung.

### Stundenausgleich (Tab + global-Unterreiter)
- **UI-Typ & Aufbau:** Reiter mit Zeitraumfilter [Diese Woche] +
  Mitarbeiterauswahl. `[+Stundenausgleich]` → Fenster mit [Ausgleichsart]
  (z. B. „Einbehalten (reduziert Minusstunden)") und Stundenanzahl, `[Speichern]`
  → Saldo ausgeglichen. Strukturelle Alternative: Vertrag → Bearbeiten →
  Wochenarbeitszeit anpassen.
- **Wiederverwendung:** Bearbeiten-Dialog.

### Zeitkategorien (`/mitarbeiter/zeitkategorien`)
- **UI-Typ & Aufbau:** Liste + Dialog. Spalten [Zeitkategorie], [Beschreibung],
  [Arbeitszeitrelevant] Ja/Nein, [Status] Aktiv/Inaktiv. `[+Zeitkategorie
  hinzufügen]`, Stift zum Bearbeiten. Deaktivierte Kategorien bleiben auf
  gebuchten Zeiten sichtbar, nicht mehr wählbar für Neueinträge. Warnung beim
  Umbenennen (kann bestehende Einträge überschreiben).
- **Wiederverwendung:** Ressourcen-Liste, Anlege-Dialog, Statuswechsel-Steuer.

### Pausenverwaltung (`/mitarbeiter/pausenverwaltung`)
- **UI-Typ & Aufbau:** Einstellungsseite, Auswahl eines von drei Regeltypen:
  (1) Keine Pausenregel (Standard); (2) Gesetzliche Vorgaben (>6h→30min, >9h→
  45min automatisch); (3) Pausen zu festen Zeiten (z. B. 12:00–12:30). Wirkt
  automatisch auf alle künftigen Zeiteinträge.
- **Wiederverwendung:** Einstellungs-Formular (Radio-Gruppe + bedingte Parameter).

### Lohngruppen (`/mitarbeiter/lohngruppen`)
- **UI-Typ & Aufbau:** Liste + Dialog „Lohngruppe erstellen" mit Name,
  Selbstkosten, Gesamtkosten. `[+Lohngruppe]`. Zuordnung an Mitarbeiter im
  Detail-Tab Lohngruppe. Hinweis: Lohngruppe wird auch für Maschinenkosten
  genutzt → generisches Kostensatz-Konzept (siehe Offene Punkte, Bezug `08`).
- **Wiederverwendung:** Ressourcen-Liste, Anlege-Dialog.

### Abwesenheiten (`/mitarbeiter/abwesenheiten` + `…/auswertung`)
- **UI-Typ & Aufbau:** Übersichtsliste; `[+Neuer Abwesenheitsantrag]` oben
  rechts → Formular (Zeitraum, Art: Urlaub/Krankheit/Elternzeit u. a., optional
  Dokument-Upload z. B. Atteste). `[Speichern]` = Entwurf, `[Einreichen]` =
  eingereicht. Separater Bereich `[Derzeit Abwesend]` (Liste aktuell Abwesender).
  Genehmigter Zeitraum erscheint automatisch in der Plantafel (Sektion `06`).
  Unterreiter **Auswertung**: Dashboard, Filter Beginn/Ende, Datenbasis
  eingereichte + bestätigte Abwesenheiten. Export CSV/Excel.
- **Zustände:** Antrag-Statusautomat Entwurf → eingereicht → genehmigt/abgelehnt.
  Genehmigen nur mit Recht „Abwesenheit freigeben".
- **Wiederverwendung:** Ressourcen-Liste, Anlege-Dialog, Statuswechsel-Steuer,
  Dashboard-Kachel + Diagramm + Export-Menü.

## API-Endpunkte (django-ninja)

Alle schreibenden Endpunkte laufen über `db_core.db_context.business_transaction`
(siehe `00`/`backend/README.md`); Auth „Session" = eingeloggter `app_user` mit
passendem Recht. Pfade unter `/api/mitarbeiter…` (Arbeitstitel).

| Methode | Pfad | Zweck | Auth | Service-Funktion |
|---|---|---|---|---|
| GET | `/api/mitarbeiter?status=aktiv\|inaktiv` | Liste | Session | `mitarbeiter.list_employees` |
| POST | `/api/mitarbeiter` | Anlegen (Account+Rechte+Signatur) | Session | `mitarbeiter.create_employee` |
| GET | `/api/mitarbeiter/{id}` | Detail | Session | `mitarbeiter.get_employee` |
| PATCH | `/api/mitarbeiter/{id}/persoenliches` | Persönliche Daten | Session | `mitarbeiter.update_personal` |
| PATCH | `/api/mitarbeiter/{id}/berechtigungen` | Rechte/Rolle/Niederlassung/Account-Typ | Session | `security.update_permissions` |
| POST | `/api/mitarbeiter/{id}/deaktivieren` | Deaktivieren (+Projekt-Neuzuweisung) | Session | `security.deactivate_account` |
| POST | `/api/mitarbeiter/{id}/reaktivieren` | Reaktivieren | Session | `security.reactivate_account` |
| POST | `/api/mitarbeiter/{id}/passwort-reset` | Passwort zurücksetzen | Session | `security.reset_password` |
| GET | `/api/mitarbeiter/{id}/vertraege` | Vertragshistorie | Session | `mitarbeiter.list_contracts` |
| POST | `/api/mitarbeiter/{id}/vertraege` | Neuer Vertrag (Sollzeiten, Urlaub) | Session | `mitarbeiter.create_contract` |
| PATCH | `/api/vertraege/{id}` | Vertrag ändern (nur vor Startdatum) | Session | `mitarbeiter.update_contract` |
| POST | `/api/vertraege/{id}/kuendigen` | Vertrag kündigen (Ende=heute) | Session | `mitarbeiter.terminate_contract` |
| GET | `/api/mitarbeiter/{id}/urlaubsbudget?jahr=` | Budget/Jahr | Session | `mitarbeiter.get_leave_budget` |
| POST | `/api/mitarbeiter/{id}/urlaubsbudget/resturlaub` | Resturlaub ermitteln/übertragen | Session | `mitarbeiter.carry_over_leave` |
| PATCH | `/api/mitarbeiter/{id}/urlaubsbudget` | Manuelle Anpassung (±) | Session | `mitarbeiter.adjust_leave_budget` |
| GET | `/api/zeiterfassung?von=&bis=&ma=` | Zeiten (global/gefiltert) | Session | `workflow.list_time_entries` |
| POST | `/api/zeiterfassung` | Zeiteintrag (Arbeit/Pause) | Session | `workflow.create_time_entry` |
| POST | `/api/zeiterfassung/{id}/bestaetigen` | Bestätigen | Session | `workflow.confirm_time_entry` |
| POST | `/api/zeiterfassung/{id}/ablehnen` | Ablehnen (Begründung) | Session | `workflow.reject_time_entry` |
| POST | `/api/zeiterfassung/{id}/korrigieren` | Korrigieren | Session | `workflow.amend_time_entry` |
| POST | `/api/stundenausgleich` | Ausgleichsbuchung | Session | `workflow.book_time_adjustment` |
| GET | `/api/zeiterfassung/stundenliste?ma=&von=&bis=` | Excel-Export | Session | `workflow.export_hours` |
| GET/POST/PATCH | `/api/zeitkategorien[/ {id}]` | Kategorien CRUD-analog | Session | `workflow.manage_time_category` |
| GET/PUT | `/api/pausenregel` | Pausenregel lesen/setzen | Session | `workflow.set_break_rule` |
| GET/POST/PATCH | `/api/lohngruppen[/ {id}]` | Lohngruppen/Kostensätze | Session | `pricing.manage_wage_group` |
| GET | `/api/abwesenheiten?von=&bis=&status=` | Liste/Auswertung | Session | `workflow.list_absences` |
| POST | `/api/abwesenheiten` | Antrag (Entwurf) | Session | `workflow.create_absence` |
| POST | `/api/abwesenheiten/{id}/einreichen` | Einreichen | Session | `workflow.submit_absence` |
| POST | `/api/abwesenheiten/{id}/genehmigen` | Genehmigen | Session | `workflow.approve_absence` |
| POST | `/api/abwesenheiten/{id}/ablehnen` | Ablehnen (Begründung) | Session | `workflow.reject_absence` |
| GET | `/api/abwesenheiten/export?format=csv\|xlsx` | Export | Session | `workflow.export_absences` |
| GET | `/api/mitarbeiter/{id}/dokumente` / POST | Dokumente lesen/hochladen | Session | `content.list_docs` / `content.upload_doc` |

Reine Lese-/Export-Endpunkte umgehen `business_transaction`; alle POST/PATCH/PUT
gehen zwingend hindurch.

## DB-Bezug

- **`security`** — Mitarbeiter-Account, Account-Typ (Standard-/App-Nutzer),
  Rollen/Zugriffsrechte, Niederlassung, aktiv/inaktiv, Credentials
  (Passwort-Reset). Rechtematrix ist Voraussetzung für Schreib-UIs.
- **`workflow`** — Zeiteinträge (Arbeits-/Pausenzeit, Projektbezug, Status
  bestätigt/abgelehnt), Zeitkategorien, Pausenregel, Stundenausgleichsbuchungen,
  Abwesenheitsanträge (Typ, Zeitraum, Status-Automat Entwurf→eingereicht→
  genehmigt/abgelehnt), Soll-/Ist-Berechnung.
- **`pricing`** — Lohngruppe/Kostensatz (Selbstkosten, Gesamtkosten); generisch
  auch für Maschinenkosten (siehe Offene Punkte).
- **`management`** — Projektzuweisungen (Übertragung bei Deaktivierung),
  Plantafel-Integration genehmigter Abwesenheiten.
- **`content`** — Mitarbeiter-Dokumente, Antrags-Anhänge (Atteste).
- **`identity`** — personenbezogene Kontaktdaten (Name/Adresse/Telefon).
- **OPEN (Personal-/HR-Teilschema)** — Arbeitsvertrag (Wochen-/Tages-Sollzeiten,
  Zeitraum, Urlaubsanspruch), Urlaubsbudget/Resturlaub je Jahr, Steuerdaten,
  Bankdaten. Kein eindeutiges Zuhause in der bestehenden Schemaliste (siehe
  Offene Punkte).

**Statusautomaten/Constraints, die die UI respektieren muss:**
- Vertrag: Beginn in der Zukunft (Pflicht); nach Erreichen des Startdatums
  unveränderlich (Versionierung); Kündigung setzt Ende = heute.
- Zeiteintrag: keine Überschneidungen, Endzeit ≥ Startzeit, erster Eintrag keine
  Pause; Status bestätigt/abgelehnt.
- Abwesenheit: Status-Automat mit Freigabe; Genehmigung erzeugt Plantafel-Eintrag.
- Zeitkategorie inaktiv: für Neueinträge gesperrt, historisch sichtbar.

## KI-Andockpunkte (`ai.ai_proposal`)

Über dieselben Service-Tore (siehe `00`) kann die KI vorschlagen:
- **Zeiterfassung plausibilisieren:** fehlende/auffällige Tage erkennen und einen
  Zeit- oder Ausgleichs-Eintrag vorschlagen; Vorgesetzter bestätigt.
- **Abwesenheits-Genehmigung vorbereiten:** Konflikte mit Plantafel/anderen
  Abwesenheiten prüfen und Genehmigung/Ablehnung mit Begründung vorschlagen.
- **Resturlaub-Übertrag** zum Jahreswechsel als Vorschlag anstoßen.
- **Vertrags-Folge** vorschlagen (z. B. bei angekündigter Arbeitszeitänderung
  neuen Vertragsentwurf ab Stichtag).
- **Deaktivierung + Projekt-Neuzuweisung** als Aktionspaket vorschlagen, wenn ein
  Austritt bekannt wird. Alle Vorschläge durchlaufen Freigabe/Vier-Augen/Audit.

## No-Delete/Audit/GoBD-Übersetzung

- **„Mitarbeiter löschen"** (Hero-Quelldatei „Wie lösche ich einen Mitarbeiter")
  = **Deaktivieren**, kein Hard-Delete. Ein Feature, nicht zwei (Dublette in der
  Spec). Account bleibt inaktiv erhalten; historische Zuweisungen unberührt
  (Grundsatz: keine Wiederverwendung alter Datensätze für neue Personen).
- **Vertrag „ändern"** = neue Vertragsversion (aktive Verträge unveränderlich).
- **Zeiteintrag „korrigieren"** = Korrektur-/Ausgleichsbuchung mit Audit, kein
  stilles Überschreiben bestätigter Zeiten.
- **Zeitkategorie „löschen"** = auf inaktiv setzen (historische Buchungen bleiben).
- **Urlaubsbudget** = jahresweise, additive manuelle Anpassungen (auch negativ)
  statt Überschreiben; Resturlaub-Übertrag als nachvollziehbarer Vorgang.
- Alle Schreibvorgänge audit-getriggert; Statuswechsel mit `status_reason`.

## Offene Punkte / Entscheidungen

Aus der Spec übernommen:
1. **Personal-/HR-Teilschema (Grundsatzentscheidung):** Arbeitsvertrag,
   Urlaubsbudget/Resturlaub, Steuer- und Bankdaten haben kein Zuhause in der
   bestehenden Schemaliste. Entscheiden: eigenes Schema `hr`/`personal` anlegen
   vs. `security` erweitern. Empfehlung: eigenes Fachschema (erbt
   No-Delete/Audit-Standard), weil rechtlich/fachlich eigenständig und Quelle
   für `workflow`-Berechnungen.
2. **Unterjähriger Vertragsbeginn/-ende im Urlaubsbudget:** in Hero „in Arbeit".
   Bewusst korrekt implementieren statt Hero-Bug nachbauen.
3. **Lohngruppe generisch:** Kostensatz auch für Maschinen/Ressourcen, nicht nur
   Mitarbeiter. Mit `pricing`/`08` abstimmen, ob ein gemeinsames Kostensatz-
   Objekt entsteht.
4. **Abwesenheits-Einstiegspunkt** in Hero uneinheitlich benannt (Übersicht vs.
   Mitarbeiter→Export). Entscheidung: ein eigener Menüpunkt `/mitarbeiter/
   abwesenheiten` als kanonischer Zugang; Export dort statt unter Mitarbeiterliste.
5. **Navigationspfad „Auswertung Abwesenheiten"** in Hero-Doku unbelegt — hier
   als Unterreiter der Abwesenheiten festgelegt.
6. **Lizenz-/Nutzerlimit** (Standard-/App-Nutzer, support@hero) ist Hero-SaaS-
   Spezifikum — für MCN nicht übernommen. Account-Typ-Feld bleibt trotzdem
   (Standard vs. App-Nutzer) als Rollen-/Zugriffsmerkmal.
7. **Self-Service-Parität:** verbleibende Urlaubstage in Hero nur Web. Für MCN
   Web-first umsetzen; Mobile später (siehe `00`/Architektur). Self-Service-
   Screens gehören organisatorisch zu Sektion `14`.

## Abhängigkeiten

- **Auth + Rechtematrix (`security`)** — harte Voraussetzung: fast alle
  Schreib-UIs hier hängen an Rollen/Rechten. Ohne durchgesetzte Rechtematrix kein
  Anlegen/Deaktivieren/Freigeben. Phase 0 (`00`).
- **Shared Components** (Ressourcen-Liste, Detail-„Mappe", Anlege-Dialog,
  Statuswechsel-Steuer, Logbuch, Dashboard-Kachel/Export) — vor dem Slice
  fertig (`00`).
- **Personal-/HR-Teilschema** (offener Punkt 1) muss vor Vertrag/Urlaub stehen.
- **`workflow`-Zeitmodell** (Zeiteintrag, Statusautomat, Soll-/Ist) — Grundlage
  für Zeiterfassung/Stundenausgleich; teilt Fläche mit `06 Planung`
  (Plantafel-Integration Abwesenheit) und `07 Aufgaben`.
- **`pricing`** (`08`) für Lohngruppen/Kostensätze.
- **`management`** für Projekt-Neuzuweisung bei Deaktivierung.
- **`content`** für Dokumenten-Upload.

## Aufwand & Priorität

Gesamtbereich: Phase 4 (Administration, siehe `00`) — nach operativem Kern und
Belegwesen, weil viele Nutzer, aber nicht auf dem kritischen Auftrags-Pfad. Die
Zeiterfassungs-Selbsterfassung ist der einzige täglich von allen genutzte Teil
und kann vorgezogen werden, sobald das `workflow`-Zeitmodell steht.

| Screen | Größe | Empfohlene Reihenfolge |
|---|---|---|
| Mitarbeiterliste (Aktiv/Inaktiv) | M | 1 |
| Anlegen (3-Reiter-Wizard) + Rechte-Editor | L | 2 |
| Detail-Mappe Übersicht (Unter-Tabs) | L | 3 |
| Deaktivieren/Reaktivieren + Projekt-Neuzuweisung | M | 4 |
| Passwort-Reset | S | 4 |
| Vertrag (versioniert, Wochentag-Soll) | L | 5 |
| Zeiteintrag-Dialog (Self-Service) | M | 6 |
| Zeiterfassung global + Bestätigen/Ablehnen | L | 7 |
| Zeitkategorien | S | 8 |
| Pausenverwaltung | S | 8 |
| Stundenausgleich | M | 9 |
| Stundenliste-Export | S | 9 |
| Lohngruppen | S | 10 |
| Abwesenheiten (Anträge + Derzeit Abwesend) | L | 11 |
| Urlaubsbudget/Resturlaub | M | 12 |
| Abwesenheiten-Auswertung (Dashboard) | S | 13 |
| Export CSV/Excel (Abwesenheiten) | S | 13 |
| Dokumente-Tab | S | 14 |

## Screenshots zur Vorlage (Wiedererkennung)

Nur HOCH-Wiedererkennung, als visuelle Vorlage beim Bau:
- **Detail-Mappe/Tab-Layout:** `Wie kann ich die Daten meiner Mitarbeiter
  ändern` image1–12 (12 Screenshots — prägt das gesamte Tab-/Unterbereich-Layout).
- **Liste + Anlage-Dialog (3 Reiter):** `Wie lege ich neue Mitarbeiter in HERO an`
  image1–3.
- **Listeneinstieg:** `Wo kann ich meine Mitarbeiter verwalten` image1–3.
- **Zeiterfassung/Eintrags-Interaktion:** `Zeiten im Web eintragen und
  bestätigen` image1–5 (image2 = GIF, Interaktions-Demo des Eintragsfensters).
- **Stundenausgleich/Saldo-Darstellung:** `Stundenausgleich bei einer 4-Tage
  Woche` image1–7 (umfangreichste Bebilderung).
- **Urlaubsbudget-Kacheln:** `Kann ich Rest-Urlaub … Folgejahr übernehmen`
  image1–4.
- **Abwesenheitsantrag + Derzeit Abwesend:** `Abwesenheitsanträge verwalten und
  einreichen` image1–5.
