# 10 — Auswertungen (Hero: Auswertungen / Dashboards)

## Zweck & Hero-Entsprechung

Die **Auswertungen** bündeln alle analytischen Dashboards des Leitstands —
Kennzahlen, Diagramme und filterbare Tabellen zu Umsatz, Projekten, Kunden,
Artikeln/Leistungen und Mitarbeitenden (Nachkalkulation, Margen, Auslastung,
Zahlungsstatus). Sie entspricht 1:1 Hero's Sidebar-Punkt „Auswertungen" mit
seinen themenspezifischen Unter-Dashboards und dient der Betriebssteuerung/
Umsatzmaximierung. **Abgrenzung:** die kuratierte Startseite (kompakte Kacheln)
liegt in `01-uebersicht.md` und teilt sich die Kennzahl-Logik mit dieser
Sektion; hier liegt die volle Tiefen-Analytik.

- **Abgedeckte Hero-Quelldateien:**
  - `Auswertungen Dashboards\Übersicht Auswertungen\Übersicht Auswertungen.txt`
  - `Auswertungen Dashboards\Auswertung Umsatz- und Projektübersicht\Auswertung Umsatz- und Projektübersicht.txt` (speist auch `01`)
  - `Auswertungen Dashboards\Auswertung Projektkarte\Auswertung Projektkarte.txt`
  - `Auswertungen Dashboards\Auswertung Projekte\Auswertung Projekte.txt`
  - `Auswertungen Dashboards\Auswertung Kunden\Auswertung Kunden.txt`
  - `Auswertungen Dashboards\Auswertung Artikel & Leistungen\Auswertung Artikel & Leistungen.txt`
  - `Auswertungen Dashboards\Auswertung Mitarbeitende\Auswertung Mitarbeitende.txt`
  - `Auswertungen Dashboards\Auswertung Umsätze Details\Auswertung Umsätze Details.txt`

## Ziel-Navigation & Routen

Hauptroute `/auswertungen` (Sidebar-Hauptpunkt, spiegelt Hero) mit Unterrouten
je Dashboard. Reihenfolge wie Hero-Sidebar:

| Route | Dashboard | Hero-Paket (nur Info) |
|---|---|---|
| `/auswertungen` | Übersicht/Landingpage (Dashboard-Liste + Zugriffsrechte-Link + Export-/Filter-Hinweis) | — |
| `/auswertungen/umsatz-projektuebersicht` | Umsatz- und Projektübersicht | Starter + Pro |
| `/auswertungen/projektkarte` | Projektkarte (Kartenansicht) | Starter + Pro |
| `/auswertungen/projekte` | Projekte | nur Pro |
| `/auswertungen/kunden` | Kunden | nur Pro |
| `/auswertungen/artikel` | Artikel & Leistungen | nur Pro |
| `/auswertungen/mitarbeitende` | Mitarbeitende | nur Pro |
| `/auswertungen/umsaetze-details` | Umsätze Details | OFFEN (nicht in Paket-Tabelle) |

- **Tab-Struktur je Dashboard** (Teilansichten als Tabs statt einer Bildschirm-
  wüste):
  - `projekte`: Tabs **Marge nach Gewerk** | **Nachkalkulation** | **Offene
    Umsätze** | (+ **Gebuchte Zeiten** | **Projektquelle**).
  - `mitarbeitende`: Tabs **Zeitverteilung** | **Produktivität** |
    **Abwesenheiten**.
  - `umsaetze-details`: Tabs **Angebote** | **Auftragsbestätigungen** |
    **Rechnungen**.
- Paketgrenzen (Starter/Pro) sind reine Hero-Lizenzlogik — für MCN vermutlich
  irrelevant (siehe Offene Punkte); Spalte oben nur zur Nachvollziehbarkeit.
- Globale Nav-Konventionen: siehe `00-informationsarchitektur.md`.

## Screens & Komponenten

Alle Dashboards teilen sich drei wiederkehrende Bausteine (siehe „Shared
components" unten). Je Screen ein Unterabschnitt.

### Übersicht Auswertungen (Landingpage `/auswertungen`)

- **UI-Typ & Aufbau:** Kachel-/Listenübersicht der verfügbaren Dashboards;
  Bereich „Zugriffsrechte" (Rechteverwaltung für den Auswertungsbereich);
  Erklärung der Filter- und Export-Mechanik. Aktionen: `[⋯]` →
  `[Ergebnis exportieren]` (CSV/XLSX/JSON), `[!]` (Ausrufezeichen) öffnet die
  Berechnungs-Erklärung der jeweiligen Auswertung.
- **Zustände:** Leer/Fehler pro Kachel; Zugriffsrechte-Bereich nur für
  Berechtigte (siehe unten).
- **Shared vs. neu:** nutzt Export-Menü + Info-Popover; das Dashboard-Verzeichnis
  selbst ist neu (leichtgewichtig).

### Umsatz- und Projektübersicht (`/auswertungen/umsatz-projektuebersicht`)

- **UI-Typ & Aufbau:** Dashboard/Kacheln. Filter: **Gewerk**, **Projektdatum**
  (Erstellungs- bzw. Abschlussdatum je nach Kachel). Umsatzkennzahlen: **2
  Kacheln + Zeitstrahl** (Umsatzverlauf). Projektkennzahlen: **4 Kacheln** —
  Gewinn (absolute Marge), Offene Umsätze, Erstellte Projekte (nach Gewerk),
  Abgeschlossene Projekte.
- **Zustände:** Laden (Skeleton je Kachel), Leer (kein Umsatz im Zeitraum),
  Fehler pro Kachel; kennzahlengated (siehe unten).
- **Shared vs. neu:** Filterbar + Kachel + Diagramm + Export/Info — alles shared.
  **Diese Kachel-Logik speist auch die Startseite `01`.**

### Projektkarte (`/auswertungen/projektkarte`)

- **UI-Typ & Aufbau:** Dashboard mit **Kartenansicht/Map**. Pin je Projekt;
  **Hover** zeigt Zusatzinfos (Titel, Kundenanschrift u. a.); **Klick** öffnet
  das zugehörige Projekt (Absprung in `04`-Projektmappe). Filter: Datum
  (Projekterstellung), Projekt-ID, Projektbeteiligte, Projekttyp, Projektstatus
  (z. B. „Kundenanfrage", „In Umsetzung", „Abgeschlossen").
- **Zustände:** Laden (Karte + Pins), Leer (keine Projekte im Filter), Fehler
  (Kartendienst nicht erreichbar).
- **Shared vs. neu:** Filterbar shared; **Kartenkomponente neu** (Sonderfall
  gegenüber den tabellarischen Dashboards) — Kartenlib ist zu entscheiden
  (siehe Offene Punkte).

### Projekte (`/auswertungen/projekte`)

- **UI-Typ & Aufbau:** Dashboard/Kacheln + Tabellen mit Tabs. Filter: Datum der
  Projekterstellung, Gewerk, Hauptansprechpartner, Projekt-ID, Projekttyp,
  Projektstatus, Projektquelle, **Projektbeteiligte** (erfasst alle Beteiligten,
  nicht nur den Hauptansprechpartner). Inhalte:
  - **Allgemeine Kennzahlen:** durchschnittliche Durchlaufzeit „Erstellung" →
    „Abgeschlossen"; Graph erstellte & abgeschlossene Projekte je Monat.
  - **Tab „Marge nach Gewerken"** (Tabelle: Gewerk, Anzahl Projekte,
    Rechnungsvolumen, Marge €/%).
  - **Tab „Nachkalkulation"** (Tabelle: Projekt-ID, Kunde, Projekttyp,
    Projektleitung, Rechnungsvolumen, Marge €/%, Lohn-/Materialkosten,
    Ist-Marge €/%).
  - **Tab „Offene Umsätze pro Projekt"** (Tabelle: Projekt-ID, Kundenname,
    Projekttyp, Projektleitung, Angebots-/Auftragsvolumen, Rechnungsvolumen,
    Offener Umsatz).
  - **Tab „Gebuchte Zeiten":** Soll/Ist-Vergleich geleistete vs. geplante
    Arbeitsstunden.
  - **Tab „Projektquelle":** Übersicht Auftragsherkunft.
- **Zustände:** je Tab Laden/Leer/Fehler; große Tabellen mit horizontalem
  Scroll-Container.
- **Shared vs. neu:** Filterbar/Export/Info shared; die 5 Tab-Ansichten neu
  (überwiegend Tabellen). Umfangreichstes Dashboard.

### Kunden (`/auswertungen/kunden`)

- **UI-Typ & Aufbau:** Dashboard/Kacheln + Tabellen. **Allgemeine Kennzahlen:**
  Liniendiagramm „Umsatz pro Zeitraum"; Kreisdiagramm „Umsatz pro Kundenquelle".
  **Kundenübersicht** (sortierbare Tabelle): Kundennummer, Firmenname,
  Kundenname, Rechnungsvolumen (€), Anteil (%), Projekte mit diesem Kunden,
  Projekte mit Rechnungen im Zeitraum, Anzahl Rechnungen. **Dokumentenübersicht
  „Rechnungen – Details"** (Tabelle): Dokumentennummer, Dokumentendatum, VK,
  Quelle (Erstellungsebene), Gewerk, Projekt/Auftrag, Firma/Vor- und Zuname,
  Rechnungsart, Zahlstatus (Offen/Teilzahlung/Bezahlt).
- **Zustände:** Laden/Leer/Fehler; Tabellen sortierbar, horizontaler Scroll.
- **Shared vs. neu:** Filterbar/Diagramm/Export shared; Kunden-/Rechnungstabellen
  neu.

### Artikel & Leistungen (`/auswertungen/artikel`)

- **UI-Typ & Aufbau:** Dashboard/Kacheln + Tabelle + Diagramm. **Allgemeine
  Kennzahlen:** „Top 10 Artikel & Leistungen nach Marge (%)"; „Top 10:
  Meistverkaufte Positionen" (nach Anzahl); Hinweis: nur Artikel aus Projekten
  innerhalb des Datumsfilters. **Positionsübersicht** (Tabelle): Positionsname,
  Anzahl (verkaufte Einheiten über Rechnungen), Ø VK (€), Ø EK (€), Ø Marge (€)
  und (%). **Verkäufe im Zeitverlauf:** Liniendiagramm (X = Datum, Y = Anzahl
  Verkäufe) für Verkaufsspitzen/saisonale Trends.
- **Zustände:** Laden/Leer/Fehler; Datumsfilter maßgeblich.
- **Shared vs. neu:** Filterbar/Diagramm/Export shared; Top-10-Listen +
  Positionstabelle neu.

### Mitarbeitende (`/auswertungen/mitarbeitende`)

- **UI-Typ & Aufbau:** Dashboard/Kacheln + Tabellen + Diagramme mit Tabs.
  Filter: Datum, MitarbeiterIn, Projekt-ID.
  - **Tab „Zeitverteilung":** „Zeiteinträge nach Kategorie [h]" (Balken);
    „Zeiteinträge im Zeitverlauf [h]" (Linie).
  - **Tab „Produktivität":** „Anzahl Projekte nach zugewiesenem Mitarbeitenden"
    (Tabelle: Projekte insgesamt, davon als HauptansprechpartnerIn, Laufende
    Projekte im Zeitraum, Abgeschlossene Projekte im Zeitraum); „Zeiteinträge pro
    Mitarbeitenden nach Projekt" (Tabelle: ProjektID, Kundenname, Mitarbeiter,
    Ist-Stunden, Projekttyp, Projektstatus, Rechnungsvolumen [€]). Hinweis: keine
    archivierten Projekte; Kategorien Pause/Schlechtwetter/Privat und
    nicht-arbeitszeitrelevante individuelle Kategorien ausgeschlossen.
  - **Tab „Abwesenheiten":** „Übersicht der Abwesenheiten" (Tabelle:
    Mitarbeiter, Gesamt Abwesenheitstage, Urlaub — nur bestätigte Abwesenheiten;
    Wochenenden/Feiertage nicht enthalten).
- **Zustände:** je Tab Laden/Leer/Fehler; personenbezogene Daten — Rollen-Gating
  beachten.
- **Shared vs. neu:** Filterbar/Diagramme/Export shared; Produktivitäts-/
  Abwesenheitstabellen neu.

### Umsätze Details (`/auswertungen/umsaetze-details`)

- **UI-Typ & Aufbau:** Dashboard/Kacheln + 3 Detailtabellen (Tabs). Filter:
  Dokumentenerstellungsdatum, Gewerk, Datum-Projekterstellung, Projekt-ID,
  Projektansprechpartner, **Schalter „Nur letztes ANG/AB"**. Kennzahlenblöcke:
  „Wert der Angebote" (Anzahl + Wert erstellter/versendeter Angebote), „Wert der
  Auftragsbestätigungen" (bei mehreren AB je Projekt nur die zuletzt erstellte),
  „Wert der Rechnungen" (Stornorechnungen ausgeschlossen), „Rechnungen nach
  Zahlstatus" (bezahlt/offen/Teilzahlung).
  - **Tab „Angebote – Details":** Dokumentnummer, Dokumentendatum, EK, VK,
    Marge [%], Gewerk, Projekt, Auftrag.
  - **Tab „Auftragsbestätigung – Details":** Dokumentnummer, Dokumentendatum,
    EK, VK, Marge [%], Gewerk, Auftrag/Projekt.
  - **Tab „Rechnungen – Details":** Dokumentnummer, Dokumentendatum, VK, Gewerk,
    Projekt/Auftrag, Vor- und Nachname, Rechnungsart, Zahlstatus, Zahldatum.
- **Zustände:** je Tab Laden/Leer/Fehler; „Nur letztes ANG/AB" ändert die
  Kennzahlenbasis.
- **Shared vs. neu:** Filterbar (inkl. Schalter)/Export shared; die 3
  Detailtabellen neu. Granularste Umsatz-/Dokumentenauswertung.

### Shared components (für alle Dashboards)

- **`DashboardFilterBar`** — wiederkehrende Filter (Datum, Gewerk, Status,
  Beteiligte, Projekt-ID …).
- **`ExportMenu`** — `[⋯]` → `[Ergebnis exportieren]` (CSV/XLSX/JSON).
- **`CalcInfoPopover`** — `[!]`-Info-Icon mit Berechnungs-Erklärung.
- Zusätzlich die globalen **Dashboard-Kachel** und **Diagramm**-Komponenten
  (siehe `00`); Diagrammtypen aus der Spec: **Balken, Linie, Kreis**.

## API-Endpunkte (django-ninja)

Ausschließlich **lesend** — Auswertungen aggregieren, sie schreiben nicht.
Berechnungsdefinitionen (siehe DB-Bezug) 1:1 in die Service-Queries übernehmen.

| Methode | Pfad | Zweck | Auth | Service-Funktion |
|---|---|---|---|---|
| GET | `/api/auswertungen/dashboards` | Liste verfügbarer Dashboards (Landing) | Session | `auswertungen.list_dashboards` |
| GET | `/api/auswertungen/umsatz-projektuebersicht` | Umsatz-/Projektkennzahlen + Zeitstrahl | kennzahlengated | `auswertungen.umsatz_projektuebersicht_summary` |
| GET | `/api/auswertungen/projektkarte` | Projekt-Pins (Geo + Kurzinfo) | kennzahlengated | `auswertungen.projektkarte_pins` |
| GET | `/api/auswertungen/projekte` | Kennzahlen + 5 Teilansichten (Marge/Nachkalk/offene Umsätze/Zeiten/Quelle) | kennzahlengated | `auswertungen.projekte_*` |
| GET | `/api/auswertungen/kunden` | Umsatz/Marge/Rechnungen je Kunde + Rechnungen-Details | kennzahlengated | `auswertungen.kunden_*` |
| GET | `/api/auswertungen/artikel` | Top-10 Marge/Meistverkauft + Positionsübersicht + Verkaufsverlauf | kennzahlengated | `auswertungen.artikel_*` |
| GET | `/api/auswertungen/mitarbeitende` | Zeitverteilung/Produktivität/Abwesenheiten | kennzahlengated | `auswertungen.mitarbeitende_*` |
| GET | `/api/auswertungen/umsaetze-details` | ANG/AB/Rechnungen-Kennzahlen + 3 Detailtabellen | kennzahlengated | `auswertungen.umsaetze_details_*` |
| GET | `/api/auswertungen/{key}/export` | Ergebnis-Export (CSV/XLSX/JSON) je Dashboard/Filter | kennzahlengated | `auswertungen.export` |

- Alle Endpunkte akzeptieren die jeweiligen Filter als Query-Parameter (Datum,
  Gewerk, Projekt-ID, Status, Beteiligte, „Nur letztes ANG/AB" …).
- **Kein Schreibpfad** in dieser Sektion. „kennzahlengated" = an das
  Auswertungs-Zugriffsrecht gebunden (Hero „Zugriffsrechte" → `security`).

## DB-Bezug

Aggregierend über mehrere Schemas; keine eigenen Tabellen. **Berechnungs-
definitionen exakt aus der Spec (1:1 in Queries übernehmen):**

- **Umsatz** = Rechnungsvolumen aus Rechnungen mit Status „erstellt"/„gesendet"
  (inkl. Korrekturen; **ohne** „storniert"/„Entwurf"/„archiviert") aus
  Projekten, Aufträgen und direkt an Kontakten abgelegten Rechnungen →
  `invoicing`.
- **Offener Umsatz** = letzte Auftragsbestätigung (sonst letztes Angebot) minus
  bereits erstellte Rechnungssumme → `invoicing`, `workflow`.
- **Marge [%]** = (VK − EK) / VK × 100; **Ist-Marge [%]** = (VK − tatsächliche
  Kosten) / VK × 100 → `pricing`, `invoicing`.
- **Lohn-/Materialkosten** (gebuchte Ist-Kosten) → `management`/`workflow`
  (Zeiterfassung), `pricing`.
- Projektstatus, Gewerk, Beteiligte, Projektquelle → `management`, `workflow`,
  `identity` (Ansprechpartner).
- Projektkarte: `property` (Anschrift/Liegenschaft), `management`/`workflow`.
- Kunden/Artikel: `identity`, `invoicing`, `content`, `pricing`.
- Mitarbeitende: `identity`, `management`/`workflow` (Zeiteinträge,
  Projektzuweisung, Abwesenheiten), `invoicing`.
- Zugriffsrechte: `security`.

**Statusautomaten/Constraints, die die UI respektieren muss** (rein lesend, aber
korrekt filtern):
- Umsatz-Statusfilter (erstellt/gesendet inkl. Korrekturen, ohne storniert/
  Entwurf/archiviert) — die konkreten Statuswerte müssen mit dem `invoicing`-
  Schema abgeglichen werden (Statusnamen ggf. abweichend, siehe Offene Punkte).
- Bei mehreren AB je Projekt nur die zuletzt erstellte werten.
- Stornorechnungen aus dem Rechnungswert ausschließen.
- Mitarbeitende: keine archivierten Projekte; bestimmte Zeitkategorien (Pause/
  Schlechtwetter/Privat u. a.) ausschließen; nur bestätigte Abwesenheiten
  (ohne Wochenende/Feiertage).

## KI-Andockpunkte (`ai.ai_proposal`)

- Auswertungen sind **lesend**; die KI führt hier keine Datenänderungen aus.
- Sinnvolle KI-Vorschläge docken indirekt an: aus einer Kennzahl abgeleitete
  **Handlungsvorschläge** (z. B. „offener Umsatz Projekt X → Rechnung stellen",
  „Kunde Y mit hoher Marge → Angebot nachfassen", „Position Z margenschwach →
  Preis prüfen"). Diese werden als `ai.ai_proposal` erzeugt und landen in der
  **KI-Vorschläge-Kachel der Übersicht (`01`)**; die Ausführung läuft über die
  Service-Tore der jeweiligen Zielsektion (Belege `05`, Vorgänge `04`, Artikel
  `08`) — kein KI-Sonderweg, exakt dieselben Freigaben wie beim Menschen.
- Reine Kennzahl-/Analyse-Anfragen der KI nutzen dieselben Read-Services wie die
  UI.

## No-Delete/Audit/GoBD-Übersetzung

- Diese Sektion hat **keine** „Löschen"/„Bearbeiten"-Aktionen — sie liest und
  exportiert nur. Damit entfällt die Storno-/Archivierungs-Übersetzung hier;
  sie gilt in den datenliefernden Sektionen (`05` Belege, `09` Buchhaltung).
- **GoBD-relevant bleibt die Korrektheit der Auswertung:** stornierte/
  archivierte Belege dürfen den Umsatz nicht verfälschen (Statusfilter oben);
  Storno statt Bearbeitung wird in der Belegquelle durchgesetzt, nicht hier.
- Export ist lesend/unkritisch (CSV/XLSX/JSON), verändert keine Fachdaten.

## Offene Punkte / Entscheidungen

- **„Umsätze Details" — eigener Menüpunkt oder Unterbereich?** In der
  Hero-Paket-Verfügbarkeitstabelle nicht aufgeführt (nur 6 Dashboards gelistet).
  Unklar, ob Teil von „Umsatz- und Projektübersicht" oder eigenständiger,
  vergessener Menüpunkt. **Vorschlag:** als eigenständige Route
  (`/auswertungen/umsaetze-details`) vorsehen und bei Bedarf zusammenlegen.
  (Aus Spec übernommen — OFFEN.)
- **Paketmodell Starter/Pro:** reine Hero-Lizenzlogik; für MCN vermutlich
  irrelevant (kein Paketmodell erwähnt). Grundsatzentscheid: alle Dashboards für
  alle (mit Recht) verfügbar, oder äquivalentes Feature-Gating nötig? (Aus Spec —
  OFFEN.)
- **`invoicing`-Statusnamen** für den Umsatzfilter müssen mit unserem Schema
  abgeglichen werden (Hero-Werte „erstellt"/„gesendet"/„storniert"/„Entwurf"/
  „archiviert" ggf. abweichend benannt). (Aus Spec — OFFEN.)
- **Kartenbibliothek** für die Projektkarte (Geo-Rendering) ist zu wählen; muss
  self-hostbar/WCAG-tauglich und Brand-konform sein. (Eigen — entscheidbar.)
- **Diagramm-/Visualisierungslib:** in der Spec keine Vorgabe (nur Typen Balken/
  Linie/Kreis). Visuelles Redesign eigenständig nach MCN-Marke — „kein
  0815-CRM-Look" (siehe CLAUDE.md); `dataviz`-konform. (Eigen — entscheidbar.)
- **„Zugriffsrechte" für den Auswertungsbereich** nur erwähnt, nicht im Detail
  spezifiziert — vermutlich Teil von Einstellungen/Rechte (`13`/`security`),
  nicht hier zu definieren. (Aus Spec.)

## Abhängigkeiten

- **Datenliefernde Schemas müssen befüllt/nutzbar sein:** `invoicing`,
  `pricing`, `workflow`, `management`, `identity`, `content`, `property` — also
  faktisch die operativen Sektionen `04` (Vorgänge), `05` (Belege), `08`
  (Artikel), `09` (Buchhaltung), teils `12` (Mitarbeiter/Zeiten). Ohne diese
  Daten sind die Dashboards leer.
- **`security`/Rechtematrix** für das Auswertungs-Zugriffsrecht (Gating).
- **Shared components:** `DashboardFilterBar`, `ExportMenu`, `CalcInfoPopover`,
  Dashboard-Kachel, Diagramm (siehe `00`) — vor den Dashboards zu bauen.
- Liefert seinerseits die Kennzahl-Query-Logik für die **Startseite `01`**.

## Aufwand & Priorität

- **Empfohlene Phase:** Phase 3 (siehe `00`), nach dem operativen Kern und dem
  Belegwesen (deren Daten die Auswertungen aggregieren).
- **Reihenfolge & Aufwand je Screen** (T-Shirt):
  1. Shared components (`DashboardFilterBar`/`ExportMenu`/`CalcInfoPopover`) —
     **M**, zuerst.
  2. Landing `/auswertungen` — **S**.
  3. Umsatz- und Projektübersicht — **M** (speist auch `01`, deshalb früh).
  4. Umsätze Details — **M** (Kennzahlenblöcke + 3 Tabellen).
  5. Kunden — **M**.
  6. Artikel & Leistungen — **M**.
  7. Mitarbeitende — **L** (3 Tabs, viele Ausschlussregeln, personenbezogen).
  8. Projekte — **L/XL** (5 Teilansichten, Nachkalkulation, umfangreichste
     Berechnungen).
  9. Projektkarte — **M** (funktional einfach, aber neue Kartenkomponente).
- Empfehlung: Umsatz-/Projektübersicht vorziehen (Startseiten-Reuse), die
  großen Dashboards (Projekte, Mitarbeitende) zuletzt.

## Screenshots zur Vorlage (Wiedererkennung)

- `Übersicht Auswertungen` **image1–image6.png** — Dashboard-Liste, Paket-
  Gating und **Exportdialog** (`[⋯]`/CSV-XLSX-JSON); prägt das wiederkehrende
  Export-/Filter-Muster.
- `Auswertung Umsatz- und Projektübersicht` **image1.png, image2.png** —
  Kachel-Layout (2 Umsatz-Kacheln + Zeitstrahl, 4 Projekt-Kacheln); HOCH,
  zugleich Vorlage für `01`.
- `Auswertung Projekte` **image1–image6.png** — Tabellen der 3 Kern-
  Teilansichten (Marge/Nachkalkulation/offene Umsätze); HOCH, prägt das
  Tabellen-Layout der großen Dashboards.
- `Auswertung Umsätze Details` **image1–image7.png** — Kennzahlenblöcke + 3
  Detailtabellen; HOCH, granularstes Layout.
- `Auswertung Mitarbeitende` **image1–image4.png** — Diagramm-/Tabellen-Mix der
  Produktivitäts-/Abwesenheitsansicht; HOCH.
- (Projektkarte, Kunden, Artikel — MITTEL bis HOCH; bei Bau der jeweiligen
  Screens die zugehörigen Screenshots heranziehen.)
