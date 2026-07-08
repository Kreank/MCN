# 04 — Vorgänge / Projekte (Hero: Projekte / Projektassistent)

## Zweck & Hero-Entsprechung

Dies ist der **operative Kern** des Leitstands: die Arbeitsfläche, auf der ein
Kundenvorgang von der Anfrage bis zur Abrechnung geführt wird. Er spiegelt Heros
Bereich **Projekte** — die **Projektmappe** (Akte eines Projekts mit Logbuch,
Checklisten, Bildern, Kalkulation …) und den **Projektassistenten/die
Projektpipeline** (Kanban nach Status). Heros konfigurierbare **Projekttypen**
entsprechen unseren **Statusautomaten** (`workflow`-Schema), die die DB physisch
über Trigger durchsetzt.

**Namens-Entscheidung (00, offener Punkt 1):** Hero-Nutzer kennen
**„Projekte"/„Projektmappe"**. Der Leitstand-Nav-Punkt heißt heute
**„Vorgänge"**. Empfehlung 00: Hero-Begriff **„Projekte"** übernehmen (max.
Wiedererkennung). Diese Sektion nennt beides und nutzt durchgängig
**„Vorgänge/Projekte"**; im UI ist der sichtbare Begriff eine Produktentscheidung
(siehe Offene Punkte).

**Wichtige Architektur-Klärung (MCN ≠ Hero 1:1):** Hero bündelt in *einem* Objekt
„Projekt" sowohl den konfigurierbaren Status-Workflow als auch die Akte/Cockpit.
Unser DB-Modell **trennt** das bewusst:

| Hero-Konzept | MCN-DB-Entität | Rolle |
|---|---|---|
| Projekt-Akte / Projektmappe (Cockpit) | `workflow.project` | Optionale Klammer für größere Maßnahmen (B-09/B-10), trägt Logbuch, Notizen, Checklisten, Bilder, Kalkulations-Cockpit; kann mehrere Liegenschaften umfassen. Minimalstatus `OPEN`/`CLOSED`. |
| Projekt-**Status-Pipeline** (Kanban, Projektassistent) | `workflow.service_case` = **Vorgang** | Der reiche, konfigurierbare Statusautomat (`NEU → IN_PRUEFUNG → … → BEAUFTRAGT → ABGESCHLOSSEN`, `ABGELEHNT`). Ein Vorgang hängt optional an einem Projekt. |
| Projekttypen individualisieren | Pipeline-Editor (`workflow.status_catalog`/`status_transition`) | Konfigurationsebene der Statusautomaten. |
| Projektordner (Heizung/Sanitär/Wartung) | `workflow.project_category` | Benutzerdefinierte Gliederung/Filter, **kein** eigener Statusautomat. |

Der Kanban/Pipeline-Screen spiegelt also **Vorgänge (service_case)** nach Status;
die Projektmappe spiegelt **das Projekt (project)** als Akte. Beide erscheinen unter
demselben Nav-Punkt und sind verlinkt. Diese Trennung ist der zentrale Umsetzungs-
Unterschied zu Hero und muss im UI kaschiert werden (ein Hero-Nutzer erwartet „ein
Projekt mit Pipeline und Akte").

**Abgedeckte Hero-Quelldateien** (Kategorie `Projekte/`):
- `Wie erstelle ich ein Projekt mit der HERO Software/…txt`
- `Was ist mit Projektdetails gemeint/…txt`
- `Wie hinterlege ich einen Projektnamen Und kann ich diesen im Projekt wieder umändern/…txt`
- `Wie kann ich die Adresse eines Projekts ändern/…txt`
- `Kann ich das Gewerk von einem Projekt nachträglich verändern/…txt`
- `Hauptansprechpartner eines Projekts ändern/…txt`
- `Kann ich Objektadressen in Projekten nutzen/…txt`
- `Kann ich ein Projekt nachträglich einem anderen Kunden zuordnen/Microsoft Word-Dokument (neu).txt`
- `Kann ich ein Projekt deaktivierenlöschen/…txt`
- `Was ist der Unterschied zwischen archivierten Projekten und Abgeschlossenen Projekten/…txt`
- `Projektstatus ändern/…txt`
- `Kann ich meinen Projektassistenten meine Projektpipeline individualisieren/…txt`
- `Wie kann ich Projekte in den Status ''spätere Projekte'' verschieben/…txt`
- `Wo finde ich laufende oder abgeschlossene Projekte/…txt`
- `Sichtbare Projekte/…txt`
- `Projektlogbuch/…txt`
- `Kann ich eine Notiz aus dem Logbuch im Nachgang bearbeiten/…txt`
- `Wo kann ich mündliche Absprachen hinterlegen/…txt`
- `Individuelle Projektfelder  Eigene Felder …/…txt`
- `Was ist der Datenerfassungsbogen/…txt`
- `Checklisten erstellen und nutzen/…txt`
- `Wie kann ich Bilder hinzufügen, löschen, sortieren  Kategorien anlegen/…txt`
- `SollIst Vergleich in Projekten (Kalkulation)/…txt`

Für alle **Querschnitts-Prinzipien** (KI-first, No-Delete/GoBD, `business_transaction`,
Auth/Rechte, WCAG/Design, shared components) gilt `00-informationsarchitektur.md` —
hier nur das für diese Sektion Spezifische.

## Ziel-Navigation & Routen

Nav-Punkt bestehend: `vorgaenge` (heute Platzhalter in `app.routes.ts`). Wir bauen
ihn zum operativen Kern aus. Routen (Angular, lazy standalone components, deutsche
Pfade wie im Bestand):

```
Vorgänge/Projekte (Sidebar-Hauptpunkt)
├── /vorgaenge                         → Pipeline/Kanban (Default) — Vorgänge nach Status
│     Filter-Leiste: Alle offenen | [je Status] | Spätere | Überfällig | Archiviert | Abgeschlossen
│     Ordner-Segmente (project_category): Alle | Heizung | Sanitär | Wartung | …
├── /vorgaenge/neu                     → Overlay-Route „Projekt/Vorgang erstellen" (Wizard/Modal)
├── /vorgaenge/:id                     → Projektmappe (Detail-„Mappe", Tab-/Sektionsstruktur)
│     ├── (Kopf, immer sichtbar)       Ansprechpartner · Erinnerung/NV-Datum · [Aktionen] · [Status ändern]
│     ├── /uebersicht (Default)        Projektdaten · Logbuch · individuelle Felder · Datenerfassungsbogen
│     ├── /checklisten
│     ├── /bilder
│     ├── /kalkulation                 Soll/Ist-Vergleich
│     ├── /beteiligte
│     ├── /dokumente                   Querverweis → 05 (im Projekt verankert)
│     └── /aufgaben                    Querverweis → 07 (Aufgaben/Termine)
│
Einstellungen (Admin, separater Bereich → 13)
├── /einstellungen/pipeline            Pipeline-Editor (Hero: Projekttypen) — Statusautomat konfigurieren
├── /einstellungen/projektordner       project_category verwalten (Heizung/Sanitär/Wartung …)
└── /einstellungen/individuelle-felder Custom-Field-Verwaltung (Projekt) — DB-Gap, siehe unten
```

**Tab vs. Kachel-Scroll (Spec-OFFEN):** Hero beschreibt die Mappe eher als
vertikal gestapelte Kacheln, nur „Projektdaten" explizit als Reiter. Empfehlung:
horizontale **Tab-Leiste** (moderner, kompakter, Deep-Linkbar per Route), aber der
Default-Tab *Übersicht* rendert Projektdaten + Logbuch + individuelle Felder +
Datenerfassungsbogen zusammen als Kachel-Stapel — so bleibt Heros Landing-Gefühl
erhalten. Reihenfolge der Tabs = Hero-Reihenfolge.

## Screens & Komponenten

### Pipeline / Kanban (Projektassistent) — Default-Screen

- **UI-Typ & Aufbau:** Board mit **Status-Spalten** aus `workflow.status_catalog`
  (entity = `service_case`), sortiert nach `sort_order`, Label aus `status_catalog.label`.
  Jede Spalte zeigt Vorgangskarten; **Spalten-Zähler**: schwarz = Gesamtzahl,
  rot = überfällig (`priority`/Reaktionsziel bzw. NV-Datum überschritten). Zusätzlich
  **Ordner-Segmente** (`project_category`, farbige Pillen via `color_hex`) und die
  Filter „Alle offenen | Spätere | Überfällig | Archiviert | Abgeschlossen".
  Karte: Vorgangsnummer (`V-JJJJ-######`), Betreff, Liegenschaft, Priorität, NV-Datum;
  Klick → Details-Button (blauer Pfeil bei Hero) öffnet die Mappe.
- **Primäraktion oben rechts:** `[+ Neu]` (siehe Erstellen-Wizard). Kein Massen-
  Statuswechsel (Hero-Einschränkung bewusst übernommen).
- **Statuswechsel per Drag&Drop:** Karte in Nachbarspalte ziehen = Statuswechsel —
  aber **nur entlang erlaubter Kanten** (`workflow.status_transition`); unzulässige
  Ziele werden als Drop-Ziel deaktiviert/verworfen. Verlangt der Übergang eine
  Begründung (`requires_reason`), öffnet die Statuswechsel-Steuer einen Grund-Dialog
  (→ `SET LOCAL app.status_reason`).
- **Zustände:** Laden = Skelett-Spalten; Leer = „Noch keine offenen Vorgänge";
  Fehler = Retry-Hinweis. **Rollen-Sichtbarkeit:** Geschäftsführer = alle; Monteur =
  nur zugewiesene Vorgänge (Hero „Sichtbare Projekte", `security`/`identity`).
- **Shared:** `Ressourcen-Liste`-Muster als Board-Variante; `Statuswechsel-Steuer`;
  `Dashboard-Kachel`-Zähler. **Neu:** Kanban-Board-Komponente (Spalten aus
  status_catalog, DnD mit Transition-Guard).

### Projektmappe — Detail-„Mappe" (zentral für Wiedererkennung)

- **Kopfbereich (immer sichtbar, kein Tab):** links Projektname + Nummer + Ordner-
  Pille; rechts **Hauptansprechpartner** (Name anklickbar → Wechsel aus Beteiligten),
  Button **[Erinnerung (Datum)]** (NV-/Wiedervorlage-Datum, Checkbox „Spätere
  Projekte"), Button **[Aktionen]** (Archivieren, Projekttyp/Ordner ändern), Button
  **[Projektstatus ändern]** (öffnet Statuswechsel-Steuer für den zugehörigen Vorgang).
- **Tab „Übersicht" (Default):**
  - *Projektdaten* (Kachel/Reiter rechts): Projektname, Gewerk, Projektanschrift
    (Objektadresse aus `property`); Stift-Button → selber Dialog wie Neuanlage
    (Name/Gewerk/Adresse ändern).
  - *Logbuch* (mittig, Unternehmensfeed): chronologische Einträge (`workflow.project_log`),
    `[+Eintrag]` mit Kategorie (NOTIZ/ANRUF/ABSPRACHE/ENTSCHEIDUNG/SYSTEM), Dokument-
    Upload, optional E-Mail-Weiterleitung, Sichtbarkeit je Eintrag. **Append-only:**
    kein Editieren/Löschen (siehe No-Delete). *Notizen* (`workflow.project_note`) sind
    die editierbare, archivierbare Variante (24h-Regel siehe Offene Punkte).
  - *Individuelle Felder* (Kachel): frei definierte Zusatzfelder (Text/Dropdown/Checkbox).
    **DB-Gap** — Tabelle existiert noch nicht (siehe Abhängigkeiten). Ist nichts
    konfiguriert: Hinweis-Kachel mit Link zur Verwaltung (nur Geschäftsführer).
  - *Datenerfassungsbogen*: web-only Freitext-Notizfeld. **DB-Gap** (siehe unten).
- **Tab „Checklisten":** Listen (`workflow.checklist`) mit Punkten
  (`workflow.checklist_item`, Position, `done_by`/`done_at`), `[+Liste]` frei oder aus
  Vorlage (`workflow.checklist_template`/`_item`). Punkt abhaken = Audit-Trigger.
  Speichern-als-Vorlage. Checklisten sind projektgebunden (nicht auftragsgebunden — Hero
  erlaubt beides; MCN-Tabelle hängt an `project_id`, siehe Offene Punkte).
- **Tab „Bilder":** Galerie mit Upload; Kategorisierung tag-basiert, „NC" =
  unkategorisiert. Speicherung über `content.file_link` (neu um `project_id` erweitert,
  Ein-Ziel-Regel), Objekt-Storage MinIO. Umkategorisieren per Stift (1:1, keine
  Mehrfachzuordnung — Hero-Design bewusst).
- **Tab „Kalkulation" (Soll/Ist):** Tabelle 3 Zeilen (SOLL/IST/DIFFERENZ), Spalten
  Stunden · Ø Lohnsatz · Lohn gesamt · Material · Kosten ges. · Umsatz. **Soll** aus
  letztem Angebot/Auftragsbestätigung (`invoicing.quote`/`quote_line` mit eingefrorenen
  `unit_cost`/`markup_percent`, → 05/08). **Ist** aus `workflow.time_entry` (Stunden ×
  `pricing.wage_group.cost_rate`/`hourly_rate`) + `workflow.material_entry` bzw.
  zugeordneten Belegen; **Umsatz** aus abgerechneten Positionen (`invoicing`). Rein
  lesend/aggregierend — keine manuelle Eingabe. Bei fehlenden Kostensätzen konservativ
  rechnen und ausweisen (nicht Gewinn erfinden — vgl. Migration 0034).
- **Tab „Beteiligte":** Projektbeteiligte (Mitarbeiter) einsehen/verwalten, Haupt-
  ansprechpartner setzen. (`identity`/`security`; Zuordnungstabelle projektbeteiligte
  ist ein DB-Gap, siehe Abhängigkeiten.)
- **Tab „Dokumente"** (Querverweis → 05) und **„Aufgaben"** (→ 07): im Projekt
  verankerte Listen, Anlage im Kontext.
- **Zustände:** Laden = Skelett-Mappe; Leer je Kachel eigenständig; Fehler pro Kachel
  isoliert (eine fehlende Kachel darf die Mappe nicht sprengen). Rollen-Sichtbarkeit:
  Datenerfassungsbogen web-only; individuelle Felder alle; Kalkulation ggf.
  kaufmännische Rolle.
- **Shared:** `Detail-Mappe` (Kopf + Tabs + Kacheln + Logbuch/Feed), `Logbuch/
  Aktivitätsfeed`, `Statuswechsel-Steuer`, `Anlege-/Bearbeiten-Dialog`.

### „Projekt/Vorgang erstellen" — Wizard/Modal

- **UI-Typ & Aufbau:** einstufiges Modal (Overlay-Route `/vorgaenge/neu`):
  Kontaktauswahl (oder `[+Neu]` Kontakt → 02) → Betreff/Projektname, Gewerk, Quelle →
  Ansprechpartner (ggf. abweichend) → **Objektadresse/Liegenschaft** (Auswahl aus
  Kontakt-Objektadressen oder `[+Neu]`, → 03) → weitere Beteiligte → `[Speichern]`.
  Ersteller wird automatisch Hauptansprechpartner.
- **Wichtig:** Anlage erzeugt einen **Vorgang** (`service_case`, Pflicht:
  `property_id`, `subject`), optional gekoppelt an ein **Projekt** (`project`) als
  Klammer. Für kleine Maßnahmen genügt der Vorgang ohne Projekt (B-09). Der Meldende
  wird **nie automatisch Auftraggeber** (A-01).
- **Zustände:** Validierung inline; Speichern über `business_transaction`; danach
  Redirect auf die Mappe.
- **Shared:** `Anlege-/Bearbeiten-Dialog`.

### Pipeline-Editor (Hero: Projekttypen) — `/einstellungen/pipeline`

- **UI-Typ & Aufbau:** Verwaltung der Statusautomaten je Entity (`service_case`,
  `work_order`, `service_job`, `quote`). Status-Katalog (`status_catalog`: Label,
  `sort_order`, `is_initial`/`is_final`/`is_frozen`) als Spalten; **Übergangs-Editor**
  (`status_transition`: from→to, `requires_reason`) als Kanten-Matrix/Graph.
  `[+Übergang]`, Übergang entfernen. Jede Änderung erzeugt `pipeline_change` (Audit).
- **Guards, die die UI respektieren muss:** keine Selbstkanten; kein Übergang verlässt
  `is_final` (z. B. Auftrag `ABGERECHNET`); keine Kante von `is_frozen`→nicht-frozen
  (Angebots-Freeze). Neue Status **erfinden ist gesperrt** — der CHECK der Entity-Tabelle
  bleibt die Wahrheit (nur Katalog-Status wählbar).
- **Rolle:** Admin/Geschäftsführer. Hoher DB-Bezug (siehe unten).
- **Shared:** `Anlege-/Bearbeiten-Dialog`, `Statuswechsel-Steuer`-Vokabular.

### Projektordner — `/einstellungen/projektordner`

- CRUD auf `workflow.project_category` (Name, `color_hex`, `sort_order`, aktiv/inaktiv).
  Löschen erlaubt: enthaltene Projekte wandern eine Ebene hoch (API-Transaktion löst
  Zuordnung, FK schützt). Reines Gliederungs-/Filtermittel, kein Statusautomat.

## API-Endpunkte (django-ninja)

Alle Pfade unter `/api`. Lesend = offen (Dev-Phase), schreibend = Session +
`app_user`, **immer** über `db_core.db_context.business_transaction`.

| Methode | Pfad | Zweck | Auth | Service-Funktion (Vorschlag) |
|---|---|---|---|---|
| GET | `/vorgaenge` | Pipeline: Vorgänge gefiltert nach Status/Ordner/Fälligkeit | offen | `list_service_cases` |
| GET | `/vorgaenge/pipeline` | Spalten + Zähler (schwarz/rot) aus status_catalog | offen | `pipeline_columns` |
| POST | `/vorgaenge` | Vorgang (opt. + Projekt) anlegen | Session | `create_service_case` |
| GET | `/vorgaenge/{id}` | Mappe-Kopf + Projektdaten | offen | `get_service_case` |
| PATCH | `/vorgaenge/{id}` | Projektdaten (Name/Gewerk/Adresse) ändern | Session | `update_project_data` |
| POST | `/vorgaenge/{id}/status` | Statuswechsel (+ `status_reason`, NV-Datum) | Session | `change_status` |
| POST | `/vorgaenge/{id}/erinnerung` | NV-Datum / „Spätere Projekte" setzen | Session | `set_reminder` |
| POST | `/vorgaenge/{id}/archivieren` | Archivieren (Grund Pflicht) / Reaktivieren | Session | `archive_project` |
| PATCH | `/vorgaenge/{id}/ansprechpartner` | Hauptansprechpartner wechseln | Session | `set_main_contact` |
| GET/POST | `/vorgaenge/{id}/logbuch` | Logbuch lesen / Eintrag anlegen (append-only) | offen/Session | `list_log` / `add_log_entry` |
| GET/POST/PATCH | `/vorgaenge/{id}/notizen` | Notizen (editierbar, archivieren) | offen/Session | `notes_*` |
| GET/POST | `/vorgaenge/{id}/checklisten` | Checklisten + Punkte, aus Vorlage | offen/Session | `checklists_*` |
| PATCH | `/checklisten/punkte/{id}` | Punkt abhaken (done_by/done_at) | Session | `toggle_checklist_item` |
| GET/POST/DELETE | `/vorgaenge/{id}/bilder` | Bilder Upload/Kategorie (MinIO, file_link) | offen/Session | `images_*` |
| GET | `/vorgaenge/{id}/kalkulation` | Soll/Ist-Aggregation | offen | `soll_ist` |
| GET/POST | `/vorgaenge/{id}/beteiligte` | Projektbeteiligte | offen/Session | `participants_*` |
| GET/POST/DELETE | `/einstellungen/pipeline` | Statuskatalog/Übergänge lesen/ändern | offen/Session | `pipeline_*` |
| GET/POST/PATCH/DELETE | `/einstellungen/projektordner` | project_category CRUD | offen/Session | `categories_*` |
| GET/POST/PATCH/DELETE | `/checklisten/vorlagen` | Checklisten-Vorlagen | offen/Session | `checklist_templates_*` |

Statuswechsel setzt vor dem UPDATE `SET LOCAL app.status_reason` (und
`app.current_user_id`), damit `validate_status_change`/`log_status_change` greifen —
Retry-Pflicht beachten (siehe `db/README.md`).

## DB-Bezug

Betroffene Schemas/Tabellen (Quelle: `db/migrations/*.sql`):

- **`workflow.project`** (0011): Projekt-Klammer; `project_number` `P-JJJJ-######`,
  `status` `OPEN`/`CLOSED`, `responsible_user_id`, `category_id` (0043).
  `workflow.project_property` (n:m Liegenschaften, B-10).
- **`workflow.service_case`** = Vorgang (0012): `case_number` `V-JJJJ-######`,
  `project_id` (optional), `property_id` (Pflicht), `subject`, `priority`, `status`
  (7 Werte), `responsibility_scope` (A-21, KI darf nur vorprüfen), Meldender ≠
  Auftraggeber (A-01). Status-Trigger: `enforce_initial_status('NEU')`,
  `validate_status_change('service_case')`, `log_status_change`.
- **Statusautomat-Infrastruktur** (0010): `workflow.status_transition` (erlaubte
  Kanten, `requires_reason`), `workflow.status_change` (append-only Protokoll),
  `workflow.priority_level`, `workflow.number_range`/`next_number`.
- **Pipeline-Editor** (0042): `workflow.status_catalog` (Label/Reihenfolge/
  `is_initial`/`is_final`/`is_frozen`), FK-Härtung von `status_transition`,
  `guard_pipeline_config` (final/frozen-Invarianten), `workflow.pipeline_change`
  (append-only Audit).
- **Projektordner** (0043): `workflow.project_category`.
- **Cockpit** (0035): `workflow.project_log` (append-only), `workflow.project_note`
  (editierbar+Audit, archivieren), `workflow.checklist(_item)`,
  `workflow.checklist_template(_item)`; `content.file_link` um `project_id` erweitert
  (Ein-Ziel-CHECK) für Bilder.
- **Kalkulation** (0033/0034): `pricing.wage_group` (`hourly_rate` VK,
  `cost_rate` intern), `invoicing.quote_line`/`invoice_line` (eingefrorene
  `unit_cost`/`markup_percent`), `invoicing.beleg_rubrik`. Ist-Werte:
  `workflow.time_entry`, `workflow.material_entry` (0017).

**Statusautomaten/Constraints, die die UI respektieren muss:**
- Kanban-Drops nur entlang `status_transition`-Kanten; `requires_reason`-Kanten
  erzwingen Grund-Dialog → `app.status_reason`.
- Neue Zeilen starten im Anfangsstatus (`enforce_initial_status`); die UI darf keinen
  „fortgeschrittenen" Status beim Anlegen anbieten.
- `project_log` ist append-only → kein Edit-/Löschen-UI; Notizen stattdessen.
- Pipeline-Editor: keine Selbstkanten, `is_final`/`is_frozen`-Guards, keine neuen
  Status.

## KI-Andockpunkte (`ai.ai_proposal`)

Die KI geht durch **dieselben Service-Tore** wie der Mensch (Statusautomat, Audit):
- **Vorgang anlegen** aus eingehender Kommunikation/Meldung (Mail/Telefonnotiz) —
  Vorschlag mit vorbelegtem Betreff, Liegenschaft, Priorität. Verantwortungs-Scope
  darf die KI nur **vorprüfen**, nie bestätigen (A-21, `responsibility_scope` bleibt
  `UNKNOWN` bis Fachrolle bestätigt).
- **Statuswechsel vorschlagen** (z. B. „In Prüfung → Freigabe ausstehend"), inkl.
  Begründungstext für `requires_reason`-Kanten.
- **Logbuch-Zusammenfassung/Eintrag** vorschlagen (Anruf transkribiert → ABSPRACHE-Eintrag).
- **Checkliste aus Vorlage** vorschlagen passend zum Gewerk.
- **Soll/Ist-Abweichung** proaktiv melden (Ist-Lohn läuft aus dem Ruder → Vorschlag
  Nachtragsangebot, → 05).
- **Nächste Aktion / Erinnerung** (NV-Datum) vorschlagen.

Jeder Vorschlag ist ein `ai.ai_proposal`, den ein Mensch freigibt; erst die Freigabe
löst den echten `business_transaction`-Write aus. Kein KI-Sonderweg an Triggern vorbei.

## No-Delete/Audit/GoBD-Übersetzung

Wo Hero „löschen/bearbeiten" sagt, setzen wir Archivieren/Storno/neue Version um:

- **Projekt „löschen" → archivieren:** `[Aktionen] → Archivieren` mit Pflicht-Grund;
  Reaktivieren jederzeit. Passt exakt zu Hero (Projekte sind ohnehin nur archivierbar)
  und unserem Schutzstandard. „Abgeschlossen" ≠ „Archiviert" (Endstatus vs. deaktiviert;
  aus „Abgeschlossen" heraus nicht archivierbar).
- **Logbuch-Einträge:** append-only (`project_log`, Trigger `forbid_mutation`) — keine
  Korrektur, nur neuer Eintrag. Für editierbaren Bedarf: **Notizen** (`project_note`,
  Audit + archivieren statt löschen).
- **Checklisten-Punkte:** Abhaken/Ändern per Audit-Trigger protokolliert.
- **Pipeline-Änderungen:** `pipeline_change` (append-only) statt stiller Rekonfiguration.
- **Bilder:** file_link entfernen ist eine Zuordnungsauflösung; das Storage-Objekt bleibt
  auditierbar (MinIO). (OFFEN: exakte Löschsemantik für Bilder, siehe unten.)
- **Kalkulation** ist rein abgeleitet (keine eigene Persistenz zu löschen).

## Offene Punkte / Entscheidungen

- **Projekt vs. Vorgang im UI (zentral):** Präsentieren wir ein Hero-nahes „ein
  Projekt mit Pipeline+Akte" (project als Träger, service_case im Hintergrund) oder
  zeigen wir Vorgänge und Projekte getrennt? Empfehlung: **eine Mappe**, die Projekt
  (Akte) und den zugehörigen Vorgang (Status) vereint; Kanban zeigt Vorgänge.
  Produktentscheidung nötig, weil DB-Trennung ≠ Hero-Modell.
- **UI-Begriff:** „Projekte" (Hero, Empfehlung 00) vs. bestehendes „Vorgänge" im Nav.
- **Individuelle Felder & Datenerfassungsbogen:** **DB fehlt** — braucht neue Migration
  (generisches Custom-Field-Definition/-Value am Projekt; web-only Notizfeld). Feldtyp
  nach Erstellung unveränderlich, gelöschte-aber-befüllte Felder read-only (Hero-Regel).
- **Projektbeteiligte:** Zuordnungstabelle Projekt↔Mitarbeiter existiert noch nicht
  (nur `responsible_user_id` am Projekt) — Migration nötig.
- **Checklisten auch an Aufträgen:** Hero erlaubt Checklisten in Projekt *oder* Auftrag;
  MCN-Tabelle hängt nur an `project_id`. Generalisieren (polymorph) oder projektgebunden
  lassen? (Fachliche Entscheidung.)
- **24h-Editierfenster für Notizen:** Hero-Regel (nur eigene, nur 24 h). Als
  DB-Constraint/Trigger übernehmen? (`project_note` ist derzeit frei editierbar mit Audit.)
- **Kunde nach Anlage unveränderlich** (Hero): übernehmen oder Neuzuordnung erlauben?
  (Vorgang bindet über `reported_by`/property, nicht hart an einen „Kunden" — prüfen.)
- **„Spätere Projekte"** als NV-Flag (Hero) vs. eigener Status: 00/Spec empfiehlt
  eher eigenen Status; hier als NV-Datum + Flag modelliert (kein DB-Feld dafür vorhanden
  → Migration oder Ableitung aus NV-Datum + Zähler-Ausschluss klären).
- **Bilder-Löschsemantik & Mehrfach-Kategorie** (Hero: 1:1, kein Batch) — MCN-Design
  bestätigen oder n:m erlauben.
- **Soll/Ist noch in Entwicklung** (Hero selbst unfertig, Skonto fehlt) — als Zielbild
  weiterdenken statt 1:1.

## Abhängigkeiten

- **Vorher:** Auth/Login + Rechtematrix (`security`), shared components (Liste/Mappe/
  Dialog/Statuswechsel-Steuer/Logbuch) — 00 Phase 0.
- **DB vorhanden:** `workflow.project/service_case/status_*/project_log/note/checklist*/
  project_category`, `content.file_link.project_id`, `pricing.wage_group`,
  `invoicing.quote*`, `workflow.time_entry/material_entry`.
- **DB-Neu (Migrationen) nötig:** Custom-Fields + Datenerfassungsbogen; Projektbeteiligte-
  Zuordnung; ggf. „Spätere Projekte"-Feld; ggf. polymorphe Checklisten; ggf.
  Notiz-24h-Constraint.
- **Querverweise:** Kontakte (02), Liegenschaften (03), Dokumente (05, Angebot→Soll,
  Kalkulation), Planung/Einsätze (06), Aufgaben (07), Artikel/Leistungen (08, Kalkulation),
  Einstellungen (13, Pipeline-Editor), Wartung (11, kann Vorgänge auslösen).

## Aufwand & Priorität

Phase 1 „Operativer Kern" (00). Empfohlene Reihenfolge:

| Screen | Größe | Reihenfolge |
|---|---|---|
| Pipeline/Kanban (service_case nach Status) | **L** | 1 |
| Projekt/Vorgang erstellen (Wizard) | **M** | 2 |
| Projektmappe-Gerüst (Kopf + Tabs + Übersicht/Projektdaten) | **L** | 3 |
| Logbuch + Notizen | **M** | 4 |
| Checklisten (+ Vorlagen) | **M** | 5 |
| Bilder (MinIO/file_link) | **M** | 6 |
| Statuswechsel-Steuer + Archivieren/Erinnerung | **M** | 7 (quer zu 1/3) |
| Pipeline-Editor (Einstellungen) | **L** | 8 |
| Projektordner-Verwaltung | **S** | 9 |
| Kalkulation Soll/Ist | **L** | 10 (nach 05/08) |
| Individuelle Felder + Datenerfassungsbogen (inkl. DB) | **M** | 11 |
| Beteiligte (inkl. DB) | **S–M** | 12 |

## Screenshots zur Vorlage (Wiedererkennung)

HOCH-Wiedererkennung, beim Bau als visuelle Vorlage:
- `Wo finde ich laufende oder abgeschlossene Projekte/` image1–3 — **Pipeline/Kanban**
  mit Status-Spalten und Zählern (schwarz/rot).
- `Was ist mit Projektdetails gemeint/` image1–3 — **Projektmappe-Gesamtaufbau**.
- `Projektlogbuch/` image1–5 — **Logbuch/Feed**.
- `Checklisten erstellen und nutzen/` image1–14 — **Checklisten** (Listen + Punkte).
- `Kann ich meinen Projektassistenten meine Projektpipeline individualisieren/`
  image2–12 — **Projekttypen-/Pipeline-Editor** (Statusautomat-Konfiguration).
- `Kann ich ein Projekt deaktivierenlöschen/` image1–5 — **Archivieren/Reaktivieren**.
- `Projektstatus ändern/` image1–5 — **Statuswechsel-Dialog**.
- `Wie hinterlege ich einen Projektnamen …/` image1–12 — **Projektdaten-Reiter**.
- `Wie kann ich Bilder hinzufügen …/` image1–7 — **Bilder-Galerie/Kategorien**.
- `SollIst Vergleich in Projekten (Kalkulation)/` image1 — **Kalkulations-Tabelle**.
