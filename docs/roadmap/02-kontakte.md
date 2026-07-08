# 02 — Kontakte (Hero: Kontakte)

> Baut auf `00-informationsarchitektur.md` auf. Globales (KI-first, No-Delete/
> Audit/GoBD, Service-Schicht, Auth, Barrierefreiheit, shared components,
> Phasierung) dort nachschlagen — hier nur, was speziell für **Kontakte** gilt.

## Zweck & Hero-Entsprechung

Kontakte ist Hero's zentraler Stammdaten-Bereich für Personen und Firmen: Kunden,
Lieferanten, Partner und Ansprechpartner werden angelegt, gepflegt, gesucht,
archiviert/gelöscht und mit Objektadressen, Ansprechpartnern, Dokumenten, Bildern
und Nachrichten verknüpft. Entspricht 1:1 dem Hero-Bereich **[Kontakte]** und
seiner **„Kontaktmappe"** (Detailseite mit Reitern). Die **Liste** ist bereits
gebaut (Angular-Feature + `/api/identity/parties`, Schema `identity`); dieses Doc
plant den **Ausbau**: v. a. die **Kontakt-Detailmappe** mit ihren Reitern, das
Anlegen/Bearbeiten und das **Archivieren-statt-Löschen** (DSGVO vs. unser
No-Delete/Audit-Standard). Liegenschaften/Objektadressen sind in MCN in `03`
gehoben (Hero führt sie als Reiter unter Kontakten) — hier nur als Querverweis.

**Abgedeckte Hero-Quelldateien** (Kategorie „Kontakte"):
- `Kontakte\Kontakte bearbeiten, löschen und archivieren\Kontakte bearbeiten, löschen und archivieren.docx`
- `Kontakte\Neue Kontakte hinzufügen\Microsoft Word-Dokument (neu).docx`
- `Kontakte\Wie exportiere ich Kundendaten als Excel- oder CSV-Datei\Wie exportiere ich Kundendaten als Excel- oder CSV-Datei.docx`
- `Kontakte\Wie füge ich einem Kontakt mehrere Objektadressen zu\Microsoft Word-Dokument (neu).docx`
- `Kontakte\Wie führe ich Aktionen für mehrere Kontakte gleichzeitig aus\Wie führe ich Aktionen für mehrere Kontakte gleichzeitig aus.docx`
- `Kontakte\Wie kann ich Kontaktdaten suchen\Wie kann ich Kontaktdaten suchen.docx`
- `Kontakte\Wie kann ich weitere Ansprechpartner zum Kunden hinzufügen\Wie kann ich weitere Ansprechpartner zum Kunden hinzufügen.docx`
- `Kontakte\Wie lade ich AngeboteRechnungen für Kontakte hoch\Wie lade ich AngeboteRechnungen für Kontakte hoch.docx`
- `Kontakte\Wie lade ich Bilder bei Kontakten hoch\Wie lade ich Bilder bei Kontakten hoch.docx`
- `Kontakte\Wie lege ich einen Lieferanten an\Wie lege ich einen Lieferanten an.docx`
- `Kontakte\Wie lösche ich einen Kontakt bei HERO\Wie lösche ich einen Kontakt bei HERO.docx`
- `Kontakte\Wie verschicke ich Nachrichten an Kontakte\Wie verschicke ich Nachrichten an Kontakte.docx`
- `Kontakte\Wo kann ich Informationen zu den Kontakten abrufen\Wo kann ich Informationen zu den Kontakten abrufen.docx`

## Ziel-Navigation & Routen

Sidebar-Punkt **Kontakte** (bereits vorhanden). Routen:

| Route | Screen | Status |
|---|---|---|
| `/kontakte` | Kontaktliste (Tabelle, Suche, Filter-Tabs, Mehrfachauswahl, `[+ Kontakt]`, Export) | ✅ Liste live, Filter/Auswahl/Export ausbauen |
| `/kontakte?kategorie=lieferant` | Lieferantenübersicht = gefilterte Kontaktliste (Hero: Reiter [Lieferanten]) | ausbauen |
| `/kontakte/:id` | Kontaktmappe (Detailseite, Tabs) | **neu (Kern dieses Docs)** |
| `/kontakte/neu` bzw. Dialog | Anlegen Person/Firma/Lieferant | neu |

**Filter-Tabs über der Liste** (spiegeln Hero): Alle / Kunden / Lieferanten /
Partner / Ansprechpartner / **Archiv**. Kategorie ist ein Feld am Kontakt, kein
eigener Endpunkt — technisch dieselbe Liste mit Kategorie-/Status-Filter.

**Tab-Struktur der Kontaktmappe** (Reihenfolge wie Hero):
1. **Kontaktdaten** (Stammdaten) — Standard-Tab
2. **Objektadressen** → Querverweis `/objekte` (Liegenschaften, `03`)
3. **Ansprechpartner**
4. **Dokumente** (Unterreiter Angebote / Rechnungen)
5. **Bilder**
6. **Logbuch** (Aktivitäten-/Nachrichtenverlauf)

Kopfbereich der Mappe: Name/Typ/Kategorie, Status (aktiv/archiviert), Aktionen
(Bearbeiten, Archivieren/Wiederherstellen). Hero leitet beim Öffnen teils direkt
ins Logbuch — bei uns ist Kontaktdaten der Default-Tab, Logbuch ein eigener Tab.

## Screens & Komponenten

### Kontaktliste (bereits gebaut, Ausbau)
- **UI-Typ & Aufbau:** Ressourcen-Liste (shared, siehe `00`). Suchleiste oben
  (Freitext über Stammdatenfelder, Hero: Suchfeld + [Suchen]); Filter-Tabs
  (Alle/Kunden/Lieferanten/Partner/Ansprechpartner/Archiv); Zeilen mit
  Aktions-Icons (Stift = bearbeiten, Kiste = archivieren); primär `[+ Kontakt]`
  oben rechts; `[Export]` (Excel/CSV); Mehrfachauswahl (Klick/STRG/SHIFT) mit
  `[Gruppenaktion]`.
- **Mehrfachauswahl-Verhalten (Hero exakt):** Klick = Auswahl (farblich
  hinterlegt); **ohne Auswahl gelten alle als vorausgewählt**; STRG = einzeln
  hinzufügen/entfernen; SHIFT = Bereich ab zuletzt gewähltem Kontakt.
- **Zustände:** Laden/Leer/Fehler wie shared Liste. Schreibaktionen
  (archivieren, Gruppenaktion) rollen-/session-gebunden; Lesen offen (`00`).
- **Wiederverwendet vs. neu:** Ressourcen-Liste vorhanden; **neu**: Filter-Tabs,
  Mehrfachauswahl, Gruppenaktion-Dialog, Export-Menü.

### Gruppenaktion-Dialog (neu)
- **UI-Typ & Aufbau:** Modal mit zwei Reitern — **Aktion** (Auswahl:
  archivieren / wiederherstellen) und **Liste** (betroffene Kontakte zur
  Kontrolle); Button `[Auf Kontakte anwenden]`. Hero kennt nur diese beiden
  Massenaktionen (archivieren/wiederherstellen).
- **Zustände:** Bestätigung vor Ausführung; schreibend → `business_transaction`,
  je Datensatz Statuswechsel. Rollen-/Session-gebunden.
- Nutzt shared **Statuswechsel-Steuer** (Batch).

### Kontaktmappe / Detailseite (neu — Kern)
- **UI-Typ & Aufbau:** shared **Detail-„Mappe"** (Kopf + Tab-Leiste + Kachel-
  Inhalte + Logbuch). Tabs siehe oben.
  - **Kontaktdaten:** Stammdatenblock (Typ Person/Firma, Kategorie, Adresse,
    Kommunikation) + `[Stift]` → Bearbeiten-Dialog; ZUGFeRD-Feld (USt-IdNr. /
    fiktive Angabe, „[ZUGFERD 2.0 STANDARD]"); Aktionen Bearbeiten / Archivieren
    (Hero-Label [Löschen] = Archivieren, siehe unten).
  - **Objektadressen:** Liste zugeordneter Objekt-/Liegenschaftsadressen,
    `[+ Adresse]`; jede Adresse verlinkt in `/objekte` (`03`). Verknüpfung
    Kontakt↔Objekt.
  - **Ansprechpartner:** Liste, `[+ Ansprechpartner]`; je Ansprechpartner eigene
    Kontaktdetails/Adresse/Konditionen/Zahlungsdaten; Option, für den
    Ansprechpartner eigene Projekte anzulegen (er wird dann Dokumentenempfänger,
    Verweis `04`/`management`).
  - **Dokumente:** Unterreiter Angebote / Rechnungen; `[Hochladen]` → Dialog
    (Dokumententyp, Zielordner, Datei) → Speichern. Verweis `05` (Belege).
  - **Bilder:** Galerie, `[Bild hochladen]` → Dateiauswahl → Hochladen.
  - **Logbuch:** chronologischer Aktivitäts-/Änderungsfeed je Kontakt (wer hat
    was getan), audit-gespeist (shared Logbuch/Feed); `[+ Eintrag]` für neue
    Nachricht (E-Mail) mit CC an Kollegen + Sichtbarkeitseinstellung → Abschicken;
    versendete Nachrichten erscheinen im Logbuch.
- **Zustände:** Laden/Leer/Fehler je Tab; archivierte Kontakte read-only mit
  `[Wiederherstellen]`. Rollen-Sichtbarkeit: Nachricht-Sichtbarkeit steuerbar.
- **Wiederverwendet vs. neu:** Detail-Mappe, Anlege-/Bearbeiten-Dialog,
  Statuswechsel-Steuer, Logbuch/Feed sind shared; **neu**: die kontaktspezifischen
  Tab-Inhalte und Upload-Dialoge.

### Anlegen / Bearbeiten-Dialog (neu)
- **UI-Typ & Aufbau:** shared Anlege-/Bearbeiten-Dialog. Feld **Typ**
  (Person / Firma), Feld **Kategorie** (Kunde / Lieferant / Partner / …),
  Stammdatenfelder, Bereich **ZUGFeRD** (USt-IdNr. / fiktive Angabe). Lieferant =
  derselbe Anlage-Weg mit Kategorie = Lieferant. Button `[Speichern]`.
- **Zustände:** schreibend → `business_transaction`. Pflichtfelder/Validierung
  von Hero **nicht** dokumentiert → OFFEN (unten).

## API-Endpunkte (django-ninja)

Basis `/api/identity/…`, Schema `identity`. Schreibend immer über
`business_transaction`.

| Methode | Pfad | Zweck | Auth | Service-Funktion (Vorschlag) |
|---|---|---|---|---|
| GET | `/api/identity/parties` | Liste (Suche, Filter Kategorie/Status, Pagination) | offen | `parties.list_parties` (vorhanden) |
| GET | `/api/identity/parties/{id}` | Kontaktmappe-Detail | offen | `parties.get_party` |
| POST | `/api/identity/parties` | Kontakt anlegen (Typ/Kategorie/ZUGFeRD) | Session | `parties.create_party` |
| PATCH | `/api/identity/parties/{id}` | Stammdaten bearbeiten | Session | `parties.update_party` |
| POST | `/api/identity/parties/{id}/archive` | Archivieren (Hero [Löschen]) | Session | `parties.archive_party` |
| POST | `/api/identity/parties/{id}/restore` | Wiederherstellen | Session | `parties.restore_party` |
| POST | `/api/identity/parties/batch-status` | Gruppenaktion archivieren/wiederherstellen | Session | `parties.batch_change_status` |
| GET | `/api/identity/parties/export?format=csv\|xlsx` | Export Kundendaten | offen/Session | `parties.export_parties` |
| GET/POST | `/api/identity/parties/{id}/object-addresses` | Objektadressen lesen/verknüpfen → `03` | Session | (in `03`) |
| GET/POST | `/api/identity/parties/{id}/contacts` | Ansprechpartner lesen/anlegen | Session | `parties.add_contact_person` |
| GET/POST | `/api/identity/parties/{id}/documents` | Dokumente (Angebote/Rechnungen) → `05` | Session | (in `05`) |
| GET/POST | `/api/identity/parties/{id}/images` | Bilder lesen/hochladen (MinIO) → `content` | Session | `parties.add_image` |
| GET/POST | `/api/identity/parties/{id}/logbook` | Logbuch lesen / `+Eintrag` (Nachricht) | Session | `parties.list_log`, `parties.post_message` |

Lesend: `list`, `get`, `export`, `logbook`(GET), Tab-Listen. Schreibend (alles
Übrige): über `business_transaction`.

## DB-Bezug

- **Primär `identity`** (Person/Firma-Stammdaten, Typ, Kategorie, Status
  aktiv/archiviert). Ansprechpartner-Modellierung offen (eigener `identity`-
  Datensatz mit Relation vs. Sub-Entität — siehe OFFEN).
- **`property`** (+ `tenure`/`management`): Objektadressen-Verknüpfung → `03`.
- **`content`**: Bilder/Mediendateien (MinIO-Objektspeicher); ggf.
  Dokumentenablage/Ordner.
- **`invoicing`**: ZUGFeRD (USt-IdNr. als Voraussetzung), Angebot/Rechnung-
  Dokumente am Kontakt; GoBD-Aufbewahrung.
- **`pricing`**: Lieferant als Basis für Datanorm/IDS-Connect-Artikelimport (`08`).
- **`audit`**: Logbuch/Aktivitätsprotokoll je Kontakt, DSGVO-Löschprotokoll.
- **Zu respektieren:** Statusautomat aktiv↔archiviert (Trigger/Constraints);
  No-Delete/Audit-Standard auf allen neuen/betroffenen Tabellen; GoBD-
  Unveränderlichkeit von Belegen. UI darf Status nur über die dafür
  vorgesehenen Service-Tore ändern (mit `status_reason`).

## KI-Andockpunkte (`ai.ai_proposal`)

Jede anlegende/ändernde Aktion hier kann die KI durch dieselben Service-Tore
vorschlagen (siehe `00`). Konkret in Kontakte:
- **Kontakt anlegen/ergänzen:** KI schlägt Neuanlage oder Stammdaten-Vervoll-
  ständigung vor (z. B. aus eingehender Nachricht/Dokument extrahiert).
- **Kategorisierung:** KI schlägt Kategorie (Kunde/Lieferant/Partner) und
  Ansprechpartner-Zuordnung vor.
- **Dubletten/Archivierung:** KI schlägt Archivierung inaktiver oder Dubletten-
  Kontakte als `ai_proposal` vor (Mensch bestätigt, Vier-Augen).
- **Logbuch/Nachricht:** KI entwirft Nachrichten-Einträge (`+Eintrag`) als
  Vorschlag; Versand erst nach Freigabe.
- **Objektadress-Verknüpfung:** KI schlägt vor, erkannte Objektadresse mit
  Kontakt zu verknüpfen (→ `03`).

## No-Delete/Audit/GoBD-Übersetzung

Hero mischt „löschen/archivieren" widersprüchlich; unser Standard ist
**Archivieren statt Löschen** (`00`). Konkrete Übersetzung:

- **Hero [Löschen] im Bearbeiten-Fenster / unter Kontaktdaten** → bei uns
  **Archivieren** (Statuswechsel aktiv→archiviert, reversibel, Audit-Eintrag).
  Kein physisches Delete.
- **Hero [Archivieren] (Kisten-Symbol)** → identisch: Statuswechsel, erscheint
  im Reiter **Archiv**.
- **Hero [Wiederherstellen] (Recycle-Symbol im Archiv)** → `restore`
  (archiviert→aktiv), Audit-Eintrag.
- **Hero „endgültige DSGVO-Löschung" (Mülleimer im Archiv)** → **kein Row-Delete.**
  Umsetzung als **Anonymisierung/Sperrung** personenbezogener Felder unter
  Beibehaltung des Datensatzes und aller GoBD-relevanten Belegverknüpfungen
  (Hero selbst sagt: Eintrag bleibt bestehen, „da er ggf. Dokumente enthält").
  Vorgang wird im **DSGVO-Löschprotokoll** (`audit`) festgehalten. Konkrete
  Regeln (welche Felder anonymisiert, Aufbewahrungsfristen, Kollision GoBD ↔
  DSGVO) müssen wir **eigenständig GoBD-konform definieren** — Hero **nicht**
  1:1 übernehmen (siehe OFFEN).
- **Gruppenaktion** kennt nur archivieren/wiederherstellen — keine Massenlöschung.
- **Dokumente/Bilder/Nachrichten:** append-only; Belege unveränderlich (Storno
  statt Bearbeitung, → `05`/`invoicing`).

## Offene Punkte / Entscheidungen

Aus der Spec übernommen:
1. **Ansprechpartner-Modell:** eigener `identity`-Datensatz mit Relation zum
   Kunden, oder untergeordnete Sub-Entität (mit eigenen Kontaktdetails/Adresse/
   Konditionen/Zahlungsdaten)? Muss vor DB-Arbeit an `identity` geklärt werden.
2. **„Partner"-Kategorie:** taucht als Filter auf, ist aber in keiner Quelldatei
   fachlich beschrieben — Bedeutung/Abgrenzung OFFEN.
3. **DSGVO-„Endlöschung" vs. No-Delete/GoBD:** eigene, GoBD-konforme Regeln
   nötig (Anonymisierung statt Delete; welche Felder, Fristen). Grundsatz-
   entscheidung → User (fachlich/rechtlich).
4. **ZUGFeRD-Feld:** genaues UI/Speicherort („[ZUGFERD 2.0 STANDARD]", USt-IdNr./
   fiktive Angabe) nicht dokumentiert; eng mit `invoicing` — Detailform OFFEN.
5. **Pflichtfelder/Validierung/Adressformat:** in keiner Quelldatei beschrieben —
   müssen wir selbst festlegen.

Eigene, entscheidbare Punkte:
6. **Kategorie als Feld** (ein Endpunkt, Filter) bestätigen — Empfehlung: ja,
   Lieferantenübersicht ist dieselbe Liste mit `kategorie=lieferant`.
7. **Export-Umfang/Scope:** ganze Liste vs. aktueller Filter/Auswahl? Empfehlung:
   folgt aktivem Filter + Mehrfachauswahl.
8. **Default-Tab der Mappe:** Kontaktdaten (statt Hero-Direktsprung ins Logbuch).

## Abhängigkeiten

- **Auth + Rechtematrix** (`security`, Phase 0) für alle Schreib-UIs (Anlegen,
  Bearbeiten, Archivieren, Gruppenaktion, Uploads, Nachrichten).
- **Shared components** (`00`): Ressourcen-Liste (da), Detail-Mappe, Anlege-/
  Bearbeiten-Dialog, Statuswechsel-Steuer, Logbuch/Feed, Export-Menü.
- **`identity`-Schema** inkl. geklärtem Ansprechpartner-Modell (OFFEN 1).
- **`03` Liegenschaften** für Objektadressen-Tab (Verknüpfung + `/objekte`).
- **`05` Belege / `content` / MinIO** für Dokumente- und Bilder-Uploads.
- **`invoicing`** für ZUGFeRD-Feld; **`pricing`/`08`** für Lieferant→Artikelimport.
- **`audit`** für Logbuch + DSGVO-Löschprotokoll.

## Aufwand & Priorität

Empfohlene Phase: **Phase 0 — Fundament** (Kontakte-Detailmappe fertigstellen),
Reihenfolge innerhalb der Sektion:

| # | Screen | Größe | Bemerkung |
|---|---|---|---|
| 1 | Anlegen/Bearbeiten-Dialog (Typ/Kategorie/ZUGFeRD) | M | braucht Auth + Validierungsentscheid |
| 2 | Archivieren / Wiederherstellen + Archiv-Tab | S | Statuswechsel-Steuer |
| 3 | Kontaktmappe-Gerüst + Tab Kontaktdaten | M | shared Detail-Mappe |
| 4 | Tab Logbuch (+Eintrag/Nachricht) | M | audit-Feed + E-Mail-Versand (später real) |
| 5 | Tab Ansprechpartner | M | abhängig von Ansprechpartner-Modell (OFFEN 1) |
| 6 | Tab Objektadressen | S–M | dünn hier, Kern in `03` |
| 7 | Tab Dokumente (Angebote/Rechnungen-Upload) | M | abhängig `05`/MinIO |
| 8 | Tab Bilder (Upload) | S | `content`/MinIO |
| 9 | Filter-Tabs + Mehrfachauswahl + Gruppenaktion | M | Listen-Ausbau |
| 10 | Export (CSV/XLSX) | S | |

Zuerst 1–4 (macht die Mappe nutzbar), dann 9 (Bulk), dann uploadabhängige 6–8.

## Screenshots zur Vorlage (Wiedererkennung)

Nur HOCH-Wiedererkennung, als Layout-Vorlage beim Bau:
- **Kontaktliste mit Zeilen-Icons** + **Kontaktmappe-Ansicht** + **Bearbeiten-
  Fenster** — `Kontakte bearbeiten, löschen und archivieren` (image1–image6).
- **+Kontakt-Formular** mit Typ-/Kategorie-Auswahl — `Neue Kontakte hinzufügen`
  (image1–image6).
- **Mehrfachauswahl-Tabelle** + **Gruppenaktion-Fenster** (Reiter Aktion/Liste) —
  `Wie führe ich Aktionen für mehrere Kontakte gleichzeitig aus` (image1–image4).
- **Objektadressen-Reiter** (+Adresse) — `Wie füge ich einem Kontakt mehrere
  Objektadressen zu` (image1–image3).
- **Ansprechpartner-Reiter** (+Ansprechpartner-Formular) — `Wie kann ich weitere
  Ansprechpartner zum Kunden hinzufügen` (image1–image3).
- **Lieferantenübersicht** als gefilterte Tabelle über [Kontakte] — `Wie lege ich
  einen Lieferanten an` (image1–image5).
