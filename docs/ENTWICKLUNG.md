# Entwicklungshandbuch — MCN

> Wie hier gearbeitet wird: Umgebung, Konventionen, Muster, Slice-Rezept.
> Ergänzt `CLAUDE.md` (Regeln) und `docs/INVARIANTEN.md` (fachliche Regeln).

---

## 1. Sofort loslegen: Umgebung

**Dev-Datenbank** (Docker): Container `mitra-crm-test`, Port `55432`, DB heißt
**`mitra_crm_test`** (NICHT der Django-Default `mitra_crm_dev`!), User `postgres`,
Passwort **`mcn_dev_local`** (lokales Wegwerf-PW, in einer früheren Session gesetzt).

**Die Dev-DB wurde am 2026-07-12 komplett neu aufgebaut** (Entscheidung des Users):
Sie enthielt 52 Dubletten einer Zeitbuchung aus Agenten-Testläufen, die der neue
EXCLUDE-Constraint aus 0066 zu Recht zurückwies. Die frische DB migriert die ganze
Kette **sauber durch — kein Migrationsfehler.** (Migration 0066 nennt bei
Überlappungen jetzt die schuldigen Zeilen, statt roh abzubrechen.) Reine
Scratch-Daten, kein Verlust. Danach `seed_demo` neu fahren.

```bash
docker start mitra-crm-test           # falls gestoppt (Exited)
```

Für ALLE Backend-Befehle diese Env-Vars setzen (sonst schlägt die DB-Verbindung fehl):
```bash
export MCN_DB_NAME=mitra_crm_test MCN_DB_PASSWORD=mcn_dev_local MCN_DEBUG=1
```

**MinIO** (Container `mitra-crm-minio`, API-Port **9100**, Konsole 9101, Bucket
`mcn-belege`). Der Settings-Default für das Secret ist FALSCH — ohne die beiden
Variablen scheitert **jeder Datei-Upload** mit `AccessDenied`:
```bash
export MCN_MINIO_ACCESS_KEY=minioadmin MCN_MINIO_SECRET_KEY=minio-test-pilot
```
(Alle lokalen Passwörter sind Wegwerf-Werte und werden vor dem Live-Gang rotiert;
das Auslesen der Dev-Container per `docker inspect` ist vom User freigegeben.)

**Mailversand** (Slice „SMTP-Fundament"): Das SMTP-Passwort liegt Fernet-
verschlüsselt in `company.mail_account` (Migration 0046). Der Schlüssel kommt aus
`MCN_MAIL_KEY` (base64 Fernet-Key) — **fail-closed**: ohne Schlüssel ist weder
Speichern noch Versenden möglich. Der Wert wird NICHT eingecheckt; den Dev-Key aus
dem Slice-Report übernehmen bzw. neu erzeugen:
```bash
export MCN_MAIL_KEY="<base64-fernet-key>"   # NICHT ins Repo; Dev-Wert in der Memory `hero-vollsurvey-2026-07`
# neuen erzeugen: uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```
Ohne `MCN_MAIL_KEY` startet das Backend normal — nur Mailversand/-konfiguration ist
gesperrt. **Wichtig:** Ein NEUER Schlüssel kann das gespeicherte SMTP-Passwort nicht
mehr entschlüsseln → dann das Mailkonto unter Einstellungen → Mailversand einmal neu
speichern. Für Verifikation dient der lokale SMTP-Fänger `scratchpad/smtp_sink.py`
(Port 1025).

**E-Rechnungs-Validatoren** (optional, nur für `test_erechnung_konformitaet.py`):
veraPDF 1.30.2 und Mustang CLI 2.24.0 brauchen Java (Temurin JDK 21). Ohne die
Variablen **skippen** die 12 Konformitätstests sauber — sie fallen nicht um.
```bash
export MCN_VERAPDF=/pfad/zu/verapdf        # PDF/A-3B, Flavour 3b
export MCN_MUSTANG_JAR=/pfad/zu/Mustang-CLI-2.24.0.jar   # XSD + EN16931-Schematron
export MCN_ERECHNUNG_DUMP=/pfad/zum/dump   # optional: Belege zur Sichtprüfung ablegen
```
Anleitung: `docs/erechnung-validierung.md`.

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
| `timo.kalinski@mitra-sanitaer.de` | MONTEUR | nur eigene Einsätze/Aufgaben/Zeiten (row_scope EIGENE) |

**Backend** (`cd backend`, uv):
```bash
uv run python manage.py check
uv run pytest -p no:cacheprovider -q          # aktuell 2510 grün, 14 skipped
uv run python manage.py migrate               # Migrationskopf: 0077 (einziges Leaf)
uv run python manage.py runserver 127.0.0.1:8000 --noreload
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

**Seit dem Neuaufbau der Dev-DB (2026-07-12) ist die ganze Kette real durchmigriert** —
`django_migrations` kennt die Baseline jetzt. **Erst `showmigrations db_core` ansehen:**

- Steht alles auf `[X]` → einfach `uv run python manage.py migrate db_core`. Fertig.
- Steht alles auf `[ ]` (alte, nicht neu aufgebaute DB) → gilt der alte Gotcha: Das
  Fachschema ist physisch da, aber unbekannt; ein direktes `migrate` scheitert an
  `0001_baseline` („schema identity already exists"). Dann:

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
  Modul `hr` per 0021, Modul **`maintenance` per 0071** ergänzt — Wartung lief vorher
  auf `workflow` mit, kein Rollenverlust): `db_core/services/rechte.py` wertet aus,
  `api/permissions.py` setzt durch. Rollen **addieren** Rechte; beim `row_scope`
  gewinnt die weiteste Sicht (`ALLE`).
- **Drei Torfunktionen — fail-closed als Grundhaltung:**
  - `require(request, modul, aktion)` — **Regelfall.** Wirft 403 auch dann, wenn
    die Rolle nur `EIGENE` sehen darf, der Endpunkt das aber nicht umsetzt.
    `EIGENE` wird **nie** stillschweigend zu `ALLE`.
  - `require_scoped(...)` — nur für Endpunkte, die wirklich auf eigene Zeilen
    filtern (aktuell: Aufgaben, Einsätze inkl. Zeit-/Materialbuchung, **Dateien**).
    Wer das nutzt, **muss** filtern, sonst ist die Begrenzung wirkungslos.
  - `require_create(...)` — für ANLEGEN, **aber nur bei Zeilen ohne setzbares
    Owner-Feld UND ohne fremdes Elternobjekt.**
- **Faustregel (aus drei Review-Befunden gelernt):** Hängt die neue Zeile an einem
  Elternobjekt, das der Akteur womöglich nicht sehen darf, oder trägt sie ein Feld,
  mit dem er sie jemand anderem zuordnen kann → **`require`** (bzw.
  `require_scoped` und den Akteur als Owner erzwingen). Über `create_task` ließ
  sich sonst eine Aufgabe fremd zuweisen, über `create_service_case` ein
  nummerierter Vorgang an einem fremden Projekt anlegen — und über den
  **Datei-Upload** ein Foto an einen fremden Baustellenbericht/Auftrag hängen.
- **Fremde Zeilen → 404, nicht 403** (Detail/Schreibzugriff), damit ihre Existenz
  nicht verraten wird.
- **Zwei Ebenen nicht verwechseln:** Recht (403, `permissions.py`) vs. fachliches
  Tor (422, Service + DB-Trigger). Wer FREIGEBEN darf, darf trotzdem keinen
  Auftrag freigeben, dem die Vorbedingungen fehlen.
- **Payload-Fremdschlüssel vorab prüfen** (`services/_validation.py`:
  `ensure_exists`, `ensure_all_exist`, `ensure_party_usable`) — sonst schlägt eine
  unbekannte UUID als IntegrityError durch (500 statt 422).
- **Frontend**: `core/auth.service.ts` (Signal + `darf()`), `auth.interceptor.ts`
  (`withCredentials`, `X-CSRFToken`, 401 → `/login`, 403 **nicht** umleiten),
  `auth.guard.ts` (`authGuard` + `darfGuard(modul, aktion)` je Route),
  `shared/http-fehler.ts` (403 → `kind:'forbidden'`), `shared/kein-zugriff`.

## 2c. Schreib-UI-Bausteine (seit dem Schreibpfad-Slice)

Vorher gab es kein Formular außer dem Login. Diese Bausteine tragen jetzt alle
Bereiche — **nutze sie, baue nichts Eigenes**:

| Baustein | Zweck |
|---|---|
| `shared/dialog` | native `<dialog>`-Hülle: Fokus-Trap, Fokus-Rückgabe, Escape/Backdrop abschaltbar, Scroll-Lock |
| `shared/formular/feld` | ein Feld für Text/Textarea/Zahl/Datum/Select/Checkbox, mit Label, `aria-invalid`, `aria-describedby` |
| `shared/formular/dezimal.ts` | deutsche Komma-Eingabe ⇄ API-Punkt-String. **Decimal bleibt String.** `apiZuDeEingabe` = **ohne** Tausenderpunkt (Formulare), `apiZuDeAnzeige` = mit (nur Anzeige) — nie vertauschen, siehe Invariante |
| `shared/formular/api-fehler.ts` | `apiFehlerZuweisen` — versteht beide 422-Formen (Pydantic-Feldfehler und Freitext aus `HttpError(422, str(exc))`) |
| `shared/formular/referenz-wahl` | WAI-ARIA-Combobox mit Serversuche für Fremdschlüssel (statt roher UUID) |
| `shared/bestaetigung` | Konsequenz-Text, optionales Pflicht-Begründungsfeld, „Bestätigen" ist nie der Standardfokus |

Regeln, die überall gelten:
- Aktion nur rendern, wenn `authService.darf(modul, aktion)` — der Server lehnt
  sonst ohnehin mit 403 ab.
- **Geld und Mengen sind Strings.** Nie `parseFloat`/`Number()` ins Datenmodell,
  nur zur Anzeige. Der **Server rechnet Summen verbindlich** — der Belegeditor
  zeigt bewusst keine eigene Summe.
- Jede unumkehrbare Aktion (veröffentlichen, versenden, stornieren, archivieren,
  austragen, kündigen, ablehnen) hinter `shared/bestaetigung`.

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


## 9. Wo alles liegt

- **Roadmap/Pläne:** `docs/roadmap/` (README + 00 IA + 01–14 je Sektion, aus 221
  Hero-Artikeln abgeleitet). Hero-Quelle: `Hero Wissen/` (untracked, .docx).
- **Memory** (lädt jede Session automatisch): `backend-stack-entscheidung`,
  `design-und-marke`, `dev-db-zugang`, `roadmap-hero-mapping`,
  `umsetzungsstand-frontend`, dieses Handoff.
- **Git:** Branch `master`. Jeder Slice ist ein eigener Commit mit ausführlicher
  deutscher Message — `git log --oneline` gibt die Historie.

