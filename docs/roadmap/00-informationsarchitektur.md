# MCN Leitstand — Informationsarchitektur & Roadmap-Grundlage

> Grundstein-Dokument. Es legt die **Ziel-Navigation**, die **Abbildung Hero →
> Leitstand**, die **Seiten-Konventionen** und die **Phasierung** fest. Alle
> Sektions-Dokumente (`01`–`14`) bauen darauf auf und verweisen zurück.

## Zweck

Wir bauen MCN als Nachfolger des bestehenden **Hero-CRM** (Handwerk/
Gebäudeservice). Die Datenbank ist bereits aus Hero abgeleitet und gehärtet.
Damit die Kollegen, die sich schwer umgewöhnen, sich wiederfinden, soll das
neue Angular-Frontend **Hero's Aufbau spiegeln** — gleiche Bereichsgliederung,
gleiche Arbeitsflächen-Muster, vertraute Begriffe — auf unserer eigenständigen,
hochmodernen „Leitstand"-Oberfläche (Navy/Orange, WCAG 2.2 AA).

## Quellenbasis

Diese Roadmap ist aus der vollständigen Hero-Bedienungsanleitung abgeleitet:
**221 `.docx`-Artikel** in 13 Kategorien (die parallel liegenden `.md`-Dateien
wurden vereinbarungsgemäß ignoriert). Alle Artikel wurden zu Text extrahiert und
je Kategorie von einem Recherche-Agenten zu Feature-Specs verdichtet (Rohspecs:
Projekt-intern im Scratchpad, nicht eingecheckt). Insgesamt **952 Screenshots**
der echten Hero-Oberfläche stehen als Layout-Vorlage zur Verfügung.

Jedes Sektions-Dokument nennt die konkreten Hero-Quelldateien, die es abdeckt —
so ist jede der 221 Dateien einer Frontend-Sektion zugeordnet (Nachweis in
`README.md`, Abschnitt „Abdeckung").

Die zwei Dateien der Kategorie **Erste Schritte** speisen kein einzelnes
Fachmodul, sondern dieses Grundstein-Dokument und das Phase-0-Fundament:
- `Erste Schritte\Dein Start in 10 Schritten\…` → prägt diese IA (die
  Onboarding-Checkliste verweist der Reihe nach auf den Firmeneinstellungen-
  Cluster `13` und den Artikelstamm `08`; als geführter Setup-Assistent auf dem
  Dashboard `01` umsetzbar).
- `Erste Schritte\Wie kann ich mich anmelden\…` → Login/Auth-Fundament (Phase 0),
  Bezug siehe `14` (Passwort/Profil) und der Auth-Abschnitt weiter unten.

## Hero's Informationsarchitektur (konsolidiert)

Hero trennt **fachliche Arbeitsbereiche** (linke Sidebar) von einem
**Einstellungs-Cluster** und einem **persönlichen Bereich**. Aus allen Kategorien
rekonstruierte Hauptnavigation (Sidebar, oben → unten):

Die Spalte **H#** ist nur Hero's Sidebar-Reihenfolge (nicht zu verwechseln mit
den MCN-Sektionsnummern `01`–`14` weiter unten).

| H# | Hero-Bereich | Kern |
|---|---|---|
| H1 | **Übersicht / Dashboard** | Startseite mit Kacheln (Aufgaben, Dokumente, Umsatz) |
| H2 | **Kontakte** | Kunden/Firmen als „Kontaktmappe" mit Reitern |
| H3 | **Projekte** | Zentrale operative Arbeitsfläche „Projektmappe"; Pipeline nach Projekttyp |
| H4 | **Dokumente** | Größter Bereich: Editor + Konfigurator; Angebote, Rechnungen, Aufmaß, Baustellenberichte |
| H5 | **Aufgaben** | Globale To-dos + eingebettet in Kontakt/Projekt |
| H6 | **Planung** | Plantafel (Schwimmbahnen), Kalender, Termine, Ressourcen |
| H7 | **Artikelstamm** | Artikel/Leistungen, Verkaufspreise, DATANORM-Import, Lager |
| H8 | **Buchhaltung** | Rechnungen, Belege, Mahnwesen, DATEV/Lexware-Export |
| H9 | **Auswertungen** | 7 Dashboards (Umsatz, Projekte, Kunden, Artikel, Mitarbeiter, …) |
| H10 | **Wartungsverträge** (Modul Kundendienst) | Wiederkehrende Wartung, löst Projekt/Auftrag/Aufgabe aus |
| H11 | **Mitarbeiterverwaltung** | Mitarbeiter, Zeiterfassung, Lohngruppen, Abwesenheiten |
| H12 | **Firmeneinstellungen** | Firmenprofil, Mailserver, Gewerke, Rechte, Layout, Niederlassungen, Projekttypen, Nummernkreise |
| H13 | **Persönliche Daten** | Eigenes Profil, Passwort, Signatur, Mailserver-OAuth |

**Wiederkehrendes Seitenmuster in Hero** (für uns verbindlich, weil es die
Wiedererkennung trägt):
- **Liste** eines Fachbereichs mit Filter/Suche und **primärem Anlage-Button
  oben rechts** (`[+ …]`); Zeilen haben rechts Aktions-Icons (Stift = bearbeiten,
  Status-Toggle, Archivieren).
- Klick auf einen Eintrag öffnet eine **„Mappe" (Detailseite) mit Reitern/Tabs**
  (Kontaktmappe, Projektmappe): Kopfbereich mit Ansprechpartner/Status/Aktionen,
  darunter fachliche Kacheln und ein **Logbuch/Aktivitätsfeed**.
- **Editor** (Dokumente) und **Konfigurator** (Dokumententypen/Layout) sind
  eigene, mehrstufige Vollbild-Arbeitsflächen.
- **Dashboards** bestehen aus Kachel-/Diagramm-Rastern mit Filterleiste und
  Export-Menü (`[⋯]` → CSV/XLSX/JSON).

## Ziel-Navigation MCN Leitstand

Der Leitstand hat heute fünf Nav-Punkte (Übersicht, Kontakte, Liegenschaften,
Vorgänge, Belege). Wir erweitern ihn so, dass er Hero **spiegelt**, aber die
MCN-Besonderheit **Liegenschaften** (property/tenure/management — Hero führt
das nur als „Objektadressen" unter Kontakten) als eigenen Bereich hebt.

| MCN-Leitstand-Bereich | Hero-Entsprechung | DB-Schema(s) | Sektions-Doc | Status |
|---|---|---|---|---|
| **Übersicht** | Übersicht/Dashboard | (aggregiert) + `ai` | `01` | geplant |
| **Kontakte** | Kontakte | `identity` | `02` | ✅ Liste live, Ausbau offen |
| **Liegenschaften** | Kontakte→Objektadressen (gehoben) | `property`,`tenure`,`management` | `03` | ✅ Liste live, Ausbau offen |
| **Vorgänge** | Projekte (+ Wartungsverträge) | `workflow`,`management` | `04`,`11` | Platzhalter |
| **Belege** | Dokumente | `content`,`invoicing`,`pricing` | `05` | Platzhalter |
| **Planung** | Planung | `workflow` (Einsatz) | `06` | — |
| **Aufgaben** | Aufgaben | `workflow` | `07` | — |
| **Artikel & Leistungen** | Artikelstamm | `pricing` | `08` | — |
| **Buchhaltung** | Buchhaltung | `invoicing`,`billing` | `09` | — |
| **Auswertungen** | Auswertungen/Dashboards | (aggregiert) | `10` | — |
| **Wartung** | Wartungsverträge | `management`,`workflow` | `11` | — |
| **Mitarbeiter** | Mitarbeiterverwaltung | `security`,`pricing`(Lohngruppe) | `12` | — |
| **Einstellungen** | Firmeneinstellungen | `security`,`content`,`invoicing` | `13` | — |
| **Mein Profil** | Persönliche Daten | `security` | `14` | — |

### Offene Namens-/Struktur-Entscheidungen (für Produkt/User)

Diese Punkte betreffen die Wiedererkennung direkt und sollten bewusst
entschieden werden — die Roadmap nutzt vorläufig die MCN-Begriffe mit
Hero-Begriff in Klammern:

1. **„Vorgänge" vs. „Projekte":** Hero-Nutzer kennen **Projekte/Projektmappe**.
   Empfehlung: Hero-Begriff „Projekte" übernehmen (max. Wiedererkennung), auch
   wenn die DB `workflow.project`/`vorgang` heißt.
2. **„Belege" vs. „Dokumente":** Hero-Nutzer kennen **Dokumente** (der Editor
   heißt so). Empfehlung: „Dokumente" statt „Belege".
3. **Liegenschaften als eigener Punkt** oder als Reiter in Kontakten (wie Hero)?
   Empfehlung: eigener Punkt (MCN-Domäne Gebäudeservice), aber im Kontakt-Detail
   zusätzlich verlinkt.
4. **Wartung**: eigener Sidebar-Punkt oder Unterbereich von Vorgängen? (Hero
   uneindeutig; siehe `11`.)

## Querschnitts-Prinzipien (gelten für ALLE Sektionen)

Diese unterscheiden MCN bewusst von Hero und müssen in jeder Sektion mitgedacht
werden:

- **KI-first (`ai.ai_proposal`):** Jede anlegende/ändernde Aktion, die ein Mensch
  auslösen kann, kann auch die KI vorschlagen — durch **dieselben Service-Tore**
  (Statusautomaten, Freigaben, Vier-Augen, Audit). In jeder Sektion ist zu
  benennen, wo KI-Vorschläge andocken (z. B. „KI schlägt Angebotspositionen vor").
- **No-Delete / Audit / GoBD:** Hero bietet vielerorts „Löschen" (Kontakte,
  Dokumente, Artikelstamm). Unser DB-Standard ist **Archivieren statt Löschen**,
  Append-only, Audit-Trigger; GoBD-Belege sind unveränderlich (Storno statt
  Bearbeitung). Wo Hero „löschen" sagt, meinen wir **archivieren/stornieren** —
  jede Sektion muss das explizit übersetzen.
- **Schreiben nur über Service-Schicht** (`business_transaction`) → dieselben
  Regeln für Mensch und KI; keine ORM-Direktwrites, kein KI-Sonderweg.
- **Auth/Rechte:** Lesen ist in der Dev-Phase offen; Schreiben braucht Session +
  `app_user`. Die Rechtematrix (Hero: „Zugriffsrechte" je Rolle) wird über
  `security` durchgesetzt — Voraussetzung für fast alle Schreib-UIs.
- **Barrierefreiheit & Design:** WCAG 2.2 AA, Light+Dark, Status nie nur über
  Farbe, Brandtokens — wie im bestehenden Kontakte/Liegenschaften-Slice.

## Wiederkehrende UI-Bausteine (shared components)

Aus dem Hero-Muster ergeben sich Komponenten, die einmal gebaut und überall
wiederverwendet werden (eigene Aufgaben, vor den Fachsektionen zu priorisieren):

- **Ressourcen-Liste** (Suche + Filter-Segmente + Pagination + `[+ …]` oben
  rechts + Zeilen-Aktionen) — bereits als Muster in Kontakte/Liegenschaften da.
- **Detail-„Mappe"** (Kopf + Tab-Leiste + Kachel-Inhalte + Logbuch/Feed).
- **Anlege-/Bearbeiten-Dialog** (mehrstufig/Tabs) über `business_transaction`.
- **Statuswechsel-Steuer** (Statusautomat-konform, mit Begründung/`status_reason`).
- **Logbuch/Aktivitätsfeed** (Audit-gespeist, KI-Vorschläge inline).
- **Dashboard-Kachel** + **Diagramm** + **Export-Menü** (`dataviz`-konform).
- **Dokumenten-Editor** (Vollbild, Positionen/Titel/Kalkulation) — Kern von `05`.

## Phasierung (empfohlene Reihenfolge)

Die Sektionen werden als vertikale Slices gebaut (DB→Service→API→UI→Verifikation
→Review), wie Kontakte/Liegenschaften. Reihenfolge nach fachlicher Abhängigkeit
und Nutzen:

- **Phase 0 — Fundament:** Auth/Login + Rechtematrix, shared UI-Bausteine
  (Liste/Mappe/Dialog), Kontakte-Detailmappe & Liegenschaften-Detail fertig
  (`02`, `03`).
- **Phase 1 — Operativer Kern:** Projekte/Vorgänge inkl. Projektmappe & Pipeline
  (`04`), Aufgaben (`07`), Planung/Einsätze (`06`).
- **Phase 2 — Belegwesen:** Dokumente-Editor & Konfigurator, Angebot/Rechnung
  (`05`); darauf Buchhaltung/Mahnwesen (`09`); Artikel & Leistungen als
  Zulieferer (`08`).
- **Phase 3 — Steuerung & Verwaltung:** Auswertungen/Dashboards (`10`),
  Übersicht-Landing (`01`), Wartungsverträge (`11`).
- **Phase 4 — Administration:** Mitarbeiterverwaltung (`12`),
  Firmeneinstellungen (`13`), Persönliche Daten (`14`).
- **Querschnitt (laufend):** KI-Layer (`ai_proposal`) andockt an jeden Slice,
  sobald dessen Service-Tore stehen.

> Phasen sind Empfehlungen, keine harte Sequenz — `08` (Artikel) ist z. B.
> Voraussetzung für den Angebots-Editor in `05`, kann also vorgezogen werden.

## Dokumenten-Verzeichnis

Siehe `README.md` für Index, Abdeckungs-Nachweis (alle 221 Hero-Dateien) und
Lesereihenfolge. Sektions-Dokumente: `01`–`14` (Nummer = Ziel-Navigationspunkt).
