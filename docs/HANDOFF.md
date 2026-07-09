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
| Projekte (30) | …zusätzlich **Aufträge**-Tab (work_order) in der Projektmappe | `/api/workflow/work_orders` |
| Dokumente (40) | **Angebote + Rechnungen**: Liste + Mappe, Anlegen bis ENTWURF; **Veröffentlichen (Rechnung→VEROEFFENTLICHT) / Versenden (Angebot→VERSENDET)** inkl. Snapshot+Hash+Beteiligte | `/api/invoicing/…/publish`,`/send`,`/parties` |
| Aufträge | Detail-Mappe (Übersicht/Beteiligte/Verlauf), Statusautomat bis KAUFMAENNISCH_GEPRUEFT/ABGERECHNET mit DB-Toren | `/api/workflow/work_orders` |
| Planung (50) | **Einsätze** (`workflow.service_job`): Liste + Einsatz-Mappe (Übersicht, Zuweisungen, Zeiten & Material, Verlauf) + **Plantafel** (Schwimmbahnen-Board) + **Kalender** (Monatsansicht), Subnav. Read-only | `/api/planung/einsaetze`, `/api/planung/plantafel` |
| Wartung (55) | **Wartungsverträge** (`maintenance.*`, NEUES Schema): Liste + Detail-Mappe (Details/Erinnerung/Verlauf), Fälligkeits-Aktionen. Write-Service (create/status/trigger) existiert + getestet | `/api/maintenance/contracts` |
| Aufgaben (60) | Liste + Statusaktionen; **neue Tabelle `workflow.task`** | `/api/workflow/tasks` |
| Artikel (70) | Artikel + Leistungen (Stücklisten), Liste + Detail + **VK-Kalkulation** (Verkaufspreis-Formel je Artikel) | `/api/pricing`, `/articles/{id}/kalkulation` |
| Buchhaltung (80) | **Offene Posten** + Detail-Mappe (Übersicht/Zahlungen/Mahnverlauf, **Storno-/Gutschrift-Referenzen**) + **Mahnwesen-Screen**. Services: Zahlung/Mahnung + **Storno/Rechnungskorrektur** (STORNO/GUTSCHRIFT, `POST …/cancel`,`/correction`) getestet | `/api/buchhaltung` |
| Auswertungen (90) | Landing + **Umsatz-/Projektübersicht** (KPIs, Umsatzverlauf, Projekte nach Gewerk) | `/api/auswertungen/…` |

Nav-Marks: Planung=50, Wartung=55 (bewusst nicht-rund, Service-Cluster),
Aufgaben=60, Artikel=70, Buchhaltung=80, Auswertungen=90.

Backend: **247 Tests grün**, db_core-Migrationen bis **0018** (0016 = Hand-SQL
`maintenance`-Schema; 0017/0018 = State-only Models). Neue Dependency **fpdf2**
(Beleg-PDF). `seed_demo` deckt
alle Bereiche ab (Kontakte, Liegenschaften, Projekte+Vorgänge, **durchgeschalteter
Auftrag**, Aufgaben, Angebot [versendet], **veröffentlichte Rechnung**, Artikel,
Cockpit).

Neu seit dem letzten Handoff (Kette Auftrag→Beleg→Auswertung, 3 Commits):
- **Aufträge** `workflow.work_order`: Models/Service/API, Statusübergänge +
  Freigabe-/Abrechnungs-Tore (DEFERRED Constraint-Trigger). `db_core.gate_errors.
  as_business_error` übersetzt fachliche DB-Tor-Fehler (SQLSTATE P0001) in 422.
- **Beleg-Veröffentlichung**: `publish_invoice`/`send_quote` (Snapshot + SHA-256-
  Hash, DB vergibt Belegnummer), `InvoiceParty`. **Kein PDF nötig** — die DB
  verlangt zur Veröffentlichung nur Snapshot+Hash (PDF-Index 0032 = „höchstens
  eine Ausfertigung", keine Vorbedingung).
- **Auswertungen**: erste Aggregations-Dashboards (Umsatz aus VEROEFFENTLICHT-
  Rechnungen; `dataviz`-konforme Inline-Diagramme).

**Wichtige Erkenntnis (Test-Gotcha korrigiert):** DEFERRED Constraint-Trigger
feuern unter der pytest-Transaktion NICHT am Blockende — im Test mit
`SET CONSTRAINTS ALL IMMEDIATE` scharf prüfen (Muster in
`test_auftrag_service.py::_force_deferred_checks`). Der publish-Pfad ruft die
Tore aber real; deshalb bauen Tests, die veröffentlichen, ein vollständig
gültiges Szenario (geprüfter Auftrag + Beteiligte).

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
`docs/roadmap/README.md`.

**Erledigt** (frühere Session): ✔ Auswertungen (Landing + Umsatz-/Projektübersicht
— weitere Dashboards offen), ✔ Aufträge (`workflow.work_order`), ✔ Beleg-
Veröffentlichung (invoice→VEROEFFENTLICHT / quote→VERSENDET, ohne PDF).

**Erledigt** (diese Session):
- ✔ **Einsätze/Planung** (`workflow.service_job`): Models/Service/API + Liste +
  Einsatz-Mappe (read-only). Write-Service `services/einsatz.py` (create/
  set_schedule/advance_status/assign_user/log_time/log_material) getestet, noch
  nicht im UI (kommt mit Auth). **Offen:** Plantafel (Schwimmbahnen + Drag&Drop,
  XL), Kalender, Terminkategorie-/Ressourcen-Schema (fehlen in der DB →
  Migration nötig, siehe `docs/roadmap/06-planung.md`).
- ✔ **Buchhaltung** (`invoicing.payment/dunning_level/dunning_notice`, 0025):
  Models/Service/API + Offene-Posten-Liste + Detail-Mappe (Übersicht/Zahlungen/
  Mahnverlauf), read-only. Write-Service `services/buchhaltung.py` (record_payment/
  reverse_payment/issue_dunning_notice) getestet. Zahlungsstatus/offener Betrag
  sind **abgeleitet** (nicht gespeichert; Vorzeichenkonvention `PAYMENT_SIGN`).
  **Offen:** Storno/Rechnungskorrektur-Flow (invoice_type STORNO/GUTSCHRIFT +
  reference_invoice_id), Belegerfassung (Eingangsrechnungen, neues/erweitertes
  Schema), Stammdaten-CRUD (ledger_account/cost_center fehlen), Mahnwesen-Screen
  (Endpoint `/api/buchhaltung/dunning` existiert + getestet, aber noch kein UI),
  Mahnstufen-Ausbau 3→6, DATEV/Lexware-Export. Details `docs/roadmap/09-buchhaltung.md`.
  Mahnverlauf-Pausieren fehlt im DB-Schema.
- ✔ **Wartung** (`maintenance.*` — **erstes selbst angelegtes Fachschema**,
  Hand-SQL-Migration 0016): `maintenance_contract` (objektzentriert, Statusautomat
  AKTIV↔INAKTIV→ARCHIVIERT per eigenem Trigger, Belegkreis 'W') + append-only
  `maintenance_event`. Write-Service `services/wartung.py` (create/set_status/
  trigger_action; Aktion AUFGABE erzeugt eine workflow.task). Liste + Detail-Mappe
  read-only. **Offen:** Fälligkeits-Scheduler (Cron/Worker, erzeugt Folgeobjekte
  automatisch — aktuell nur manuell über Service), Aktionen PROJEKT/AUFTRAG
  (aktuell nur protokolliert), Anlege-/Auslöse-UI (mit Auth). Muster für neue
  Fachtabellen: `migrations/0016_maintenance_wartung.py` (RunSQL + Schutzstandard).
- ✔ **„Kein-neues-Schema"-Ausbau** (auf vorhandenem Fachschema, User-Wunsch
  „erst das, dann Schema+Login") — **komplett abgearbeitet**:
  - **Mahnwesen-Screen** (UI zu `/buchhaltung/dunning`).
  - **Plantafel + Kalender** (read-only Board/Monatsansicht auf `service_job`,
    Endpoint `/planung/plantafel`, Subnav).
  - **Storno/Rechnungskorrektur** (STORNO/GUTSCHRIFT-Folgebelege, `beleg.py`
    create_cancellation/create_correction, `POST /buchhaltung/invoices/{id}/cancel`|
    `/correction`; Detail zeigt Ursprung/Folgebelege). **Invariante:** create_invoice
    lehnt Credit-Typen ab — Folgebelege nur über die dedizierten Funktionen (immer
    negativ). Umsatz-Aggregation entsprechend gefixt (Summe über alle Belege).
  - **Kunden-Dashboard** (`/auswertungen/kunden`, Umsatz je primärem Schuldner).
  - **VK-Kalkulation** (`/pricing/articles/{id}/kalkulation`, Formel Basis
    EK/Listenpreis × Auf-/Abschlag; Models SalePriceGroup/ArticleSalePrice/
    ArticleSupplierReference; Artikel-Detail-Tab).
  - **Beleg-PDF** (`GET /invoicing/invoices/{id}/pdf`, on-the-fly via **fpdf2**,
    nur veröffentlicht; Link auf Rechnung-Detail). Persistente MinIO-Archivierung
    (content.document + file_link) noch offen.
  **Noch offen (kleinere Reste):** weitere Dashboards (Projekte/Artikel/Mitarbeitende),
  DATANORM-Import-Wizard (Schreib-Flow, mit Auth), Beleg-PDF-Archivierung (MinIO).
  Danach: **Schema-Bereiche** (HR/Mitarbeiter, Belegerfassung, Ressourcen/
  Terminkategorien, Firmeneinstellungen) + **Auth/Login** + alle Anlege-Formulare.

Empfohlene nächste Reihenfolge:

1. **Auswertungen ausbauen**: die übrigen Dashboards (Projekte/Kunden/Artikel/
   Mitarbeitende/Umsätze-Details/Projektkarte). **Marge** braucht die EK-Ebene
   (`pricing.article_supplier_reference.last_purchase_price`, noch kein Model)
   und ist aus Belegzeilen NICHT ableitbar — ggf. über den `billing_snapshot`.
   Startseite `01` kann jetzt die Umsatz-Kennzahlen aus `/auswertungen` ziehen.
2. **Beleg-PDF (optional)**: PDF-Ausfertigung + `content.file_link`
   (`link_category='BELEG_PDF'`, Einmaligkeits-Index 0032) — reine Ausgabe,
   nicht Voraussetzung der Veröffentlichung.
4. **VK-Kalkulation/DATANORM**: Verkaufspreis ist eine Formel über
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
