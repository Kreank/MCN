# Archiv — Chronik bis 2026-07-22

> Historisch gewachsene Session- und Wellenberichte aus `HANDOFF.md`.
> **Nicht als aktueller Stand lesen** — Zahlen, Migrationsköpfe und TODO-Listen
> darin sind zum Zeitpunkt ihrer Niederschrift richtig gewesen und heute teils
> überholt. Der geltende Stand steht in `docs/HANDOFF.md`, die dauerhaften
> Regeln in `docs/INVARIANTEN.md`.

---

## ⚡ SESSION 2026-07-20 — Dublettenvermeidung bei der Erfassung

**Auslöser (Fall des Users, wörtlich nachstellbar):** WEG Albrechtstraße 30 existiert,
darin das Gebäude Albrechtstraße 22. Mieter Klaus (EG links, Eigentümer Braun) ruft an.
Der Mitarbeiter sucht „Klaus" → nichts. Er sucht im Liegenschafts-Picker „Albrechtstraße 22"
→ **ebenfalls nichts**, weil `list_properties` nur `name`/`property_number` durchsuchte.
Also legt er eine zweite Liegenschaft an — stillschweigend, ohne Warnung. Zusätzlich trug
`quick_intake` ihn pauschal als `PROPERTY_OWNER` ein, obwohl er Mieter ist.

**Gebaut in diesem Slice:** Adresssuche in `list_properties` (inkl. Gebäudeadressen per
Exists-Subquery), Steckbrief je Treffer (Adresse/Eigentümer/Verwaltung/Telefon+Quelle/
Einheitenzahl), neuer Endpoint `GET /api/property/properties/adress-dubletten`
(Trefferarten `EXAKT` | `GEBAEUDE` | `STRASSE`), Telefon-/Adresssuche bei `list_parties`,
Merkmalzeilen im `ReferenzWahl`-Picker, Live-Dublettenpanel in Schnellerfassung und
Liegenschaften-Dialog. Das Panel ist bewusst **Hinweis, kein Blocker**.

**Stand:** zwei Opus-Review-Runden bestanden (Runde 1: 1 Blocker + 3 wichtig, alle behoben;
Runde 2: ship-ready, keine Regression). Verifiziert in einem **isolierten Worktree** gegen
einen Wegwerf-Postgres-16 (Port 55439), weil parallel zwei weitere Agenten auf
`mitra-crm-test` arbeiteten: `check` sauber, `makemigrations --check` „No changes detected",
volle Suite **3832 passed, 15 skipped, 19 errors, 0 failed**; die 19 Fehler sind das bekannte
`CommandError: flush`-Artefakt und wurden per Gegenprobe mit gestashten Änderungen als
vorbestehend **nachgewiesen**. `ng build` grün.

### ⚠️ COMMIT-HYGIENE — vor dem Commit entscheiden

`backend/db_core/services/suche.py` trägt im Arbeitsbaum **zwei Slices gleichzeitig**: die
`textsuche`-Extraktion (dieser Slice) UND die Gewerk-Kürzel-Erweiterung von `kennung_parsen`
(`_KENNUNG_GEWERK`, Format `AU-HZG-26-0142`, gehört zu den Migrationen 0120/0121 des parallel
laufenden Gewerk-Slice). `backend/db_core/tests/test_suche.py` (+34 Zeilen) gehört ganz zum
Gewerk-Slice. Wer `suche.py` pauschal `git add`t, zieht fremde halbfertige Arbeit mit in den
Commit. Entweder `git add -p` und nur die `textsuche`-Hunks nehmen — oder bewusst
entscheiden, dass beide Slices gemeinsam gehen.

### 🔴 BEWUSST VERTAGT — im Folge-Slice nachziehen (Zusage an den User, 2026-07-20)

1. **`building.address_id` ist über die API nicht befüllbar.** Die Spalte existiert seit
   `0004_property.sql:57` und der Anzeige-Fallback „Gebäudeadresse, sonst Liegenschaftsadresse"
   ist in `backend/api/planung.py:322` implementiert — aber `BuildingIn`
   (`backend/api/property.py`) kennt kein Adressfeld, `add_building` übergibt kein
   `address_id`, und `frontend/src/app/core/property.model.ts` hat es am `Building` nicht.
   Der Service-Parameter ist da (`db_core/services/property.py`), wird aber nur aus Tests
   benutzt. **Folge:** Die WEG-über-mehrere-Adressen-Struktur, die dieser Slice suchbar
   macht, lässt sich im UI gar nicht erfassen. Ohne diesen Punkt greift die
   `GEBAEUDE`-Trefferart nur bei Datensätzen, die per SQL entstanden sind.
2. **`quick_intake` kennt keine Mieter.** `backend/api/projekt.py:1302` trägt den Melder
   bei neuer Liegenschaft immer als `PROPERTY_OWNER` ein; `tenure.occupancy` /
   `occupancy_party` werden nie angefasst. Der Anrufer muss im Intake als **Mieter oder
   Eigentümer** erfassbar sein. Zieht nach sich: `tenure.ownership_period` /
   `ownership_interest` haben bis heute **kein ORM-Modell und keinen Endpoint** (die
   Eigentums-Ansicht in `liegenschaft-detail.html:180` ist ein Platzhalter).

---

## ⚡ SESSION 2026-07-18/19 (d) — KI Slice 5: konversationeller „frag das CRM"-Assistent

Der nächste große KI-Schritt aus dem Backlog. **Im Arbeitsbaum auf `develop`, NICHT
committet** (der User committet/pusht selbst). Neue Tests grün, zwei Opus-Reviews
bestanden (Backend ship-ready; Frontend nach Fixes). Details Memory
`ki-slice5-assistent`.

**Was ist es:** Ein mehrturniger Chat-Assistent, der Fragen zu Kunden/Objekten/
Projekten/Aufträgen beantwortet — gegroundet in der globalen Suche + den Dossiers,
**mit der Sicht des fragenden Nutzers** (nie privilegiert). Umfang (User-Entscheidung
„1+3", serverseitig persistiert): Auskunft + Kennzahlen UND aus dem Chat einen echten
`ai_proposal` (Berichtsentwurf) anlegen; Gespräche serverseitig gespeichert, DSGVO-löschbar.

**Mechanismus (kein offener Agent-Loop):** Zweitakt-Pipeline `db_core/ai/assistent.py`.
(1) Modell wählt (constrained) Intent + Treffer-**Indizes** aus der rechtegefilterten
Suche; (2) Code montiert kompakte Dossier-Kontexte (+ Kennzahlen); (3) Modell antwortet
gegroundet mit Quellen. **INVARIANTE (Opus-bestätigt): das Modell darf nur Treffer wählen,
die die Suche geliefert hat — und die ist objektsicht-gefiltert. Das ist zugleich
Halluzinations- UND Objektgrenze; eine fremde Entität kann gar nicht in den Kontext
geraten.** Kennzahlen fail-closed auf row_scope ALLE; VORSCHLAG nur mit workflow/ANLEGEN.
Fällt das Modell aus → deterministischer Fallback (Trefferzusammenfassung). Vorlage:
`leitstand_briefing.py`.

**DB (Migration 0117 Hand-SQL + 0118 State-only):** `ai.conversation` +
`ai.conversation_turn`. Rohtext (Nachrichten) **löschbar** (DSGVO Art. 17, Kaskade);
Turns **append-only** (forbid_mutation auf UPDATE), Einzel-Löschen via REVOKE DELETE
gesperrt (nur Kaskade). Unveränderliches Audit = `ai_run` je Antwort (nur Refs/Verbrauch,
**nie** Rohtext; `ai_run_id` nullbar — der Fallback ohne Modell trägt keinen Lauf).
`proposal_id` = weiche Referenz ohne FK (Vorschlag eigenständig löschbar).
Migrationskopf jetzt **0118**.

**API:** `POST /api/ai/conversations/frage` (mit optionaler conversation_id),
`GET /conversations`, `GET/DELETE /conversations/{id}`. Eintritts-Tor wie die globale
Suche (403 wenn kein Modul lesbar), Ownership-getort. **Frontend:** `/ki-assistent`
(Zwei-Spalten-Chat, Nav mark '64', `darfGuard('workflow','LESEN')` — Scope EIGENE
erlaubt, NICHT in BEREICH_NUR_ALLE). Quellen-Chips verlinken ins Dossier; „zur Freigabe"
bei Vorschlags-Turn.

**Chat→Vorschlag:** VORSCHLAG-Intent + Ziel-Auftrag + Recht → PREISFREIER Berichtsentwurf
(BERICHT_SCHEMA) → `ai_proposal` (SITE_REPORT_ENTWURF, gleiche Form wie Sprachmemo) →
materialisierbar über das bestehende `proposal.approve`. **Review-Fix:** Proposal wird
ATOMAR mit den Turns in `_speichere` angelegt (keine Waise, keine Doppelanlage), +
Conversation-Lock (`select_for_update`) gegen seq-Races.

**Verifikation:** Neue Tests grün — `db_core/tests/test_ki_assistent_gespraech.py` (10,
DB-Invarianten), `…_service.py` (9, Pipeline+Fallback+Zitat-Grenze+VORSCHLAG→approve),
`api/tests/test_ki_assistent_api.py` (7, Wiring/Auth/Eigentum). Endpoint-Schutz-Scanner
akzeptiert die 4 Endpunkte. `ng build` sauber (Assistent-Komponente unter Budget).
**Frontend-Review-Fixes:** HIGH `outline: var(--focus-ring)` (nur Farbe → kein Fokusring)
auf `outline: 2.5px solid …`; Renn-Zustände in `senden`/`neuesGespraech`; verschachtelte
interaktive Elemente in der Gesprächsliste entschachtelt.
⚠️ **Volle Backend-Suite (~3700 Tests) lief in dieser Umgebung NICHT durch** (Runner-
Zeitlimit; Teillauf erreichte 32 % ohne Fehler). Die berührte Fläche (alle KI-Tests +
Endpoint-Scanner) ist grün; die Änderungen sind rein additiv (neue Tabellen/Modul/
Endpunkte/Frontend). **Vor dem Merge einmal die volle Suite laufen lassen.**

**Offen/ehrlich:** Live-Browser-Durchklick steht aus (braucht laufenden Stack +
`MCN_AI_PROFILES`, sonst greift der Fallback) — gleicher Stand wie die KI-Vorschläge.
Semantische Suche/Embeddings weiter ungebaut (v1 nutzt die lexikalische Suche).

**Commit-Fläche (Slice 5, noch nicht committet — der User committet selbst):**
- *Neu:* `backend/db_core/ai/assistent.py`, `backend/db_core/migrations/0117_ki_assistent_gespraech.py`
  + `0118_conversation_conversationturn.py`, `backend/db_core/tests/test_ki_assistent_gespraech.py`
  + `…_service.py`, `backend/api/tests/test_ki_assistent_api.py`,
  `frontend/src/app/features/ki-assistent/` (ts/html/scss).
- *Geändert:* `backend/db_core/models.py` (Conversation/ConversationTurn),
  `backend/api/ki.py` (4 Endpunkte + `_assistent_sicht`), `frontend/src/app/core/ki.model.ts`
  + `ki.service.ts`, `frontend/src/app/app.ts` + `app.routes.ts` + `core/bereiche.ts`.
- *Separat (die zwei Extra-Aufträge dieser Session):* `CLAUDE.md` (CI bleibt bewusst aus,
  Backup als gebaut vermerkt — Memory `ci-entscheidung`).

**Nächste Session — Startschritte:** (1) `git branch --show-current` = `develop` prüfen.
(2) Migrationskopf ist **0118**; bei DB-Arbeit weiter dort ansetzen. (3) **Vor dem Merge
`develop`→`main`: einmal die VOLLE Backend-Suite laufen lassen** (`cd backend && MCN_DEBUG=1
MCN_DB_NAME=mitra_crm_test MCN_DB_PASSWORD=mcn_dev_local uv run pytest -q`) — sie lief in
der Bau-Session nur in Teilen (Umgebungs-Zeitlimit; berührte Fläche 964 grün, Teillauf 32 %
ohne Fehler). (4) Optional: KI-Assistent live durchklicken (`MCN_AI_PROFILES` setzen, sonst
Fallback) und/oder als Folge-Slice die Vorschlags-Typen über `SITE_REPORT_ENTWURF` hinaus
erweitern (nur dieser Typ ist heute chat-entwerfbar/materialisierbar).

---

## ⚡ SESSION 2026-07-18 (c) — Brute-Force-Schutz am Login + Backup-Routine

Zwei Sicherheits-/Betriebslücken geschlossen (im Arbeitsbaum, **NICHT committet**;
Tests grün, je Opus-reviewt inkl. behobener Befunde).

**1. Brute-Force-/Rate-Limit-Schutz am Login** (der Blocker fürs Device-Auth-Go-Live).
- DB-gestützt (`security.login_throttle`, Migration **0116**; transienter Cache OHNE
  No-Delete): zählt Fehlversuche pro **Konto+IP** (Schwelle 5) und pro **IP** (30) in
  gleitendem Fenster, sperrt mit **429**. PL/pgSQL-UPSERT = atomar. Service
  `db_core/services/login_schutz.py`, verdrahtet in `api/auth.py` (Session- **und**
  Geräte-Login teilen die Zähler). Schwellen env-tunbar (`MCN_LOGIN_*` in settings).
- **Kein reiner Konto-Lockout** (das wäre DoS gegen beliebige Nutzer) — Sperre hängt an
  Konto+IP. Erfolg räumt den Konto+IP-Zähler ab.
- **Review-HIGH behoben (war Deploy-Blocker):** Client-IP kam aus X-Forwarded-For
  (erster Eintrag = **client-kontrolliert**, weil nginx via `proxy_add_x_forwarded_for`
  hinten anhängt) → Schutz komplett umgehbar **und** gezielter Aussperr-DoS. Fix: IP nur
  aus **`X-Real-IP`** (nginx setzt sie überschreibend) und nur wenn `MCN_TRUST_PROXY_IP`
  (aus `MCN_BEHIND_TLS_PROXY`); sonst REMOTE_ADDR. Test beweist: ohne Proxy-Vertrauen
  wird ein gefälschter Header ignoriert.
- Prune-Command `login_throttle_aufraeumen` läuft jetzt **im scheduler** (sonst wüchse
  die Tabelle unbegrenzt). 7 Throttle- + 30 Auth-Regressionstests grün.

**2. Backup-Routine** (Pflicht vor der ersten echten Rechnung — jetzt gebaut).
- Eigener Compose-Dienst `backup` (`deploy/backup.Dockerfile` = postgres:16-alpine +
  gepinntes `mc`; `deploy/backup-entrypoint.sh` = Tages-Loop): nächtlich **pg_dump**
  (gzip) + **MinIO-Bucket-Spiegel** + **.env/Schlüssel-Sicherung** (chmod 600) +
  Retention. Bind-Mount `${MCN_BACKUP_DIR:-./backups}`. Restore-Runbook in
  `docs/deployment.md` Abschnitt 8a.
- **Review-HIGH behoben:** (H1) `pg_dump | gzip` maskierte einen abgebrochenen Dump →
  korruptes „gültiges" Backup; Fix `set -o pipefail` + `gzip -t` + Nicht-Leer-Test.
  (H2) `date +%H` liefert `08`/`09` → BusyBox liest das als **Oktal** → Arithmetik-Crash
  → Loop stirbt (Backup läuft scheinbar, tut nichts); Fix: führende Nullen strippen.
  Dazu: `--remove` am Spiegel entfernt (additiv, Löschung propagiert nicht ins Backup),
  `umask 077` (Dump war welt-lesbar), Restore mit `ON_ERROR_STOP=1 --single-transaction`.
- **Verifiziert:** Skript-Syntax, Oktal-/pipefail-Fix gegen echtes sh, **pg_dump gegen
  die Dev-DB (gültiger gzip-Dump)**, `docker compose config` sauber. Voller Container-Bau
  + Live-Compose-Backuplauf bleibt der Deploy-Schritt des Users.
- **Offen (User/Ops):** `MCN_BACKUP_DIR` auf ANDERE Platte + off-box (rsync/S3);
  Restore-Probelauf auf Wegwerf-Server; MinIO-Versioning gegen Objekt-Überschreibung.

---

## ⚡ SESSION 2026-07-18 (b) — KI-Vorschlagskette geschlossen (Backend + Frontend)

**Wichtige Richtigstellung zuerst:** Das KI-Fundament war schon **weiter** gebaut, als
frühere Abschnitte dieses Dokuments behaupten. Der Tool-Vertrag Stufe 1–5, der v1-
Workflow **Sprachmemo→Bericht** (`db_core/ai/`), das **Tagesbriefing** und die
Migrationen **bis 0115** standen bereits (git log: `050bf6c`, `0f7ea0b`, `3ada6de` …).
Der Satz „KI-Fundament = nächster Slice, null Backend-Zeilen" (weiter unten) ist
**überholt** — er stammt vom 2026-07-15, davor.

**Die echte Lücke war die Sackgasse der Vorschlagskette:** ein `ai_proposal` wurde
erzeugt (PENDING), konnte aber **nirgends angenommen/materialisiert** werden. Das ist
jetzt geschlossen. Alles committet auf `develop`? **NEIN — noch nicht committet**
(der User committet/pusht bewusst selbst). Stand: im Arbeitsbaum, Tests grün.

**Was gebaut wurde (4 Slices, je Opus-reviewt):**
- **A — Vorschlagskette (Backend).** `db_core/ai/proposal.py`: `approve` materialisiert
  einen `SITE_REPORT_ENTWURF` über die **Fach-Service-Schicht** (`site_report.create_report`
  + `set_report_lines`) in einen echten `workflow.site_report` (ENTWURF) — dieselben
  Tore/Rechte/Trigger wie die manuelle Anlage, **atomar** (eine `business_transaction`,
  verschachtelte Savepoints), **idempotent/serialisiert** (`SELECT FOR UPDATE`),
  **preisfrei**. Unvollständige Entwurfszeilen (Menge/Einheit fehlt) werden zu TEXT-
  Notizen herabgestuft statt mit erfundener Menge durchgedrückt. Endpunkte in
  `api/ki.py`: `GET /proposals`, `GET /proposals/{id}`, `POST /proposals/{id}/approve`,
  `POST …/reject`, `DELETE …`. Rechte: Liste/Detail `workflow/LESEN` (fail-closed),
  Annehmen `workflow/ANLEGEN`+`AENDERN` (spiegelt die manuelle Anlage — kein KI-Seitentor),
  Ablehnen/Löschen `AENDERN`. **INVARIANTE:** `delete_proposal` reicht den rohen
  DB-Löschtor-Fehler (P0001) durch; die **API-Schicht** übersetzt in 422
  (`as_business_error`) — so erwartet es `test_ai_grundlagen`. Provenienz
  `aus_untrusted_quelle` server-abgeleitet aus `content_item.is_untrusted`
  (fail-safe = untrusted). Review: **sauber, keine HIGH/MEDIUM.**
- **B — Betreiber-Enablement.** `manage.py ki_tool {list,register,set-bearer,activate,
  deactivate}` — der User bindet ASR-Handy/Vision/OCR ohne Python-Shell an. Bearer
  secret-sicher (`--bearer-env` bevorzugt, nie geloggt/ausgegeben). `registry.set_status`
  neu. `deploy/.env.example`: **`MCN_AI_PROFILES`/`MCN_AI_DEFAULT_PROFILE`/`MCN_AI_KEY`**
  dokumentiert (LLM-Anbindung; `queue-worker` bekommt sie via `env_file`). Review-Fix:
  `set_bearer`-Fehler in `register` als `CommandError` statt Traceback.
- **C — Frontend `/ki-vorschlaege`.** Freigabe-Queue (Liste + Segmentfilter, Entwurf-
  Aufklapp, **„unsichere Quelle"-Warnung** bei untrusted, Annehmen→Bericht/Ablehnen/
  Löschen via Bestätigungsdialog). Nav-Eintrag (mark 63, `nurAlle`), Route
  `darfAlleGuard('workflow','LESEN')`, `bereiche.ts` ergänzt. Vorlage: `features/freigaben`.
  Review: **sauber, ship-ready.** **Offen/ehrlich: noch NICHT im Live-Browser
  durchgeklickt** (build- + review-verifiziert; API HTTP-getestet). Das **Sprachmemo-
  *Aufnehmen*** ist bewusst ausgeklammert — gehört auf die native App/das Monteurs-Handy,
  ohne registriertes ASR-Gerät nicht E2E-nachweisbar.
- **D — Robustheit.** `engine.reap_stale_workflows` (hängende RUNNING → FAILED, Schwelle
  `--wf-stale-seconds` Default 900) + `proposal.expire_stale_proposals` (PENDING→EXPIRED,
  macht abgelaufene Vorschläge DSGVO-löschbar) — beide im `ki_tool_queue_tick`.

**Verifikation:** Volle Backend-Suite **3724 passed**. Die **19 „Errors"** sind
ausschließlich `@pytest.mark.django_db(transaction=True)`-Tests (Abrechnung/Mahnung),
die am DB-**Flush** gegen die **No-Truncate-Schutztrigger** der geteilten Test-DB
scheitern — Umgebungsartefakt (CLAUDE.md: solche Tests gehören auf Wegwerf-DBs),
**unabhängig von diesem Slice** (kein Abrechnungs-/Migrations-/Trigger-Code angefasst;
die nicht-transaktionalen Tests derselben Dateien sind grün). Neue KI-Tests: Slice A 15,
B 8, D 5 — alle grün; KI-Gesamtfläche 182 grün. Frontend `ng build` sauber (nur die zwei
**vorbestehenden** SCSS-Budget-Warnungen angebot-editor/plantafel).

**Nächste sinnvolle Schritte:** (1) Frontend im Live-Browser durchklicken (Login
`admin@mitra-sanitaer.de` im Dev-Stand, PW unbekannt — im Dev setzen). (2) User-Ops:
echtes ASR-Handy per `ki_tool register` anbinden + `MCN_CRED_KEY` auf dem Server,
LLM-Profil via `MCN_AI_PROFILES` setzen, dann läuft der volle Pfad Memo→Bericht.
(3) Sprachmemo-Aufnahme/Upload-UI (native App). (4) `git add`/commit/`develop→main`
liegt beim User.

---

## ⚡ SESSION 2026-07-17/18 — Fresh-Reset, neuer Artikelstamm, Gerätewissen + DRINGENDER OFFENER PUNKT (DNS)

### 🔴 ZUERST: Alle Seiten waren offline — DNS zeigt auf alte IP (server-weit, nicht MCN)
- **Ursache:** Anschluss hat über Nacht neue öffentliche IP bekommen (dynamische IP, Zwangstrennung). DNS-A-Record von `tech-artist.de` (alle Subdomains CNAME darauf) zeigt noch auf **ALT 77.13.203.157**, Server hat aber **NEU 77.191.86.207**. Server-Stack ist komplett gesund (Uptime 7+ Wochen, keine Reboots) — reines DNS-Problem.
- **Sofort-Fix (Saschas Strato-Panel):** A-Record `tech-artist.de → 77.191.86.207` setzen. ODER lokal Windows-hosts-Datei.
- **Dauer-Fix HALB FERTIG:** DDNS-Updater gebaut unter `/srv/storage/scripts/strato-ddns/` (`update.sh` + `strato-ddns.conf` chmod 600). Endpoint verifiziert = **`dyndns.strato.com`** (NICHT `dyn.strato.com`), Basic-Auth, User `tech-artist.de`, Host `tech-artist.de`, `myip=`. **BLOCKER:** Strato antwortet **`badauth`** — Saschas Testpasswort war falsch bzw. DynDNS im Strato-Panel noch nicht aktiviert. **TODO:** Sascha aktiviert DynDNS im Panel + setzt DynDNS-Passwort → in `strato-ddns.conf` eintragen → `update.sh` laufen lassen (behebt Ausfall sofort) → dann **cron alle 5 Min** einrichten (noch NICHT installiert). Ggf. `curl -4` erzwingen (Strato echote IPv6).

### Was diese Session gemacht wurde (alles live, außer wo vermerkt)
- **Demo komplett verworfen:** `mcn_pgdata`+`mcn_miniodata`-Volumes gelöscht, Stack frisch migriert (0001–0113, `MCN_SEED=0`). Sicherungen in `backups/`: `mcn-db-demo-final-20260717.sql.gz`, `env-demo-final-20260717.bak`, **`mcn-mail-key-20260717.txt`** (chmod 600: neuer MCN_MAIL_KEY + Erst-Admin-Zugang).
- **Neuer MCN_MAIL_KEY** in `.env` (alter Demo-Key ungültig).
- **Erst-Admin:** `sascha@mitra-sanitaer.de` / Passwort **`Mitra-2026-Start!`** (2026-07-17 neu gesetzt+verifiziert; das erste generierte PW verifizierte NICHT — Hash-Mismatch, ist ersetzt). Superuser + Rolle ADMINISTRATION, app_user_id `cff3a146-73e7-440c-b79b-ba3bd6b4e8bf`. Login end-to-end getestet (CSRF via `GET /api/auth/csrf`, Login mit Origin/Referer → 200).
- **Artikelstamm NEU:** B&O-Original-Datanorm importiert = **2.043.336 Artikel, alle mit Listenpreis** (`datanorm_import --stamm 3STAMM.ZIP --preise DATANORM.ZIP --namespace bo`, ZIPs aus `websearch/datanorm_sample/`). VK-Standardregel „Händler-Listenpreis (Standard)" (LISTENPREIS +0 %) neu angelegt.
- **Hersteller-Ersatzteile importiert:** Vaillant **10.942** (`~/datanorm_quellen/Anleitungen/Preisliste_ET_DE_Update_01.07.2026.zip`, namespace `vaillant`) + Bosch/Junkers **17.921** (`~/datanorm_quellen/Datanorm_bosh_junkers/ers0726e.zip`, namespace `junkers`). Nur Listenpreis (EK=NULL ohne passende RAB). Importer erweitert für Hersteller-Datanorm-Variante (Preis im A-Satz, `--rabatt`-Flag, .002-Toleranz).
- **Neuer Reiter „Gerätewissen" LIVE** (Commit `ee4e0d4`): read-only `/geraetewissen`, zeigt NUR Ersatzteil-Namespaces (`GERAETEWISSEN_NAMESPACES=("vaillant","junkers")`, erweiterbar per Django-Setting), Großhandel `bo` ausgeschlossen. Endpunkte `/api/geraetewissen/{hersteller,ersatzteile,ersatzteile/{id}}`.
- **Emblem** entorangt (Navy statt Orange), live.

### ⚠️ Deploy-Weg & Saschas paralleler App-Login
Sascha entwickelt **parallel im selben Checkout** einen **App-Login (Device-Bearer-Token)** für die native Android-App. Diese Arbeit liegt jetzt committet auf develop (**Commit `17ca41a`**, WIP): `security.device_token` (Migr. 0114/0115), `api/device_auth.py`, `db_core/services/geraetetoken.py`, `/auth/device/login`+`logout`, globale Bearer-Auth. **⚠️ Nicht deployen bis Brute-Force-Schutz für `/auth/device/login` drin ist** (fehlt noch). **Live-Backend enthält diesen Code NICHT** — es lief aus isoliertem Worktree ohne Device-Auth (`security.device_token` existiert produktiv nicht, Migrationskopf 0113). **DESHALB Deploy IMMER aus isoliertem worktree** (`git worktree add /tmp/x <clean-commit>` → `docker build -f deploy/{backend,nginx}.Dockerfile -t mcn-{backend,nginx}:latest /tmp/x` → `docker compose up -d --no-build`), NIE `up --build` solange unfertige Login-Arbeit im Baum liegt. Ein zweites, agent-gebautes Device-Auth-System ist auf Branch `feature/device-token-auth` geparkt.

### Offene TODOs
1. **DNS/DDNS** (oben, dringend, Sascha-Passwort).
2. **Backup-Routine** — jetzt Pflicht (2 Mio echter Artikelstamm live), noch nicht gebaut. Nächtlicher pg_dump + MinIO-Spiegel + Mail-Key-Sicherung, am besten Compose-Dienst wie Scheduler.
3. **IDS-Anbindung G.U.T.** neu einrichten (Saschas UI-Schritt, neuer Mail-Key) — `net_price_semantics=GESAMT`.
4. **App-Login** fertigstellen (Brute-Force-Schutz) bevor Device-Auth live geht.
5. Optional: EK für Vaillant/Bosch-Ersatzteile via passender RAB nachziehen.

---

> TL;DR: MCN ist ein KI-first CRM (Nachfolger des Hero-CRM) für Handwerk/
> Gebäudeservice. DB ist database-first PostgreSQL (Regeln in Triggern). Backend
> Django 5 + django-ninja. Frontend Angular „Leitstand". Es wird in **vertikalen
> Slices** gebaut (DB→Service→API→UI→Verifikation→Review). Aktuell **~22 Bereiche
> live und bedienbar** (Kontakte, Liegenschaften, Projekte, Dokumente, Planung
> inkl. Plantafel mit Drag&Drop/Kalender/Ressourcen, Wartung inkl.
> **Fälligkeiten/Prüffristen/Gewährleistung**, Aufgaben,
> Mitarbeiter/HR inkl. **Zeiterfassung mit Stempeluhr** und Stundenausgleich,
> Artikel inkl. VK-Kalkulation und **EK→VK-Aufschlagsmatrix**,
> Buchhaltung inkl. Mahnwesen + Storno/Korrektur + Beleg-PDF +
> E-Rechnung, Auswertungen, **Werkzeuge** (Heizlast, Aufmaß u. a.), Einstellungen,
> Mein Profil).
> **Auth/Login + Rechtematrix stehen** (eigenes Login, kein SSO); die gesamte API
> ist anmeldepflichtig. **Der Schreibpfad ist verdrahtet**: „+ Neu", Statusaktionen
> und Freigaben laufen aus dem UI durch Rechte, Statusautomaten und DB-Trigger.
> Dazu **Vier-Augen-Freigaben**, **Belegerfassung** (Eingangsrechnungen) und die
> **Rechtematrix-Pflege** als UI.
> **2510 Backend-Tests grün** (14 skipped: 12 E-Rechnungs-Validatortests ohne Java,
> 1 MinIO-E2E), db_core-Migrationen bis **0077**, accounts bis **0002**.
> Das **Frontend baut erstmals ohne Budget-Warnung** (8/10 kB gehalten — nicht
> lockern, sondern auslagern).
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
> Welle 3 (2026-07-11): **Schnellerfassung + Zum-Projekt-Hochstufen** (Parallel-Agent,
> von mir eingecheckt), **Erste-Schritte-Checkliste** auf der Übersicht,
> **Akquisekanäle/Quellen** (`/company/acquisition-sources` + Quelle am Kontakt,
> Migration 0049/0050).
> **Migrationskopf 0075** (einziges Leaf; Arbeitsbaum sauber, alles committet).
> **Neu (2026-07-11):** **DATEV-EXTF-Export** (Buchungsstapel aus
> veröffentlichten Rechnungen, `GET /buchhaltung/datev-export.csv`, Config am
> Firmenprofil, Migration 0051; Dialog in der Buchhaltung). v1-Grenzen dokumentiert
> (Sammeldebitor, Automatik = aktuelle Sätze, Steuerberater-Roundtrip offen) —
> Details Memory `datev-export`. Dazu **IDS-Connect Slice 1: Lieferanten-Anbindungs-
> Verwaltung** (`pricing.supplier_connection` — Model/Trigger existierten schon;
> neu Service `anbindung.py` + `GET/POST/PATCH /api/pricing/supplier-connections` +
> Frontend `features/haendler-anbindungen`, Einstieg aus Artikel). `credential_reference`
> bleibt reiner Verweis (nie Secret). Details Memory `ids-connect`.
> **2026-07-12, Welle 1:** Erst der konsolidierte Commit `170f3c7`
> (**IDS-Connect komplett** inkl. Warenkorb-Roundtrip — nur Live-Test mit echter
> G.U.T.-URL offen —, **Baustellenberichte**, **DATANORM-Frontend-Import**), dann die
> **vier priorisierten Slices aus den Grundsatzentscheidungen — alle vier gebaut**:
> **Skonto** (`ae4d241`, 0058), **E-Rechnung ZUGFeRD/Factur-X** (`12e4f7f`, 0059),
> **Abschlags-/Teil-/Schlussrechnung** (`5c66561`, 0060/0061), **freier Termin ohne
> Auftrag** (`eb215f3`, 0062). Danach drei weitere Slices: **E-Rechnung extern
> gegengeprüft** (veraPDF + Mustang/Schematron, 6/6 PASS — die frühere Warnung
> „nicht zertifiziert" ist erledigt), **DATEV-Abschläge auf Anzahlungskonto**
> (0063, Schalter am Firmenprofil) und **Baustellenbericht am freien Termin**
> (0064/0065, dabei zwei Sicherheitslücken in der Datei-API geschlossen). Details
> unten. Grundsatzentscheidungen: Memory `grundsatz-entscheidungen-2026-07`.
> **2026-07-12, Welle 2:** `f1ed9d9` **Plantafel Drag & Drop, Design-
> Behebung und ein Datenverlust-Bug** (die deutsche Dezimal-Formatierung schrieb
> stillschweigend falsche Mengen — siehe Invariante unten) und `9893120`
> **Zeiterfassung mit Stempeluhr** (Migrationen 0066–0068, gesetzliche Pflicht
> nach § 17 MiLoG), **Plantafel auf Dispo-Niveau** und **Werkzeuge** (Nav 92:
> Heizlast, Heizkörper-Umrechnung, Volumenstrom, Einheiten). Details unten.
> **AKTUELL (2026-07-14, Welle 5):** Der Baustellenbericht ist von einem Freitext-
> zettel zur **beweisbaren Abrechnungsgrundlage** geworden. `3fcc37d`
> **Berichtspositionen + Soll-Ist** (0080–0083: der Bericht führt Artikel/Leistungen
> und Mengen — **niemals Preise**, denn er wird unterschrieben und versiegelt; er
> startet vorbelegt mit dem Angebot als **Soll**, der Monteur korrigiert nur die
> Abweichung, und daraus fällt Mehrverbrauch/Minderverbrauch/Zusatz/entfallen
> heraus). `573a72d` + `b5e5c3b` **Abrechnung** (0084/0085/0088: Rechnung aus Angebot
> bzw. aus Bericht+Zeiten, **„was ist noch nicht abgerechnet"**, Preisklärung statt
> Sackgasse — **fehlender EK ergibt NIE 0 €**; die **Doppelabrechnung ist physisch
> gesperrt**, und der **Storno löst die Bindung**, damit stornierte Leistung wieder
> abrechenbar wird). Dabei **drei Doppelerstattungen** im Storno-/Gutschrift-Pfad
> geschlossen (zwei davon vorbestehend) und ein **Zeitzonen-Fehler**, durch den eine
> nachts erfasste Rechnungsadresse nicht galt — der Beleg wäre **ohne
> Empfängeranschrift** rausgegangen. `ea6ebc9` **Raumaufmaß/Bauteilkatalog/Grundriss**
> (0086/0087, 0089–0094, Arbeit eines Parallel-Agenten, **nicht reviewt**).
> **2929 Tests grün.** Konzept für den KI-Ausbau: **`docs/ki-first-konzept.html`**.
> **Welle 3 (2026-07-12):** `e0403af` **EK→VK-Matrix, Fälligkeiten-Engine,
> HR-Reste und Aufmaß** — vier parallel gebaute Slices, wegen geteilter Dateien in
> EINEM Commit: **EK→VK-Aufschlagsmatrix** (0069/0073 — Großhandelspreis rein,
> Verkaufspreis raus; fehlt der EK, bleibt der VK **unbekannt, nie 0**),
> **Fälligkeiten-Engine** (0071/0074 — Wartungsintervalle, **Prüffristen**
> (TrinkwV/KÜO/SV…) und **Gewährleistung** unter einem Dach; Idempotenz garantiert
> die **DB**, nicht der Code), **HR-Reste** (0072/0075 — Stundenausgleich in
> **Minuten**, Resturlaubs-Übertrag, **Attest** hinter eigenem DSGVO-Tor,
> „Wer fehlt?"), **Aufmaß-Rechner** + Wasserinhalt/Ausdehnungsgefäß aus der
> NotizApp. Details unten.

---

## 0. Nächste Session — Stand & offene Entscheidungen (ZUERST LESEN)

### ⭐ HIER ANFANGEN (Stand 2026-07-15)

**Branches & Betrieb — DAS ZUERST:**
- **`main` ist der eingefrorene DEMO-Stand und läuft auf dem Server des Users.**
  Nicht direkt darauf entwickeln. Ein neuer Stand für den Server entsteht nur
  bewusst: `develop` → `main` mergen, dann auf dem Server pullen + `docker compose`.
- **`develop` ist der Arbeitsbranch — hier wird entwickelt.** (Beim Start prüfen:
  `git branch --show-current` sollte `develop` sein.)
- **GitHub-Remote `origin` = `github.com/Kreank/MCN`, PRIVAT.** Details/Konvention
  stehen jetzt in `CLAUDE.md` (Abschnitt „Betrieb, Branches & Deployment").
- **Deployment ist gebaut** (`deploy/`, Anleitung `docs/deployment.md`): vier
  Container (nginx, backend/gunicorn, postgres, minio) + Scheduler. Auf der
  Demo-Instanz ist **Mailversand totgelegt** und **`/admin/` gesperrt** (Abschnitt 10).
  **Offen:** Let's-Encrypt-Ausstellung nur mit selbstsigniertem Cert getestet
  (braucht die echte Domain — beim ersten echten Deploy prüfen); **Backup weiter
  bewusst offen** (Abschnitt 10, Invarianten stehen bereit).

**IDS-Connect Preis-Semantik — der „GC-Quirk" (Fix 2026-07-16, Migration 0111):**
- **Symptom (Live-Repro, produktive Instanz):** Punchout zu G.U.T. ONLINE PLUS
  (echte Connector-URL `gutonlineplus.de/ids.aspx` — steht an der produktiven
  Anbindung; `seed_demo` behält seinen `.example`-Platzhalter). 5 m Kupferrohr kamen
  mit EK **35,30 €/m** an statt **7,06 €/m** — Faktor = Menge.
- **Ursache:** GC liefert `OrderItem/NetPrice` als **Positionssumme** (5 × 7,06 =
  35,30), obwohl `PriceBasis=1.0` „je Einheit" behauptet und kein `AQU`-Element
  vorliegt. Das verletzt die itek-Spec (ohne AQU beziehen sich
  OfferPrice/NetPrice/PriceBasis auf **1 Einheit** der Qty — itek OrderItem-Wiki:
  `itek-wissen.atlassian.net/wiki/spaces/GW/pages/32866531/OrderItem`) und ist in
  sich inkonsistent (OfferPrice je Einheit, NetPrice als Summe). Der spec-treue
  `_unit_price` übernahm die Summe als Einheits-EK; der VK wird aus diesem EK
  abgeleitet (`ek_override`) → VK ebenfalls ×Menge zu hoch.
- **Fix (deterministisch, nie geraten):** Preis-Semantik ist **Anbindungs-Konfig** —
  `pricing.supplier_connection.net_price_semantics` (`EINHEIT` = itek-Standard,
  Default, byte-gleich zu heute | `GESAMT` = Positionssumme, NetPrice zusätzlich
  durch die Menge teilen). Durchgereicht bis `ids_warenkorb.parse_returned_cart`
  (vor der VK-Ableitung). Zusätzlich eine **Plausibilitäts-Warnung** (kein
  Auto-Umrechnen): unter EINHEIT wird an der Warenkorb-Position gewarnt, wenn der EK
  wie eine Positionssumme wirkt (`NetPrice/PriceBasis > OfferPrice` und
  `NetPrice/Qty ≤ OfferPrice`). Frontend: Dropdown „NetPrice-Interpretation" an der
  Anbindung + Amber-Hinweis in der IDS-Bestellvorschau.
- **Offen (empirisch):** Ob GC pro Position oder pro Verpackungseinheit (Stange = 5 m)
  summiert, war mit dem einen Fall (Qty = genau 1 Stange) nicht unterscheidbar. Beim
  Live-Test einen Korb mit **10 m (2 Stangen)** bauen — das Roh-XML landet in
  `pricing.punchout_session.returned_cart_xml` und entscheidet die Frage.

**Was seit dem letzten Handoff dazukam (Commits `858ca85`, `ff711b9`, `4cd52e6`,
`e41c36d`; alles auf `main`, deployed):**
- **Globale Suche + Kommandopalette** (`6e6e4ee` bzw. im Block): ein Feld,
  Beziehungstreffer (Adresse der Liegenschaft findet das Projekt), exakte
  Belegnummer = Direkttreffer Platz 1. Trigram-Index (Migration 0098) gegen den
  800k-Artikel-Seq-Scan.
- **Der Monteur sieht sein ganzes Objekt** (0099/0100/0102): eine Heimat der Regel
  `db_core/services/objektsicht.py`. Straße, Mieter mit Telefon, Aufträge/Berichte
  der Kollegen am selben Objekt, Wartung/Prüffristen/Gewährleistung. Fremdes Objekt
  → 404 auf jedem Weg. Rechnungen nie; Angebote nur **preisfrei** (eigenes Schema,
  EK/Marge strukturell ausgeschlossen, Test scannt die serialisierte Antwort).
- **Technische Anlagen** (0101): Tabelle lag seit 0004 tot in der DB — jetzt API +
  UI, **echte Stammdaten-Spalten** (Hersteller/Modell/Baujahr/`supply_type`
  ZENTRAL|DEZENTRAL, Index für die spätere Ersatzteilsuche), Schutzstandard
  nachgezogen, `work_order.asset_id` verdrahtet. **Damit ist die frühere Backlog-
  Notiz „Anlagen-Stammdaten als echte Spalten" ERLEDIGT.**
- **Mieter & Verwaltung** (0103/0104): `tenure.occupancy_party` (gab es seit 0005,
  ungenutzt) + `management.*` verdrahtet. Mieter = normaler Kontakt mit
  Wähl-Link an der Wohnung; Verwaltung über ein **Mandat**, nicht als Rolle.
  **KEINE** `occupancy.party_id`-Spalte gebaut (wäre zweite Wahrheit).
- **Nachtrag aus Soll-Ist + Angebotsausgang**: „Nachtrag abrechnen" holt den
  Mehrverbrauch als Rechnung aus den Abweichungen; Angebot annehmen/ablehnen
  (Wiring fehlte seit 0018). **DREI Review-Runden**, drei Doppelabrechnungswege +
  Nebenläufigkeit geschlossen: Sperre hängt an der **fakturierten Menge je
  Artikel-IDENTITÄT** (`_billed_je_identitaet`), quellenunabhängig; divergente
  Einheit (Monteur „Stk", Angebot „Stück") → **fail-closed `EinheitUneindeutig`**;
  `SELECT FOR UPDATE` auf `work_order` mit Zwei-Sitzungs-Test bewiesen.
- **Monteurs-Startseite** (Einsätze heute, Adresse, Wähl-Link, Stempeluhr) statt
  Büro-Dashboard; 14 tote Knöpfe entfernt; **Kontrast-Fix zentral in den Tokens**
  (Bedienelement-Rand trägt jetzt WCAG 1.4.11, beide Themes).
- **`seed_szenario`** = der freigegebene Demo-Datenbestand (WEG Badensche 53 mit
  Stegos + 6 Mietern, EFH Peter Borm, Zentralheizung, 6 SHK-Szenarien). Erfundene
  Daten. `seed_demo` bleibt Entwicklerfutter daneben.

**Stand: 3345 Tests grün** (14 Skips Bestand), Migrationskopf **0104**,
`makemigrations --check` sauber, Frontend-Build ohne Budget-Warnung.

**→ Der nächste große Slice bleibt das KI-Fundament** (`docs/ki-first-konzept.html`,
Abschnitt 9, Zeile 4): Models für `ai.*` (die vier Tabellen aus
`0027_ki_grundlagen.sql` sind gebaut, **null** Backend-Zeilen benutzen sie),
Executor, Hash-/Version-Verifizierer, `EXPIRED`-Job, Router, Vorschlags-Kachel
(die Übersicht zeigt schon den Platzhalter „Anbindung folgt"). Danach die Auskunft
per Tool-Calling (Slice 5) — die dafür nötigen **Dossiers** (`a3af19c`) und die
**globale Suche** (KI-Auflösung „welche Entität ist gemeint?") stehen bereits.

**Kleinere offene Enden** (aus den Slice-Berichten, ehrlich):
- **Alte Artikel-Server-DB des Users** (Bär&Ollenroth-Stamm + Junkers/Vaillant-
  Ersatzteile): Klärung mit dem Server-Agenten offen — was steckt drin (Gerät→
  Ersatzteil-Zuordnung?), erst dann Import-Weg. **Löschregel: erst löschen, wenn
  der Import in MCN nachweislich steht.** Details `docs/demo-szenario.md` Abschnitt 4.
- **`management.party_authority`** (Wertgrenzen „wer darf bis 5.000 € beauftragen")
  ist Model-frei/API-frei — eigener Slice, wenn gebraucht.
- **`tenure.ownership_period`/`ownership_interest`** (Reiter „Eigentum") noch Platzhalter.
- Kundenhistorie am Auftrag schluckt für den Monteur still einen 403 (Rauschen).

---

#### Vorgeschichte: `a3af19c` — Entitäts-Dossiers (Stand 2026-07-14)

#### Was `a3af19c` enthält

**Die Dossiers** (`GET /api/dossier/{kontakt|liegenschaft|projekt|auftrag}/{id}`,
Ansicht `/dossier/:typ/:id`, verlinkt aus den vier Mappen): ein deterministischer,
rechtegefilterter Read-Service je Entität, der alles Relevante in **einem** Aufruf
zusammenträgt. Kein Modell — aber ohne ihn kann die KI nie zuverlässig antworten.

- **INVARIANTE: Der KERN der Entität ist hart getort (`require` → 403), jeder
  weitere Baustein prüft SEIN Modul mit dem weichen `check()`.** Fehlt das Recht,
  fehlt der **Baustein** (`null` + `<name>_sichtbar=false`), nicht die Antwort —
  sonst gäbe es kein Dossier für den, der 90 % davon sehen darf. Muster: das
  `ek_allowed`-Flag der Auswertungen. `row_scope EIGENE` bleibt **fail-closed**:
  Ein MONTEUR bekommt gar kein Dossier — das ist richtig, nicht aufzuweichen.
- **INVARIANTE: „Nicht sichtbar" wird ausgesprochen, nie als 0 gezeigt.** Das UI
  nennt das fehlende Recht beim Namen (`invoicing/LESEN`). Ein fehlender *Wert* ist
  „unbekannt", nie 0,00 € (Review-Fund: das Projekt-Dossier **crashte**, wenn die
  Marge unbekannt war — der Guard prüfte das Recht, nicht den Wert, und `!`
  dereferenzierte `null`. Der Build war grün; gefunden hat es erst die Review).
- **Zutrittshinweise gibt es NICHT an der Liegenschaft** (nur am Einsatz). Das
  Dossier zeigt sie **mit benannter Herkunft**, statt ein Objektfeld zu erfinden.
  Ein permanentes Feld wäre eine eigene fachliche Entscheidung — offen.
- Wiederverwendet statt nachgebaut: `soll_ist`, `offene_abrechnung`,
  `faelligkeit.liste`, `_marge_by_project` (jetzt **projektgefiltert** — vorher
  hätte ein Einzel-Dossier mit der **Firmengröße** skaliert). Read-only ist
  statisch **und** verhaltensbasiert nachgewiesen.

**Die Forderungsgrenze** liegt jetzt an **einer** Stelle
(`services/buchhaltung.py`: `forderungen` / `offene_forderungen` /
`mit_zahlungsstand` / `zahlungsspiegel`). Vorher galt überall „veröffentlicht und
nicht bezahlt" — eine **stornierte Rechnung blieb damit offener Posten UND
Mahnkandidat**. Beim Beheben fielen vier weitere Geldfehler heraus:

- **Der Mahnbrief nannte den ALTEN Betrag.** `beleg_versand` rechnete `Brutto −
  Gezahltes` und kannte Gutschriften nicht: Bildschirm 285,60 €, versendeter Brief
  **880,60 €**. Die einzige Stelle, die nach außen wirkt.
- Eine Rechnung mit **Teilgutschrift + Restzahlung** galt als TEILZAHLUNG und wurde
  **weiter gemahnt**, obwohl der Kunde alles gezahlt hatte, was er schuldete.
- **Der Schreibpfad des Mahnwesens hielt die Grenze nicht** (`POST …/dunning` auf
  eine stornierte Rechnung → **201**). Jetzt Guard im Service (422 mit Grund) **und
  im Trigger** (0097). Der Kreditbeleg-Weg war vorher nur *zufällig* dicht (die DB
  lehnte ihn als „nicht fällig" ab) — eine Grenze aus Versehen.
- Ein **vollständig erstatteter Kreditbeleg** blieb dauerhaft „OFFEN".

**Die Erstattung wird auf dem STORNO-/GUTSCHRIFTBELEG gebucht** (User-Entscheidung).

- **INVARIANTE: Die Erstattungspflicht steht auf GENAU EINEM Beleg — dem
  Kreditbeleg.** Das Original zeigt nach der Verrechnung **nie** einen negativen
  offenen Betrag. Ein Kreditbeleg wird zuerst mit der noch offenen Forderung
  **verrechnet**; nur was darüber hinausgeht (bereits gezahltes Geld) bleibt als
  Erstattung stehen. Vorher stand die Pflicht auf **zwei** Belegen, und **keine
  Buchung machte beide ruhig** — die Einladung zur Doppelerstattung, die in Welle 5
  schon dreimal geschlossen wurde.
- Der **Erhaltungssatz fällt aus der Formel**: Σ offen = Σ Brutto − Σ Gezahltes,
  cent-genau, ohne Division. Die Zuteilung bei mehreren Kreditbelegen kann Geld
  zwischen Zeilen verschieben, aber **keins erfinden und keins verlieren**.
- **SQL-Annotation und Python-Sicht werden bei JEDEM Test gegeneinander gefahren**
  (`_spiegel()`); Service und DB-Trigger sind mit 10 Fällen als deckungsgleich
  **bewiesen**, nicht behauptet.

**Review-Funde aus `ea6ebc9`** (Raumaufmaß — war tatsächlich ungeprüft):

- Die **Heizlast ließ sich über Query-Parameter auf 0 kW rechnen**
  (`?kennwert_w_m2=0`) bzw. negativ. Service, DB-CHECK und Frontend prüften — nur
  dieser Pfad nicht. Jetzt **eine** Prüfstelle + **Paritätstest** gegen den
  DB-CHECK („Service weist ab ⟺ DB weist ab"). Dass der Angular-Client die
  Parameter nie sendet, ist **kein Argument**: Die KI geht nach der Vision durch
  denselben Service.
- Die **negative Nettowandfläche war NICHT physisch unmöglich** (kein
  INSERT-Trigger auf `room_surface`) — der Schutz lag in der **Reihenfolge im
  Service**. Migration **0095**. Erst seit ihr stimmt, was 0089 und dieses Dokument
  behaupteten.
- Zwei Quelldateien trugen ein **NUL-Byte** und waren für git **binär** (nicht
  diffbar) — in einem Projekt mit Review-Pflicht ein echter Mangel. Statischer
  Wächter dagegen.
- `GET /work_orders/{id}/offene-abrechnung` hing nur an `workflow/LESEN`, **führt
  aber Einzelpreise**. Das Frontend gatete längst auf `invoicing/LESEN` — **der
  Server war laxer als sein eigenes UI**. Nachgezogen.

**Migrationen:** 0095 (INSERT-Trigger Wandfläche), 0096 (Index
`reference_invoice_id`), 0097 (Mahnsperre im Trigger).

**Lehre dieser Welle:** Sieben der neun Fehler saßen **nicht im neuen Code**,
sondern im Bestand — sichtbar wurden sie erst, als der neue Code die Grenze
*richtig* zog und dem Altbestand widersprach. **Eine konsolidierte Rechenstelle ist
ein Fehlerdetektor**, nicht nur Kosmetik.

---

#### ✅ ERLEDIGT: der „GoBD-Bug" aus der Raumaufmaß-Session

Der Befund war **richtig gefunden, aber falsch diagnostiziert** — und ist behoben
(`573a72d`). Zur Richtigstellung, damit niemand an der falschen Stelle sucht:

- **Es war KEIN Snapshot-Bruch.** Der veröffentlichte Beleg zieht seine Anschrift
  sehr wohl aus dem eingefrorenen `billing_snapshot`. Umgefallen ist die Zeile
  *davor*: die Prüfung, dass die **neue** Adresse nach dem Umzug überhaupt gilt.
- **Die echte Ursache:** `beleg.party_address` nahm `dj_timezone.localdate()` — und
  weil `settings.TIME_ZONE = "UTC"` ist, ist das **das UTC-Datum**, nicht der Tag,
  an dem der Betrieb arbeitet. Zwischen 00:00 und 02:00 MESZ liegen beide einen Tag
  auseinander. Eine in diesem Fenster erfasste Rechnungsadresse galt für die
  Belegausgabe **erst morgen** → der Beleg wäre **ohne Empfängeranschrift**
  rausgegangen. Der Docstring warnte wörtlich vor genau dieser Falle und benannte
  `localdate()` als Schutz — der wegen der Settings keiner war.
- **Fix:** `db_core/betriebszeit.py` (`BETRIEBS_TZ`/`betriebs_datum()`, zentral — die
  Zeitzone war im Repo bereits **zweimal dupliziert**). Umgestellt sind
  `beleg.party_address` und `api/qualifikation.py` (dort rechnete der Service längst
  in Betriebszeit, die API daneben in UTC — zwei Wahrheiten für dasselbe Datum).
  **`publish_invoice` bleibt bewusst bei UTC** (muss deckungsgleich mit dem DB-Trigger
  `(now() AT TIME ZONE 'UTC')::date` bleiben — nicht „mitziehen"!).
- **Regressionstest:** `db_core/tests/test_betriebszeit.py` friert die Uhr auf
  **00:30 Berlin** ein. Gegengeprüft: mit `localdate()` fällt er um. Ein Test, der
  nicht rot wird, wenn der Fehler drin ist, wäre wertlos gewesen.

**(3) Der Monteur kann keine Räume erfassen** — bewusst so. Das Rechtemodul
`property` kennt **kein `EIGENE`**: Wer Räume anlegen darf, dürfte jede
Liegenschaft ändern. Deshalb hat die MONTEUR-Rolle **keine** `property`-Schreib-
rechte bekommen (fail-closed). Wenn Begehungen vom Monteur gemacht werden sollen,
braucht `property` einen echten **Zeilen-Scope** — eigener Slice, User-Entscheidung
steht aus.

**(4) Leitungslängen sind eine SCHÄTZUNG**, klar deklariert: `2 × Σ riser_distance_m`
(Vor-/Rücklauf), ohne Formstücke und Steigstrang. **Bewusst kein erfundener
Zuschlagsfaktor.** Für eine echte Rohrnetzberechnung braucht es eine
Leitungsführung — der **Grundriss (0091) ist jetzt die Grundlage dafür**. Das ist
der natürliche nächste Ausbauschritt (und der Weg zur 3D-Planung).

---

### Welle 5 (2026-07-13/14): Berichtspositionen, Soll-Ist und Abrechnung

Zwei Slices, die den Baustellenbericht vom Freitextzettel zur **beweisbaren
Abrechnungsgrundlage** machen. **Kein KI-Anteil — reine Rechenarbeit** (und genau
das ist der Punkt: was deterministisch geht, gehört deterministisch gebaut).

**A) Berichtspositionen + Soll-Ist** (`3fcc37d`, Migrationen 0080–0083)

`workflow.site_report_line` — der Bericht führt Positionen aus dem Artikel-/
Leistungsstamm. Er startet **vorbelegt mit den Angebotspositionen als Sollmenge**;
der Monteur korrigiert nur die Abweichung. Daraus fällt der Soll-Ist-Abgleich
(MEHRVERBRAUCH / MINDERVERBRAUCH / ZUSATZ / ENTFALLEN / UNVERAENDERT), Reiter
„Soll-Ist" in der Auftragsmappe. Vorbelegungs-Rangfolge, falls kein Angebot da ist:
Wartungsvertrag → letzter Bericht an derselben Anlage → Vorgang → leer.

- **INVARIANTE: Der Bericht führt KEINE PREISE.** Er wird vom Kunden unterschrieben
  und danach versiegelt — ein unterschriebener Bericht *mit* Preisen wäre eine
  **Preisvereinbarung**, die der Monteur auf der Baustelle abschließt; *mit* Mengen
  ist er ein Leistungsnachweis. Ein Schema-Test durchsucht `information_schema` nach
  Geldspalten und hält die Invariante auch gegen künftige Migrationen.
- **INVARIANTE: Ein unterzeichneter Bericht ist versiegelt — auch seine Positionen**
  (`protect_site_report_lines`, INSERT/UPDATE/DELETE, **OLD und NEW**; das
  *Wegbewegen* einer Position ist damit abgedeckt). `FOR SHARE` serialisiert gegen
  `sign_report`; Zwei-Sitzungs-Test in `db/tests/nebenlaeufigkeitstest_berichtspositionen.sh`.
- **INVARIANTE: Trägt eine Position eine Herkunft, wird ihre Identität aus der
  Angebotszeile ABGELEITET, nie vom Client geglaubt** (Trigger 0083, **fünf**
  Gleichungen: Bezeichnung, Einheit, Sollmenge, Artikel, Leistung). **Drei
  Review-Runden** waren nötig: Die Prüfung im *Service* ließ sich umgehen, indem der
  Client die Felder schlicht **weglässt** — dann ließ sich ein fremdes Soll
  unterschieben („angeboten: 500" neben einer Position mit 5 Stück, auf einem
  versiegelten Kundendokument). **Deshalb liegt die Regel im TRIGGER.** Die
  Präzisierung des Monteurs gehört in die **Notiz**, nicht in die Bezeichnung.
- **Der Soll-Ist schlüsselt über die QUELLZEILE**, nicht über den bearbeitbaren Text.
  Sonst zerfiel eine präzisierte Position („Rohr" → „Rohr DN20") in ENTFALLEN +
  ZUSATZ, und das Büro fakturierte **14 Einheiten als Zusatzleistung statt 2 Einheiten
  Mehrverbrauch**.
- **`invoicing.quote.work_order_id` ist jetzt echt verdrahtet.** Die Spalte lag seit
  0018 in der DB und wurde von **keinem Produktpfad** gesetzt — nur die Tests setzten
  sie per Raw-ORM. Das Feature wäre in der Demo gelaufen und im Betrieb bei jedem
  Auftrag ohne Projekt an einer leeren Angebotsliste gescheitert. **Der Projekt-Fallback
  ist RAUS** (bei mehreren Aufträgen am selben Projekt war dasselbe Angebot Soll für
  jeden Auftrag). Migration **0082 nimmt `work_order_id` von `freeze_sent_quote` aus**:
  ein interner Verweis ist kein Beleginhalt — sonst wäre die Zuordnung genau dann
  gesperrt, wenn man sie braucht (Angebot versenden → Kunde nimmt an → **dann** Auftrag).
  **B-30 bleibt im Kern unangetastet** (Test beweist es).

**B) Abrechnung** (`573a72d` Backend, `b5e5c3b` UI, Migrationen 0084/0085/0088)

Rechnung **aus Angebot** (Positionen 1:1 kopiert) und **aus Auftrag** (Regie:
Berichtspositionen + Zeitbuchungen). Dazu **„Was ist noch nicht abgerechnet?"** als
Abfrage. `work_order.billing_mode` = `PAUSCHAL` (Default) | `REGIE`.

- **INVARIANTE: Die Doppelabrechnungssperre liegt in der DATENBANK.**
  `invoicing.billing_link` bindet Rechnungsposition an ihre Quelle; **drei partielle
  UNIQUE-Indizes `WHERE released_at IS NULL`**. Ein UNIQUE auf der Rechnungsposition
  selbst ginge **nicht**: nach einem Storno müssen dieselben Stunden wieder abrechenbar
  sein, aber die Position ist dann unveränderlich und nicht entwertbar. **Der Storno
  LÖST die Bindung** (Trigger) — die Leistung wird frei. Gleiches Muster wie bei den
  Fälligkeiten: *die Idempotenz garantiert die DB, nicht der Service.*
- **Was die DB NICHT sehen kann, fängt der Service:** Angebots- und Berichtspositionen
  sind **disjunkte Quellen** — dieselbe Leistung ließe sich über beide Wege je einmal
  fakturieren, ohne dass ein Index anschlägt (reproduziert: 178,50 € auf zwei
  veröffentlichten Rechnungen). Die Sperre hängt deshalb am **Auftrag** (der einzigen
  Klammer) und sitzt in **beiden** Rechnungswegen — **nicht** am Moduswechsel: der war
  an `work_order.status='ABGERECHNET'` gehängt, einen Status, den im ganzen Repo
  **niemand je setzt**. Eine Sperre, die aussah wie eine Sperre und keine war.
- **INVARIANTE: Fehlender Preis ist keine Sackgasse, aber NIEMALS 0 €.** Ein Preis gilt
  erst **ab > 0** als Preis (`_ist_preis`) — an allen drei Stellen, an denen die
  DB-CHECKs `>= 0` erlauben: Festpreis 0, VK-Gruppe auf einem **0-EK aus einem
  DATANORM-Importfehler**, Lohnsatz 0. Sonst landete die Position mit **0,00 €** auf
  einer plausibel aussehenden, um den vollen Betrag zu niedrigen Rechnung — und die
  Vorschau nickte es ab. Stattdessen: **strukturierte Klärungsliste** (422 mit
  `preis_unbekannt`), Dialog fragt die Preise ab, Vorschläge sind **nie
  vorausgefüllt**, **kein „später"-Knopf**. Ein genannter Preis wird **nur akzeptiert,
  wo der Server keinen hat** — sonst wäre die Aufschlagsmatrix (Mindestmarge!)
  umgehbar. **Kein Schreibpfad in `pricing.article`** (statisch getestet).
- **Storno/Gutschrift: der Zustandsraum ist vollständig entschieden** (Tabelle im
  Docstring von `create_correction`). **Drei Doppelerstattungen geschlossen, zwei davon
  VORBESTEHEND:** Storno nach Gutschrift; Gutschrift nach Storno (die neue
  Vollgutschrift-Sperre griff dort ausgerechnet nicht, weil der Storno die Bindungen
  bereits gelöst hatte); mehrfache Vollgutschrift auf eine **ungebundene** Rechnung
  (200 % Erstattung). **Vollgutschrift auf eine gebundene Rechnung ist verboten** (sie
  ist ein verkappter Storno); **Teilgutschrift bleibt erlaubt** und lässt die Leistung
  abgerechnet — eine Kulanz heißt nicht, dass nicht gearbeitet wurde. Präzedenzfall ist
  die bestehende Grenze bei den Abschlägen.
- **Zeile anhängen** (`POST /invoices/{id}/lines`, `DELETE .../lines/last`): 0088
  erlaubt das INSERT einer ungebundenen Zeile am gebundenen Beleg — aber es **führte
  kein Pfad dorthin** (`update_invoice` ersetzt den ganzen Satz per Delete+Insert).
  **Die neue Zeile wird VOR die Anrechnungspositionen eingefügt** (UPDATE **absteigend**,
  sonst kollidiert das Umnummerieren mit sich selbst); *die Anrechnung schließt den
  Beleg ab*. `remove_last_invoice_line` weist eine **Anrechnungsposition** ab — sie
  steht per Konstruktion hinten, die „letzte Zeile" einer SR **ist** regelmäßig die
  Anrechnung; sie zu löschen forderte den **bereits gezahlten Abschlag ein zweites Mal**
  (Brutto sprang 4.760 → 5.950 €).
- **§ 35a:** Der Anhänge-Dialog hätte den Arbeitskosten-Ausweis **zerstört** — eine
  einzige Zeile ohne Lohnkennzeichnung macht ihn für die **ganze** Rechnung
  unbestimmbar, und ausgerechnet die im Dialog beworbene „Anfahrtspauschale" fällt
  darunter (Privatkunde verliert **20 % Steuerbonus**). Der Dialog führt die Angabe
  jetzt in derselben Konvention wie der Beleg-Editor: **Pflicht genau dort, wo Schaden
  entsteht**, sonst freiwillig. Wo der Anteil ehrlich nicht ermittelbar ist, ist der
  Ausweg das **bewusste Abschalten** des Ausweises — nicht das Raten.
  *Der Fehler steckte nicht in einer Zeile Code, sondern in dem, was das Formular nicht
  gefragt hat.*

**Offen / ehrlich:**
- Die **Nebenläufigkeit beim Anhängen** ist konstruktiv gesichert (`select_for_update`
  in allen drei Schreibern + Constraint-Mapping → 422 statt 500), aber **nicht durch
  einen Zwei-Sitzungs-Test nachgewiesen**. Muster dafür existiert
  (`db/tests/nebenlaeufigkeitstest_berichtspositionen.sh`).
- **Schlussrechnung aus dem Abrechnungslauf** gibt es nicht (`rechnung_aus_*` erzeugt
  immer `RECHNUNG`); der bestehende Abschlagspfad (0060/0061) bleibt zuständig.
- Der **Anrechnungs-Umnummerierungspfad ist aus dem UI nicht erreichbar** (gebundene
  Belege sind nie SCHLUSSRECHNUNG) — reine API-Absicherung.

### Lehre aus dieser Welle (für die nächste Session wichtiger als der Code)

**Elf Fehler fand erst die Review-Schleife — ausnahmslos solche, die in Geld geendet
hätten.** Das Muster war jedes Mal dasselbe: **Nicht der Code war naiv, sondern der
Testumfang.** „Grün" hieß zuverlässig „der Normalfall stimmt"; die Fehler wohnten in
den Sonderfällen, die Geld bewegen — Storno, Gutschrift, Abschlag, Nachtstunde,
Importfehler mit Preis 0, mehrere Steuersätze.

Zwei Konsequenzen fürs Vorgehen:
1. **Die Bruchfälle dem Implementierer NAMENTLICH vorgeben**, statt „schreib Tests" zu
   sagen. Das ist der einzige Weg, der funktioniert hat.
2. **Was im Service sitzt, ist umgehbar; erst was im Trigger sitzt, hält.** Drei
   Reparaturen mussten deshalb ein zweites Mal gemacht werden.

**(5) Dev-DB-Rest:** In `mitra_crm_test` stehen Testräume aus den E2E-Läufen
(`ZZ-TEST-RUN2`, `ZZ-UI-E1`, `E2E-B-Raum`, `E2E-C-Grundriss`, `Wohnzimmer`,
Liegenschaft/Raum `ZZ-REVIEW-WEGWERF`). Dazu wurden an **OBJ-00001** die
Auslegungsdaten (−12 °C / 100 W/m²) und zwei Katalog-U-Werte gesetzt. Reine
Scratch-Daten — sie ändern die Heizlast aller Räume dieser Liegenschaft. Räume
tragen No-Delete: nur auf INAKTIV setzen.

---

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

### Stand 2026-07-12 & was als Nächstes ansteht (AKTUELL)

**Frühere Sessionswelle (alle reviewed + Browser-E2E + committet):**
Lohngruppen (`e9bfe06`), Mahnlauf (`e6ac0c4`), HR-Selbstauskunft (`6fd4f80`),
Onboarding-Checkliste (`468ddd4`), Akquisekanäle/Quellen (`a346fa8`). Dazu die
fertige Parallel-Agent-Arbeit **Schnellerfassung + Zum-Projekt-Hochstufen**
konsolidiert eingecheckt (`f681efd`). **1607 Tests grün, Migrationen bis 0050.**

**Seither (alles committet, `170f3c7` konsolidiert die frühere Arbeitsbaum-Arbeit):** DATEV-Export
(s. u.), IDS-Connect (Anbindungsverwaltung + Warenkorb-Kern + Credentials/Punchout;
Slice „Shop-Roundtrip/HOOK-Return + Frontend-Button" wartet auf G.U.T.-Connector-URL
des Users), sowie die Test-Feedback-Punkte: Angebots-/Rechnungs-Editor-Einstieg
wiederhergestellt + **Rechnungs-Editor** (Artikel-Palette), **Plantafel Vollbreite**
+ Klick→Termin-für-Auftrag, Auftrags-Reiter **Termine** (Kundenhistorie), und
**Baustellenberichte** (`workflow.site_report`, Migration 0054/0055): Bericht +
Fotos (`content.file_link.site_report_id`) + Kunden­unterschrift (Canvas-Pad → PNG
im Objektspeicher, besiegelt ENTWURF→UNTERZEICHNET, danach per Trigger
unveränderlich — **inklusive der Anhänge**, siehe 0065). Reiter „Berichte" im
Auftrags-Detail **und im Einsatz-Detail** (auch am freien Termin, siehe 0064).

Danach der **IDS-Connect Warenkorb-Roundtrip** (`pricing.punchout_session`,
Migration 0056/0057): „Bei Händler bestellen" aus dem Angebots-Editor →
Punchout-Formular (itek 2.5, WKE/WKS) öffnet den Großhändler-Shop → der Shop
POSTet den fertigen Warenkorb an einen **token-gesicherten HOOK**
(`POST /api/pricing/warenkorb-return/{token}`, auth=None, Token nur als SHA-256-Hash,
Einmal-Einlösung via select_for_update) → Positionen werden gegen den Artikelstamm
aufgelöst (inkl. EK aus `NetPrice/PriceBasis`) und im Editor übernommen. Zugangsdaten
(Benutzer/Passwort, Fernet) pflegt man je Anbindung über „Zugangsdaten"; die
Shop-/Connector-URL ist das `shop_url`-Feld (die G.U.T.-URL trägt der User dort ein —
sie ist Konfig, kein Code). Die itek-2.5-XSDs liegen unter `d:\Mitra\MCN\IDS\`.

Und der **DATANORM-Frontend-Import**: der DATANORM-4-Parser + CLI-Command
existierten schon; neu ist der **Upsert-fähige Import-Service**
(`services/datanorm_import.py`) mit Datei-Upload — Artikel/Preise anlegen ODER
aktualisieren, Löschung (VKZ L) → Artikel INAKTIV, EK auch direkt aus dem A-Satz
(Preiskennzeichen), Zip-Bomben-Schutz (entpackte Byte-Grenze), Vorschau (dry-run).
Endpoint `POST /supplier-connections/{id}/imports/datanorm` (Stamm + optional
Preisdatei, `pricing/ANLEGEN`, 80 MB, stempelt `last_import_at`, nur Großhändler).
Frontend: „DATANORM-Import"-Dialog je Großhändler-Anbindung (Datei wählen →
Vorschau/Importieren, Fortschritt, Ergebnis-Tabelle). Vollkataloge (mehrere GB)
bleiben beim CLI-Kommando. Kein offener Feedback-Punkt mehr aus dem Testlauf.

**⚠️ Gotcha Parallel-Agent:** Ein zweiter Agent baut manchmal mit, committet seine
Arbeit aber NICHT selbst — sie liegt fertig+getestet im Arbeitsbaum. Vor eigenen
Migrationen/`models.py`-Änderungen prüfen: liegt Fremdarbeit uncommittet herum?
Dann erst einchecken (nach `makemigrations --check` sauber + Suite grün), sonst
kollidiert der Migrations-Graph (zwei Blätter auf demselben Parent). Beim Commit
sonst **nur eigene Slice-Dateien** stagen (selektives Hunk-Staging bei geteilten
Dateien wie `app.routes.ts`).
**Stand jetzt:** Der Parallelbau ist abgeschlossen und als `e0403af` eingecheckt
(0069/0071/0072 + State-only 0073/0074/0075 — **kein 0070**, der Aufmaß-Slice brauchte
keine Migration). Der Arbeitsbaum ist sauber, **0075 ist das einzige Leaf.** Die Lehre
bleibt: Wer dazwischen etwas anfasst, prüft vorher `git status` und `showmigrations` —
halbfertige Migrationsdateien im Arbeitsbaum können sogar `manage.py` zum Absturz
bringen.

### Die vier priorisierten Slices sind GEBAUT (2026-07-12)

Die Grundsatzfragen waren entschieden (Memory `grundsatz-entscheidungen-2026-07`),
die vier daraus abgeleiteten Slices sind umgesetzt, reviewed und committet:

1. ✔ **Skonto: Zahlungsbedingungen je Rechnung** (`ae4d241`, Migration 0058).
   `invoicing.invoice` trägt `payment_term_days`, `discount_percent`,
   `discount_days` — je Rechnung, kein Kundenstandard. `publish_invoice` schreibt
   das **Belegdatum immer** fest (deckungsgleich mit dem DB-Trigger) und leitet die
   Fälligkeit aus dem Zahlungsziel ab. Die **Skontofrist darf nicht hinter der
   Fälligkeit liegen** — geprüft beim Anlegen, beim Ändern (gegen den gemergten
   Ergebniszustand) und als letzte Instanz beim Veröffentlichen; sonst landete der
   Widerspruch auf einem bereits unveränderlichen Kundenbeleg. Genau **eine
   Rechenstelle**: `beleg.zahlungsbedingungen()` (Skontodatum/-betrag/-zahlbetrag) —
   PDF, XML, API und Frontend ziehen von dort. **Skonto bucht NICHTS aus**: offener
   Betrag und Zahlungsstatus bleiben unverändert abgeleitet.
2. ✔ **E-Rechnung ZUGFeRD/Factur-X** (`12e4f7f`, Migration 0059), Profil EN16931.
   Zuerst die **Snapshot-Härtung** (`snapshot_version=2`): `publish_invoice` friert
   jetzt auch Aussteller (`header["issuer"]`), Leistungsort (`header["delivery"]`)
   und je Beteiligtem Name/Anschrift/USt-IdNr (`parties[i]["snapshot"]`) ein — ohne
   das hätte eine spätere Firmenprofil-Änderung den Inhalt eines festgeschriebenen
   Belegs verändert. **Bestehende Belege werden NICHT rehasht**, fehlende Felder
   fallen **feldweise** auf Live-Daten zurück. Neuer Service `services/erechnung.py`:
   CII-XML (factur-x, XSD-geprüft) + Hybrid-PDF/A-3B (fpdf2 `enforce_compliance`).
   `beleg_pdf.py` nutzt jetzt **eingebettetes DejaVu Sans** (PDF/A verbietet
   Kernfonts; Nebeneffekt: Umlaute/€/m² werden nicht mehr ersetzt). Skonto steht
   maschinenlesbar in BT-20 (`#SKONTO#TAGE=..#PROZENT=..#BASISBETRAG=..#`).
   Endpunkte `GET /invoicing/invoices/{id}/zugferd.pdf` und `.../zugferd.xml`,
   Archivierung in MinIO (`link_category='E_RECHNUNG'`).
   **Inzwischen extern gegengeprüft — siehe „E-Rechnung ist belegt" weiter unten.**
   XRechnung (B2G) bleibt offen und optional.
3. ✔ **Abschlags-, Teil- und Schlussrechnung** (`5c66561`, Migration 0060/0061) —
   der Kernprozess des Users. `invoicing.invoice_advance` friert Netto/Steuer/Brutto
   je (Schlussrechnung, Abschlag, Steuercode) ein, `invoice_line.advance_invoice_id`
   trägt den Rückverweis. **Anrechnung als NEGATIVE POSITIONEN je Steuersatz**, nicht
   als Kopffeld: so trägt die DB-Summenprüfung sie unverändert, `gross_total` der
   Schlussrechnung IST der Zahlbetrag, und offener Posten, Mahnwesen, DATEV und
   Auswertungen bleiben ohne Umbau korrekt (Aufteilung je Steuersatz wegen § 14
   Abs. 5 UStG). Auftrags-Tor **B-08 ist belegartabhängig**: AR/TR ab FREIGEGEBEN,
   RECHNUNG/SR erst ab KAUFMAENNISCH_GEPRUEFT. **Doppelanrechnung ist physisch
   ausgeschlossen**; eine SR, die einen anrechenbaren Abschlag **übergeht**, ist
   nicht veröffentlichbar (Service UND DB) — der teuerste denkbare Bedienfehler der
   Domäne. Storno/Gutschrift eines angerechneten Abschlags ist gesperrt, solange die
   SR veröffentlicht ist; eine SR mit Anrechnung ist nur **voll**stornierbar. Die DB
   verbietet jetzt jeden Kreditbeleg mit positivem Betrag. **BT-113
   (TotalPrepaidAmount) wird bewusst NICHT genutzt** — es meint den GEZAHLTEN Betrag
   und mindert die Steuerbasis nicht, der Empfänger zöge die Vorsteuer doppelt.
   0-€-Abschläge sind weder anrechenbar noch blockierend.
4. ✔ **Freier Termin ohne Auftrag** (`eb215f3`, Migration 0062).
   `workflow.service_job.work_order_id` ist nullable; neu `title` (Pflicht, sobald
   kein Auftrag dranhängt) und `property_id`, abgesichert über den zusammengesetzten
   FK gegen den Auftrag (ein auftragsgebundener Einsatz kann keine fremde
   Liegenschaft tragen). Die zwei auftragsabhängigen Tore prüfen bei NULL nichts,
   bleiben für auftragsgebundene Einsätze unverändert scharf. **Neu dafür: der
   Auftragsbezug ist unveränderlich** — sonst ließe sich ein laufender Einsatz
   nachträglich an einen abgerechneten Auftrag hängen und beide Tore umgehen. Das
   B-28-Korrekturfenster joint jetzt per LEFT JOIN (notwendige Anpassung an den
   NULL-Fall, **kein Bugfix** — mit NOT NULL konnte der Fall nie auftreten). Die
   EIGENE-Sicht hängt bei Einsätzen allein an der Zuweisung, nie am Auftrag: ein
   freier Termin wird also nicht öffentlich. `PATCH /einsaetze/{id}` lässt den
   Monteur am eigenen **freien** Termin nur Kontakt und Zugangshinweise nachtragen.
   `seed_demo` legt eine Begehung an. Der Baustellenbericht am freien Termin ist
   inzwischen nachgezogen (siehe unten).

Dazu ✔ **DATEV-Export** (EXTF-Buchungsstapel, Migration 0051, `services/datev.py`,
UI in der Buchhaltung; Memory `datev-export`).

### Drei weitere Slices (2026-07-12, im Anschluss)

5. ✔ **E-Rechnung ist belegt, nicht mehr nur behauptet** (Migration nicht nötig).
   Werkzeuge: Temurin JDK 21, **veraPDF 1.30.2** (PDF/A) und **Mustang CLI 2.24.0**
   (ZUGFeRD/Factur-X: XSD **plus** EN16931-Schematron). Ergebnis: **6/6 PASS bei
   veraPDF (Flavour 3b)** und **6/6 PASS bei Mustang** über sechs Belegformen —
   Rechnung mit Skonto, ohne Skonto, zwei Steuersätze, Schlussrechnung mit negativer
   Anrechnung, Kreditbeleg mit negativen Summen, Rechnung mit Firmenlogo (PNG mit
   Alphakanal/SMask). **Negativkontrollen gefahren:** ein Nicht-PDF/A wird korrekt
   abgelehnt, ein manipulierter Bruttobetrag als **BR-CO-15** gemeldet — die
   Prüfkette greift wirklich, sie nickt nicht bloß ab.
   Dabei ein **echter Fehler gefunden und behoben: BR-DE-18** — die maschinenlesbare
   Skonto-Zeile `#SKONTO#…#` braucht einen **abschließenden Zeilenumbruch**, sonst
   verwirft der Validator sie. Die Skonto-Angabe hätte im PDF gestanden und wäre
   maschinell trotzdem wertlos gewesen.
   Neu: `backend/db_core/tests/test_erechnung_konformitaet.py` fährt beide
   Validatoren gegen alle sechs Formen und **skippt sauber**, wenn `MCN_VERAPDF`/
   `MCN_MUSTANG_JAR` oder Java fehlen (daher 12 der 13 Skips). Anleitung:
   `docs/erechnung-validierung.md`. Mit `MCN_ERECHNUNG_DUMP=<dir>` fallen die Belege
   zur manuellen Prüfung ab.
   **Aussage jetzt:** „PDF/A-3B konform" und „EN16931 konform" sind **belegt**.
   **Weiterhin bewusst NICHT konform** (B2G/XRechnung-Terrain, war nie Teil des
   Slices): **BT-10** (Buyer reference), **BT-41** (Seller contact point), **BT-24**
   (XRechnung-Kennung), **BR-DE-TMP-32** (Lieferdatum BT-72) und die PEPPOL-Regeln.
   **BT-72 bleibt bewusst leer** — die Rechnung führt kein Lieferdatum, ein
   erfundenes wäre eine falsche Tatsachenbehauptung. Nebenbefund: **BG-6** (Seller
   contact) entsteht nur, wenn im Firmenprofil Telefon/E-Mail gepflegt sind.
6. ✔ **DATEV: Abschlagsrechnungen konfigurierbar auf Anzahlungskonto**
   (Migration 0063). Schalter `datev_advance_mode` (`ERLOES`|`ANZAHLUNG`) am
   Firmenprofil, **Default ERLOES** — Bestandsverhalten unverändert, der
   ERLOES-Export ist nachweislich **byte-identisch** zum bisherigen.
   Im Modus **ANZAHLUNG**: Abschlags-/Teilrechnung bucht Debitor an
   **Anzahlungskonto**; die **Schlussrechnung löst auf** (Leistungsteil auf Erlös,
   Anrechnungsteil gegen das Anzahlungskonto). **Der Leistungsteil wird als REST
   ermittelt** (Gruppensumme − eingefrorene Anrechnung aus
   `invoicing.invoice_advance`), **nie neu gerechnet** — sonst ginge die Kette bei
   ungünstiger Rundung um einen Cent nicht auf. Das Anzahlungskonto saldiert nach
   Abschlag + Schlussrechnung auf **null**; die Erlöse sind in Summe identisch zu
   Modus ERLOES.
   **Standardkonten:** 19 % → SKR03 **1718** / SKR04 **3272** (NICHT 3270 — das ist
   der 16-%-Corona-Satz); 7 % → SKR03 **1711** / SKR04 **3260**; steuerfrei und
   § 13b teilen sich das neutrale **1710/3250** — **begründete Annahme, kein
   DATEV-Standard, mit dem Steuerberater klären.** Alle vier per Override am
   Firmenprofil änderbar.
   **Ein Moduswechsel wird vom Server abgelehnt (422)**, solange veröffentlichte,
   nicht schlussgerechnete Abschläge offen sind — sonst bliebe ein Saldo auf dem
   Anzahlungskonto stehen. Der saubere Schnitt ist **erzwungen**, nicht nur
   empfohlen.
   Nebenbei: Die DATEV-Konfiguration ist jetzt **überhaupt erst im UI pflegbar**
   (Firmenprofil: Berater-/Mandantennummer, SKR, Kontenlänge, WJ-Monat,
   Konten-Overrides, Abschlagsmodus).
7. ✔ **Baustellenbericht am freien Termin** (Migrationen 0064/0065).
   `site_report.work_order_id` ist **nullable**; **Anker-CHECK** (Auftrag ODER
   Einsatz), **Konsistenz per Trigger** — nicht per zusammengesetztem FK: MATCH
   SIMPLE prüft gar nicht, sobald eine Spalte NULL ist, MATCH FULL verböte den
   freien Termin. **Kein `property_id` am Bericht** (die Liegenschaft steht am
   Anker). Berichte sind jetzt auch aus dem **Einsatz-Detail** erreichbar (Komponente
   nach `shared/berichte` verschoben — ein Baustein für beide Einstiege).
   **Dabei zwei Sicherheitslücken im Bestandscode geschlossen:**
   - Die **Datei-API nutzte `require_create`** für den Upload — damit konnte ein
     Monteur (`row_scope EIGENE`) ein Foto an einen **fremden** Bericht oder Auftrag
     hängen (verifiziert: 201). Jetzt **`require_scoped` + Ziel-Guard für JEDE
     Zielart**: bei EIGENE ist `service_job_id` nur der eigene Einsatz und
     `site_report_id` nur ein Bericht daran (sonst 404); alle anderen Ziele → 403,
     fail-closed. Lesen und Download hängen an derselben Grenze — vorher sah der
     Monteur seine **eigenen** Fotos gar nicht (403).
   - Der **unterzeichnete Bericht war nicht wirklich versiegelt**: Fotos ließen sich
     danach noch anhängen oder lösen. Neuer Trigger
     `content.protect_signed_site_report_links` auf `content.file_link`
     (INSERT/UPDATE/DELETE, OLD **und** NEW). Die Race gegen `sign_report` ist
     serialisiert (nachgewiesen).

### Welle 2 (2026-07-12): Datenverlust-Bug, Zeiterfassung, Plantafel, Werkzeuge

8. ✔ **Plantafel Drag & Drop + Design-Behebung + ein Datenverlust-Bug** (`f1ed9d9`,
   keine Migration).
   **Der Bug war KRITISCH und vorbestehend:** Die deutsche Dezimal-Formatierung
   setzte einen Tausenderpunkt (1200 → „1.200"), die Rückwandlung entfernte Punkte
   aber nur, wenn ein Komma vorhanden war. Wer eine **Menge von 1200 auf „1.500"
   änderte, speicherte 1,5** — ohne Warnung, ohne Fehler. Jetzt sind die beiden
   Richtungen getrennt: `apiZuDeEingabe` (**ohne** Gruppierung, für Eingabefelder)
   und `apiZuDeAnzeige` (mit Tausenderpunkt, **nur** Anzeige); eine mehrdeutige
   Eingabe wie „1.500" wird **abgelehnt** statt geraten. 22 Unit-Tests.
   **Design:** Der Hinweistext im Formularfeld stand ZWISCHEN Label und Input und
   schob das Feld nach unten — ~25 Formulare fluchteten deshalb nicht (Rechnungskopf:
   5 Felder in 4 Höhen, 83 px Spreizung). Der Hinweis steht jetzt UNTER dem Feld.
   Beleg-Editor: Breitendeckel angehoben, Positionszeile ist eine echte
   Spaltentabelle mit Inline-Edit, Palette gefixt, Kalkulationsleiste kollabierbar.
   Neuer Kontrast-Token `--ink-hint`, Container-Tokens, Legenden vereinheitlicht.
   **Plantafel:** Drag & Drop per Maus, Tastatur und Touch, neuer Endpunkt zum
   Aufheben einer Zuweisung, Belegungswarnung für Mitarbeiter UND Ressourcen.
9. ✔ **Zeiterfassung mit Stempeluhr** (`9893120`, Migrationen 0066–0068).
   Gesetzliche Pflicht (BAG 2022; **§ 17 MiLoG** für Bau/Gebäudedienst: Beginn/Ende/
   Dauer, binnen 7 Tagen aufzuzeichnen, 2 Jahre aufzubewahren, dem Zoll vorzulegen).
   - **Architektur: EIN Zeitstrahl, zwei Auswertungen.** `workflow.time_entry` bleibt
     die einzige Wahrheit. Neu `hr.time_category` — **`is_work_time` ist das einzige
     harte Attribut** (PAUSE ist nicht umschaltbar). **`time_type` ist GEDROPPT** (eine
     Read-only-Property spiegelt es für Altcode). Der Einsatzbezug ist für JEDE
     Kategorie optional — Werkstatt/Büro/Schulung sind erstklassige Zeiten.
     Überlappungssperre per EXCLUDE (nur über abgeschlossene Buchungen; die laufende
     Buchung ist über einen partiellen UNIQUE-Index eindeutig).
   - **Stempeluhr** (Start/Pause/Weiter/Stopp) unter `features/meine-zeiten`; die
     Buchung hängt am Termin und erscheint im Baustellenbericht
     (`shared/einsatz-zeiten`).
   - `workflow.work_day` (ENTWURF→EINGEREICHT→BESTAETIGT|ABGELEHNT, **Vier-Augen als
     DB-Trigger**); die Änderung an einem bestätigten Tag verlangt eine Begründung und
     wirft den Tag auf ENTWURF zurück. **Das kaufmännische Tor B-28 bleibt daneben
     scharf — zwei unabhängige Schlösser, nicht eins.**
   - `hr.break_rule` (KEINE/GESETZLICH/FESTE_ZEITEN), `hr.holiday` (2026/27),
     Soll/Ist/**Saldo abgeleitet, nie gespeichert**, Stundenliste als CSV.
   - **Nachtschicht zählt zum Anfangstag** (22:00–06:00 = EIN Arbeitstag);
     `workflow.local_day()` und `BETRIEBS_TZ` sind deckungsgleich (über DST geprüft).
   - Nebenbefund behoben: Die Mitarbeiter-Auswertung zählte nur `ARBEITSZEIT` —
     Fahrt-, Bereitschafts- und Nacharbeitszeit fielen unter den Tisch. Jetzt
     `category__is_work_time`.
   - Rechtematrix: MONTEUR bekommt `hr/LESEN` + `hr/AENDERN` mit row_scope EIGENE.
10. ✔ **Plantafel auf Dispo-Niveau** (`9893120`, keine Migration): Rückstandsleiste
    (ungeplante Einsätze, Drag UND Tastatur, Rückweg ins Backlog mit Begründung),
    **Mehrtages-Balken** statt Punkt am Starttag, Abwesenheiten und Feiertage als
    Sperrflächen, Konflikte an der Kachel (Text UND Symbol), Ansichten Tag/Woche/2W/4W,
    Termin anlegen/bearbeiten mit n Mitarbeitern und n Ressourcen in EINER Transaktion,
    Auslastung je Bahn gegen die Vertrags-Sollstunden (ohne Vertrag `null` =
    „unbekannt", **nie 0**). `actual_start`/`actual_end` werden im Statusautomaten
    gestempelt.
    **Hero kann laut eigener Doku beides NICHT**: keine Verfügbarkeitsprüfung beim
    Anlegen, kein Ort für ungeplante Arbeit. Das sind unsere zwei echten Vorsprünge.
    **Zwei Review-Funde, die es nicht ins Produkt geschafft haben:**
    - Das Board gab die **Abwesenheitsart** (Krankheit!) an jeden mit `workflow`-Recht
      preis — DSGVO Art. 9. Das Repo hatte diese Grenze für dieselben Daten in
      `api/mitarbeiter.py` längst gezogen. Jetzt zeigt das Board nur „abwesend, von–bis".
    - Der Termin-Dialog löschte beim Ändern der Uhrzeit stillschweigend Vor-Ort-Kontakt
      und Zutrittshinweise (er schickte Felder mit, die er nie geladen hatte).
11. ✔ **Werkzeuge** (`9893120`, `features/werkzeuge`, Nav 92): Heizlastrechner
    (überschlägig) fachlich 1:1 aus `D:\Mitra\NotizApp_Win` portiert (nachgerechnet,
    gleiche Zahlen), Heizkörper-Umrechnung (WP-Umstellung), Volumenstrom,
    Einheiten-Umrechner. Einstieg auch aus Liegenschaft und Beleg-Editor.
    Ein Ergebnis geht **nur als Textposition** in einen Beleg — **kein in JavaScript
    gerechneter Wert wird je Menge oder Preis.** Unübersehbarer Hinweis im UI: kein
    Nachweis nach DIN EN 12831, nicht förderfähig.
    **Normrecht (wichtig für künftige Werkzeuge):** Die bloße Anwendung einer
    Rechenvorschrift ist frei — **Norm-Tabellenwerte abzudrucken oder mitzuliefern ist
    es nicht** (das sagt DIN ausdrücklich für Software). Deshalb keine
    DIN-Klimadaten/-Tabellen im Produkt.

### Welle 3 (2026-07-12, `e0403af`): EK→VK-Matrix, Fälligkeiten, HR-Reste, Aufmaß

Vier Slices, parallel gebaut, geteilte Dateien → **ein** Commit. Migrationen
0069/0071/0072 (Hand-SQL) + 0073/0074/0075 (State-only bzw. Trigger-Nachzug).
**Ein 0070 gibt es nicht** — der Aufmaß-Slice ist rein clientseitig.

12. ✔ **EK→VK-Aufschlagsmatrix** (Migration 0069 + State-only 0073). Der größte Hebel
    im Backlog: Großhandelspreis rein, Verkaufspreis raus — statt jede Position von
    Hand zu rechnen. Materialgruppen-Aufschlag, Rabattstaffel, Mindestmarge. Die
    Einkaufspreise liefern IDS und DATANORM bereits.
    - **Architektur: die Matrix sitzt UNTER der bestehenden Artikelkalkulation**
      (`sale_price_group`/`article_sale_price`), nicht daneben. Rangfolge in
      `services/aufschlagsmatrix.py::vk_vorschlag` — **die einzige Rechenstelle**:
      1. **Handpreis** am Artikel (`price_origin='MANUELL'`) schlägt alles →
      2. **VK-Gruppe** am Artikel (bestehende Formel) →
      3. **Matrix** (Artikel > Warengruppe+Lieferant > Warengruppe > Lieferant >
      Standard) → Staffel → **Mindestmarge als Untergrenze** →
      4. sonst **`null` = unbekannt, NIE 0**.
    - **INVARIANTE: Die Regel ist die einzige Wahrheit.** Ein gespeicherter
      MATRIX-Preis wird **nirgends gelesen**, sondern live nachgerechnet
      (`kalkulation.article_kalkulation()` erkennt `price_origin='MATRIX'` und rechnet
      live). Sonst zeigten Artikelansicht und Editor verschiedene Preise, sobald jemand
      die Regel ändert.
    - **Mindestmarge wird mit `ROUND_CEILING` quantisiert** — eine abgerundete
      Untergrenze ist keine Untergrenze. (Review-Fund: bei DATANORM-Kleinteilen mit
      1 Cent EK fiel eine eingestellte 33-%-Mindestmarge komplett weg — 0 % erzielt.)
    - **Massenpflege** mit **Fortsetzungscursor** (`ab_artikelnummer`); **Vorschau ==
      Anwenden** (derselbe Code, `dry_run`), idempotent. Handpreise werden **nie**
      angefasst; fehlender oder 0-EK → übersprungen.
    - Der **IDS-Warenkorb rechnet den VK aus dem ZURÜCKGEGEBENEN EK** (`ek_override`),
      nicht aus dem gespeicherten Stamm-EK (Review-Fund: die Position trug sonst zwei
      Wahrheiten).
    - UI: `features/aufschlagsmatrix` (Regelpflege, Staffel, Massenpflege mit
      Vorschau); das Artikel-Detail zeigt **„Woher der Verkaufspreis kommt"**.
13. ✔ **Fälligkeiten-Engine** (Migration 0071 + State-only 0074). Drei Fristenarten
    unter einem Dach: **Wartungsintervalle**, **Prüffristen** (TrinkwV/Legionellen,
    Schornsteinfeger/KÜO, Rückflussverhinderer, SV, Rauchwarnmelder, Druckbehälter) und
    **Gewährleistung**. `maintenance.due_item` mit genau **einem** Anker; erzeugt
    Termine (→ Plantafel-Rückstand), Angebote und Aufgaben.
    - **INVARIANTE — die Idempotenz garantiert die DATENBANK, nicht der Code:** drei
      partielle UNIQUE-Indizes über (Anker, `due_date`), **statusunabhängig**. Damit
      kann auch ein **VERWORFENER** Eintrag nicht wieder auferstehen. Jeder Insert läuft
      in einem eigenen Savepoint → zwei parallele Scheduler-Läufe erzeugen keine
      Dublette.
    - **Verwerfen schreibt die Quelle fort** — sonst stünde der Vertrag für immer auf
      demselben Datum und wäre still tot.
    - **INVARIANTE: Ein Fälligkeitsdatum wird NIE verschoben** (eine Frist ist eine
      Frist). Verschoben wird nur der daraus abgeleitete **Wunschtermin**
      (`naechster_werktag()` über Sonntage und `hr.holiday`; **Samstag bleibt bewusst
      Arbeitstag**). Der aus einer Fälligkeit erzeugte Einsatz bekommt **kein
      `scheduled_start`** — er landet UNGEPLANT im **Plantafel-Rückstand**, der
      Wunschtermin steht als Notiz am Einsatz. (Review-Fund: mit gesetztem
      `scheduled_start` war der Einsatz weder im Rückstand noch im Raster sichtbar —
      die Plantafel ordnet Kacheln nur über **Zuweisungen** in Bahnen ein.)
    - **Kein Rechtsrat im Produkt:** `basis` (BGB/VOB/INDIVIDUELL) ist ein **Etikett** —
      der Code leitet daraus **keine** Frist ab; maßgeblich ist allein `duration_months`
      (je Auftrag einstellbar, Default am Firmenprofil). Prüfarten sind
      `is_suggestion`-Stammdaten, die der Betrieb selbst pflegt. `is_machinery`
      **verkürzt nichts**, es schaltet nur den Vertriebshinweis „Anlage ohne
      Wartungsvertrag" frei.
    - **Neues Rechte-Modul `maintenance`** (Wartung lief vorher auf `workflow` mit —
      kein Rollenverlust). **STORNIEREN ist das Tor fürs Verwerfen**: DISPOSITION darf
      erledigen, aber eine Frist nicht bewusst verstreichen lassen.
    - Der Bestands-Scheduler `wartung_faellige_ausloesen` wurde **erweitert, nicht
      ersetzt** (zwei Phasen). `erledigen()` schreibt einen `maintenance_event` **und**
      den Vertrag über den Stichtag hinaus fort, damit die Vollautomatik kein zweites
      Folgeobjekt erzeugt.
    - UI: `features/faelligkeiten`, `features/pruefungen`, `features/gewaehrleistung`
      (Subnav Wartung).
14. ✔ **HR-Reste** (Migration 0072 + 0075).
    - **Stundenausgleich** (`hr.time_adjustment`): in **MINUTEN**, nicht in
      Dezimalstunden — 20 min sind 0,333… h und in einer Dezimalspalte nicht
      verlustfrei; ein Arbeitszeitkonto, das bei jeder Drittelstunde rundet, ist eine
      Schätzung, keine Aufzeichnung. **Append-only + Storno** (`reversal_of_id`, beide
      Zeilen fallen aus der Summe). **Saldo bleibt abgeleitet:** `Ist − Soll + Σ
      Ausgleich`.
    - **INVARIANTE (Migration 0075): Niemand gleicht sein eigenes Arbeitszeitkonto aus —
      auch nicht über den STORNO.** Ein Storno **IST** eine Ausgleichsbuchung. Die Regel
      liegt **physisch im DB-Trigger** (`hr.enforce_time_adjustment`), nicht nur im
      Service. (Review-Fund, reproduziert: 30 h aufs eigene Konto buchen wurde
      abgewiesen — dieselben 30 h über den Storno zurückholen ging durch.)
    - **Resturlaubs-Übertrag:** idempotent **by construction** — er **SETZT**
      `carryover_days` des Folgejahres, er addiert nicht. **Verfall standardmäßig AUS**
      (Default NULL): § 7 Abs. 3 BUrlG *erlaubt* den 31.03.-Verfall, ordnet ihn nicht
      an, und nach BAG/EuGH verfällt Urlaub nur bei erfüllter Hinweisobliegenheit.
      **Es wird nichts weggerechnet, was der Betrieb nicht ausdrücklich einstellt.**
    - **Attest (DSGVO Art. 9):** `content.file_link.absence_id`. **Eigenes Tor,
      unabhängig vom `content`-Recht** — die Disposition hat `content/LESEN|AENDERN` mit
      Scope ALLE und kommt trotzdem nicht heran. Zugriff nur für den **Betroffenen**
      oder die **Personalverwaltung** (`hr/LESEN` + `hr/AENDERN`, ALLE). Fremd →
      **404, nicht 403** (ein 403 bestätigte die Existenz der Krankmeldung). Der
      Dateiname wird serverseitig neutralisiert; **keine Diagnose** wird gespeichert.
      UI: `shared/attest`.
    - **INVARIANTE: Ein Attest bekommt IMMER ein eigenes Speicherobjekt** — der
      SHA-256-**Dedup ist dafür abgeschaltet**, und der Guard ist **fail-closed** (eine
      Attest-Verknüpfung sperrt die ganze Datei). (Review-Fund: hängte der Monteur sein
      Attest **zusätzlich** an seinen eigenen Einsatz, war es über den Dedup für die
      ganze Disposition lesbar.)
    - **`/planung/abwesend`** („Wer fehlt?", **ohne Abwesenheitsart**, `workflow/LESEN`,
      `features/planung-abwesend`) und **`/hr/abwesenheiten.csv`** (**mit** Art →
      `hr/EXPORTIEREN`). Dieselbe Grenze wie in der Plantafel.
15. ✔ **Aufmaß-Rechner + die zwei letzten NotizApp-Rechner** (keine Migration).
    Aufmaß mit **Teilmaßen** (je mit Bezeichnung und Rechenweg), **Abzügen**
    (Fenster/Türen), **Verschnitt** und Aufrundung auf **Gebinde/VE**. Das Ergebnis geht
    als **Angebotsposition mit Menge, Einheit und nachvollziehbarer Rechenaufstellung**
    in den Beleg.
    - **INVARIANTE gewahrt:** Das Aufmaß liefert eine **Menge, keinen Preis** —
      `unit_price: null`, der Positionsdialog verlangt ihn, **der Server rechnet
      weiterhin jede Geldzahl.** Menge als Dezimal-**String** (max. 3 NK = DB-Skala).
      Das ist die Grenze aus dem Werkzeuge-Slice (Punkt 11) — sie ist nicht aufgeweicht,
      sondern präzisiert: eine Menge darf aus dem Rechner kommen, eine **Geldzahl nie**.
    - `WasserinhaltRechner` und `AusdehnungsgefaessRechner` aus `D:\Mitra\NotizApp_Win`
      fachlich **1:1 portiert** (gegengerechnet; **kein Rechenfehler im Original
      gefunden**). Das MAG-Ergebnis ist als **Auslegungshilfe/Plausibilisierung**
      deklariert, **kein Nachweis**; **keine Normtabellen** mitgeliefert (siehe
      Normrecht oben).

### Welle 4 (2026-07-13): § 35a-Ausweis und Plantafel Stufe 1 (Welle A)

16. ✔ **§ 35a-Arbeitskostenausweis** (`9aa2e7f`, Migration 0076). Lohn-, Maschinen- und
    Fahrtkosten getrennt vom Material auf der Privatkundenrechnung — ohne den Ausweis
    verliert der Kunde 20 % davon (max. 1.200 €/Jahr) an Steuerermäßigung.
    - **LEITINVARIANTE: unbestimmt ist NICHT null.** `line_type` klassifiziert die
      Position bereits, aber für **PAUSCHALE, FREMDLEISTUNG und ZUSCHLAG ist der Anteil
      daraus nicht ableitbar** (eine Pauschale enthält beides). Dort bleibt
      `labour_net_amount` NULL, und der Beleg weist **gar nichts** aus, statt eine
      geratene Zahl gegenüber dem Finanzamt zu behaupten. Abgeleitet wird nur, wo die
      Art eindeutig ist: ARBEITSZEIT/FAHRT voll, MATERIAL 0,00 — **überschreibbar**,
      denn Verbrauchsmittel sind trotz Material begünstigt.
    - Der DB-CHECK bindet den Anteil **je Position** an den Positionsbetrag. Die Prüfung
      auf **Belegebene** ist nicht redundant dazu (Review-Fund): Die Anrechnung eines
      Abschlags zieht dessen Arbeitskosten ab — trug er mehr Lohn als die
      Schlussrechnung abrechnet, stand ein **negativer** Betrag im festgeschriebenen
      PDF; war er ein Materialvorschuss, **überstieg** der Ausweis den Rechnungsbetrag.
      Beides ist jetzt `UNSTIMMIG` = kein Ausweis mit benanntem Grund. **Kein
      Veröffentlichungsverbot** — das machte die SR dauerhaft unstellbar, obwohl ihre
      Beträge stimmen.
    - Storno/Gutschrift negiert den Anteil mit (der negative Ausweis auf einem
      Kreditbeleg ist richtig). Anrechnungspositionen tragen den negativen Anteil des
      Abschlags je Steuergruppe — sonst zählte der Kunde dieselben Arbeitskosten
      zweimal. `SNAPSHOT_VERSION 3`; Altbelege weisen korrekterweise nichts aus.
    - Genau **eine Rechenstelle**: `beleg.arbeitskosten()`. Der Editor unterscheidet
      **abgeleitet vs. abweichend angegeben** — ein abgeleiteter Wert geht NIE in den
      Payload, sonst erstarrte er und stünde nach einer Mengenänderung falsch (600 €
      Lohn auf einer 1.200-€-Position).
17. ✔ **Plantafel Stufe 1, Welle A** (`dc40145`, Migration 0077): **Default-Dauer je
    Terminkategorie** + **Serientermine**.
    - Die Dauer ist ein **Vorschlag** für den Dialog; der Server leitet daraus nie ein
      `scheduled_end` ab, und eine geänderte Kategoriedauer verschiebt **keinen
      bestehenden Termin** (das wäre eine stille Umplanung zugesagter Termine).
    - Ein Serientermin ist eine Reihe **echter, eigenständiger Einsätze** (wie die
      Fälligkeits-Engine ihre Folgetermine materialisiert), keine Regel: eigener Status,
      eigene Zuweisungen, eigene Nummer. Ein abgesagter Dienstag macht den Mittwoch
      nicht kaputt. `series_id` ist reine Herkunftsklammer **ohne FK/Serientabelle**.
    - **`series_anchor` ist der Taktgeber** (Beginn des ERSTEN Vorkommens). Jeder Takt
      zählt aus IHM — nie aus dem Vorgänger (der geklemmte 28.02. weiß nicht mehr, dass
      „der 31." gemeint war) und nie aus dem aktuellen Bestand (ein verschobenes
      Vorkommen machte aus „jeden Montag" sonst dauerhaft „jeden Dienstag").
    - Ein zweites „Wiederholen" **verlängert** die Reihe, statt sie neu auszurollen
      (Review-Fund: es erzeugte Dubletten auf denselben Tagen).

18. ✔ **Plantafel Stufe 1, Welle B** (`ceb1aac`, Migration 0078/0079): **Qualifikationen**
    (frei pflegbarer Katalog — `kind` ist ein DATENWERT ohne CHECK, Gewerk/Zertifikat/
    Herstellerschulung liegen in derselben Tabelle) und **Zuweisungs-Vorlagen** (lose
    Gruppen als Vorschlag, **kein Team-Modell** — User-Entscheidung).
    - **INVARIANTE: Der Abgleich WARNT, er BLOCKIERT NICHT** (wie die Doppelbelegung).
    - **INVARIANTE: Stichtag ist der TERMINBEGINN in ORTSZEIT** — nicht „heute", nicht
      der UTC-Tag.
    - **DSGVO:** Katalog + Bedarf = `workflow`, NACHWEIS = `hr`. Das Board zeigt die
      FOLGE („kein Nachweis für X"), **nicht** das Gültig-bis aus der Personalakte.
19. ✔ **Zeitskala der Plantafel** (User-Meldung: „ein 7–9-Uhr-Termin sieht aus, als ginge
    er den ganzen Tag"). In der Wochenansicht ist eine Spalte ein TAG — die Balken sind
    jetzt **zeitgenau innerhalb der Spalte** positioniert (prozentuale Margins gegen die
    Grid-Area), der Tag ist sichtbar in Stunden unterteilt, und die Reihen-Packung läuft
    über die **Zeit** statt über Spalten: Zwei Termine am selben Tag liegen nebeneinander
    auf ihrer Uhrzeit, nicht übereinander.
    - **INVARIANTE: gegen den Tag DER SPALTE rechnen, nicht gegen den Tag des Termins.**
      Sonst stand ein Termin, der von Sonntag in den Montag ragt, bei 88 % der
      Montagsspalte, und ein Termin, der um Mitternacht endet, kollabierte auf die
      Mindestbreite (16 Stunden als 25-Pixel-Stummel).
    - **INVARIANTE: Die Reihen-Packung muss dieselbe Geometrie annehmen wie das
      Rendering.** Kurze Termine werden auf eine Mindestbreite geklemmt; rechnete die
      Packung mit der echten Dauer, überlappten die Kacheln wieder.
    - **INVARIANTE: Konflikt- und Statusmarke bleiben in JEDER Kachelbreite sichtbar.**
      Die Container-Query blendet bei schmalen Kacheln Titel, Ort und Chips aus — Status
      und Konflikt nie, sonst hinge beides nur noch an der Farbe (Projektregel).
    - **Bewusst vertagt (notiert, nicht vergessen):** (a) In der **Tagesansicht** baut
      `slots()` die Stundenspalten per `setHours` — öffnet ein Nachttermin das Band auf
      0–24, sitzt an den zwei Zeitumstellungstagen jeder Balken eine Spalte daneben.
      (b) Die Kachel-Aktionen („Verschieben"/„Bearbeiten") hängen an `:hover`/
      `:focus-within` — auf **Touch** sind sie vom Board aus nicht erreichbar (Umweg über
      die Einsatz-Mappe). Beide brauchen einen eigenen kleinen Slice.

20. ✔ **Plantafel: Lesbarkeit, Grundraster, Übersicht** (`382fa15`, keine Migration).
    Drei User-Meldungen, eine Geometrie:
    - **Lesbarkeit** („bei nur 1 Std. Zeitfenster sieht das kacke aus"): Zu kurze Kacheln
      werden auf eine **lesbare** Mindestbreite gestreckt (`KACHEL_LESBAR_REM`), und die
      Streckung wird **sichtbar gemacht** — ein Dauerbalken zeigt Länge UND Lage der
      echten Dauer. Der Beginn bleibt auf der Skala; nur am rechten Bandrand wächst die
      Kachel nach links (der Dauerbalken wandert mit).
    - **Grundraster** (Arbeitszeit 07–17, einstellbar, `localStorage`).
      **INVARIANTE: Das Raster ist eine ANZEIGE-Einstellung, KEIN Filter.** Liegt ein
      Termin außerhalb, weitet sich das Band und das Board sagt es an. Zwei Wege in die
      Unsichtbarkeit wurden im Review gefunden und geschlossen: ein Termin **ohne Ende**
      außerhalb des Rasters, und der **Nachtteil eines Termins über Mitternacht** in der
      Tagesansicht des Folgetags (dort öffnet das Band auf 0–24). Ein unsichtbarer Termin
      wäre der gefährlichste Fehler einer Plantafel.
    - **Übersicht** („eine komplette Woche in der Übersicht"): Die Stundenbreite wird aus
      der **gemessenen** Board-Breite zurückgerechnet (ResizeObserver) — die Woche passt
      in den Schirm. Reicht der Platz nicht (4 Wochen, geweitetes Band), scrollt das
      Board; gestaucht wird die Zeit nie. Dazu flacher Seitenkopf (Titel + Reiter in einer
      Zeile) und **einklappbare Navigation** (`mcn.nav.schmal`, gut 11 rem mehr Breite).
    - **INVARIANTE (verschärft): Die Reihen-Packung rechnet in GEZEICHNETEN Koordinaten**
      (Spalte + Anteil), nicht in echter Zeit — nur so kennt sie jede Streckung (auch die
      nach links) und kann Kacheln nicht fälschlich für überschneidungsfrei halten.
    - **INVARIANTE: Die gezeichnete Mindestbreite muss ≥ der CSS-`min-width` der Kachel
      sein.** Sonst wächst die Kachel über ihre Grid-Area hinaus, und die Packung — die
      nur die Grid-Area kennt — weiß nichts davon. Deshalb ist `stundePx` nach unten
      geschrankt, und **rem wird gemessen, nicht mit 16 px geraten** (WCAG 1.4.4).
    - Behoben: der gemeldete **Scroll-Fehler**. Das Board ist jetzt so breit wie sein
      Inhalt (`width: max-content`), nicht wie sein Scrollfenster — sonst enden Zeilen und
      Kopfzeile an der Fensterkante und die klebende Bahnenspalte hört auf mitzulaufen
      (`sticky` klebt nur im umgebenden Block). Kacheln schoben sich zudem über die
      Bahnenspalte (z-index 2 → 4).

**★ NÄCHSTE SCHRITTE (offen).** Zuerst das, was auf Externe wartet, sichtbar halten:

**Noch offen, ableitbar (selbst entscheidbar):**
- **Die zwei vertagten Punkte der Zeitskala** (siehe 19: DST in der Tagesansicht,
  Touch-Erreichbarkeit der Kachel-Aktionen).
- **Board-Einstellungen** (der letzte offene Teil von „Plantafel Stufe 1"): Grundraster
  und Navigationszustand liegen derzeit im **`localStorage`** — bewusst, weil es keine
  User-Preference-Struktur im Schema gibt. Erst klären, ob die Einstellungen **firmenweit**
  (`company.company_profile`) oder **je Benutzer** gehören; dann umziehen.
- **XRechnung** (reines XML, B2G/Leitweg-ID) — optional, User hat 1–2×/Jahr öffentliche
  Auftraggeber. Das CII-Mapping steht bereits.
- **Vier-Augen auf weitere Aktionen ausrollen** (Dubletten-Merge, Massenexport,
  KI-Massenaktionen): Applier in `_APPLIERS` bzw. `claim()` — der Flow steht.
- **E-Mail-Vorlagenverwaltung.**
- **Wunschtermin auf der Rückstandskarte anzeigen** — braucht ein Feld in
  `BacklogJobOut`. Klein, aber die Fälligkeits-Engine legt den Wunschtermin derzeit nur
  als Notiz am Einsatz ab.

**Wartet auf den User / auf Externe:**
- **Lexware Office** — **vom User ans Ende geschoben.** Cloud-API erst ab Tarif XL, der
  Voucher-Weg ist recherchiert. **Zwei offene Fragen:** wer macht die
  USt-Voranmeldung — und öffnet sich `app.lexware.de/addons/public-api` mit
  „API-Schlüssel erstellen"? Bis dahin reicht DATEV.
- **DATEV-Roundtrip beim Steuerberater** (echter Import des Buchungsstapels),
  Personenkonten/OPOS.
- **Anzahlungskonto für steuerfrei und § 13b bestätigen lassen** — aktuell das
  neutrale 1710/3250 (begründete Annahme, kein DATEV-Standard). Steuerberater.
- **Gewerke-Firmenzuordnung** — **Semantik erst klären:** „welche Gewerke bietet die
  Firma" (fast redundant zum `company.trade`-Katalog) vs. je Niederlassung
  (`branch_trade`-Link) vs. je Auftrag/Projekt. Wahrscheinlichste Auslegung: je
  Niederlassung.

**Erledigt, aus der Liste gestrichen:** OAuth-Absenderkonten (**verworfen** — es wird
immer über die Firmen-Mail versendet), veraPDF/Schematron-Gegenprüfung,
Baustellenbericht am freien Termin, Plantafel Drag & Drop, **EK→VK-Aufschlagsmatrix**,
**Fälligkeiten-Engine**, **HR-Reste** (Stundenausgleich, Resturlaubs-Übertrag, Attest,
„Wer fehlt?"), **Aufmaß-Rechner**, **Wasserinhalt-/Ausdehnungsgefäß-Rechner**.

**Bewusst NICHT gebaut:** „Freien Termin zum Auftrag hochstufen". Der Auftragsbezug
eines Einsatzes ist **unveränderlich** (sonst Torumgehung, s. o.). Wer das will,
braucht einen eigenen, getorten Servicepfad (Auftrag anlegen + Einsatz übertragen),
kein Aufweichen des Triggers.

**Dev-Notiz:** Der Demo-Login `admin@` ist testweise auch Mitarbeiter (MA-00004,
30 Urlaubstage) — damit „Mein Profil → Personalakte" live etwas zeigt. Reine
Scratch-Daten; bei Bedarf auf INAKTIV setzen.

### Welle 5 (2026-07-13): Raumaufmaß — der Raum wird ein Fachobjekt

19. ✔ **Raumaufmaß** (Migrationen **0086** + **0089**, State-only 0087).
    Ausgelöst durch `docs/issue.md`: Der bisherige „Aufmaß"-Rechner (Welle 3, Punkt 15)
    war ein reiner Browser-Taschenrechner — Teilmaße → Verschnitt → Gebinde, Ergebnis
    als Menge in eine Belegposition, danach **weg**. Der Betrieb muss die Räume eines
    Objekts aber **einmal** aufnehmen und **dauerhaft** behalten (Heizlast,
    Leitungslängen, später 3D). Der Raum ist deshalb **Objektstammdatum**, kein
    Werkzeug-Zwischenwert: `property.room` hängt an der **Liegenschaft**, nicht am
    Vorgang.
    - **Drei Tabellen:** `property.room` (Fläche/Höhe/Umfang, `volume_m3` als
      **GENERATED**-Spalte), `property.room_surface` (Hüllflächen: Außenwand, Dach-
      schräge, Boden … mit U-Wert und **`adjacent`** = AUSSENLUFT|ERDREICH|UNBEHEIZT|
      BEHEIZT) und `property.room_opening` (Fenster/Türen, `area_m2` GENERATED).
      Eine Öffnung sitzt **in ihrer Wand** (zusammengesetzter FK) — bei zwei Außenwänden
      wäre sonst undefiniert, aus welcher das Fenster ausgeschnitten wird.
    - **Die Fläche ist die Wahrheit, `length_m × width_m` nur die Herleitung.** Kein
      CHECK erzwingt das Rechteck: Der L-förmige Raum, der Erker, die Dachschräge sind
      genau die Fälle, für die man ein Aufmaß braucht.
    - **INVARIANTE: Die Nettowandfläche wird nie negativ** —
      `property.enforce_room_opening_fits` prüft **(a) je Wand** (Σ Öffnungen ≤ Brutto)
      **und (b) je Raum** (Σ ALLER Öffnungen ≤ Σ aller Bauteilflächen). (b) fängt die
      Öffnungen ohne Wandzuordnung, die (a) nicht sieht (Review-Fund: 25 m² Fenster
      gegen 10 m² Wand → `wall_area_net_m2 = −15,000`, das wäre als **Menge in ein
      Angebot** gelaufen). Serialisierungspunkt ist die **Raumzeile** (`FOR UPDATE`) —
      über eine Wandsperre wären zwei Schreibvorgänge an *verschiedenen* Wänden desselben
      Raumes nicht gegen (b) serialisiert. Hat der Raum **noch keine** Hüllfläche, greift
      (b) nicht: Die Wandfläche ist dann **unbekannt**, nicht 0 — Fenster dürfen vor den
      Wänden erfasst werden.
      **Erst seit Migration 0095 stimmt das wirklich** (späterer Review-Fund): Auf
      `room_surface` lag **kein INSERT-Trigger**, und der Ausstieg „Σ Wand = 0 ⇒ keine
      Prüfung" deckte den Fall zu, in dem die erste Wand **unter** einer bereits
      erfassten freien Öffnung entsteht (reproduziert, −15,000 m²). Erreichbar war das
      nur am Service vorbei, weil `set_aufbau` Wände vor Öffnungen einfügt — der Schutz
      lag also in der **Reihenfolge im Service**, nicht in der DB. Genau die Projektlehre:
      *was im Service sitzt, ist umgehbar.* 0095 lässt den Trigger auch bei INSERT feuern
      und rechnet die neue Wand zu Σ Bauteilflächen hinzu; der legitime Fall „Öffnung vor
      der Wand" bleibt erlaubt — verboten ist das **Ergebnis**, nicht die Reihenfolge.
    - **INVARIANTE: Der Anker ist unveränderlich** (`property.forbid_room_reassign`,
      0089). Eine Wand wandert nicht in einen anderen Raum, ein Fenster auch nicht —
      sonst umginge ein `UPDATE room_id` beide Grenzen (Review-Fund, reproduziert).
      Dieselbe Haltung wie beim Auftragsbezug des Einsatzes (0062).
    - **PL/pgSQL-Falle, die zweimal zuschlug** (beim nächsten Trigger dieser Art
      beachten): (1) `FOR UPDATE` ist mit **Aggregatfunktionen** nicht erlaubt.
      (2) Eine `NEW.<feld>`-Referenz in einer SQL-Anweisung wird **beim Planen**
      aufgelöst — auch in einem CASE-Zweig, der nie zutrifft. Feuert derselbe Trigger aus
      zwei Tabellen, sprengt das die Funktion. Felder **vorher in lokale Variablen
      heben**.
    - **Heizlast: zwei Verfahren, und „unbestimmt ist NICHT null".** Kennwertverfahren
      (Fläche × Kennwert) und **raumweises Hüllflächenverfahren**
      (Σ U·A·f·ΔT + 0,34·n·V·ΔT). Fehlt ein U-Wert, ein `temp_factor`, die
      Luftwechselrate, die Innen- oder die Außentemperatur, ist das Ergebnis
      **`null` mit BENANNTEM Grund** („Der U-Wert der Fläche 'Nordwand' fehlt.") —
      **niemals 0**. Ein fehlender U-Wert als 0 hieße: „diese Wand verliert keine
      Wärme". Eine `BEHEIZT`-Fläche trägt dagegen **definitionsgemäß 0 W** bei und
      braucht weder U-Wert noch Faktor — das ist kein Unbekanntes. Ebenso: eine
      Liegenschaft **ohne aufgenommene Räume** hat eine **unbekannte** Heizlast, keine
      von 0 kW; Umfang und Leitungslängen-Schätzung sind **unbekannt**, wenn niemand sie
      gemessen hat (eine Leitungslänge „0,0 m" liefe als Menge in ein Angebot).
    - **Auslegungsdaten gehören ans OBJEKT** (0089: `property.design_outdoor_temp_c`,
      `property.heat_load_w_per_m2`). Sie waren zuerst Query-Parameter — und wurden damit
      **nirgends** abgefragt: Der Rechner meldete für jeden noch so sorgfältig
      aufgenommenen Raum „unbekannt", das Feature war **inert** (Review-Fund). Die
      Auslegungs-Außentemperatur ist keine Frage an den Aufruf, sondern folgt aus dem
      **Standort**. Rangfolge Kennwert: **Raum → Objekt → `null`**.
    - **KEINE DIN-Tabellen im Produkt** (Normrecht, siehe Welle 2/Punkt 11): keine
      Klimadaten, keine U-Wert-Tabellen, keine f-Faktoren, keine Standard-Luftwechsel-
      raten. Alles Eingaben des Betriebs. Beide Verfahren sind ausdrücklich
      **überschlägig — KEIN Nachweis nach DIN EN 12831**.
    - **Der Was-wäre-wenn-Pfad ist kein Nebeneingang** (Review-Fund, behoben): Die
      Query-Parameter `?kennwert_w_m2=` / `?aussentemperatur_c=` machten **keine** der
      Prüfungen, die `set_auslegung` und die DB-CHECKs aus 0089 machen —
      `kennwert_w_m2=0` verwarf den Objektwert (0 ist nicht `None`) und wies **0,0 kW**
      aus statt „unbekannt"; `aussentemperatur_c=500` ergab eine **negative** Heizlast.
      Jetzt läuft die Übersteuerung durch **dieselbe Prüfstelle**
      (`raum._pruefe_auslegungswerte`) → 422 mit Grund, nie ein gerechnetes Ergebnis.
      Dass der Angular-Client die Parameter nie sendet, ist **kein** Argument: Die KI
      geht durch denselben Service.
    - **Rechte:** kein neues Modul — die Endpunkte hängen am bestehenden `property`
      (LESEN/ANLEGEN/AENDERN). **Achtung:** `property` kennt **kein `EIGENE`**; wer Räume
      erfassen darf, darf jede Liegenschaft ändern. Die MONTEUR-Rolle bekommt deshalb
      **bewusst keine** `property`-Schreibrechte; wer Begehungen macht, braucht eine
      eigene Rolle über die Rechtematrix-Pflege. Ein echter Zeilen-Scope für `property`
      wäre ein eigener Slice.
    - **UI:** Reiter **„Räume"** am Liegenschafts-Detail (`features/raumaufmass`) —
      mobil bedienbar (Baustelle, große Zahlenfelder), Raumliste je Geschoss,
      Raum-Editor mit Aufbau, Kennzahlen-Panel, Panel „Auslegungsdaten des Objekts".
      Der Server rechnet, das UI zeigt nur an (live vorgerechnet wird ausschließlich die
      triviale Geometrie).
    - **Der alte Rechner heißt jetzt „Mengenermittlung"** (Werkzeuge-Tab
      `mengenermittlung`). Er bleibt fachlich — Verschnitt/Gebinde ist beim
      Angebotschreiben nützlich —, aber der Name „Aufmaß" gehört jetzt dem Raumwerkzeug.
      Dateinamen und Symbole (`aufmass-rechner.ts`, `aufmass()`) sind bewusst **nicht**
      umbenannt.

### Welle 6 (2026-07-13/14): Bauteilkatalog und zeichenbarer Grundriss

20. ✔ **Bauteilkatalog** (Migration **0090**). Statt an jeder Wand einen U-Wert zu tippen,
    wählt man ein Bauteil: „Fenster, Doppelkastenfenster", „Außenwand, Ziegel ungedämmt".
    `property.component_template` (kind FLAECHE|OEFFNUNG), Pflege unter
    **Einstellungen → Bauteilkatalog**, Auswahl im Raum-Editor.
    - **INVARIANTE: Die Vorlage ist eine KOPIERQUELLE, kein Verweis.** Der U-Wert wird
      beim Erfassen in `room_surface`/`room_opening` **kopiert**; `template_id` ist nur
      ein Herkunftsvermerk, und der Heizlast-Rechner liest den Katalog **nie**. Sonst
      änderte eine spätere Katalogkorrektur rückwirkend die Heizlast eines Objekts, das
      der Betrieb dem Kunden längst vorgerechnet hat — dieselbe Regel wie bei der
      Belegposition. **Im Browser gegengeprüft:** Katalogwert 2,7 → 1,1 geändert, Raum
      liefert unverändert U 2,7 und dieselbe Transmission.
    - **INVARIANTE: Der Katalog wird OHNE U-Werte ausgeliefert** (29 Seed-Zeilen, nur
      Namen). Normrecht (keine DIN-Tabellen im Produkt) **und** Verantwortung: Der Betrieb
      unterschreibt die Auslegung, er soll keine Zahlen unterschreiben, die eine Software
      geraten hat. Eine Vorlage ohne Wert ist **kein Fehler**, sondern der
      Auslieferungszustand — die Heizlast ist dann **unbekannt, nicht 0**.
21. ✔ **Grundriss** (Migration **0091**, `property.room_vertex`). Der Raum bekommt einen
    **Umriss**; Fläche, Umfang und die Wandflächen **fallen aus der Zeichnung heraus**,
    statt getippt zu werden. Polygon + `room_height_m` ist ein extrudierbarer Körper —
    die Vorstufe zur 3D-Planung.
    - Koordinaten sind **ganzzahlige Millimeter** im System des **Geschosses** (nicht des
      Raumes): Gleitkomma erzeugte Kanten, die „fast" aufeinander liegen; und weil alle
      Räume einer Etage im selben Raster liegen, entsteht die **Etagenübersicht ohne
      weitere Daten**.
    - **INVARIANTE: Wer zeichnet, misst nicht doppelt.** Mit Umriss rechnet der Server
      `floor_area_m2` (Gauß'sche Trapezformel, **Betrag** — der Umlaufsinn darf keine
      negative Fläche erzeugen) und `perimeter_m` und **verwirft den Client-Wert**
      (Vorbild: `planned_quantity`). Ohne Umriss bleibt die Handeingabe.
    - `room_surface.edge_index` = Kante, auf der die Wand steht; partieller UNIQUE gegen
      **zwei Wände auf derselben Kante** (die zählten dieselbe Fläche doppelt in die
      Heizlast). `room_opening.position_m` = Lage in der Kante; **fehlt sie, ist die Lage
      unbekannt — NICHT 0**: Die Öffnung zählt in Fläche und Heizlast, sie wird nur nicht
      gezeichnet und steht in der Liste „ohne Lage in der Wand".
    - **UI:** SVG-Zeichner mit **eintippbaren Kantenlängen** (ein Handwerker misst 4,37 m
      mit dem Laser — er tippt sie, er zieht sie nicht). Die **Kantenliste ist das
      vollwertige Äquivalent zur Zeichnung**: Der Raum ist **vollständig ohne Maus**
      erfassbar (WCAG). Der **Nordpfeil wird aus den Wandausrichtungen abgeleitet** —
      widersprechen sie sich, erscheint **kein** Pfeil (ein erfundener wäre schlimmer).
22. ✔ **`area_is_derived`** (Migration **0093**) — der wichtigste Fund dieser Welle.
    Eine Wandfläche auf einer Kante ist **gerechnet** (Kantenlänge × Raumhöhe). Ohne
    Kennzeichen wusste danach niemand mehr, dass sie gerechnet war:

        Raumhöhe 2,50 → 2,80 m korrigiert ⇒ Wandflächen blieben stehen
                                          ⇒ die Heizlast rechnete still mit 2,50 m weiter.

    Ein pauschales Nachrechnen verbot sich, weil die **Übersteuerung ein legitimer
    Fachfall** ist (Giebel, Erker, Dachschräge). Beides zugleich geht nur, wenn die Zeile
    **selbst weiß**, woher ihr Wert stammt: `area_is_derived = true` ⇒ der Server rechnet
    sie bei **jeder** Änderung von Umriss oder Raumhöhe neu; `false` ⇒ Handeingabe, wird
    **nie** überschrieben. Dieselbe Unterscheidung wie beim § 35a-Anteil (0076):
    „abgeleitet" vs. „abweichend angegeben".
    **Der Client darf `gross_area_m2` für eine abgeleitete Kantenwand NICHT mitschicken**
    — täte er es, deutete der Server das als Handeingabe und die Fläche erstarrte. Er
    lässt das Feld beim Laden deshalb **leer**, statt es mit dem Serverwert vorzubelegen.
23. ✔ **Kante nur an der Wand** (Migration **0094**, Review-Fund). Über die API ließ sich
    einer **DECKE** ein `edge_index` geben: Ihre Fläche wurde dann als Kantenlänge ×
    Raumhöhe abgeleitet (12,5 m² statt 20 m²) — und **wuchs fortan mit der Raumhöhe**.
    Der Angular-Client hielt die Regel von sich aus ein; **das ist genau kein Argument**:
    Nach der Vision geht die KI durch **dieselben Tore wie ein Mensch**, und ihr Weg ist
    der Service. Deshalb steht die Regel jetzt als CHECK auf der Zeile:
    `edge_index IS NULL OR surface_type IN ('AUSSENWAND','INNENWAND')` — ein Polygon ist
    die **Draufsicht**, seine Kanten sind die senkrechten Bauteile.
24. **Serialisierung (Review-Fund):** `set_grundriss`, `set_aufbau` und `update_room`
    nehmen jetzt **ausdrücklich** `SELECT … FOR UPDATE` auf die Raumzeile — und lesen
    Umriss und Raumhöhe **erst unter der Sperre**. Vorher entstand die Sperre nur als
    Nebenwirkung von `enforce_room_opening_fits`, der bei einem `set_aufbau` **ohne
    Öffnungen** gar nicht feuert. Folge wäre eine Wand auf einer Kante, die es nicht mehr
    gibt — die `_rechne_abgeleitete_flaechen` danach **still überspringt**.

**Zwei Fallen, die diese Welle gekostet hat (für den nächsten, der so etwas baut):**
- **Ein grüner Unit-Test kann an einem toten Werkzeug vorbeilaufen.** Das Zeichnen mit
  der Maus war nach dem ersten Punkt **komplett tot** (die Ansicht passte sich auf einen
  einzelnen Punkt ein → Zoom 560 Einheiten/mm → jeder Klick snappte zurück auf Punkt 1).
  Alle Einzelteile waren getestet und grün; der Fehler lebte in der **Kette**. Gefunden
  hat ihn nur der **echte Browser-Durchlauf**. Teste Interaktionsketten, nicht nur
  Funktionen.
- **Vorschau und Server müssen dieselbe Zahl rechnen.** Der Zeichner summierte rohe
  Gleitkomma-Kantenlängen und rundete am Schluss, der Server rundete **je Kante**. Der
  Umfang sprang beim Speichern (5,657 → 5,656 m). Regel wie bei der Heizlast: **Die
  ausgewiesene Summe ist die Summe der ausgewiesenen Teile.**

### Bewusst offene Invarianten (nicht versehentlich „reparieren")

- **§ 35a: unbestimmt ist NICHT null.** Wo die Positionsart den Arbeitskostenanteil nicht
  hergibt (PAUSCHALE/FREMDLEISTUNG/ZUSCHLAG), bleibt er NULL und der Beleg weist **gar
  nichts** aus. Kein Default auf 0 („verschenkt still den Bonus") und keiner auf „voll"
  („Steuerverkürzung"). Der Ausweis wird zusätzlich auf **Belegebene** geprüft: negativ
  oder größer als der Rechnungsbetrag → `UNSTIMMIG`, kein Ausweis. Das ist **nicht**
  redundant zum DB-CHECK (der prüft je Position) — die Anrechnung eines Abschlags kann
  die Summe kippen.
- **Ein Handwerkstermin ist eine Uhrzeit auf der WANDUHR.** Serientakte rechnen in
  `BOARD_TZ` (Europe/Berlin), nicht in UTC — sonst verschiebt die Sommerzeitumstellung
  den Termin um eine Stunde. Auch Wochentag und Feiertagsvergleich müssen den
  **Berliner** Kalendertag treffen (Montag 00:30 Ortszeit ist in UTC noch Sonntag).
- **Der Serien-Takt zählt aus `series_anchor`, nie aus dem Bestand.** Ein verschobenes
  oder abgesagtes Vorkommen darf den Takt der Reihe nicht kippen, und der Monatstag muss
  den geklemmten Februar überleben. Die Werktagsverschiebung wirkt auf das **einzelne
  Vorkommen**, nie auf den Takt.

- **Ein Wert, der in ein EINGABEFELD geht, darf NIE gruppiert formatiert sein.**
  `apiZuDeEingabe` (ohne Tausenderpunkt) für Formulare, `apiZuDeAnzeige` (mit) nur für
  reine Anzeige. Die Rückwandlung **lehnt Mehrdeutiges wie „1.500" ab** statt zu raten.
  Das ist die Lehre aus einem stillen Datenverlust: 1200 → „1.200" → editiert zu
  „1.500" → gespeichert als **1,5**. Nicht wieder zu einer Formatierfunktion
  zusammenlegen.
- **Gesundheitsdaten gehören hinter `hr/LESEN` — nie in eine `workflow`-Schnittstelle.**
  Die Plantafel zeigt „abwesend, von–bis", **nicht** die Abwesenheitsart (DSGVO Art. 9).
  `api/mitarbeiter.py` und `/planung/abwesend` ziehen dieselbe Grenze; ein Review hat
  sie im Board-Endpunkt gerissen vorgefunden. Die Art gibt es nur über
  `/hr/abwesenheiten.csv` (`hr/EXPORTIEREN`).
- **Ein Attest bekommt IMMER ein eigenes Speicherobjekt.** Der SHA-256-**Dedup ist für
  Atteste abgeschaltet**, und der Zugriffs-Guard ist **fail-closed**: eine
  Attest-Verknüpfung sperrt die ganze Datei. Sonst reicht es, dieselbe Datei ein zweites
  Mal an ein harmloses Ziel zu hängen (Review-Fund: Attest zusätzlich am eigenen Einsatz
  → für die ganze Disposition lesbar). Das Attest-Tor hängt **nicht** am
  `content`-Recht, sondern an Betroffener-oder-`hr`; fremd → **404, nicht 403**.
- **Niemand gleicht sein eigenes Arbeitszeitkonto aus — auch nicht über den STORNO.**
  Ein Storno **IST** eine Ausgleichsbuchung. Die Regel liegt im **DB-Trigger**
  (`hr.enforce_time_adjustment`, Migration 0075), nicht nur im Service — ein Review hat
  genau diesen Umweg reproduziert. Stundenausgleich wird in **MINUTEN** geführt; nicht
  „der Einfachheit halber" auf Dezimalstunden umbauen (20 min = 0,333… h).
- **Beim Resturlaub wird nichts weggerechnet, was der Betrieb nicht ausdrücklich
  einstellt.** Verfall ist standardmäßig **AUS** (Default NULL): § 7 Abs. 3 BUrlG
  *erlaubt* den 31.03.-Verfall, ordnet ihn nicht an; nach BAG/EuGH verfällt Urlaub nur
  bei erfüllter Hinweisobliegenheit. Kein „Default = Verfall" nachrüsten.
- **Ein Fälligkeitsdatum wird NIE verschoben** (eine Frist ist eine Frist). Verschoben
  wird nur der abgeleitete **Wunschtermin**. Und: die **Idempotenz der Fälligkeiten
  garantiert die DATENBANK** (drei partielle UNIQUE-Indizes über Anker + `due_date`,
  **statusunabhängig**) — nicht der Service. Statusabhängig gemacht, könnte ein
  **verworfener** Eintrag wieder auferstehen.
- **Kein Rechtsrat im Produkt.** `due_item.basis` (BGB/VOB/INDIVIDUELL) ist ein
  **Etikett**; der Code leitet daraus **keine** Frist ab — maßgeblich ist allein die
  eingestellte `duration_months`. Prüfarten sind Vorschläge (`is_suggestion`), die der
  Betrieb pflegt. Keine Fristen aus Normen/Gesetzen hart verdrahten.
- **Vergessenes Ausstempeln wird NICHT automatisch beendet.** Ein erfundenes Ende wäre
  eine Falschaussage in einer gesetzlichen Aufzeichnung (§ 17 MiLoG). Die Buchung
  bleibt offen und wird als **überfällig markiert**. Das ist bewusst unbequem — kein
  „Auto-Stopp nach 12 h" nachrüsten.
- **Die gesetzliche Pause wird VOLL abgezogen, nicht auf die Schwelle gekappt**
  (6 h 01 Arbeit → 5 h 31 Nettozeit). Eine gekappte 1-Minuten-„Pause" wäre keine
  Ruhepause nach § 4 ArbZG (Mindestabschnitt 15 min), sondern eine erfundene Zahl.
- **`row_scope='EIGENE'` ist nur für Aufgaben und Einsätze umgesetzt.** Überall
  sonst gilt **fail-closed**: `require()` wirft 403. Ein MONTEUR sieht Projekte,
  Aufträge, Wartung und Plantafel deshalb gar nicht. Wer das ändern will, setzt
  EIGENE dort **echt** um und stellt auf `require_scoped` — niemals einfach auf
  `require` zurückfallen, das wäre ein stiller Datenleak. Drei Reviews haben hier
  je ein Loch gefunden (`create_task`, `create_service_case`, **Datei-Upload**);
  alle drei sind geschlossen, das Muster bleibt gefährlich.
- **`require_create` ist NUR zulässig, wenn die erzeugte Zeile kein Feld trägt, mit
  dem der Erzeuger sie einem FREMDEN Elternobjekt zuordnen kann.** Die Datei-API war
  genau dieser Fall (Upload an fremden Bericht/Auftrag, verifiziert 201) — das ist
  der **dritte Fund dieser Art**. Wo ein Ziel-/Eltern-Feld im Payload steht, gehört
  ein **Ziel-Guard je Zielart** dazu: erlaubte Ziele bei EIGENE positiv aufzählen,
  alles andere fail-closed ablehnen.
- **Ein unterzeichneter Baustellenbericht ist versiegelt — auch seine Anhänge.**
  Trigger `content.protect_signed_site_report_links` (INSERT/UPDATE/DELETE, OLD und
  NEW). Ohne den war der Bericht nur scheinbar unveränderlich: die Fotos, auf die er
  sich beruft, ließen sich danach noch tauschen.
- **Doppelbelegung bleibt eine WARNUNG, keine Sperre** (Invariante aus 0025 — nicht
  aufweichen, auch nicht „nur für Ressourcen"). Der maßgebliche Zeitraum liegt auf
  `service_job` und ist dort nullable; ein EXCLUDE käme nur mit Denormalisierung plus
  Synchron-Trigger zustande und griffe an NULL-Rändern still nicht. Der Service warnt
  (Mitarbeiter UND Ressourcen), blockiert aber nicht. Die Roadmap führt Doppelbuchung
  ausdrücklich als weich. **Nicht zu verwechseln mit der Zeiterfassung:** dort ist die
  Überlappung eigener Zeitbuchungen per EXCLUDE **hart** gesperrt.
- **Mahnstufen:** `fee`/`interest_note` bleiben NULL (Beschluss B-22,
  Steuerberater-Vorbehalt). Aktive Stufen müssen einen lückenlosen Präfix bilden,
  sonst könnte der DB-Trigger sie nie ausstellen.
- **Der Feiertagskalender (`hr.holiday`, 2026/27, Migration 0068) gilt für
  Zeiterfassung und Plantafel — NICHT für die Urlaubstage-Zählung.** `days_count`
  einer Abwesenheit zählt einen gesetzlichen Feiertag weiterhin als Arbeitstag, wenn
  der Vertrag für den Wochentag ein Soll ausweist (`services/zeiterfassung.py` und
  `planung.py` lesen `Holiday`, die Abwesenheitsberechnung nicht). Wer das umstellt,
  ändert rückwirkend Urlaubssalden — bewusst noch nicht getan.
- **Belegeditor rechnet keine Summen.** Exakte Rundung je Steuergruppe ist in
  JavaScript-`number` nicht verlustfrei; der Server rechnet verbindlich. Nicht
  „nachrüsten". **Auch die Werkzeuge halten diese Grenze:** Heizlast & Co. liefern nur
  eine **Textposition**, das Aufmaß eine **Menge** (`unit_price: null`) — **kein in
  JavaScript gerechneter Wert wird je ein Preis.**
- **Der Verkaufspreis kommt aus genau EINER Rechenstelle** (`aufschlagsmatrix.
  vk_vorschlag`), und die **Regel ist die einzige Wahrheit**: ein gespeicherter
  MATRIX-Preis wird nirgends gelesen, sondern live nachgerechnet. Sonst zeigten
  Artikelansicht und Editor verschiedene Preise, sobald jemand die Regel ändert.
  Fehlt der EK, ist der VK **`null` = unbekannt — nie 0** (gleiche Konvention wie bei
  Marge und Auslastung). Die **Mindestmarge wird mit `ROUND_CEILING` quantisiert**: eine
  abgerundete Untergrenze ist keine Untergrenze (bei 1-Cent-EK fiel sie sonst ganz weg).
- **`beleg.anzeige_menge_preis()` ist die EINZIGE Vorzeichenstelle für die AUSGABE
  von Kreditbelegen.** Web-Mappe und Editor zeigen bewusst die **DB-Wahrheit**
  (100 × −2,40 €), PDF und XML die **EN16931-Darstellung** (−100 × 2,40 €), weil
  BR-27 negative Einzelpreise verbietet. Das ist **kein Bug** — und es ist keine
  zweite Umrechnung an anderer Stelle nachzurüsten: PDF und XML nutzen dieselbe
  Funktion, damit Sichtbild und Daten dasselbe zeigen.
- **Bekannte Sichtbild-Divergenz bei Altbelegen.** Belege, deren `BELEG_PDF` vor der
  Font-Umstellung archiviert wurde, behalten ihre Ausfertigung; eine später erzeugte
  E-Rechnung desselben Belegs zeigt die neue Typografie und den vollständigen
  Anschriftsblock. **Die Beträge sind identisch.** Ein Neurendern der archivierten
  Ausfertigung ist per GoBD ausgeschlossen — nicht „reparieren".
- **Eine NULL im `billing_snapshot` ist eine LÜCKE, keine eingefrorene Aussage.**
  Deshalb der **feldweise** Live-Fallback (nicht: „Snapshot vorhanden → alles aus dem
  Snapshot"). Belege vor `snapshot_version=2` haben schlicht weniger Felder; sie
  werden nicht rehasht.
- **`db/migrations/0014_einsatz.sql` und `0017` spiegeln die aktuellen
  Trigger-Funktionen NICHT mehr.** Repo-Praxis ist inzwischen Django-`RunSQL`
  (`backend/db_core/migrations/`). Wer `db/` als Quelle der Wahrheit liest, sieht die
  **alten** Tore (z. B. ohne den NULL-Fall des freien Termins). Maßgeblich ist die
  zuletzt angewandte Django-Migration.
---


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
| Planung (50) | **Einsätze** (`workflow.service_job`): Liste + Einsatz-Mappe (Übersicht, Zuweisungen, Zeiten & Material, Verlauf) + **Plantafel** (Schwimmbahnen-Board, **Drag & Drop per Maus/Tastatur/Touch**, Rückstandsleiste, Mehrtages-Balken, Abwesenheits-/Feiertags-Sperrflächen, Auslastung je Bahn, Termin anlegen/bearbeiten) + **Kalender** (Monatsansicht), Subnav | `/api/planung/einsaetze`, `/api/planung/plantafel` |
| Wartung (55) | **Wartungsverträge** (`maintenance.*`, NEUES Schema): Liste + Detail-Mappe (Details/Erinnerung/Verlauf), Fälligkeits-Aktionen. Dazu die **Fälligkeiten-Engine** (0071/0074): Subnav **Fälligkeiten** (Wartung/Prüfung/Gewährleistung unter einem Dach), **Prüffristen** (TrinkwV/KÜO/SV…) und **Gewährleistung**. Eigenes Rechte-Modul `maintenance` | `/api/maintenance/{contracts,faelligkeiten,pruefungen,gewaehrleistung}` |
| Aufgaben (60) | Liste + Statusaktionen; **neue Tabelle `workflow.task`** | `/api/workflow/tasks` |
| Mitarbeiter (65) | **Personalstamm** (`hr.*`, NEUES Schema 0019): Liste + Mappe (Persönliches/Vertrag/Abwesenheiten/Urlaub). Write-Service (employee/contract/absence/urlaubskonto) existiert + getestet | `/api/hr/employees` |
| Artikel (70) | Artikel + Leistungen (Stücklisten), Liste + Detail + **VK-Kalkulation** (Verkaufspreis-Formel je Artikel) + **EK→VK-Aufschlagsmatrix** (Regelpflege, Rabattstaffel, Mindestmarge, Massenpflege mit Vorschau; Artikel-Detail zeigt „Woher der Verkaufspreis kommt") | `/api/pricing`, `/articles/{id}/kalkulation`, `/pricing/aufschlagsmatrix` |
| Buchhaltung (80) | **Offene Posten** + Detail-Mappe (Übersicht/Zahlungen/Mahnverlauf, **Storno-/Gutschrift-Referenzen**) + **Mahnwesen-Screen**. Services: Zahlung/Mahnung + **Storno/Rechnungskorrektur** (STORNO/GUTSCHRIFT, `POST …/cancel`,`/correction`) getestet | `/api/buchhaltung` |
| Auswertungen (90) | Landing + **Umsatz-/Projektübersicht** (KPIs, Umsatzverlauf, Projekte nach Gewerk) | `/api/auswertungen/…` |
| Einstellungen (95) | **Firmenprofil, Mahnstufen (6), Gewerke, Niederlassungen** (`company.*`, NEUES Schema 0023). Das Firmenprofil speist Aussteller und Fußzeile des Beleg-PDF | `/api/company`, `/api/buchhaltung/dunning-levels` |
| Freigaben (62) | **Vier-Augen-Anträge** (`security.approval_request`, 0028): Liste + Statusfilter, Genehmigen/Ablehnen (Pflicht-Begründung)/Zurückziehen. Payload nur für Antragsteller und Entscheider | `/api/security/approvals` |
| Belegerfassung (82) | **Eingangsrechnungen** (`accounting.*`, NEUES Schema 0030/0031): Liste + Beleg-Mappe (Positionen/Verlauf), Editor, Statusautomat ERFASST→GEPRUEFT→FREIGEGEBEN→GEBUCHT/ABGELEHNT, Freigabe-Tor (Kontierung je Position), Stammdaten (Buchungskonten/Kostenstellen) | `/api/accounting` |
| Einstellungen · Rechte | **Rechtematrix-Editor** (Rolle × Modul × Aktion + row_scope) + **Rollenzuordnungen**. Härtungen: keine Selbst-Erweiterung, keine Selbstzuweisung, letzte ADMINISTRATION geschützt | `/api/security/{roles,permissions,users,user-roles}` |
| Mein Profil | Anzeigename/E-Mail/Rollen read-only + **Passwort ändern** (Sitzung bleibt gültig) | `/api/auth/password` |
| Meine Zeiten | **Stempeluhr** (Start/Pause/Weiter/Stopp), eigene Zeitbuchungen, Arbeitstag einreichen (`workflow.time_entry`/`work_day`, 0066–0068). Für Auswerter: Stundenliste + CSV. Dazu **Stundenausgleich** (in Minuten, append-only + Storno, Vier-Augen im DB-Trigger, 0072/0075) | `/api/zeiterfassung` |
| Mitarbeiter (65) | …zusätzlich **Resturlaubs-Übertrag** und **Attest-Upload** (DSGVO Art. 9, eigenes Tor, `shared/attest`); **„Wer fehlt?"** unter Planung (ohne Abwesenheitsart) | `/api/hr`, `/api/planung/abwesend` |
| Werkzeuge (92) | **Heizlast** (überschlägig), Heizkörper-Umrechnung (WP), Volumenstrom, Einheiten, **Aufmaß** (Teilmaße/Abzüge/Verschnitt/Gebinde → Angebotsposition mit **Menge, ohne Preis**), **Wasserinhalt**, **Ausdehnungsgefäß** (Auslegungshilfe, kein Nachweis). Ein gerechnetes Ergebnis wird **nie ein Preis** | (rein clientseitig) |

**Der Schreibpfad ist verdrahtet.** In allen Bereichen gibt es „+ Neu",
Statusaktionen, Freigaben; unumkehrbare Aktionen laufen über einen
Bestätigungsdialog. Zusätzlich neu: Zahlung erfassen/stornieren, Mahnung
erzeugen, Belegeditor mit Positionen, Einsatz-Zuweisung, Zeit-/Materialbuchung
(auch für den Monteur auf eigenen Einsätzen), Ressourcen und Terminkategorien.

Nav-Marks: Planung=50, Wartung=55 (bewusst nicht-rund, Service-Cluster),
Aufgaben=60, Mitarbeiter=65, Artikel=70, Buchhaltung=80, Auswertungen=90,
Werkzeuge=92, Einstellungen=95.

Backend: **808 Tests grün**, db_core-Migrationen bis **0027**, accounts bis 0002.
Hand-SQL-Fachschemata: 0016 `maintenance`, 0019 `hr`, 0023 `company`,
0025 `resource` + `workflow.appointment_category`; 0021/0024 erweitern die
Rechtematrix um die Module `hr` und `company`; 0025 baut die Mahnleiter auf sechs
Stufen aus. **Achtung:** zwei Migrationen heißen `0025_*` (paralleler Bau); der
Graph ist gültig (0026 führt beide Zweige zusammen, 0027 ist das einzige Leaf),
aber `migrate db_core 0025` ist mehrdeutig — vollen Namen angeben.
Dependencies **fpdf2** (Beleg-PDF, PDF/A-3B) und **factur-x** (ZUGFeRD-XML).
`seed_demo` deckt
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

