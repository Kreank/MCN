# HANDOFF — MCN Leitstand (für die nächste Session)

Dieses Dokument macht eine frische Session sofort handlungsfähig. **Zuerst lesen**,
dann `docs/roadmap/README.md` + `docs/roadmap/00-informationsarchitektur.md`.

> TL;DR: MCN ist ein KI-first CRM (Nachfolger des Hero-CRM) für Handwerk/
> Gebäudeservice. DB ist database-first PostgreSQL (Regeln in Triggern). Backend
> Django 5 + django-ninja. Frontend Angular „Leitstand". Es wird in **vertikalen
> Slices** gebaut (DB→Service→API→UI→Verifikation→Review). Aktuell **~16 Bereiche
> read-only live** (Kontakte, Liegenschaften, Projekte, Dokumente, Planung inkl.
> Plantafel/Kalender, Wartung, Aufgaben, **Mitarbeiter/HR**, Artikel inkl.
> VK-Kalkulation, Buchhaltung inkl. Mahnwesen + Storno/Korrektur + Beleg-PDF,
> Auswertungen). **Auth/Login + Rechtematrix stehen** (eigenes Login, kein SSO);
> die gesamte API ist anmeldepflichtig. **500 Backend-Tests grün**,
> db_core-Migrationen bis **0022**, accounts bis **0002**.

---

## 0. Nächste Session — Empfehlung & offene Entscheidung (ZUERST LESEN)

Der breite **read-only-Ausbau ist abgeschlossen**, **HR-Schema (0019) steht**, und
**Auth/Login + Rechtematrix sind gebaut**. Für praktisch jede Aktion existiert ein
**getesteter Schreib-Service** (create/status/publish/storno/zahlung/mahnung/
wartung-trigger/kalkulation/personal …) — die meisten sind aber **noch nicht im UI
verdrahtet**. Genau das ist jetzt der größte Hebel.

**A) Schreib-UIs — „+ Neu", Bearbeiten, Statusaktionen. KEIN neues Schema.**
- *Was:* Formulare und Aktions-Buttons an die vorhandenen, getesteten
  Schreib-Endpunkte hängen. Auth, CSRF, Rechte und das 403-Handling im Frontend
  stehen bereits — `authService.darf(modul, aktion)` blendet aus, was der Server
  ohnehin ablehnen würde.
- *Warum zuerst:* verwandelt das read-only-Gerüst in ein **bedienbares System**.
  Jede Aktion ist ein kleiner, unabhängiger Slice. Größe: **L**, aber gut teilbar.
- *Reihenfolge-Vorschlag:* Aufgaben (anlegen/erledigen) → Abwesenheiten
  (einreichen/genehmigen) → Kontakte/Liegenschaften anlegen → Belege
  (anlegen/veröffentlichen/versenden) → Zahlungen/Mahnungen (Endpunkte fehlen
  noch, nur Services!) → Plantafel-Drag&Drop.

**A2) Offene Enden aus dem Auth-Slice** (klein, aber sicherheitsrelevant):
- **`row_scope='EIGENE'` ist nur für Aufgaben und Einsätze umgesetzt.** Überall
  sonst gilt **fail-closed**: `require()` wirft 403, wenn die Rolle nur eigene
  Zeilen sehen darf. Folge: ein MONTEUR sieht Projekte, Aufträge, Wartung und
  Plantafel gar nicht. Wer das ändern will, setzt EIGENE dort echt um und stellt
  den Endpunkt auf `require_scoped` um — **niemals** einfach auf `require`
  zurückfallen, das wäre ein stiller Datenleak.
- Kein „Passwort vergessen"-Flow (braucht Mailversand). Kein Passwort-Ändern-UI.
  Hero-Fakt für später: Einmal-Passwort 12 Stunden gültig.
- Keine Rechtematrix-Pflege-UI (`security.role_permission` ist Stammdaten;
  Änderungen derzeit nur per SQL/Migration). Siehe `docs/roadmap/13`.
- Zwei Sub-Ressourcen zeigen 403 noch als generischen Fehler:
  `projekt-detail` (Tabs Aufträge/Aufgaben/Logbuch/Checklisten) und
  `artikel-detail` (VK-Kalkulation). Muster zum Nachziehen:
  `shared/http-fehler.ts` + `shared/kein-zugriff`.

**B) Verbleibende Schema-Bereiche (read-only, Hand-SQL-Migrationen).**
Muster: `migrations/0019_hr_personal.py` bzw. `0016_maintenance_wartung.py`
(RunSQL + Schutzstandard). Jede braucht eine kurze fachliche Feld-Entscheidung:
- **Firmeneinstellungen** (`company_profile`/branch/gewerk/email_template fehlen) —
  klein & wertvoll: ersetzt u.a. den Aussteller-Platzhalter im Beleg-PDF.
- **HR-Nachzügler** (aus 0019 bewusst ausgeklammert, siehe Abschnitt 8):
  Steuer-/Bankdaten (DSGVO, Vier-Augen), Zeitkategorien/Pausenregeln/
  Stundenausgleich, Niederlassung (`security.branch`).
- **Belegerfassung/Eingangsrechnungen** (eigene `receipt`-Tabelle vs. gerichtete
  `invoice` — entscheiden) + `ledger_account`/`cost_center` (Buchhaltung-Ausbau).
- **Ressourcen + Terminkategorien** (Planung) — schaltet die Ressourcen-Bahnen der
  Plantafel und die Kategorie-Farben frei.

**C) Kleinere Reste (geringes Risiko, schnelle Slices).**
- Weitere Auswertungs-Dashboards (Projekte/Artikel/Mitarbeitende) — reine Read-Views.
- Beleg-PDF-**Archivierung** (MinIO + `content.document`/`file_link`,
  Einmaligkeits-Index 0032) — braucht MinIO-Anbindung (Container `mitra-crm-minio`).
- Plantafel **Drag&Drop** (Umplanen = Schreibaktion → kommt mit Auth), Mahnstufen
  3→6 (`dunning_level` seedet nur 3), DATANORM-Import-Wizard (Schreib-Flow).

**Empfehlung des bisherigen Standes:** **A (Auth) als nächstes** — es ist der
einzige Schritt, der das viele bereits Gebaute *nutzbar* macht, und braucht kein
Schema. Falls doch erst Schema: **Firmeneinstellungen** (kleinster Slice).
Details/Gotchas je Bereich in `docs/roadmap/09/11/12/13/14` + Abschnitt 8 unten.

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
export MCN_DB_NAME=mitra_crm_test MCN_DB_PASSWORD=mcn_dev_local MCN_DEBUG=1
```

**`MCN_DEBUG=1` ist seit dem Auth-Slice Pflicht für die Entwicklung.** Der Default
steht bewusst auf `0` (fail-safe: Produktion muss DEBUG nicht ausschalten, die
Entwicklung muss es einschalten). An `DEBUG` hängen die `Secure`-Flags von
Session- und CSRF-Cookie — ohne `MCN_DEBUG=1` schickt der Browser sie über
`http://localhost` nicht mit und **der Login schlägt fehl**. Ebenso vergibt
`seed_demo` die Dev-Passwörter nur bei `DEBUG`.

**Dev-Logins** (von `seed_demo` angelegt, Passwort aus `MCN_DEV_PASSWORD`,
Default `mcn-dev-passwort-2026`):

| E-Mail | Rolle | sieht |
|---|---|---|
| `admin@mitra-sanitaer.de` | ADMINISTRATION (Superuser) | alles |
| `joerg.feldmann@mitra-sanitaer.de` | ADMINISTRATION | alles |
| `petra.lindqvist@mitra-sanitaer.de` | DISPOSITION | kein `hr`, kein `pricing`/`invoicing` |
| `sven.ostmann@mitra-sanitaer.de` | NUR_LESEN | nur lesen, kein `hr` |

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

## 2b. Auth & Rechte (seit dem Auth-Slice)

**Eigenes Login, kein Fremdanbieter.** E-Mail + Passwort, Django-Session-Cookie.
Ausdrücklich **kein SSO/OIDC/Microsoft** (User-Entscheidung). Die in
`docs/roadmap/14` beschriebene Microsoft/Google-OAuth-Anbindung betrifft
ausschließlich den **Mailversand** (Absender-Konto verbinden) — nicht die
Anmeldung. Nicht verwechseln.

- **Die gesamte API ist anmeldepflichtig**: `NinjaAPI(auth=django_auth)` in
  `api/api.py`. Ausnahmen mit `auth=None`: `/api/health` und
  `/api/auth/{csrf,login,logout,me}`. Ein Test
  (`api/tests/test_endpoint_schutz.py`) zählt die Ninja-Registry durch und
  schlägt fehl, sobald jemand einen Endpunkt ungeschützt lässt.
- **Anmeldung** über `accounts.backends.EmailBackend` (E-Mail case-insensitiv
  eindeutig, `UniqueConstraint(Lower("email"))`). `username` bleibt nur
  technisches Pflichtfeld von `AbstractUser`.
- **CSRF**: django-ninja schützt Cookie-Auth-Endpunkte automatisch. Die
  `auth=None`-Endpunkte `/auth/login` und `/auth/logout` holen die Prüfung
  selbst nach (`ninja.utils.check_csrf`) — sonst wäre **Login-CSRF** möglich.
  Das Frontend holt den Token über `GET /api/auth/csrf` und schickt ihn als
  `X-CSRFToken`.
- **Rechte** (`security.role`/`user_role`/`role_permission`, Migration 0026,
  Modul `hr` per 0021 ergänzt): `db_core/services/rechte.py` wertet aus,
  `api/permissions.py` setzt durch. Rollen **addieren** Rechte; beim `row_scope`
  gewinnt die weiteste Sicht (`ALLE`).
- **Drei Torfunktionen — fail-closed als Grundhaltung:**
  - `require(request, modul, aktion)` — Regelfall. Wirft **403 auch dann**, wenn
    die Rolle nur `EIGENE` sehen darf, der Endpunkt das aber nicht umsetzt.
    `EIGENE` wird **nie** stillschweigend zu `ALLE`.
  - `require_scoped(...)` — nur für Endpunkte, die wirklich auf eigene Zeilen
    filtern (aktuell: Aufgaben, Einsätze). Wer das nutzt, **muss** filtern.
  - `require_create(...)` — für ANLEGEN; dort ist `EIGENE` bedeutungslos.
- **Fremde Zeilen → 404, nicht 403** (Detail/Schreibzugriff), damit ihre Existenz
  nicht verraten wird.
- **Zwei Ebenen nicht verwechseln:** Recht (403, `permissions.py`) vs. fachliches
  Tor (422, Service + DB-Trigger). Wer FREIGEBEN darf, darf trotzdem keinen
  Auftrag freigeben, dem die Vorbedingungen fehlen.
- **Frontend**: `core/auth.service.ts` (Signal + `darf()`), `auth.interceptor.ts`
  (`withCredentials`, `X-CSRFToken`, 401 → `/login`, 403 **nicht** umleiten),
  `auth.guard.ts` (`authGuard` + `darfGuard(modul, aktion)` je Route),
  `shared/http-fehler.ts` (403 → `kind:'forbidden'`), `shared/kein-zugriff`.

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
| Mitarbeiter (65) | **Personalstamm** (`hr.*`, NEUES Schema 0019): Liste + Mappe (Persönliches/Vertrag/Abwesenheiten/Urlaub). Write-Service (employee/contract/absence/urlaubskonto) existiert + getestet | `/api/hr/employees` |
| Artikel (70) | Artikel + Leistungen (Stücklisten), Liste + Detail + **VK-Kalkulation** (Verkaufspreis-Formel je Artikel) | `/api/pricing`, `/articles/{id}/kalkulation` |
| Buchhaltung (80) | **Offene Posten** + Detail-Mappe (Übersicht/Zahlungen/Mahnverlauf, **Storno-/Gutschrift-Referenzen**) + **Mahnwesen-Screen**. Services: Zahlung/Mahnung + **Storno/Rechnungskorrektur** (STORNO/GUTSCHRIFT, `POST …/cancel`,`/correction`) getestet | `/api/buchhaltung` |
| Auswertungen (90) | Landing + **Umsatz-/Projektübersicht** (KPIs, Umsatzverlauf, Projekte nach Gewerk) | `/api/auswertungen/…` |

Nav-Marks: Planung=50, Wartung=55 (bewusst nicht-rund, Service-Cluster),
Aufgaben=60, Mitarbeiter=65, Artikel=70, Buchhaltung=80, Auswertungen=90.

Backend: **298 Tests grün**, db_core-Migrationen bis **0020** (0016 = Hand-SQL
`maintenance`-Schema, 0019 = Hand-SQL `hr`-Schema; 0017/0018/0020 = State-only
Models). Neue Dependency **fpdf2**
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

- **Auth ist gebaut** (siehe Abschnitt 2b) — die frühere Notiz „Auth ganz zuletzt"
  ist erledigt und gilt nicht mehr. Die gesamte API ist anmeldepflichtig; die
  Dev-Phasen-Konvention „Lesen ohne Auth" ist damit **abgeschafft**.
- **Eigenes Login, kein SSO/Microsoft** (ausdrückliche User-Entscheidung). Die
  Microsoft/Google-OAuth-Anbindung in `docs/roadmap/14` betrifft nur den
  **Mailversand**, nicht die Anmeldung.
- **`row_scope='EIGENE'` ist fail-closed**: wo eine Ansicht die Zeilenbegrenzung
  nicht umsetzt, gibt es 403 statt aller Zeilen. Nicht „vorübergehend" auf
  `require_scoped` ohne Filter umstellen — das wäre ein stiller Datenleak.
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
  Danach: **Schema-Bereiche** (Belegerfassung, Ressourcen/Terminkategorien,
  Firmeneinstellungen) + **Auth/Login** + alle Anlege-Formulare.
- ✔ **Mitarbeiter/HR** (`hr.*` — **zweites selbst angelegtes Fachschema**, Hand-SQL
  0019). Grundsatzentscheidung: eigenes Schema statt `security` erweitern —
  `security` beantwortet „darf dieser Account etwas?", `hr` „welche
  arbeitsrechtliche Beziehung besteht?". Personendaten werden **nicht** dupliziert:
  `hr.employee` ankert per FK auf `security.app_user` (Login) und
  `identity.person` (Stammdaten), beide 1:1.
  - `hr.employee` — Personalnummer `MA-00001` aus **eigener Sequenz** (kein
    GoBD-Belegkreis!), Statusautomat AKTIV↔INAKTIV→AUSGETRETEN (final;
    Wiedereintritt = neuer Personalsatz).
  - `hr.employment_contract` — versioniert, **überlappungsfrei** je Mitarbeiter
    (EXCLUDE über `daterange`). Beginn, Sollstunden-Raster (Mo–So),
    Urlaubsanspruch und Lohngruppe sind nach dem INSERT **physisch
    unveränderlich** (Trigger) — Arbeitszeitänderung = Folgevertrag, der den
    laufenden automatisch am Vortag beendet.
  - `hr.absence` — Statusautomat ENTWURF→EINGEREICHT→GENEHMIGT|ABGELEHNT
    (+ZURUECKGEZOGEN); Ablehnung begründungspflichtig (CHECK). Überlappungsfrei
    für ENTWURF/EINGEREICHT/GENEHMIGT.
  - `hr.vacation_budget` — Anspruch/Übertrag/Anpassung je Jahr. **Verbrauch ist
    nicht gespeichert**, sondern aus genehmigten URLAUB-Abwesenheiten abgeleitet
    (gleiche Konvention wie der offene Betrag in der Buchhaltung).
  - **Kernregel:** `days_count` einer Abwesenheit berechnet der Service aus dem
    Sollstunden-Raster des am jeweiligen Tag gültigen Vertrags — Wochenenden und
    0-Stunden-Tage zählen nicht, halbe Randtage ziehen 0,5 ab. Der Client liefert
    `days_count` nie selbst.
  - **Bewusste Lücken:** kein Feiertagskalender (Feiertage zählen als Arbeitstage,
    wenn der Wochentag ein Soll hat); jahresübergreifende Urlaube werden komplett
    dem Startjahr zugerechnet; unterjähriger Eintritt kürzt den Anspruch nicht
    automatisch (dafür ist die begründungspflichtige Anpassung da) — Hero verhält
    sich genauso.
  - **Ausgeklammert (eigene Migration):** Steuer-/Bankdaten (DSGVO Art. 9/32;
    `security.four_eyes_action` kennt bereits 'BANKDATEN', app-seitig nicht
    durchgesetzt — hängt an Auth), Zeitkategorien/Pausenregeln/Stundenausgleich
    (erst Abgrenzung zur operativen `workflow.time_entry` klären), Niederlassung.
  - **DSGVO-Merkposten:** `GET /api/hr/absences` ist der **einzige** Lese-Endpunkt
    mit `auth=django_auth` (Krankheitsdaten über den ganzen Bestand).
    `GET /api/hr/employees/{id}` liefert ebenfalls Krankheitshistorie und ist
    noch offen — beim Auth-Slice zuerst absichern.

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
8. **Einstellungen · Profil**: `security.role/role_permission` (0026) existiert
   (Rechtematrix, app-seitig durchzusetzen). HR-Kern ist mit `hr.*` (0019)
   erledigt; offen bleiben Steuer-/Bankdaten, Zeitwirtschaft und Niederlassung.
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
