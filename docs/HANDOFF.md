# HANDOFF — MCN Leitstand (für die nächste Session)

Dieses Dokument macht eine frische Session sofort handlungsfähig. **Zuerst lesen**,
dann `docs/roadmap/README.md` + `docs/roadmap/00-informationsarchitektur.md`.

> TL;DR: MCN ist ein KI-first CRM (Nachfolger des Hero-CRM) für Handwerk/
> Gebäudeservice. DB ist database-first PostgreSQL (Regeln in Triggern). Backend
> Django 5 + django-ninja. Frontend Angular „Leitstand". Es wird in **vertikalen
> Slices** gebaut (DB→Service→API→UI→Verifikation→Review). Aktuell **~18 Bereiche
> live und bedienbar** (Kontakte, Liegenschaften, Projekte, Dokumente, Planung
> inkl. Plantafel/Kalender/Ressourcen, Wartung, Aufgaben, Mitarbeiter/HR, Artikel
> inkl. VK-Kalkulation, Buchhaltung inkl. Mahnwesen + Storno/Korrektur +
> Beleg-PDF, Auswertungen, Einstellungen, Mein Profil).
> **Auth/Login + Rechtematrix stehen** (eigenes Login, kein SSO); die gesamte API
> ist anmeldepflichtig. **Der Schreibpfad ist verdrahtet**: „+ Neu", Statusaktionen
> und Freigaben laufen aus dem UI durch Rechte, Statusautomaten und DB-Trigger.
> Dazu **Vier-Augen-Freigaben**, **Belegerfassung** (Eingangsrechnungen) und die
> **Rechtematrix-Pflege** als UI.
> **~1573 Backend-Tests grün** (Zahl schwankt mit paralleler Arbeit im Baum),
> db_core-Migrationen bis **0047**, accounts bis **0002**.
> Stand 2026-07-11 (Hero-Paritäts-Ausbau, 20 Slices an einem Tag — Details in
> `git log`): Artikelstamm nach Hero (Felder/VK-Gruppen/Lieferant/Bild,
> Suchoperatoren + · | · *, Spaltenwahl, Kopieren), **Marge/Deckungsbeitrag** in
> den Auswertungen (fehlender EK = „unbekannt", nie 0/100), **Storno/Gutschrift-UI**
> (ehrlicher 201/202-Vier-Augen-Fluss), **Kontaktmappe** verdrahtet (Ansprech-
> partner/Adressen/Kommunikationswege + Aufgaben-Tab), **Aufgaben-Formular** mit
> Zuweisung/Verknüpfung + Bearbeiten, **Vorgangs-Statuswechsel** + **Kanban-Board**
> (Projektassistent), **Mailversand komplett** (SMTP-Fundament Fernet-verschlüsselt
> `company.mail_account`/`MCN_MAIL_KEY`; Rechnungs-/Angebots-/Mahnungsversand mit
> PDF; **Angebots-PDF** neu; **Passwort-vergessen** Reset-Link), **Firmenlogo im
> Beleg-PDF**, **Wartungs-Fälligkeits-Scheduler** (Command `wartung_faellige_ausloesen`,
> PROJEKT/AUFTRAG erzeugen echte Folgeobjekte). Analyse aller offenen Hero-Bereiche
> + Mail-Details: Memory `hero-vollsurvey-2026-07`.
> Dazu (2026-07-11, Welle 2): **Auswertungs-CSV-Export** je Dashboard,
> **Lohngruppen-/Maschinengruppen-Verwaltung** (`/pricing/wage-groups`),
> **semi-automatischer Mahnlauf** (`/buchhaltung/mahnlauf` — Vorschau + bestätigter
> Stapel, verlinkt aus dem Mahnwesen), **HR-Selbstauskunft** (`GET /hr/self` —
> eigener Resturlaub/Vertrag/Abwesenheiten, `features/meine-personalakte`, verlinkt
> aus „Mein Profil"; für normale MA einmalig eine Rolle mit hr/LESEN+EIGENE anlegen).
> **Noch offen — DERIVIERBAR:** Gewerke-Firmenzuordnung + Akquisekanäle/Quellen
> (brauchen neue Tabelle/Migration — 2026-07-11 Kollisionsrisiko mit parallelem
> Agenten an models.py/0048), Wartungs-Anlage-UI.
> **Noch offen — GRUNDSATZENTSCHEIDUNG nötig:** XRechnung/ZUGFeRD (gesetzl. E-Rechnungs-
> pflicht B2B! Format-/Lib-Wahl), DATEV/Lexware-Export (Format), Skonto (Feld+Modell),
> Abschlags→Schlussrechnung-Anrechnung, freier Termin ohne Auftrag
> (`service_job.work_order_id` NOT NULL), OAuth-Absenderkonten (User-App-Registrierung),
> IDS-Connect-Ablauf.

---

## 0. Nächste Session — Stand & offene Entscheidungen (ZUERST LESEN)

**Das System ist bedienbar.** Auth, Rechtematrix und der komplette Schreibpfad
stehen: Aus dem UI laufen „+ Neu", Statusaktionen, Freigaben, Zahlungen und
Stornos durch Rechteprüfung, Service-Schicht, Statusautomaten und DB-Trigger.
**Alle Fachschemata der Roadmap sind gebaut** außer **HR-Steuer/Bank**.

### Die drei früheren Entscheidungen sind gefallen (E1–E3 erledigt)

**E1) Belegerfassung → eigene `receipt`-Tabelle im neuen Schema `accounting`.**
Nicht eine gerichtete `invoice`. Begründung in `migrations/0031`: `invoicing` ist
die GoBD-gesicherte AUSGANGSseite (Belegkreis, Snapshot/Hash, Festschreibung);
Eingangsbelege haben eigene Nummern- (`EB-00001`) und Statuslogik. Dazu
`ledger_account` + `cost_center` (0030). UI unter `/belegerfassung`.

**E2) Vier-Augen-Flow ist gebaut** (`security.approval_request`, Migration 0028).
Zwei Muster über eine Tabelle: **Applier** (die Änderung liegt im `payload` und
wird erst durch `approve()` geschrieben — so die Firmen-Bankdaten) und
**Torfunktion** (`claim`/`consume` — so Storno/Rechnungskorrektur). UI unter
`/freigaben`. **HR-Steuer/Bank bleibt offen** (DSGVO Art. 9/32, Verschlüsselung
at rest, Schlüsselverwaltung) — der Flow dafür steht aber jetzt bereit.

**E3) Beleg-PDF-Archivierung läuft über das offizielle `minio`-SDK** (nicht
boto3), `db_core/storage.py`. Erster PDF-Abruf rendert, legt in MinIO ab und
registriert `content.file`/`file_link`; Folgeabrufe liefern das Archiv. Bei nicht
erreichbarem Speicher **degradiert** der Endpunkt auf On-the-fly-Rendering statt
zu scheitern. E2E-Test überspringt sauber ohne laufenden Server.

### Invarianten des Vier-Augen-Flows (nicht versehentlich „vereinfachen")

- **Die Genehmigung ist an den `payload` gebunden, nicht nur an Aktion + Ziel.**
  Storno und Rechnungskorrektur teilen sich den `action_code`
  RECHNUNGSKORREKTUR. Ohne Payload-Bindung ließe sich eine genehmigte
  Teilgutschrift („Position 1") als **Vollstorno** der ganzen Rechnung einlösen —
  ein Review hat genau das reproduziert. `find_grant(..., payload=...)`.
- **`claim()` verbraucht die Genehmigung in DERSELBEN Transaktion wie die
  Aktion** (`SELECT … FOR UPDATE`). Nicht auf „Aktion ausführen, danach
  `consume()`" zurückbauen: zwei parallele Requests lösten sonst dieselbe
  Genehmigung doppelt ein, und ein Fehler nach dem Schreiben hinterließe einen
  Beleg mit unverbrauchter Genehmigung. Scheitert die Aktion fachlich (422),
  rollt das Verbrauchen mit zurück — die Genehmigung bleibt gültig.
- **Entscheidungen filtern auf `status='ANGEFORDERT'`** und prüfen `updated == 1`.
  Der DB-Trigger lässt GENEHMIGT→GENEHMIGT als No-Op durch; ohne den Filter
  überschriebe ein zweiter Genehmiger den Entscheider und triebe den Applier
  erneut.
- **Der `payload` wird nur an den Antragsteller und an Entscheider
  (`security/FREIGEBEN`) ausgeliefert** (`payload_verborgen`). `security/LESEN`
  hält auch NUR_LESEN — sonst läse jede Nur-Lese-Rolle die beantragte IBAN mit.
  Spätestens mit HR-Bankdaten wäre das ein DSGVO-Leck.

### Belegposition ist eine Kopie, kein Verweis (Invariante, nicht „vereinfachen")

Eine Position in Angebot/Rechnung trägt ihre Werte **eingefroren**. Ein neuer
Listenpreis im Artikelstamm verfälscht kein bereits geschriebenes Angebot, sonst
wäre dessen Marge im Nachhinein nicht mehr nachvollziehbar. Umgekehrt schreibt
das Speichern einer Position **niemals** in `pricing.article`.

Der einzige Weg vom Beleg in den Stamm ist das **Häkchen „Änderungen auch in den
Artikelstamm übernehmen"** im Positionsdetail des Angebotseditors:
- Es ist **transient** (lebt nur im Dialogformular, bei jedem Öffnen `false`,
  nie im `EditorLine`-State/`QuoteUpdate`-Payload). Sonst schlüge es bei jedem
  späteren Speichern erneut zu.
- Es löst einen **eigenen, ausdrücklichen Vorgang** aus
  (`POST /pricing/articles/{id}/stammdaten-uebernehmen`) hinter
  `shared/bestaetigung`, und verlangt **`pricing/AENDERN`** — wer ein Angebot
  schreiben darf, darf damit nicht den Stamm umschreiben, den alle anderen
  Angebote mitbenutzen.
- Der **Einkaufspreis wird bewusst NICHT übernommen** (Aussage des Händlers aus
  DATANORM; ein abweichender EK ist eine Kalkulationsentscheidung für genau
  dieses Angebot). Der Verkaufspreis wird als Standard-Festpreis hinterlegt.
- Scheitert die Übernahme (403/422), bleibt die Positionsänderung erhalten und
  der Fehler wird angezeigt — nie eine Erfolgsmeldung.

Vier Tests sichern das ab (u. a. ein statischer, der einen Schreibpfad von
`beleg.py` in den Artikelstamm verhindert).

### Ableitbare Reste (kein Entscheidungsbedarf, einfach bauen)

- **Vier-Augen auf weitere Aktionen ausrollen**: der Flow steht, angeschlossen
  sind bislang nur BANKDATEN (Applier) und RECHNUNGSKORREKTUR (Tor). Die
  Stammdaten `security.four_eyes_action` kennen außerdem Dubletten-Merge,
  Massenexport und KI-Massenaktionen. **Muster:** liegt die ganze Änderung im
  `payload` → Applier in `_APPLIERS`; ist die Durchführung ein eigener Ablauf →
  `claim()` in derselben Transaktion wie die Aktion.
- **Marge** braucht die EK-Ebene und ist aus Belegzeilen nicht
  ableitbar (ggf. über den `billing_snapshot`). Die Dashboards Projekte, Artikel
  und Mitarbeitende sind gebaut.
- **Plantafel Drag & Drop** (Umplanen ruft `POST /planung/einsaetze/{id}/schedule`,
  der Endpunkt existiert).
- **DATANORM-Import** (Artikelstamm) und **DATEV/Lexware-Export**.
- **Wartungs-Fälligkeits-Scheduler** (Cron/Worker; heute nur manuell auslösbar).
- **„Passwort vergessen"**: braucht Mailversand. Hero-Fakt: Einmal-Passwort
  12 Stunden gültig. Passwort **ändern** existiert bereits unter `/profil`.
- **Mailversand** insgesamt — dazu gehört auch der Mailserver-OAuth aus
  `docs/roadmap/14`. Das ist **Mailversand, nicht Login** (siehe Abschnitt 2b).

### Bewusst offene Invarianten (nicht versehentlich „reparieren")

- **`row_scope='EIGENE'` ist nur für Aufgaben und Einsätze umgesetzt.** Überall
  sonst gilt **fail-closed**: `require()` wirft 403. Ein MONTEUR sieht Projekte,
  Aufträge, Wartung und Plantafel deshalb gar nicht. Wer das ändern will, setzt
  EIGENE dort **echt** um und stellt auf `require_scoped` — niemals einfach auf
  `require` zurückfallen, das wäre ein stiller Datenleak. Zwei Reviews haben hier
  je ein Loch gefunden (`create_task`, `create_service_case`); beide sind
  geschlossen, das Muster bleibt gefährlich.
- **Ressourcen-Doppelbelegung ist nicht physisch verhindert.** Der maßgebliche
  Zeitraum liegt auf `service_job` und ist dort nullable; ein EXCLUDE käme nur
  mit Denormalisierung plus Synchron-Trigger zustande und griffe an NULL-Rändern
  still nicht. Der Service warnt, blockiert aber nicht. Die Roadmap führt
  Doppelbuchung ausdrücklich als weich.
- **Mahnstufen:** `fee`/`interest_note` bleiben NULL (Beschluss B-22,
  Steuerberater-Vorbehalt). Aktive Stufen müssen einen lückenlosen Präfix bilden,
  sonst könnte der DB-Trigger sie nie ausstellen.
- **Kein Feiertagskalender** — Abwesenheitstage zählen gesetzliche Feiertage als
  Arbeitstage, wenn der Vertrag für den Wochentag ein Soll ausweist.
- **Belegeditor rechnet keine Summen.** Exakte Rundung je Steuergruppe ist in
  JavaScript-`number` nicht verlustfrei; der Server rechnet verbindlich. Nicht
  „nachrüsten".
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
export MCN_MAIL_KEY="<base64-fernet-key>"   # NICHT ins Repo; Dev-Wert im Slice-Report
# neuen erzeugen: uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
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
  - `require(request, modul, aktion)` — **Regelfall.** Wirft 403 auch dann, wenn
    die Rolle nur `EIGENE` sehen darf, der Endpunkt das aber nicht umsetzt.
    `EIGENE` wird **nie** stillschweigend zu `ALLE`.
  - `require_scoped(...)` — nur für Endpunkte, die wirklich auf eigene Zeilen
    filtern (aktuell: Aufgaben, Einsätze inkl. Zeit-/Materialbuchung). Wer das
    nutzt, **muss** filtern, sonst ist die Begrenzung wirkungslos.
  - `require_create(...)` — für ANLEGEN, **aber nur bei Zeilen ohne setzbares
    Owner-Feld UND ohne fremdes Elternobjekt.**
- **Faustregel (aus zwei Review-Befunden gelernt):** Hängt die neue Zeile an einem
  Elternobjekt, das der Akteur womöglich nicht sehen darf, oder trägt sie ein Feld,
  mit dem er sie jemand anderem zuordnen kann → **`require`** (bzw.
  `require_scoped` und den Akteur als Owner erzwingen). Über `create_task` ließ
  sich sonst eine Aufgabe fremd zuweisen, über `create_service_case` ein
  nummerierter Vorgang an einem fremden Projekt anlegen.
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
| `shared/formular/dezimal.ts` | deutsche Komma-Eingabe ⇄ API-Punkt-String. **Decimal bleibt String** |
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
| Einstellungen (95) | **Firmenprofil, Mahnstufen (6), Gewerke, Niederlassungen** (`company.*`, NEUES Schema 0023). Das Firmenprofil speist Aussteller und Fußzeile des Beleg-PDF | `/api/company`, `/api/buchhaltung/dunning-levels` |
| Freigaben (62) | **Vier-Augen-Anträge** (`security.approval_request`, 0028): Liste + Statusfilter, Genehmigen/Ablehnen (Pflicht-Begründung)/Zurückziehen. Payload nur für Antragsteller und Entscheider | `/api/security/approvals` |
| Belegerfassung (82) | **Eingangsrechnungen** (`accounting.*`, NEUES Schema 0030/0031): Liste + Beleg-Mappe (Positionen/Verlauf), Editor, Statusautomat ERFASST→GEPRUEFT→FREIGEGEBEN→GEBUCHT/ABGELEHNT, Freigabe-Tor (Kontierung je Position), Stammdaten (Buchungskonten/Kostenstellen) | `/api/accounting` |
| Einstellungen · Rechte | **Rechtematrix-Editor** (Rolle × Modul × Aktion + row_scope) + **Rollenzuordnungen**. Härtungen: keine Selbst-Erweiterung, keine Selbstzuweisung, letzte ADMINISTRATION geschützt | `/api/security/{roles,permissions,users,user-roles}` |
| Mein Profil | Anzeigename/E-Mail/Rollen read-only + **Passwort ändern** (Sitzung bleibt gültig) | `/api/auth/password` |

**Der Schreibpfad ist verdrahtet.** In allen Bereichen gibt es „+ Neu",
Statusaktionen, Freigaben; unumkehrbare Aktionen laufen über einen
Bestätigungsdialog. Zusätzlich neu: Zahlung erfassen/stornieren, Mahnung
erzeugen, Belegeditor mit Positionen, Einsatz-Zuweisung, Zeit-/Materialbuchung
(auch für den Monteur auf eigenen Einsätzen), Ressourcen und Terminkategorien.

Nav-Marks: Planung=50, Wartung=55 (bewusst nicht-rund, Service-Cluster),
Aufgaben=60, Mitarbeiter=65, Artikel=70, Buchhaltung=80, Auswertungen=90,
Einstellungen=95.

Backend: **808 Tests grün**, db_core-Migrationen bis **0027**, accounts bis 0002.
Hand-SQL-Fachschemata: 0016 `maintenance`, 0019 `hr`, 0023 `company`,
0025 `resource` + `workflow.appointment_category`; 0021/0024 erweitern die
Rechtematrix um die Module `hr` und `company`; 0025 baut die Mahnleiter auf sechs
Stufen aus. **Achtung:** zwei Migrationen heißen `0025_*` (paralleler Bau); der
Graph ist gültig (0026 führt beide Zweige zusammen, 0027 ist das einzige Leaf),
aber `migrate db_core 0025` ist mehrdeutig — vollen Namen angeben.
Neue Dependency **fpdf2**
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

- ✔ **Datei-Ablage im UI** (`shared/dateien`, `core/datei.service.ts`): Upload per
  Klick und Drag&Drop (tastaturbedienbar), Fortschritt, Download über Blob
  (nicht `window.open` — Auth-Cookie/CSRF), Verknüpfung lösen hinter Bestätigung
  (die Datei selbst bleibt). Verdrahtet in **neun Mappen**: Projekt, Kontakt,
  Liegenschaft, Angebot, Rechnung, Offener Posten, Vorgang, Auftrag, Einsatz.
  **Offen:** `unit_id`/`asset_id` — beide haben (noch) keine eigene Detail-Mappe.
- ✔ **Artikel bearbeiten / Historie / Stamm-Übernahme** im UI: Reiter
  Informationen · Kalkulation · Historie (der alte `preis`-Tab war reine
  Dopplung und ist in Kalkulation aufgegangen). Bearbeiten-Dialog, GTIN mit
  Prüfziffer (Client spiegelt `artikel.py::_gtin_gueltig` — beide müssen
  synchron bleiben), Statuswechsel (Deaktivieren hinter Bestätigung), und das
  Häkchen im Angebotseditor (siehe Invariante oben).

Kleinere offene Enden: Objekt-Bilder; ISO-Datums-Formatierung im UI (aktuell teils
roh); `angebot-editor.scss` liegt über dem 8-kB-Budget (nur Warnung, vorbestehend).

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
