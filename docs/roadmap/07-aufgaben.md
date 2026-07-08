# 07 — Aufgaben (Hero: Aufgaben)

## Zweck & Hero-Entsprechung

Diese Sektion bildet Hero's Bereich **Aufgaben** ab: kurze To-dos mit
Beschreibung, zuständigem Mitarbeiter, Fälligkeit und optionaler Verknüpfung zu
Projekt und/oder Kontakt — zugleich persönliche Erinnerung („Kunde
zurückrufen") und Delegationsmittel im Team. Aufgaben erscheinen an vier
Einstiegspunkten: als **globaler Sidebar-Bereich** (Liste + Filter), als
**Dashboard-Kachel** auf der Übersicht (Zähler + nächstfällige), sowie
**eingebettet** in die Kontakt- und Projekt-Detailmappe (Tab „Aufgaben" mit
Unterreitern Offen/Erledigt). Der Datenbezug ist durchgängig `workflow`
(Aufgabe), `identity` (Kontakt/Kunde) und `security.app_user` (zuständiger
Mitarbeiter). Global-übergreifende Muster (Liste, Mappe, Dialog, No-Delete,
Auth, KI-first) siehe `00-informationsarchitektur.md` — hier nur das
Aufgaben-Spezifische.

**Abgedeckte Hero-Quelldateien:**
- `Aufgaben\Wie erstelle ich eine Aufgabe direkt für einen Kontakt\Wie erstelle ich eine Aufgabe direkt für einen Kontakt.docx`
- `Aufgaben\Wie kann ich Aufgaben erstellen, delegieren und als erledigt markieren\Wie kann ich Aufgaben erstellen, delegieren und als erledigt markieren.docx`
- `Aufgaben\Wie kann ich eine Aufgabe bearbeiten\Wie kann ich eine Aufgabe bearbeiten.docx`
- `Aufgaben\Wo finde ich Aufgaben in meiner HERO App wieder\Wo finde ich Aufgaben in meiner HERO App wieder.docx`
- `Aufgaben\Wo finde ich Aufgaben zum Projekt\Wo finde ich Aufgaben zum Projekt.docx`
- `Aufgaben\Wo kann ich sehen welche Aufgaben anstehen\Wo kann ich sehen welche Aufgaben anstehen.docx`
- `Aufgaben\Wo sehe ich die erledigten Aufgaben eines Kontaktes\Wo sehe ich die erledigten Aufgaben eines Kontaktes.docx`
- `Aufgaben\Wo sehe ich offene Aufgaben für einen Kontakt\Wo sehe ich offene Aufgaben für einen Kontakt.docx`

## Ziel-Navigation & Routen

Sidebar-Hauptpunkt **„Aufgaben"** (Hero-Begriff 1:1 übernommen; hoher
Wiedererkennungswert, ersetzt zugleich die mobile Bottom-Nav aus der Hero-App
durch einen schnell erreichbaren Web-Einstieg).

| Route | Screen | Bemerkung |
|---|---|---|
| `/aufgaben` | Globale Aufgabenliste | Filter Mitarbeiter/Projekt/Kontakt/Status; `[+ Aufgabe]` oben rechts |
| `/aufgaben` (Modal) | Dialog „Aufgabe hinzufügen" | Query-Param `?neu=1`; aus jedem Kontext aufrufbar |
| `/aufgaben/:id` (Modal) | Dialog „Aufgabe bearbeiten" | Stift-Icon-Pattern |

**Eingebettet (kein eigenes Routing-Präfix, Unterreiter bestehender Mappen):**
- `/kontakte/:id` → Tab **„Aufgaben"** → Segment **Offen** / **Erledigt**
  (spiegelt Hero's linke Spaltennavigation „Aufgabe → Offene/Erledigte").
- `/projekte/:id` (Projektmappe, `04`) → Tab **„Aufgaben"** → Segment
  **Offen** / **Erledigt**.
- `/` (Übersicht, `01`) → **Dashboard-Kachel „Aufgaben"** → Link `/aufgaben`.

Reihenfolge/Benennung der Unterreiter (Offen zuerst, dann Erledigt) und der
Tab-Position in der Mappe folgen Hero, damit umsteigende Kollegen sie am
erwarteten Ort finden.

## Screens & Komponenten

### Globale Aufgabenliste (`/aufgaben`)
- **UI-Typ & Aufbau:** shared **Ressourcen-Liste** (siehe `00`). Spalten:
  Bezeichnung, Zuständiger (Mitarbeiter), Fälligkeit, Verknüpfung
  (Projekt/Kontakt), Status. Filter-Segmente: **Offen / Erledigt / Alle**;
  zusätzliche Filter Mitarbeiter, Projekt, Kontakt. Primäraktion `[+ Aufgabe]`
  oben rechts. Zeilen-Aktionen rechts: **Erledigt-Toggle** (Checkbox),
  **Stift** (bearbeiten). Sortierung Default nach Fälligkeit aufsteigend
  (überfällige zuerst hervorgehoben — nicht nur farblich, auch mit Icon/Label,
  WCAG).
- **Zustände:** Laden (Skeleton-Zeilen), Leer („Keine offenen Aufgaben"),
  Fehler (Retry). Rollen-Sichtbarkeit: Lesen dev-offen; Anlegen/Delegieren/
  Erledigen/Bearbeiten nur mit Session + `app_user` (siehe `00`, Auth).
- **Wiederverwendet:** Ressourcen-Liste, Statuswechsel-Steuer (Erledigt-Toggle),
  Anlege-/Bearbeiten-Dialog. **Neu:** aufgabenspezifische Spaltenkonfiguration
  und Fälligkeits-/Überfällig-Darstellung.

### Dialog „Aufgabe hinzufügen / bearbeiten"
- **UI-Typ & Aufbau:** Modal (shared Anlege-/Bearbeiten-Dialog). Felder:
  **Bezeichnung** (Pflicht), **Zuständiger Mitarbeiter** (`app_user`-Auswahl =
  Delegation), **Fälligkeit** (Datum), **Verknüpfung** Projekt *oder* Kontakt
  (siehe Offene Punkte — Alternative vs. beides), optional **Beschreibung**,
  optional **Priorität** (falls `workflow.priority_level` genutzt wird).
  Beim Anlegen aus einem Kontext (Kontakt-/Projekt-Tab) ist die jeweilige
  Verknüpfung **vorbelegt und gesperrt**. Aktionen: `[Speichern]` und
  `[Speichern & neu]` (Hero-Verhalten: Formular bleibt offen, für schnelles
  Serien-Anlegen). Bearbeiten: identisches Formular, via Stift-Icon geöffnet.
- **Zustände:** Validierung (Bezeichnung nicht leer), Speichern-Spinner,
  Fehler-Feedback. Bearbeiten nur für Berechtigte.
- **Wiederverwendet:** shared Dialog + Formular-Bausteine. **Neu:** Feldsatz
  Aufgabe, `[Speichern & neu]`-Logik.

### Kontakt-Detail — Tab „Aufgaben" (Offen / Erledigt)
- **UI-Typ & Aufbau:** In die **Kontaktmappe** (`02`) eingebetteter Tab mit
  zwei Segmenten. **Offen:** Liste mit Beschreibung, Fälligkeit, zuständigem
  Mitarbeiter; `[+ Aufgabe]` (Kontakt vorbelegt). **Erledigt:** Historie der
  erledigten Aufgaben dieses Kontakts (Beschreibung, erledigt-am/-von).
- **Zustände:** Leer je Segment getrennt; Zähler-Badge am Tab („Aufgaben (3)")
  für offene. Erledigt-Toggle verschiebt eine Zeile live von Offen → Erledigt.
- **Wiederverwendet:** dieselbe Aufgaben-Listenkomponente wie global, hier auf
  `kontakt_id` gefiltert; Dialog mit vorbelegtem Kontakt.

### Projekt-Detail — Tab „Aufgaben" (Offen / Erledigt)
- **UI-Typ & Aufbau:** Analog Kontakt-Tab, in die **Projektmappe** (`04`)
  eingebettet, gefiltert auf `projekt_id`. `[+ Aufgabe]` mit vorbelegtem
  Projekt; Aufgaben hier auch als reine Erinnerung nutzbar. Bei Statuswechsel-
  bzw. Logbuch-Bezug: Erledigung kann optional einen `workflow.project_log`-
  Eintrag (Kategorie `SYSTEM`) erzeugen (siehe DB-Bezug).
- **Zustände:** wie Kontakt-Tab; Zähler-Badge für offene Projektaufgaben.
- **Wiederverwendet:** Aufgaben-Liste + Dialog; Einbettung analog Kontakt-Tab.

### Dashboard-Kachel „Aufgaben" (auf `/`)
- **UI-Typ & Aufbau:** shared **Dashboard-Kachel** (siehe `00`, Sektion `01`).
  Zeigt **Zähler offener Aufgaben** + Vorschau der **nächstfälligen** (2–3
  Zeilen), Link/Button „Aufgaben anzeigen" → `/aufgaben`. Überfällige optisch
  **und** textlich markiert.
- **Zustände:** Laden, Leer („Keine anstehenden Aufgaben"), Fehler.
- **Wiederverwendet:** Dashboard-Kachel-Baustein; Datenquelle = gefilterte
  Aufgabenliste (Status offen, sortiert nach Fälligkeit).

## API-Endpunkte (django-ninja)

Voraussichtliches Ressourcen-Präfix `/api/aufgaben`. Genaue Tabellen-/
Feldnamen nach Anlage des DB-Objekts verifizieren (siehe DB-Bezug, Offene
Punkte).

| Methode | Pfad | Zweck | Auth | Service-Funktion |
|---|---|---|---|---|
| GET | `/api/aufgaben` | Globale Liste, Filter `status`,`mitarbeiter`,`projekt`,`kontakt` | offen (dev) | `aufgaben.list_tasks` (lesend) |
| GET | `/api/aufgaben/{id}` | Einzelaufgabe | offen (dev) | `aufgaben.get_task` (lesend) |
| GET | `/api/kontakte/{id}/aufgaben` | Aufgaben eines Kontakts, `status`-Filter | offen (dev) | `aufgaben.list_by_contact` (lesend) |
| GET | `/api/projekte/{id}/aufgaben` | Aufgaben eines Projekts, `status`-Filter | offen (dev) | `aufgaben.list_by_project` (lesend) |
| GET | `/api/aufgaben/uebersicht` | Kachel-Aggregat (Zähler + nächstfällige) | offen (dev) | `aufgaben.dashboard_summary` (lesend) |
| POST | `/api/aufgaben` | Anlegen/Delegieren (Zuständiger, Fälligkeit, Verknüpfung) | Session + `app_user` | `aufgaben.create_task` → `business_transaction` |
| PATCH | `/api/aufgaben/{id}` | Bearbeiten (Beschreibung/Mitarbeiter/Fälligkeit) | Session + `app_user` | `aufgaben.update_task` → `business_transaction` |
| POST | `/api/aufgaben/{id}/erledigen` | Als erledigt markieren (setzt erledigt-von/-am) | Session + `app_user` | `aufgaben.complete_task` → `business_transaction` |
| POST | `/api/aufgaben/{id}/wieder-oeffnen` | Erledigung zurücknehmen (falls fachlich erlaubt) | Session + `app_user` | `aufgaben.reopen_task` → `business_transaction` |

Alle schreibenden Endpunkte laufen ausschließlich über
`db_core.db_context.business_transaction` (siehe `00`).

## DB-Bezug

**Wichtigster Befund: In `workflow` existiert derzeit KEINE Aufgaben-/Task-
Tabelle.** Vorhanden sind `workflow.project`, `service_case`, `work_order`,
`service_job`, `project_log`, `project_note`, `checklist(_item)`,
`priority_level`, `status_transition`/`status_change`. Diese Sektion setzt
daher eine **neue Fachtabelle voraus** (z. B. `workflow.task`), anzulegen als
**Hand-SQL-Migration** (`RunSQL`, Model `managed=False`) gemäß `db/README.md`
und `backend/README.md` — kein ORM-DDL.

Ableitbarer Mindest-Aufbau der neuen Tabelle (an bestehenden Mustern wie
`project_note`/`checklist_item` orientiert):
- `id uuid PK`, `bezeichnung text NOT NULL`, `beschreibung text NULL`,
  `assignee_user_id uuid NOT NULL REFERENCES security.app_user(id)`
  (= Zuständiger/Delegationsziel),
- `due_date date NULL` (Fälligkeit),
- **genau eine** oder **beide** Verknüpfungen `kontakt_id uuid NULL REFERENCES
  identity.…`, `projekt_id uuid NULL REFERENCES workflow.project(id)` —
  Kardinalität ist OFFEN (s.u.),
- optional `priority_level_id uuid NULL REFERENCES workflow.priority_level(id)`,
- Erledigung nach dem bewährten Muster aus `workflow.checklist_item`:
  `done_by uuid NULL REFERENCES security.app_user(id)`, `done_at timestamptz
  NULL`, mit `CHECK ((done_by IS NULL) = (done_at IS NULL))` — „erledigt = wer
  UND wann, nie nur eines von beidem".
- `created_by uuid NOT NULL`, `created_at`, `updated_at`.

**Schutzstandard erben** (siehe `00`, No-Delete): `updated_at`-Trigger,
`audit_row_update`, `forbid_mutation` auf DELETE/TRUNCATE, `REVOKE
DELETE,TRUNCATE`. Status „offen/erledigt" wird über `done_at`
(NULL = offen) abgeleitet — kein separater Statusautomat nötig, aber die UI
respektiert die Erledigt-Semantik als append-artigen Zustandswechsel.
Optionaler Bezug: Erledigung/Anlage im Projektkontext kann einen
`workflow.project_log`-Eintrag (`category='SYSTEM'`) schreiben.

## KI-Andockpunkte (`ai.ai_proposal`)

Die KI schlägt Aufgaben durch **dieselben Service-Tore** vor wie ein Mensch
(kein KI-Sonderweg). Ein Vorschlag ist ein `ai.ai_proposal`-Datensatz ohne
fachliche Wirkung, gebunden an `payload_hash`, `target_type`/`target_id`,
`target_version` und `expires_at`; die Ausführung erfolgt erst nach Freigabe
über die Fach-API (`create_task`/`update_task`).

Konkrete Andockpunkte:
- **Aufgabe vorschlagen** (`proposal_type='TASK_CREATE'`, `target_type='WORKFLOW_TASK'`):
  KI leitet aus Kontext (E-Mail „bitte zurückrufen", Einsatzbericht,
  überfälliger Vorgang) eine Aufgabe mit vorgeschlagenem Zuständigen,
  Fälligkeit und Projekt-/Kontakt-Verknüpfung ab. Anzeige als Vorschlag in der
  Liste bzw. im Logbuch/Feed der Mappe, mit Aktionen `[Übernehmen]` /
  `[Ablehnen]` (Ablehnung mit `rejection_reason`).
- **Delegation vorschlagen** (`TASK_UPDATE`): KI schlägt einen Wechsel des
  Zuständigen (`assignee_user_id`) vor — Freigabe durch Berechtigten.
- **Fälligkeit/Erinnerung vorschlagen:** KI schlägt Fälligkeit oder
  Nachfass-Aufgabe vor.
- **Erledigung vorschlagen** (`TASK_COMPLETE`): KI markiert einen Task als
  wahrscheinlich erledigt (z. B. Rückruf laut Kommunikationsverlauf erfolgt) —
  wird **nie** automatisch erledigt, sondern nur zur Bestätigung vorgeschlagen.

Freigabe-Vorschläge folgen der Ablauf-/Hash-Bindung aus Migration 0027; die
UI zeigt Vorschläge sichtbar getrennt von bestätigten Aufgaben (Herkunft „KI"
mit Text/Icon, nicht nur Farbe).

## No-Delete/Audit/GoBD-Übersetzung

Hero kennt bei Aufgaben ausdrücklich **kein Löschen** — nur „erledigt
markieren". Das passt bereits zum MCN-Standard und wird so umgesetzt:
- **„Erledigen"** = `done_by`/`done_at` setzen (kein physisches Verschieben,
  kein Löschen); die Zeile wandert per Filter unter „Erledigt". Rücknahme über
  `wieder-oeffnen` ist ein neuer, auditierter Zustandswechsel.
- **„Bearbeiten"** = UPDATE mit `audit_row_update`-Trigger; jede Änderung
  (Beschreibung, Zuständiger, Fälligkeit) ist im Audit nachvollziehbar.
- **Kein Löschbutton.** Falls fachlich eine Aufgabe „verschwinden" soll,
  wird sie **archiviert** (Status-/Flag-Feld analog `project_note.status
  = 'ARCHIVIERT'`), nicht gelöscht. GoBD-Relevanz ist bei reinen To-dos gering,
  der Audit-/No-Delete-Standard gilt trotzdem (siehe `00`).

## Offene Punkte / Entscheidungen

- **Neue DB-Tabelle nötig:** `workflow.task` (o. ä.) existiert nicht und muss
  per Hand-SQL-Migration angelegt werden — Namen/Felder mit DB-Owner
  abstimmen, bevor Service/API gebaut werden. (Blocker für alle Schreibpfade.)
- **Verknüpfung Projekt vs. Kontakt:** Hero-Formulierung („Projekt *oder*
  Kunde") legt Alternative nahe, ist aber nicht eindeutig. Entscheidung:
  entweder `CHECK (num_nonnulls(kontakt_id, projekt_id) <= 1)` (Alternative)
  oder beide gleichzeitig erlauben. Empfehlung: **beide optional erlauben**
  (Aufgabe kann Projekt- und Kontaktbezug haben), da Kontext im
  Gebäudeservice oft beides betrifft — zu bestätigen.
- **Mehrere Zuständige / Delegation an Team:** Hero spricht durchgängig von
  *einem* Mitarbeiter. Vorläufig **ein** `assignee_user_id`; Team-Delegation
  offen.
- **Priorität/Kategorien/Wiederholung/Reminder:** In Hero-Doku nicht erwähnt.
  `workflow.priority_level` existiert und könnte optional angebunden werden;
  wiederkehrende Aufgaben und Reminder-Mechanik sind **nicht** Teil des
  Hero-Scopes — separat (ggf. KI-gestützt) zu konzipieren.
- **Bearbeiten: Modal vs. Inline:** Hero-Quelle ohne Screenshots, UI-Form nicht
  belegt. Entscheidung für MCN: **Modal** (konsistent mit shared Dialog).
- **Mobile Bottom-Nav** (Hero-App) ist für das Web-Frontend nicht 1:1
  relevant; als Sidebar-Hauptpunkt umgesetzt (native App später, siehe `00`).
- **Erledigung → Logbuch:** Ob Anlegen/Erledigen automatisch einen
  `project_log`-Eintrag erzeugt, ist eine Produktentscheidung (Default: ja im
  Projektkontext, Kategorie `SYSTEM`).

## Abhängigkeiten

- **DB:** neue `workflow.task`-Tabelle (Hand-SQL) — Voraussetzung für alles
  Schreibende. Bestehend nutzbar: `security.app_user`, `identity`-Kontakt,
  `workflow.project`, optional `workflow.priority_level`, `workflow.project_log`.
- **Auth/Rechte:** Session + `app_user` und Rechtematrix (`security`) für
  Anlegen/Delegieren/Erledigen/Bearbeiten (siehe `00`, Phase 0).
- **Shared Components:** Ressourcen-Liste, Anlege-/Bearbeiten-Dialog,
  Statuswechsel-Steuer (Erledigt-Toggle), Dashboard-Kachel, Logbuch/Feed —
  alle aus `00`.
- **Einbettende Sektionen:** Kontaktmappe (`02`) und Projektmappe (`04`) müssen
  ihre Tab-Struktur bereitstellen, damit die Aufgaben-Tabs andocken können.
  Übersicht (`01`) für die Dashboard-Kachel.
- **KI-Layer:** `ai.ai_proposal`/`ai_run` (Migration 0027) steht bereits; das
  Andocken erfolgt, sobald die Aufgaben-Service-Tore existieren.

## Aufwand & Priorität

Empfohlene Phase: **Phase 1 — Operativer Kern** (siehe `00`), nach bzw. parallel
zu Projekte/Vorgänge (`04`), da die Projekt-Einbettung darauf aufbaut.

| Screen / Baustein | Größe | Reihenfolge |
|---|---|---|
| DB-Migration `workflow.task` + Schutz | S–M | 1 (Blocker) |
| Service + API (list/get/create/update/complete/reopen) | M | 2 |
| Globale Aufgabenliste `/aufgaben` | M | 3 |
| Anlege-/Bearbeiten-Dialog (`[Speichern & neu]`) | S–M | 3 |
| Kontakt-Tab „Aufgaben" (Offen/Erledigt) | S | 4 (nach `02`) |
| Projekt-Tab „Aufgaben" (Offen/Erledigt) | S | 4 (nach `04`) |
| Dashboard-Kachel „Aufgaben" | S | 5 (nach `01`) |
| KI-Andockpunkte (`ai_proposal`-Vorschläge) | M | 6 (Querschnitt) |

Gesamt: **M**. Die eingebetteten Tabs sind bewusst dünn (dieselbe Listen-/
Dialogkomponente, nur gefiltert und vorbelegt) — der Hauptaufwand liegt in
DB-Tabelle, Service und globaler Liste.

## Screenshots zur Vorlage (Wiedererkennung)

Als visuelle Vorlage beim Bau heranzuziehen (HOCH-Wiedererkennung):
- **Globale Übersicht + „Aufgabe hinzufügen"-Fenster + Erledigt-Checkbox:**
  `Wie kann ich Aufgaben erstellen, delegieren und als erledigt markieren` —
  image1–image3.png (prägend für Liste + Dialog + Erledigt-Interaktion).
- **Kontakt-Seitenspalte + Aufgabe-hinzufügen im Kontaktkontext:**
  `Wie erstelle ich eine Aufgabe direkt für einen Kontakt` — image1–image3.png.
- **Kontakt Offen/Erledigt-Trennung:**
  `Wo sehe ich offene Aufgaben für einen Kontakt` — image1–image2.png und
  `Wo sehe ich die erledigten Aufgaben eines Kontaktes` — image1–image2.png.
- **Projekt-Reiter [Aufgaben]:** `Wo finde ich Aufgaben zum Projekt` — image1.png.
- **Dashboard-Kachel „Aufgaben":** `Wo kann ich sehen welche Aufgaben anstehen`
  — image1.png.

(Der mobile Einstieg `Wo finde ich Aufgaben in meiner HERO App wieder` —
image1.jpeg — ist für das Web-Layout nur nachrangig relevant.)
