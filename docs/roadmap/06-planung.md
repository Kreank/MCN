# 06 — Planung (Hero: Planung)

## Zweck & Hero-Entsprechung

Die Sektion „Planung" ist der Leitstand-Bereich für **Termin-, Einsatz- und
Ressourcendisposition** und spiegelt Hero's gleichnamigen Bereich 1:1. Kern ist
die **Plantafel** — ein Schwimmbahnen-Board je Mitarbeiter/Ressource mit
Zeitachse und Drag-&-Drop-Disposition; dazu kommen **Kalenderansicht**,
**Terminliste**, **Einstellungen** (Termindarstellung, Kategorien) und die
**Ressourcenverwaltung** (Fahrzeuge/Werkzeuge/Räume). Datenbasis ist der
`workflow`-Einsatz/Termin (siehe `04` Vorgänge, mit dem sich Planung Projekte
und Termine teilt); Ressourcen sind schema-seitig **noch offen**. iCal
Import/Export bindet externe Kalender ein (unidirektional).

**Abgedeckte Hero-Quelldateien:**
- `Planung\Die wichtigsten Einstellungen in der Plantafel\Die wichtigsten Einstellungen in der Plantafel.txt`
- `Planung\Das Aussehen von Terminen in der Plantafel und im Kalender anpassen\Das Aussehen von Terminen in der Plantafel und im Kalender anpassen.txt`
- `Planung\Wie kann ich bestehende Terminkategorien ändern oder neue hinzufügen\Wie kann ich bestehende Terminkategorien ändern oder neue hinzufügen.txt`
- `Planung\Wie kann ich einen Termin erstellen\Wie kann ich einen Termin erstellen.txt`
- `Planung\Wie kann ich einen Termin bearbeiten\Wie kann ich einen Termin bearbeiten.txt`
- `Planung\Plantafel - Termine mit mehreren Mitarbeiterinnen und Ressourcen erstellen\Plantafel - Termine mit mehreren Mitarbeiterinnen und Ressourcen erstellen.txt`
- `Planung\Wie kann ich Ressourcen hinzufügen\Wie kann ich Ressourcen hinzufügen.txt`
- `Planung\Meine Termine werden in der Plantafel nicht angezeigt\Meine Termine werden in der Plantafel nicht angezeigt.txt`
- `Planung\Wo finde ich meine anstehenden Termine\Wo finde ich meine anstehenden Termine.txt`
- `Planung\Kalendersynchronisation\Kalendersynchronisation.txt`
- `Planung\Kann ich meine Personalplanung aus HERO in einen anderen Kalender exportieren\Kann ich meine Personalplanung aus HERO in einen anderen Kalender exportieren.txt`
- `Planung\Wie kann ich Externe Kalender importieren\Wie kann ich Externe Kalender importieren.txt`
- `Planung\Wie bekomme ich Feiertage aus anderen (Bundes-) Ländern in den Kalender\Wie bekomme ich Feiertage aus anderen (Bundes-) Ländern in den Kalender.txt`

## Ziel-Navigation & Routen

Sidebar-Hauptpunkt **Planung** (`/planung`), spiegelt Hero's Unterpunkt-Struktur:

| Route | Screen | Hero-Entsprechung |
|---|---|---|
| `/planung/plantafel` | Plantafel (Board, Default-Redirect von `/planung`) | [Planung] → [Plantafel] |
| `/planung/kalender` | Kalenderansicht (Monat/Woche) | [Planung] → [Kalender] |
| `/planung/termine` | Terminliste „Meine Termine" | [Planung] → [Termine] |
| `/planung/einstellungen` | Einstellungen, Tabs [Termindarstellung] / [Kategorien] | [Planung] → [Einstellungen] |
| `/planung/ressourcen` | Ressourcenverwaltung | [Ressourcen] bzw. [Einstellungen] (OFFEN) |

Modale/Slide-ins ohne eigene Route: Plantafel-Zahnrad → Anzeigeeinstellungen;
Import/Export-Dropdown → Kalender-Import-Modal; Termin-Slide-in (erstellen/
bearbeiten); Kalender-Export-Modal. Zusätzlicher Einstieg: Dashboard-Kachel
„Einsatzplanung" (`01`) → Deep-Link auf `/planung/plantafel`. Globales
(Sidebar-Muster, Auth, Brand/A11y) siehe `00-informationsarchitektur.md`.

## Screens & Komponenten

### Plantafel (Board mit Schwimmbahnen) — Kern-Screen

- **UI-Typ & Aufbau:** Vollflächiges Board. Y-Achse = Schwimmbahnen je
  Ressource (Mitarbeiter, Fahrzeug, Werkzeug, Raum), X-Achse = Zeit. Obere
  Leiste links: Zeitraum-Navigation (`[<]`/`[>]`, KW-/Tages-Auswahl: Tag,
  3 Tage etc.), Filter nach Kategorie/Gewerk. Oben rechts: `[+ Neuer Termin]`,
  Zahnrad (Anzeigeeinstellungen), Dropdown `[Import/Export]`,
  Bearbeiten-Symbol (Ressourcen-Sichtbarkeit/Sortierung, `+`/`-` zum
  Ein-/Ausblenden von Zeilen, `[Übernehmen]`).
- **Termin-Kacheln:** liegen in den Bahnen ihrer zugewiesenen Ressourcen; ein
  n:m-Termin erscheint in **jeder** betroffenen Bahn. Farbcodierung nach
  Kategorie (Zahnrad → [Farben zuweisen nach]). Projekte im Status „Umsetzung"/
  „Vor-Ort-Termin" erscheinen automatisch bei den Projektbeteiligten.
- **Interaktion (Hero-prägend, strukturell übernehmen):**
  - Klick auf leeren Zeitraum → Termin-Slide-in (vorbelegt Ressource + Zeit).
  - Klick auf Termin → Aktionsauswahl `[Bearbeiten]`/`[Löschen]`; Hover →
    Kurzvorschau.
  - **Drag & Drop:** Ziehen an Kachelrändern = Dauer ändern; Verschieben in der
    Bahn = Zeit ändern; Verschieben in andere Bahn = Ressource **ersetzen**.
  - **Shift + Verschieben** → Optionsmenü `[Erweitern]` (Ressource zusätzlich)
    / `[Verschieben]` (Ressource ersetzen).
  - `[Kopieren]` = exakte Kopie im Bearbeiten-Modus (für individuelle Variante
    je Mitarbeiter); `[Löschen]` entfernt Termin für **alle** Zugewiesenen
    (Einzel-Entfernung nur über Bearbeiten → Zuweisung entfernen).
- **Doppelbuchung:** wird **nicht** hart gesperrt — Überlappung ist in der Bahn
  optisch sichtbar (Hero-Verhalten). MCN kann optional einen Warn-Hinweis
  ergänzen (siehe Offene Punkte), aber keine Blockade.
- **Zustände:** Laden (Skeleton-Bahnen); Leer (keine Ressourcen sichtbar →
  Hinweis auf Ressourcen-Sichtbarkeit/Zahnrad-Zeitfenster, deckt Troubleshooting
  „Termine nicht angezeigt" ab); Fehler (Board-Reload). Rollen: Disposition
  (Schreiben) vs. Nur-Lesen für eigene Bahn.
- **Shared components:** neues Board-Widget (Schwimmbahnen + Zeitachse +
  DnD) — **Neubau**, kein bestehendes Muster. Statuswechsel-Steuer und
  Logbuch aus Overview nachrangig; Anzeigeeinstellungen als Modal wiederverwenden.

### Anzeigeeinstellungen (Zahnrad-Modal)

- **UI-Typ & Aufbau:** Modal mit Planungszeitraum, **Tageszeiten** (Zeitfenster),
  sichtbare **Wochentage** (Checkboxen), Ressourcen-Anordnung/-Reihenfolge,
  `[Farben zuweisen nach]` (z. B. Kategorie), `[Übernehmen]`. Deckt zugleich das
  Troubleshooting „Meine Termine werden nicht angezeigt" ab (Zeitfenster/
  Wochentage zu eng).
- **Zustände:** rein clientseitige Anzeige-/Filtereinstellung; pro Nutzer
  persistiert (Profil-Präferenz). Kein Fachobjekt.
- **Shared components:** generisches Einstellungs-Modal.

### Termin erstellen/bearbeiten (Slide-in rechts)

- **UI-Typ & Aufbau:** rechtsseitiges Panel. Felder: Kategorie (Auswahl aus
  Terminkategorien), Titel, Projekt (optional, Button `[Zum Projekt]` als
  Sprungmarke → `04`), Start/Ende, `[Mitarbeiter und Ressourcen zuweisen]`
  (Mehrfachauswahl → n:m), Beschreibung (Freitext), `[Übernehmen]`. Bearbeiten
  identisch, vorbefüllt; Zeitraumänderung alternativ per Drag im Board.
- **Zustände:** Validierung (Start < Ende, Pflichtfelder Kategorie/Titel/Zeit);
  Speichern über `business_transaction`. Rollen: nur mit Schreibrecht.
- **Shared components:** Anlege-/Bearbeiten-Dialog (Overview), Ressourcen-Picker
  (Mehrfachauswahl) neu.

### Kalenderansicht

- **UI-Typ & Aufbau:** klassische Monats-/Wochenansicht der Termine (und
  Aufgaben, siehe `07`). Oben rechts `[Kalender exportieren]` → Export-Modal.
  Alternative Sicht auf dieselben `workflow`-Termine wie Plantafel/Liste.
- **Zustände:** Laden/Leer/Fehler wie Liste. Lesend offen.

### Kalender-Export-Modal (iCal)

- **UI-Typ & Aufbau:** Modal: Datenkategorie (Termine / Aufgaben / beides),
  Reichweite (alle / nur eigene), `[Link abrufen]` → Freigabe-Link (iCal-URL);
  Icons „kopieren" und „per Mail versenden". Deckt „Kalendersynchronisation" und
  das Duplikat „Personalplanung-Export" gemeinsam ab. **Einweg** (keine
  Rückschreibung).

### Kalender-Import-Modal (externe iCal)

- **UI-Typ & Aufbau:** Modal listet importierte Kalender (Löschen +
  Sichtbarkeits-Toggle); `[+ Kalender importieren]` → Formular: Name, URL
  (`.ics`), Ziel-Mitarbeiter/-Ressource (Einzelauswahl), optionale Freigabe für
  weitere Mitarbeiter (Mehrfachauswahl), `[Übernehmen]`; Erfolg/Fehler-Meldung.
  Erreichbar über Plantafel-Dropdown `[Import/Export]`. Deckt zugleich den
  Feiertags-Import-Use-Case ab (gleiches Formular, anderer Link). Provider-Hilfe
  (Google/Outlook/Apple) als Inline-Hinweis.

### Terminliste „Meine Termine"

- **UI-Typ & Aufbau:** Ressourcen-Liste (Overview): Termine sortiert/filterbar
  nach `[Start]`/`[Ende]`; Zeilen-Klick → Termin-Slide-in. Konsolidiert Hero's
  mehrere Einstiege („Wo finde ich anstehende Termine") zu einer Web-Liste.
- **Shared components:** Ressourcen-Liste (aus Kontakte/Liegenschaften).

### Einstellungen → Tab „Termindarstellung"

- **UI-Typ & Aufbau:** Zweispaltig — links „Termine mit Projekt/Kontakt", rechts
  „Termine ohne"; pro Zeile Auswahl des anzuzeigenden Terminfelds oder „leer
  lassen"; erste Zeile Pflicht. Steuert Feldbelegung der Terminkacheln in
  Plantafel/Kalender. (Hero: nur „Pro"-Paket — für MCN ohne Lizenzschranke.)
- **Zustände:** Speichern-Modell OFFEN (live vs. Button, siehe unten).

### Einstellungen → Tab „Kategorien"

- **UI-Typ & Aufbau:** Tabelle bestehender Terminkategorien (Name + Farbe);
  `[+ Kategorie]` (Name + Farbwahl, sonst Auto-Farbe); je Zeile Stift
  (bearbeiten) und Archivieren (statt Löschen, siehe unten). 6 Standard-
  Kategorien vordefiniert (Umsetzung, Vor-Ort-Termin, Schlechtwetter, Büro,
  Besprechung, Schule). Umbenennen wirkt auf bestehende Termine.
- **Shared components:** Ressourcen-Liste + Anlege-Dialog; Farbpicker (mit
  A11y — nie nur Farbe, immer Name/Label, siehe `00`).

### Ressourcenverwaltung

- **UI-Typ & Aufbau:** Liste vorhandener Ressourcen; `[+ Ressource]` → Formular
  „Kategorie" (Mitarbeiter/Fahrzeug/Werkzeug/Raum) + „Name"; `[Speichern]`.
  Danach in der Plantafel als Bahn einplanbar. Navigations-Position OFFEN
  (eigener Sidebar-Punkt vs. Einstellungen-Tab).
- **Shared components:** Ressourcen-Liste + Anlege-Dialog.

## API-Endpunkte (django-ninja)

| Methode | Pfad | Zweck | Auth | Service-Funktion |
|---|---|---|---|---|
| GET | `/api/planung/termine` | Termine im Zeitraum (Board/Kalender/Liste), Filter Start/Ende/Kategorie/Ressource | offen | `planung.list_termine` |
| GET | `/api/planung/termine/{id}` | Termin-Detail inkl. Zuweisungen | offen | `planung.get_termin` |
| POST | `/api/planung/termine` | Termin anlegen (n:m Mitarbeiter/Ressourcen) | Session | `planung.create_termin` (business_transaction) |
| PATCH | `/api/planung/termine/{id}` | Termin ändern (Zeit/Kategorie/Titel/Zuweisung); auch DnD-Reschedule | Session | `planung.update_termin` (business_transaction) |
| POST | `/api/planung/termine/{id}/kopieren` | Termin kopieren (individuelle Variante) | Session | `planung.copy_termin` (business_transaction) |
| POST | `/api/planung/termine/{id}/storno` | Termin stornieren (statt Löschen) | Session | `planung.storno_termin` (business_transaction) |
| GET | `/api/planung/kategorien` | Terminkategorien | offen | `planung.list_kategorien` |
| POST/PATCH | `/api/planung/kategorien[/{id}]` | Kategorie anlegen/ändern | Session | `planung.upsert_kategorie` (business_transaction) |
| POST | `/api/planung/kategorien/{id}/archivieren` | Kategorie archivieren (statt Löschen) | Session | `planung.archive_kategorie` (business_transaction) |
| GET | `/api/planung/ressourcen` | Ressourcen (Sichtbarkeit/Reihenfolge) | offen | `planung.list_ressourcen` |
| POST/PATCH | `/api/planung/ressourcen[/{id}]` | Ressource anlegen/ändern | Session | `planung.upsert_ressource` (business_transaction) |
| GET | `/api/planung/kalender/import` | importierte externe Kalender | Session | `planung.list_ical_imports` |
| POST/DELETE | `/api/planung/kalender/import[/{id}]` | externen iCal-Kalender hinzufügen/entfernen | Session | `planung.upsert_ical_import` (business_transaction) |
| POST | `/api/planung/kalender/export` | iCal-Export-Freigabelink erzeugen (Termine/Aufgaben, alle/eigene) | Session | `planung.create_ical_share` (business_transaction) |
| GET | `/api/planung/kalender/feed/{token}.ics` | öffentlicher iCal-Feed (Token-Auth) | Token | `planung.render_ical_feed` (lesend) |
| GET/PUT | `/api/planung/einstellungen/anzeige` | Anzeige-/Termindarstellungs-Präferenzen je Nutzer | Session | `planung.user_display_prefs` |

Schreibende Endpunkte **immer** über `business_transaction` (siehe `00` und
`backend/README.md`). Der öffentliche iCal-Feed ist read-only und Token-gesichert
(kein Session-Login, da externe Kalender-Clients ihn pollen).

## DB-Bezug

- **`workflow`** (bestätigt): Termin/Einsatz ist das Kern-Fachobjekt; teilt sich
  Projekt-/Auftrags-Verknüpfung mit `04`. n:m-Zuordnung Termin ↔ Mitarbeiter
  (`identity`) und Termin ↔ Ressource als Zuordnungstabelle(n). Terminkategorie
  vermutlich Lookup in `workflow`.
- **`identity`**: Mitarbeiter als zuweisbare Ressource/Bahn.
- **Ressourcen (Fahrzeug/Werkzeug/Raum): Schema OFFEN** — kein eindeutiger
  Kandidat in den vorhandenen Schemas; Klärung mit DB-Team (Kandidat
  `management` oder neues `resource`-Konzept). Blockiert Ressourcen-Bahnen und
  n:m-Ressourcenzuweisung. Bis dahin: Mitarbeiter-Bahnen zuerst bauen.
- **Abwesenheiten (Urlaub/Krankheit):** werden in der Plantafel **nur angezeigt**
  (konsumiert); Erfassung liegt vermutlich in Personal/HR (`management`/`12`) —
  hier nur Lese-Datenquelle.
- **iCal-Import-Konfiguration** und **Export-Share-Token:** Persistenz-Schema
  OFFEN (workflow oder eigenes Integrations-Schema).
- **Statusautomaten/Trigger/Constraints:** Termine erben No-Delete/Audit
  (Standard); Statuswechsel nur über Service-Tore. Doppelbuchung ist **keine**
  DB-Constraint (Hero verhindert sie nicht) — bewusst weich.

## KI-Andockpunkte (`ai.ai_proposal`)

- **Dispositionsvorschlag:** KI schlägt Zuweisung Termin↔Mitarbeiter/Ressource
  bzw. Zeitfenster vor (z. B. „freien Monteur X am Di 8–12 einplanen"), inkl.
  Doppelbuchungs-Prüfung — als `ai_proposal`, das dieselben `create_termin`/
  `update_termin`-Tore durchläuft.
- **Umplanung bei Konflikt:** KI erkennt Überlappung/Abwesenheit und schlägt
  Verschiebung vor.
- **Automatische Terminanlage aus Projekt/Vorgang:** bei Statuswechsel
  „Umsetzung"/„Vor-Ort-Termin" schlägt KI passenden Plantafel-Termin vor.
- **Kategorisierung:** KI schlägt Terminkategorie/Titel aus Projektkontext vor.
  Alle Vorschläge erscheinen im Logbuch/Feed inline und müssen freigegeben werden.

## No-Delete/Audit/GoBD-Übersetzung

- **Termin „Löschen" (Hero):** → **Storno/Archivieren** (`storno_termin`).
  Termin verschwindet aus der Bahn, bleibt aber append-only im Audit erhalten.
  Bei n:m: einzelne Mitarbeiter-/Ressourcenzuweisung entfernen = Zuweisung
  deaktivieren (nicht physisch löschen).
- **Kategorie „Löschen" (Hero):** → **Archivieren** (`archive_kategorie`) — steht
  für neue Termine nicht mehr zur Wahl; bestehende Termine behalten die
  (archivierte) Kategorie wie in Hero. Kein Hard-Delete.
- **Ressource entfernen:** → archivieren (nicht mehr in Plantafel einplanbar,
  Historie bleibt).
- **externen Kalender „entfernen":** Import-Konfig deaktivieren (Audit-Spur).
- Termine sind i. d. R. **nicht** GoBD-Belege (kein Buchungscharakter), aber
  Audit-pflichtig — Statuswechsel/Umplanungen bleiben nachvollziehbar.

## Offene Punkte / Entscheidungen

Aus der Spec übernommen:
- **Ressourcen-Schema unklar** (Fahrzeug/Werkzeug/Raum): kein eindeutiger
  DB-Schema-Kandidat — mit DB-Team klären (`management` vs. neues `resource`).
  **Blocker** für Ressourcen-Bahnen/-Zuweisung.
- **Ressourcen-Navigation widersprüchlich:** eigener Sidebar-Punkt vs.
  Einstellungen-Tab (Quellen widersprechen sich). Empfehlung: Unterpunkt
  `/planung/ressourcen`, entscheidbar.
- **Termindarstellung — Speichern-Modell:** live/auto vs. expliziter Button
  (in Hero-Text nicht genannt). Empfehlung: explizites `[Speichern]` für
  klare Rollback-Semantik.
- **Kalender-Export unidirektional:** Rückschreibung bewusst nicht vorgesehen —
  übernehmen (Zwei-Wege-Sync wäre eigenes größeres Feature).
- **Duplikate:** „Personalplanung-Export" = „Kalendersynchronisation" (ein
  Screen); „Feiertage importieren" = „Externe Kalender importieren" (ein Modal).
- **Abwesenheiten:** Erfassung nicht in Planung — Datenquelle aus HR/`12` klären,
  hier nur Anzeige.
- **App-Parität / Push-Benachrichtigungen:** Mobile-spezifisch, für Web-Leitstand
  zunächst außen vor.
- **Doppelbuchungs-Warnung:** Hero warnt nicht — entscheiden, ob MCN einen
  weichen visuellen Warn-Hinweis ergänzt (empfohlen, keine harte Sperre).

Eigene:
- **iCal-Feed-Token-Sicherheit:** unguessbares Token, Widerruf/Rotation,
  Reichweiten-Scope (nur eigene) durchsetzen.

## Abhängigkeiten

- **Auth/Rechte** (`security`, Phase 0) für alle Schreib-UIs.
- **`workflow`-Termin/Einsatz** + Verknüpfung zu **Vorgängen `04`** (Projekt-
  Sprungmarke, Auto-Einplanung bei Status Umsetzung/Vor-Ort).
- **`identity`** (Mitarbeiter) als Bahnen.
- **Ressourcen-Schema** (OFFEN, Blocker für Ressourcen-Teil).
- **Aufgaben `07`** für Kalender-Export „Aufgaben".
- **Shared components** (Ressourcen-Liste, Anlege-Dialog, Einstellungs-Modal)
  aus Phase 0; das **Board-Widget** ist Neubau dieser Sektion.
- **iCal-Bibliothek** (Feed-Rendering/Parsing) — offizielle Quelle wählen.

## Aufwand & Priorität

Empfohlene Phase: **Phase 1 — Operativer Kern** (mit `04`/`07`). Reihenfolge
innerhalb der Sektion:

| Screen/Feature | Größe | Reihenfolge |
|---|---|---|
| Terminliste „Meine Termine" (lesend) | S | 1 (schnellster Slice, Ressourcen-Liste) |
| Termin erstellen/bearbeiten (Slide-in) | M | 2 |
| Einstellungen → Kategorien (CRUD) | S | 3 |
| Kalenderansicht (Monat/Woche) | M | 4 |
| Plantafel-Board (Mitarbeiter-Bahnen, Anzeige) | L | 5 |
| Plantafel Drag & Drop + Shift-Optionen + n:m | XL | 6 |
| Anzeigeeinstellungen-Modal | S | 7 |
| Einstellungen → Termindarstellung | M | 8 |
| iCal Export-Modal + Feed | M | 9 |
| iCal Import-Modal | M | 10 |
| Ressourcenverwaltung + Ressourcen-Bahnen | L | 11 (nach Schema-Klärung) |

Kritischer Pfad ist das **Board-Widget mit DnD** (XL, Neubau) — größter
Einzelaufwand und das zentrale Wiedererkennungsmerkmal.

## Screenshots zur Vorlage (Wiedererkennung)

- **Plantafel-Hauptansicht + Zahnrad-Modal + Ressourcen-Bearbeiten-Dialog:**
  `Die wichtigsten Einstellungen in der Plantafel` (image1–6.png) — HOCH,
  layoutprägend für das Schwimmbahnen-Board.
- **Mehrfachzuweisung (n:m) Detailansicht + Shift-Optionsmenü:**
  `Plantafel - Termine mit mehreren Mitarbeiterinnen und Ressourcen erstellen`
  (image1–10) — HOCH, prägt das differenzierende n:m-Verhalten.
- **Termin-Slide-in:** `Wie kann ich einen Termin erstellen` (image1–4.png) —
  HOCH.
- **Drag-Verhalten:** `Wie kann ich einen Termin bearbeiten` (image4.gif) — HOCH
  für DnD-Interaktion.
- **Kategorien-Tabelle:** `Wie kann ich bestehende Terminkategorien ändern…`
  (image1–5.png) — MITTEL.
- **Kalender-Export-Modal:** `Kalendersynchronisation` (image1–6) — MITTEL.
- **Kalender-Import-Formular:** `Wie kann ich Externe Kalender importieren`
  (image1–5.png) — MITTEL.
