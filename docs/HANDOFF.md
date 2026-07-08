# HANDOFF — MCN Leitstand (für die nächste Session)

Dieses Dokument macht eine frische Session sofort handlungsfähig. **Zuerst lesen**,
dann `docs/roadmap/README.md` + `docs/roadmap/00-informationsarchitektur.md`.

> TL;DR: MCN ist ein KI-first CRM (Nachfolger des Hero-CRM) für Handwerk/
> Gebäudeservice. DB ist database-first PostgreSQL (Regeln in Triggern). Backend
> Django 5 + django-ninja. Frontend Angular „Leitstand". Es wird in **vertikalen
> Slices** gebaut (DB→Service→API→UI→Verifikation→Review). Aktuell ~10 Bereiche
> live, **UI read-only** (Anlegen erst mit Auth ganz am Schluss — bewusste
> Entscheidung des Users).

---

## 1. Sofort loslegen: Umgebung

**Dev-Datenbank** (Docker): Container `mitra-crm-test`, Port `55432`, DB heißt
**`mitra_crm_test`** (NICHT der Django-Default `mitra_crm_dev`!), User `postgres`,
Passwort **`mcn_dev_local`** (lokales Wegwerf-PW, in einer früheren Session gesetzt).

```bash
docker start mitra-crm-test           # falls gestoppt (Exited)
```

Für ALLE Backend-Befehle diese Env-Vars setzen (sonst schlägt die DB-Verbindung fehl):
```bash
export MCN_DB_NAME=mitra_crm_test MCN_DB_PASSWORD=mcn_dev_local
```

**Backend** (`cd backend`, uv):
```bash
uv run python manage.py check
uv run pytest -p no:cacheprovider -q          # aktuell 109 grün
uv run python manage.py runserver 127.0.0.1:8000
uv run python manage.py seed_demo             # idempotenter Demo-Datensatz
```

**Frontend** (`cd frontend`, npm):
```bash
npm run build                                 # Typecheck + Build (schnell)
npm start                                     # ng serve auf :4200, Proxy /api -> :8000
```
Ansehen: http://localhost:4200 (Proxy `frontend/proxy.conf.json` → Backend :8000).

**Shell-Hinweise (Windows/Git-Bash):** `python` ist NICHT im PATH (Store-Alias) →
für JSON-Inspektion `curl` roh nutzen, kein `python -m json.tool`. Die
`LF will be replaced by CRLF`-Warnungen bei `git add` sind harmlos.

---

## 2. Der wichtigste Gotcha: Migrationen auf der Dev-DB

Die Dev-DB hat das Fachschema physisch, aber `django_migrations` kennt die
Baseline NICHT (`showmigrations db_core` = alles `[ ]`). Ein direktes `migrate`
scheitert an `0001_baseline` („schema identity already exists").

**Vorgehen bei einer NEUEN db_core-Migration:**
```bash
uv run python manage.py migrate db_core <bisher_letzte> --fake   # markiert Vorhandene als angewandt
uv run python manage.py migrate db_core                          # wendet die NEUE real an
```
(Die Test-DB von pytest ist davon unberührt — sie wird frisch über die ganze
Kette gebaut.)

---

## 3. Architektur & eiserne Konventionen

Pflichtlektüre: `db/README.md`, `backend/README.md`, `CLAUDE.md`.

- **Fachschema-Änderungen** nur als Hand-SQL (Django-Migration mit `RunSQL`),
  NIE ORM-generiertes DDL. Models sind `managed = False`,
  `db_table = 'schema"."tabelle'`. Neue Fachtabelle erbt den Schutzstandard
  (No-Delete/Audit/No-Truncate) — Muster: `db/migrations/0035` (project_note)
  bzw. die einzige selbst geschriebene Tabelle hier:
  `backend/db_core/migrations/0005_workflow_task.py`.
- Nach neuem Model: `makemigrations db_core` erzeugt eine **State-only**-Migration
  (CreateModel, kein DDL). Die FK-Felder fehlen darin absichtlich — das ist ok,
  `makemigrations --check` bleibt „No changes detected".
- **Fachliche Writes ausschließlich** über
  `db_core.db_context.business_transaction(app_user_id)` (setzt
  `app.current_user_id` für Audit/Trigger; bei begründungspflichtigen
  Statuswechseln `status_reason=...`).
- **django-ninja Views bleiben dünn** und rufen die Service-Schicht. Lesen ohne
  Auth (Dev-Phase), Schreiben mit `auth=django_auth` + zugeordnetem `app_user`.
- **Model-FK-Attname** ist `feldname_id` (NICHT der `db_column`!). Beispiel:
  Feld `assigned_to` mit `db_column="assigned_to_user_id"` → im Service/Filter
  `assigned_to_id=...`, nicht `assigned_to_user_id=...`. (Häufiger Fehler.)
- **DB-Defaults im Model:** Zeitstempel `db_default=Now()`; sequenzielle Nummern
  via `Func`-Subklasse (`workflow.next_number('P')` etc., siehe
  `models.py` PropertyNumberDefault/ProjectNumberDefault). NIE die Nummer selbst
  setzen — die DB vergibt sie; danach `refresh_from_db()`.
- **Geld (GoBD):** immer `Decimal`, `ROUND_HALF_UP`. Eingaben VOR der Berechnung
  auf die DB-Spaltenskala quantisieren (sonst rundet Django anders als der
  DB-CHECK → 500 statt 422). Kopf-Steuer je Steuergruppe runden (wie
  `assert_*_totals`). Referenz: `services/beleg.py::_prepare_lines`.
- **Composite-PK-Tabellen** (z. B. project_property): Django 5.2
  `models.CompositePrimaryKey('a_id','b_id')`.

## 4. Frontend-Muster (Angular „Leitstand")

- Standalone-Components, Signals, neue Control-Flow-Syntax (`@if/@for/@switch`),
  `input()`/`model()`. Lazy-Routen in `app.routes.ts`. Nav in `app.ts`.
- **Wiederverwendbare Bausteine:**
  - `shared/mappe` — Detail-„Mappe": Kopf (Kicker/Titel/Back/Stempel) + Tab-Widget
    (WAI-ARIA, Pfeiltasten). Eltern bindet `[(aktiv)]`, `[tabs]`, projiziert Tab-
    Inhalte + `[mappe-kopf]`-Stempel. **Wichtig:** projizierter Inhalt wird
    global/vom Eltern gestylt (View-Encapsulation) — generische Bausteine
    (`.feld/.tab-platzhalter/.note/.btn/.lade-hinweis`) liegen in `styles.scss`.
  - Listen-Muster (Suche + Segment-Filter + Pagination + `reqId`-Guard gegen
    Races): siehe `features/kontakte`, `liegenschaften`, `projekte`, `dokumente`.
  - Detail-Muster: `ViewState`-Union (`loading|ready|error`) + `daten()`-Computed,
    `paramMap`-Subscription mit `takeUntilDestroyed`, Tab-Reset beim Navigieren.
  - Lazy-Tab-Nachladen via `effect()` (Beispiel: Aufgaben/Logbuch/Checklisten in
    `features/projekt-detail`).
- Design-Tokens `src/styles/_tokens.scss` (Navy/Orange/Salbei/Amber). WCAG 2.2 AA
  ist Pflicht: Status nie nur über Farbe (immer Text/Stempel), Fokusringe,
  Light+Dark. Deutsche Zahlen/Währung via `Intl.NumberFormat('de-DE', …)`.
- Beträge kommen als **String** (Decimal) über die API — im Frontend als String
  behandeln (verlustfrei), nur zur Anzeige mit `Number()` formatieren.

## 5. Rezept für einen vertikalen Slice (so wurde alles gebaut)

1. **Schema-Recherche** (Sonnet-Subagent) über die relevanten `db/migrations/*.sql`
   → präzise Spalten/Enums/Trigger/Pflichtfelder. „Was ist read-only machbar,
   was braucht Vorbedingungen?"
2. **Models** in `backend/db_core/models.py` (managed=False), an das Schema
   gespiegelt. Bei neuer Tabelle zusätzlich Hand-SQL-`RunSQL`-Migration.
3. **Service** in `backend/db_core/services/<name>.py` (Writes via
   `business_transaction`, Codelisten/Wertebereiche vorab prüfen → 422 statt 500).
4. **API** in `backend/api/<name>.py` (ninja Router), in `backend/api/api.py`
   registrieren. Liste/Detail/(Anlegen). N+1 vermeiden (`select_related`/
   `prefetch_related`).
5. **Migration** generieren (`makemigrations db_core`), Dev-DB migrieren
   (ggf. `--fake`-Trick), `manage.py check`.
6. **Seed** (`seed_demo`) idempotent erweitern, damit read-only-UI Daten hat.
7. **Tests** (`db_core/tests/test_*_service.py`, `api/tests/test_*_api.py`),
   `pytest` grün.
8. **Frontend**: `core/<name>.model.ts` + `.service.ts`, Feature-Component(s),
   Route + ggf. Nav-Punkt.
9. **Verifikation**: `npm run build`; im Browser mit echten Daten prüfen
   (chrome-devtools-MCP: `navigate_page`/`take_screenshot`/`take_snapshot`/`click`).
10. **Review** (Opus-Subagent) auf Korrektheit/Schema-Konsistenz; Befunde beheben.
11. **Commit** (deutsche Message im Stil der bisherigen; Co-Authored-By-Zeile).

Delegation gemäß CLAUDE.md: **Sonnet = Recherche, Opus = Code/Review**, du selbst
orchestrierst.

---

## 6. Was schon gebaut ist (Stand des Handoffs)

Nav-Reihenfolge (Marks 00–60), alle committet, je Tests + Browser + Review:

| Nav | Umfang | API |
|---|---|---|
| Übersicht (00) | Dashboard: offene Aufgaben/Projekte/Angebote aggregiert + KI-Kachel | (reuse) |
| Kontakte (10) | Liste + Detail-Mappe | `/api/identity` |
| Liegenschaften (20) | Liste + Mappe (Struktur, Beteiligte) | `/api/property` |
| Projekte (30) | Liste + Projektmappe: Übersicht, Liegenschaften, **Vorgänge** (mit Statusverlauf), **Aufgaben**, **Logbuch**, **Checklisten** (Dateien=Platzhalter) | `/api/workflow/projects`, `/service_cases/{id}`, `/projects/{id}/log`+`/checklists` |
| Dokumente (40) | **Angebote + Rechnungen** (Umschalter): Liste + Positions-Mappe, Anlegen bis ENTWURF | `/api/invoicing/quotes` + `/invoices` |
| Aufgaben (50) | Liste + Statusaktionen; **neue Tabelle `workflow.task`** | `/api/workflow/tasks` |
| Artikel (60) | Artikel + Leistungen (Stücklisten), Liste + Detail | `/api/pricing` |

Backend: **109 Tests grün**, db_core-Migrationen bis **0011**. `seed_demo` deckt
alle Bereiche ab (Kontakte, Liegenschaften+Gebäude/Einheiten/Rollen, Projekte+
Vorgänge, Aufgaben, Angebot, Rechnung, Artikelstamm, Cockpit).

## 7. Fixierte Entscheidungen (nicht erneut aufmachen)

- **Auth ganz zuletzt** (User-Wunsch). Folge: UI ist read-only; Schreib-Endpunkte
  existieren + sind getestet, aber ohne Login nicht im UI verdrahtet. „+ Neu"-
  Buttons/Formulare kommen zusammen mit dem Auth-Slice.
- **Nav-Begriffe Hero-nah:** „Projekte"/„Dokumente" (nicht Vorgänge/Belege).
- **Liegenschaften** eigener Nav-Punkt (nicht Reiter in Kontakten).
- **Kein Löschen** (GoBD/Audit): Rechnungen nur Storno; Projekte nur verschieben/
  archivieren; überall „Löschen"→Archivieren/Storno/Status.
- **Lagerverwaltung vorerst weggelassen** (DB-Beschluss B-26 verbietet Bestände).

## 8. Nächste Bereiche (priorisierter Backlog) + Gotchas

Details je Sektion in `docs/roadmap/01..14`. DB-Befunde in
`docs/roadmap/README.md`. Empfohlene Reihenfolge:

1. **Auswertungen** (neuer sichtbarer Bereich): Aggregations-Endpoints
   (Umsatz/Marge/Projekte) + Diagramme. Vorher die **`dataviz`-Skill** laden.
   Marge/Umsatz-Definitionen stehen in `docs/roadmap/10-auswertungen.md`.
2. **Aufträge (`workflow.work_order`, Migration 0013)**: existiert in der DB,
   in ENTWURF anlegbar (wie service_case). Schließt die Kette Vorgang→Auftrag→
   Einsatz und ist Voraussetzung für Rechnungen-mit-Auftrag und Planung.
   `work_order_party` (Beteiligte) für spätere Status-Gates.
3. **Einsätze/Planung (`workflow.service_job`, 0014)**: `service_job` hängt an
   `work_order` (Pflicht) → braucht #2 zuerst. Plantafel = große Drag-&-Drop-UI.
4. **Beleg-Versand/Veröffentlichung**: quote→VERSENDET / invoice→VEROEFFENTLICHT
   erzeugt erst die Nummer, braucht aber Snapshot+`content_hash`+PDF-Rendering+
   deferred Summen-Gate (invoice zusätzlich Auftrags-Status + `invoice_party`
   Schuldner). Groß — eigener Slice mit PDF-Pipeline.
5. **VK-Kalkulation/DATANORM**: Verkaufspreis ist eine Formel über
   `sale_price_group`/`article_sale_price` (nicht ein Feld). DATANORM-Import-Wizard.
6. **Buchhaltung**: Zahlungen (0025), **Mahnwesen** (`dunning_level` seedet nur
   3 Stufen, Hero braucht 6 → ausbauen), DATEV/Lexware-Export. Baut auf Rechnungen.
7. **Wartung**: kein Schema vorhanden → neues `maintenance.*` (Hand-SQL) nötig
   (siehe `docs/roadmap/11`).
8. **Mitarbeiter · Einstellungen · Profil**: `security.role/role_permission`
   (0026) existiert (Rechtematrix, app-seitig durchzusetzen). HR-Daten (Vertrag/
   Urlaub) haben KEIN Schema → eigenes HR-Fachschema empfohlen.
9. **Auth/Login + alle Anlege-Formulare** — ganz zum Schluss (siehe Entscheidung).

Kleinere offene Enden: Datei-/Bild-Upload (`content.file`/`file_link`) für den
„Dateien"-Tab und Objekt-Bilder; ISO-Datums-Formatierung im UI (aktuell teils roh).

## 9. Wo alles liegt

- **Roadmap/Pläne:** `docs/roadmap/` (README + 00 IA + 01–14 je Sektion, aus 221
  Hero-Artikeln abgeleitet). Hero-Quelle: `Hero Wissen/` (untracked, .docx).
- **Memory** (lädt jede Session automatisch): `backend-stack-entscheidung`,
  `design-und-marke`, `dev-db-zugang`, `roadmap-hero-mapping`,
  `umsetzungsstand-frontend`, dieses Handoff.
- **Git:** Branch `master`. Jeder Slice ist ein eigener Commit mit ausführlicher
  deutscher Message — `git log --oneline` gibt die Historie.

---
Viel Erfolg. Halte dich an das Slice-Rezept, verifiziere end-to-end (nicht nur
Typecheck), und lass jeden substanziellen Slice von einem Opus-Reviewer prüfen.
