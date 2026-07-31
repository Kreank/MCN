# Reifegrad G — Architektur, Betrieb, Sicherheit, Qualitätssicherung

> Teil der Funktions- und Reifegradanalyse. Einstieg: `05-funktions-und-reifegradanalyse.md`.
> Stichtag **28.07.2026**, Arbeitsstand `develop` @ `0281db9`.

Dieser Teil bewertet nicht Funktionen, sondern **Tragfähigkeit**: Hält die
Konstruktion, wenn ein Fremdbetrieb damit arbeitet und ein Prüfer hineinsieht?

---

## G1 Aufbau

| Schicht | Technologie | Umfang (gemessen 28.07.2026) |
|---|---|---:|
| Datenbank | PostgreSQL 16, database-first | 161 Tabellen in 18 Schemata |
| Backend | Django 5, django-ninja (OpenAPI), psycopg3, uv | 474 Python-Dateien |
| Frontend | Angular, standalone, Signals, **ohne zone.js** | 254 TypeScript-Dateien |
| Object Storage | MinIO | — |
| Mobile | native Android-App | **nicht gebaut** (Geräte-Token vorbereitet) |

**Codeumfang nach Schicht** (Zeilen, ohne Fremdbibliotheken):

| Schicht | Zeilen |
|---|---:|
| Backend-Tests | 67.594 |
| Fach-Services (`db_core/services/`, 60 Module) | 38.891 |
| API-Schicht (`api/`, 38 Module) | 23.178 |
| Django-Migrationen (138 Dateien) | 16.789 |
| ORM-Modelle (`managed = False`) | 5.328 |
| KI-Schicht (`db_core/ai/`) | 3.249 |
| Frontend TypeScript | 66.357 |
| Frontend HTML/SCSS | 63.002 |
| Historische Hand-SQL-Migrationen (`db/`) | 8.114 |
| **Summe (relevanter Eigencode)** | **≈ 292.500** |

**Bemerkenswert: 42 % des Backends sind Tests.** Das ist kein Zufallsergebnis,
sondern folgt aus der Arbeitsweise (jeder Slice mit Regressionstests plus
Review-Pflicht).

**Entwicklungszeitraum:** erster Commit **06.07.2026**, aktueller Stand
**28.07.2026** — **220 Commits in 22 Tagen**. Das ist ein KI-gestützter
Entwicklungsprozess und muss in Investorenunterlagen als solcher benannt werden:
Es erklärt die Breite, und es erklärt zugleich, warum die **Betriebszeit unter
Last** noch kurz ist.

---

## G2 Die Datenbank als Regelwerk — die eigentliche technische Substanz

Die Fachregeln liegen physisch in PostgreSQL, nicht in der Anwendung. Gemessen
an der Dev-Datenbank (Migrationskopf 0136):

| Regelart | Anzahl |
|---|---:|
| **Fachregel-Trigger** (Statusautomaten, Einfrieren, Konsistenz) | **168** |
| Audit-Trigger | 123 |
| No-Truncate-Trigger | 113 |
| No-Delete-Trigger | 85 |
| technische `updated_at`-Trigger | 98 |
| **Trigger gesamt** | **587** |
| CHECK-Constraints | 660 |
| Fremdschlüssel | 363 |
| UNIQUE-Constraints | 88 |
| EXCLUDE-Constraints (Überlappungsfreiheit) | 17 |
| PL/pgSQL-Funktionen | 341 |
| Indizes | 466 |

**Leitsatz des Projekts:** *„Was im Service sitzt, ist umgehbar; erst was im
Trigger sitzt, hält."* — Drei Reparaturen mussten deshalb zweimal gemacht werden;
die Regel steht seither in `docs/INVARIANTEN.md`.

**Für die technische Due Diligence ist das die stärkste Aussage der gesamten
Analyse:** Die Doppelabrechnungssperre, die Beleg-Festschreibung, die
Versiegelung unterschriebener Berichte, die Überlappungsfreiheit von
Arbeitsverträgen und Zeitbuchungen, die Idempotenz von Fälligkeiten und die
Selbstgenehmigungssperre beim Stundenausgleich sind **nicht wegkonfigurierbar** —
auch nicht durch die KI, auch nicht durch einen zweiten Client, auch nicht durch
einen Programmierfehler in der Anwendungsschicht.

**Bewusste Einschränkung:** Es gibt **keine Row-Level-Security** und **keine
Datenbank-Views**; die Rechteprüfung liegt in der Anwendung (die Anwendung
verbindet sich als technischer DB-Benutzer). Das ist dokumentiert und begründet,
aber es bedeutet: Wer direkten Datenbankzugriff hat, umgeht die Rechtematrix.

---

## G3 Sicherheit

| Maßnahme | Stand | Evidenz |
|---|---|---|
| Gesamte API anmeldepflichtig | umgesetzt | `api/api.py`, `api/tests/test_endpoint_schutz.py`; Ausnahmen nur `/health` + vier `/auth`-Endpunkte |
| Session-Cookie + CSRF, zusätzlich Bearer für Geräte | umgesetzt | `NinjaAPI(auth=[django_auth, DeviceTokenAuth()])` |
| Brute-Force-Schutz (Konto **und** IP) | umgesetzt | `services/login_schutz.py`, Migration 0116 — vertraut bewusst **nicht** `X-Forwarded-For` |
| Rechtematrix serverseitig durchgesetzt | umgesetzt | `services/rechte.py`, 15 Module × 8 Aktionen |
| Vier-Augen-Prinzip, an den Antragsinhalt gebunden | umgesetzt | `services/vier_augen.py` |
| Secrets ausschließlich aus der Umgebung | umgesetzt | `MCN_SECRET_KEY` fail-closed |
| Zugangsdaten (SMTP, Händler, Geräte) verschlüsselt | umgesetzt | Fernet; **getrennte Schlüssel** für Mail und KI-Werkzeuge |
| `/admin/` in Produktion gesperrt | umgesetzt | nginx, IP-Allowlist/Basic-Auth |
| Postgres und MinIO ohne Port nach außen | umgesetzt | `deploy/docker-compose.yml` |
| TLS | umgesetzt | nginx + certbot-Container; ACME-Ausstellung in Produktion bestätigt |
| **Row-Level-Security** | **nicht vorhanden** | bewusst; siehe G2 |
| **Penetrationstest / externes Security-Audit** | **nicht durchgeführt** | — |
| **Verschlüsselung ruhender Daten (Volumes)** | **nicht umgesetzt** | — |

**Offene Punkte mit Außenwirkung:** kein externes Sicherheitsaudit, keine
dokumentierten Lösch-/Aufbewahrungs-/AVV-Prozesse (technisch möglich,
organisatorisch nicht ausformuliert). Für Kunden mit Datenschutzbeauftragtem ist
Letzteres eine harte Anforderung.

---

## G4 Betrieb

Der Live-Betrieb läuft auf `mitra.tech-artist.de` in acht Compose-Diensten:

| Dienst | Rolle |
|---|---|
| `postgres` (postgres:16-alpine) | Fachdatenbank, kein Port nach außen |
| `minio` + `minio-init` | Objektspeicher für Dateien und archivierte Ausfertigungen |
| `backend` (gunicorn) | API |
| `scheduler` | tägliche Fälligkeiten (`wartung_faellige_ausloesen`) |
| Queue-Worker | KI-Werkzeugaufrufe (`ki_tool_queue_tick`) |
| `backup` | nächtlicher `pg_dump` + MinIO-Spiegel + Schlüsselsicherung |
| `nginx` | liefert das statisch gebaute Angular aus, terminiert TLS |
| `certbot` | Zertifikatserneuerung |

Das Frontend hat **keinen** Laufzeitcontainer — der Angular-Build wird per
Multi-Stage-Build ins nginx-Image kopiert.

**Deploy-Verfahren** (dokumentiert in `CLAUDE.md` und `docs/deployment.md`):
DB sichern → `develop`→`main` mergen → **aus einem losgelösten Worktree bauen**
(nie aus dem Arbeitsbaum) → `docker compose up -d --no-build` → verifizieren.
Das ist ein sauberes, reproduzierbares Verfahren — es ist nur **manuell**.

### Die drei betrieblichen Risiken, die eine Unterlage benennen muss

1. **Das Backup liegt auf derselben Platte.** `MCN_BACKUP_DIR` zeigt auf
   dasselbe Gerät. Eine Off-box-Kopie und — wichtiger — ein **Restore-Probelauf**
   fehlen. Bei einem GoBD-relevanten System mit zehnjähriger Aufbewahrungspflicht
   ist ein nie zurückgespieltes Backup eine Hoffnung, keine Sicherung.
2. **Zwei scharfe Schalter in `deploy/.env`.** `MCN_SEED=0` verhindert, dass das
   Demo-Seeding (`MCN_SEED_COMMAND=seed_demo` steht weiterhin darin) gegen die
   Echtdaten läuft. Und `MCN_EMAIL_BACKEND=…console.EmailBackend` ist die
   **einzige verbleibende** Sicherung gegen echten Mailversand — die früher
   dokumentierte zweite Sperre („kein `MCN_MAIL_KEY`") gilt nicht mehr. Wer den
   Schalter umlegt, macht Rechnungs- und Mahnungsversand an echte
   Kundenadressen sofort scharf.
3. **Zwei ungleich wertvolle Datentöpfe.** Beleg-PDFs in MinIO sind ersetzbar
   (sie entstehen aus dem eingefrorenen Snapshot neu). **Unwiederbringlich sind
   Kundenunterschriften unter Baustellenberichten, Baustellenfotos und Atteste** —
   die existieren nur als Datei. Ist MinIO weg, bleibt ein versiegelter Bericht
   ohne die Unterschrift, wegen der er existiert. Reihenfolge im Backup daher
   zwingend erst DB, dann MinIO.

---

## G5 Qualitätssicherung — Stand und Lücken

### Was es gibt

| Instrument | Umfang |
|---|---:|
| Backend-Testdateien | 187 |
| Backend-Testfunktionen (Quelltext) | ≈ 3.117 |
| **ausgeführte Testfälle** (Lauf vom 28.07.2026) | **4.187 bestanden**, 15 übersprungen |
| Zeilen Testcode | 67.594 |
| SQL-Akzeptanztestsuiten (`db/tests/`) | 7 |
| Nebenläufigkeits-Testskripte | 4 |
| Frontend-Testdateien (`*.spec.ts`) | **22** |
| Review-Pflicht je Slice (max. 4 Runden) | Prozess, dokumentiert in `CLAUDE.md` |

**Externe Konformitätsprüfung** ist für die E-Rechnung durchgeführt worden —
veraPDF 1.30.2 (PDF/A-3B) und Mustang 2.24.0 (EN16931-Schematron) gegen sechs
Belegformen, reproduzierbar über `db_core/tests/test_erechnung_konformitaet.py`.
Der Test überspringt sauber, wenn die Validatoren fehlen; **auf dem heutigen
Prüfrechner ist kein Java installiert, die Prüfung wurde also nicht erneut
gefahren**, sondern ist über `docs/erechnung-validierung.md` belegt.

### Verifikation im Rahmen dieser Analyse

| Prüfung | Ergebnis |
|---|---|
| `ng build --configuration production` | **erfolgreich** (Exit 0, 19,1 s, 140 Lazy-Chunks). Eine Budget-Warnung: `angebot-editor.scss` 9,72 kB gegen ein Budget von 8 kB — die Angabe in `docs/BACKLOG.md`, das Frontend baue ohne Budget-Warnung, ist **überholt** |
| `pytest` (volle Suite) | **4.187 bestanden, 15 übersprungen, 19 Fehler, 18 min 05 s** — die 19 Fehler sind ausnahmslos Teardown-Artefakte (`flush` scheitert an den eigenen No-Truncate-Triggern), kein fachlicher Fehlschlag. Details in `05-funktions-und-reifegradanalyse.md`, Abschnitt 4 |
| OpenAPI-Schema erzeugbar | **ja** — 333 Pfade, 405 Operationen, 573 Schemata |
| Datenbankstruktur gegen Migrationskopf 0136 | **konsistent** aufgebaut |

### Was fehlt

1. **Keine CI.** Bewusst so entschieden (ein Entwickler, disziplinierte manuelle
   Absicherung). Die dokumentierten Auslöser, ab denen CI Pflicht wird — ein
   **zweiter Mitwirkender** oder Deploys, bei denen die manuelle Verifikation
   faktisch übersprungen wird — treten mit dem ersten Pilotkunden ein.
2. **Die volle Backend-Suite lief vor dem Deploy am 22.07.2026 nicht.** Der
   Rollout stützte sich auf saubere Migrationen, Healthchecks und fehlerfreie
   Logs. Das ist genau der Fall, der laut eigener Regel die CI-Pflicht auslöst.
   Der Nachlauf am 28.07.2026 zeigt, dass nichts aufgefallen wäre — das
   entlastet den konkreten Deploy, nicht das Verfahren.
3. **19 dauerhafte Teardown-Fehler in der Suite.** Sie stammen aus Tests mit
   `transaction=True`, deren `flush` an den eigenen No-Truncate-Triggern
   scheitert. Fachlich harmlos — aber eine Suite mit dauerhaft roten Einträgen
   überlässt die Unterscheidung „bekannt vs. neu" dem Gedächtnis. Behebbar durch
   Savepoints statt `transaction=True` (geschätzt ein halber Tag).
4. **Frontend-Testabdeckung ist dünn:** 22 Spec-Dateien auf 254
   TypeScript-Dateien, konzentriert auf Rechenlogik (Grundriss, Dezimalen,
   Anteile) — die Bedienketten selbst sind kaum abgedeckt. Das Projekt hat die
   Lehre bereits einmal bezahlt: *„Das Zeichnen mit der Maus war komplett tot,
   während alle Einzelteile getestet und grün waren."*
5. **Keine Telemetrie** für Prozessdauer, Fehlerraten oder KI-Qualität — damit
   ist kein Nutzennachweis messbar (siehe Messplan in
   `docs/TECHNISCHE_PRODUKTANALYSE.md`).
6. **Kein Lasttest.** Der Artikelstamm mit ~2 Mio Zeilen läuft, aber es gibt
   keine Messung zu Antwortzeiten unter Mehrbenutzerlast.

---

## G6 Dokumentation als Reifeindikator

Positiv und im Vergleich ungewöhnlich: Das Projekt führt **`docs/INVARIANTEN.md`**
— 395 Zeilen fachlicher Regeln, jede mit dem **konkreten Schaden**, der ohne sie
entstanden ist (reproduzierte Beträge, verlorene Datenwerte, Reviewfunde). Dazu
`docs/ENTSCHEIDUNGEN.md` (fixierte Festlegungen samt Begründung),
`docs/ki-tool-vertrag.md` (358 Zeilen Schnittstellenvertrag),
`docs/erechnung-validierung.md` (Anleitung zur externen Nachprüfung) und
`docs/roadmap/` (14 Fachkonzepte).

Für eine technische Due Diligence ist das ein starker Befund: Die
Entscheidungslage ist rekonstruierbar, nicht nur der Code.

**Einschränkend:** Ein Teil der Dokumentation ist nachweislich veraltet.
`docs/BACKLOG.md` mischt erledigte und offene Punkte; `docs/HANDOFF.md` nennt
Punkte als offen, die inzwischen gebaut sind (Eigentumsmodell), und behauptet,
`main` liege 27 Commits vor `origin/main` — tatsächlich ist der lokale
`main`-Zweig **89 Commits hinter** `origin/main`, das Repository ist gepusht. Wer
die Unterlagen ohne `git log` liest, bekommt ein falsches Bild. Das ist zu
bereinigen, bevor Dritte Einsicht erhalten.

---

## G7 Skalierung: der zentrale Konstruktionsvorbehalt

**MCN ist ausdrücklich Single-Tenant.** Der Beleg ist unmissverständlich: Die
Tabelle `company.company_profile` trägt eine Spalte `is_singleton boolean NOT
NULL DEFAULT true` — **eine Firma je Datenbank**. Es gibt keine `tenant_id`,
keine mandantengetrennten Indizes, keine mandantenfähige Rechteprüfung.

Damit sind viele Kunden **nicht** durch Hochskalieren eines Servers erreichbar.
Vor jeder SaaS-Zusage steht eine Produktentscheidung zwischen:

1. **isolierte Instanz je Kunde** — hohe Datenisolation, lokale KI möglich;
   dafür aufwendigere Provisionierung, Updates und Monitoring;
2. **echte Multi-Tenant-Plattform** — effizienter zentraler Betrieb; dafür
   systemweiter Umbau von Datenmodell, Rechteprüfung, Indizes, Storage,
   Migration und Support;
3. **hybrid** — zentrale Verwaltung und Updates, getrennte Kundeninstanzen oder
   regionale Appliances.

Zur Datenschutz- und Lokal-KI-Positionierung passt Variante 1 (automatisiertes
Single-Tenant/Appliance) am besten. **Diese Empfehlung ist eine These und muss
mit Zielkunden geprüft werden.** Bewertung des Aufwands: `05h`.

---

## Zusammenfassung Block G

| Dimension | Reife | Kurzurteil |
|---|---|---|
| Datenmodell und DB-Regelwerk | **sehr hoch** | 168 Fachregel-Trigger; nicht umgehbar |
| Architekturtrennung (UI/API/Service/DB/Storage) | **hoch** | sauber, OpenAPI als Client-Grundlage |
| Sicherheit (Authentifizierung, Rechte, Secrets) | **hoch** | kein externes Audit |
| Betrieb (Container, Scheduler, TLS, Backup) | **mittel-hoch** | Backup ohne Off-box-Ziel und ohne Restore-Probe |
| Backend-Qualitätssicherung | **hoch** | 3.117 Tests, aber keine CI |
| Frontend-Qualitätssicherung | **niedrig** | 22 Spec-Dateien |
| Messbarkeit / Telemetrie | **fehlt** | kein Nutzennachweis möglich |
| Mehrkundenfähigkeit | **fehlt** | `is_singleton` — bewusste Entscheidung, offene Produktfrage |
