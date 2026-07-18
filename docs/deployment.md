# MCN auf einem Server ausrollen (Demo-Instanz)

Diese Anleitung richtet sich an jemanden, der Docker kann, aber dieses Projekt
nicht kennt. Sie beschreibt den **Demo-Betrieb**: ein Stand, den man
Geschäftsführern zeigt. **Backup** ist inzwischen als Dienst gebaut (Abschnitt
„Backup & Wiederherstellung") — er läuft mit dem Stack hoch und sichert jede
Nacht Datenbank, MinIO-Belege und Schlüssel; **er muss aber ein Ziel auf einer
anderen Platte bzw. off-box bekommen, bevor die erste echte Rechnung im System
steht.**

---

## 0. Was da eigentlich hochkommt

| Container | Aufgabe | Port nach außen |
|---|---|---|
| **nginx** | liefert das gebaute Angular-Frontend aus, reicht `/api` ans Backend weiter, terminiert TLS | **80 + 443 — die einzige Tür** |
| **backend** | Django + gunicorn | keiner |
| **postgres** | PostgreSQL 16, Volume `pgdata` | keiner |
| **minio** | Objektspeicher: Beleg-PDFs, Baustellenfotos, **Kundenunterschriften**, Volume `miniodata` | keiner |
| **scheduler** | löst täglich Fälligkeiten aus (Wartung, Prüffristen, Gewährleistung) | keiner |
| *minio-init* | legt einmalig den Bucket an und beendet sich | — |
| *certbot* | holt und erneuert das TLS-Zertifikat | — |

Das Frontend hat **keinen eigenen Container**: ein Angular-Build ist statisch und
wird beim Bauen ins nginx-Image kopiert.

**Postgres und MinIO haben keine veröffentlichten Ports.** Das ist kein Detail:
ein Postgres mit offenem Port ist eine offene Datenbank im Internet, und im
MinIO-Bucket liegen unterschriebene Kundendokumente.

---

## 1. Voraussetzungen

* Ein Linux-Server mit **Docker** und **Docker Compose v2**.
* Eine **Domain**, deren A-Record (und ggf. AAAA-Record) auf die IP dieses
  Servers zeigt. Das muss **vor** Schritt 5 stimmen — Let's Encrypt prüft es.
* Ports **80 und 443** frei und aus dem Internet erreichbar (Firewall!).
* Das Repository auf dem Server:

```bash
git clone <repo-url> mcn
cd mcn
```

---

## 2. Konfiguration anlegen

```bash
cd deploy
cp .env.example .env
```

Jetzt `.env` öffnen und ausfüllen. **Die Secrets auf dem Server erzeugen** — nicht
aus einer Anleitung, einem Chat oder einer E-Mail abschreiben:

```bash
# Django-Signaturschlüssel (Sitzungen, Passwort-Reset-Token hängen daran)
openssl rand -base64 48

# Datenbank-Passwort
openssl rand -base64 24

# MinIO-Passwort
openssl rand -base64 24
```

Mindestens diese Werte müssen stimmen:

| Variable | Wert |
|---|---|
| `MCN_DOMAIN` | `demo.deine-firma.de` |
| `MCN_CERTBOT_EMAIL` | Adresse für Ablaufwarnungen |
| `MCN_SECRET_KEY` | erzeugt, **nie** der `django-insecure`-Default |
| `MCN_ALLOWED_HOSTS` | `demo.deine-firma.de` |
| `MCN_CSRF_TRUSTED_ORIGINS` | `https://demo.deine-firma.de` |
| `MCN_FRONTEND_BASE_URL` | `https://demo.deine-firma.de` |
| `MCN_DB_PASSWORD` | erzeugt |
| `MCN_MINIO_SECRET_KEY` | erzeugt |
| `MCN_DEMO_PASSWORD` | das Passwort, mit dem sich die Chefs anmelden (≥ 12 Zeichen) |

`deploy/.env` steht in `.gitignore` und wird per `.dockerignore` auch vom
Build-Kontext ferngehalten — **die Secrets landen nicht im Image**, sondern werden
zur Laufzeit eingelesen.

### Der Mailversand ist auf der Demo totgelegt — nicht „reparieren"

In `.env.example` steht:

```
MCN_EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
MCN_MAIL_KEY=
```

**Bitte so lassen.** Der Versandpfad des Systems ist scharf: Rechnung, Angebot
und **Mahnung** gehen mit PDF an echte Adressen. Ein neugieriger Klick eines
Geschäftsführers auf „Mahnung versenden" schickt eine **echte Mahnung an einen
echten Kunden**. Es ist die einzige Aktion im System, die nach außen wirkt.

Zwei Schlösser sichern das:

1. **`MCN_EMAIL_BACKEND` auf console.** Der gesamte Versand läuft über
   `django.core.mail.get_connection()` — der Belegversand
   (`db_core/services/mail.py`) genauso wie der Passwort-Reset (`api/auth.py`).
   Es gibt keinen zweiten Weg nach draußen. Mit dieser Einstellung landet jede
   Mail im Log statt beim Empfänger:
   `docker compose logs backend | grep -B2 -A20 Subject`
2. **Kein `MCN_MAIL_KEY`.** Ohne diesen Fernet-Schlüssel lässt sich nicht einmal
   ein SMTP-Absenderkonto speichern (fail-closed), und ohne aktives Konto
   verweigert der Versand-Service den Dienst.

Für eine echte Installation setzt man beide Werte — und weiß dann, was man tut.

---

## 3. Starten

```bash
cd deploy
docker compose up -d --build
```

Der erste Start dauert (Angular-Build + Python-Abhängigkeiten). Der
Backend-Entrypoint macht der Reihe nach:

1. wartet, bis Postgres **wirklich** Verbindungen annimmt,
2. `migrate` (baut das komplette Fachschema mit allen Triggern auf),
3. `collectstatic` (Django-Admin-Assets für nginx),
4. **Demo-Seed** (`MCN_SEED_COMMAND`, idempotent),
5. **Demo-Passwörter setzen**,
6. gunicorn.

Zusehen:

```bash
docker compose logs -f backend
docker compose ps          # alles "healthy"?
```

> **Warum Schritt 5 überhaupt existiert:** Die Seed-Befehle vergeben
> Login-Passwörter **nur bei `DEBUG`**, und auf dem Server ist `MCN_DEBUG=0`
> (richtig so). Ohne Schritt 5 bekämen alle Demo-Konten ein *unbenutzbares*
> Passwort — die Chefs stünden vor einem Login, durch das sie nicht kommen. Der
> Befehl `demo_passwoerter_setzen` verlangt `MCN_DEMO_INSTANZ=1` und ist auf einem
> Produktivsystem deshalb wirkungslos.

---

## 4. Zustand ohne Zertifikat (Bootstrap)

Solange es kein Zertifikat gibt, liefert nginx die Anwendung über **HTTP** aus.
Das ist Absicht: certbot braucht einen erreichbaren HTTP-Server für seine
Prüfung.

**In diesem Zustand funktioniert der Login nicht** — Session- und CSRF-Cookie
tragen das `Secure`-Flag und werden vom Browser über HTTP nicht mitgeschickt. Das
ist kein Fehler, sondern der Schutz. Also: erst das Zertifikat.

---

## 5. TLS-Zertifikat holen

DNS muss stehen (`dig +short demo.deine-firma.de` → IP dieses Servers).

```bash
cd deploy
source .env     # damit $MCN_DOMAIN / $MCN_CERTBOT_EMAIL gesetzt sind

docker compose run --rm certbot certonly \
    --webroot -w /var/www/certbot \
    -d "$MCN_DOMAIN" \
    --email "$MCN_CERTBOT_EMAIL" \
    --agree-tos --no-eff-email

docker compose restart nginx
```

nginx findet das Zertifikat beim Start und schaltet um: HTTP leitet auf HTTPS um
(die ACME-Prüfung bleibt über HTTP erreichbar, sonst scheitert jede Erneuerung),
die Anwendung läuft über HTTPS.

Prüfen:

```bash
docker compose logs nginx | grep '\[nginx\]'
# → [nginx] Zertifikat für demo.deine-firma.de gefunden — HTTPS aktiv, HTTP leitet um.
```

Der `certbot`-Container erneuert danach automatisch (zweimal täglich Prüfung).
**Nach einer Erneuerung nginx neu starten**, sonst hält er das alte Zertifikat im
Speicher:

```bash
# z. B. wöchentlich per Host-Cron:
0 4 * * 1 cd /pfad/zu/mcn/deploy && docker compose restart nginx
```

---

## 6. Anmelden

`https://demo.deine-firma.de` — alle Demo-Konten haben dasselbe Passwort
(`MCN_DEMO_PASSWORD`):

| E-Mail | Rolle |
|---|---|
| `joerg.feldmann@mitra-sanitaer.de` | Geschäftsführung |
| `petra.lindqvist@mitra-sanitaer.de` | Disposition/Innendienst |
| `timo.kalinski@mitra-sanitaer.de` | Monteur (sieht bewusst wenig — `row_scope EIGENE`) |
| `sven.ostmann@mitra-sanitaer.de` | Nur-Lesen |
| `admin@mitra-sanitaer.de` | Administration (+ Django-Superuser) |

Zum Zeigen lohnt sich der Wechsel zwischen **Geschäftsführung** und **Monteur** —
die Rechtematrix ist sichtbar, nicht behauptet.

---

## 7. `/admin/` — der Notausgang, nicht die Vordertür

Das Django-Admin **kennt die Rechtematrix des Systems nicht**. Es geht an
Statusautomaten, Vier-Augen-Freigaben und Belegtoren vorbei, und wer drin ist,
legt sich einen Superuser an. Es ist aber der einzige Weg, **neue Benutzer**
anzulegen (im Leitstand fehlt die Benutzeranlage noch). Deshalb wird es nicht
abgeschaltet, sondern **eingesperrt**: nginx lässt `/admin/` nur von erlaubten
IP-Adressen durch, optional zusätzlich hinter Basic-Auth.

### Variante A — feste Büro-IP

```
MCN_ADMIN_ALLOW_IPS=172.16.0.0/12,203.0.113.7
```

Dann geht `https://demo.deine-firma.de/admin/` aus dem Büro; alle anderen bekommen
**403**.

### Variante B — SSH-Tunnel (ohne feste IP)

Die Voreinstellung `MCN_ADMIN_ALLOW_IPS=172.16.0.0/12` erlaubt nur die
Docker-internen Netze. Anfragen aus dem Internet behalten ihre echte Quell-IP und
werden abgewiesen; Anfragen, die **auf dem Server selbst** entstehen, erscheinen
als Docker-Gateway (`172.x.0.1`) und dürfen durch. Genau das nutzt der Tunnel:

```bash
# 1. Auf dem eigenen Rechner: den HTTPS-Port des Servers herholen
ssh -L 8443:127.0.0.1:443 benutzer@server

# 2. In der lokalen hosts-Datei (Linux/macOS: /etc/hosts,
#    Windows: C:\Windows\System32\drivers\etc\hosts):
127.0.0.1  demo.deine-firma.de

# 3. Im Browser:
https://demo.deine-firma.de:8443/admin/
```

Der Umweg über den **Domainnamen** (statt `localhost`) ist wichtig: nur so passt
das Zertifikat, nur so kommt der richtige `Host`-Header an (`MCN_ALLOWED_HOSTS`),
und nur so geht die CSRF-Prüfung des Admin-Logins durch.

**Nach dem Admin-Besuch den hosts-Eintrag wieder entfernen** — sonst zeigt die
Domain für dich dauerhaft auf `127.0.0.1`.

### Zweites Schloss: Basic-Auth (empfohlen)

Die IP-Liste hilft nicht, wenn jemand hinter derselben IP sitzt (Büro-NAT,
geteilter Server). Deshalb zusätzlich:

```bash
docker run --rm httpd:2.4-alpine htpasswd -nbB admin 'DEIN-PASSWORT'
# Ausgabe (admin:$2y$05$...) als MCN_ADMIN_BASIC_AUTH in die .env,
# dann:
docker compose up -d --force-recreate nginx
```

**Achtung, wenn ein Reverse Proxy oder CDN davor hängt:** dann sieht nginx nur
noch die IP des Proxys, und die Allowlist ist wertlos. In dem Fall ist Basic-Auth
nicht optional, sondern das einzige Schloss.

### Neuen Benutzer anlegen

Über `/admin/` (Variante A oder B), oder direkt auf dem Server:

```bash
docker compose exec backend python manage.py createsuperuser
```

Ein so angelegter Benutzer hat noch **keine** fachliche Identität
(`security.app_user`) und bekommt von der API 403, bis ihm im Leitstand unter
*Einstellungen → Rechtematrix* eine Rolle zugewiesen wird. Das ist gewollt
(fail-closed).

---

## 8. Neuen Stand einspielen

```bash
cd /pfad/zu/mcn
git pull
cd deploy
docker compose up -d --build
```

Das ist alles: der Entrypoint fährt `migrate` bei jedem Start, und der Seed ist
idempotent. **Die Volumes bleiben** — die Klicks der Demo-Nutzer überleben.

Nur das Frontend geändert? `docker compose up -d --build nginx` reicht.

**Vorsicht:** `docker compose down -v` löscht die **Volumes** und damit den
gesamten Datenbestand. Ohne `-v` ist `down` harmlos.

---

## 8a. Backup & Wiederherstellung

Der `backup`-Dienst kommt mit dem Stack hoch (`docker compose up -d`) und sichert
**jede Nacht** (Default 02:30, `MCN_BACKUP_*` in der `.env`):

1. **Datenbank** — vollständiger `pg_dump` (gzip) nach `MCN_BACKUP_DIR/db/`. Ein
   abgebrochener Dump wird erkannt (pipefail + `gzip -t`) und verworfen, statt als
   vermeintlich gültiges Backup liegenzubleiben.
2. **MinIO** — **additiver** Spiegel des Beleg-Buckets nach `MCN_BACKUP_DIR/minio/`.
   Additiv heißt: im Bucket gelöschte Objekte bleiben im Backup (der Spiegel läuft
   ohne `--remove`). Er schützt gegen Plattenverlust — gegen die böswillige/
   versehentliche **Änderung** eines bestehenden Objekts ist MinIO-Versioning/
   Object-Lock die Härtung (noch offen).
3. **Schlüssel** — Kopie der `.env` (chmod 600) nach `MCN_BACKUP_DIR/env/`. **Ohne
   `MCN_MAIL_KEY`/`MCN_CRED_KEY`/`MCN_SECRET_KEY` ist ein Restore nicht
   entschlüsselbar** — der DB-Dump allein genügt nicht.

Der DB-Dump sichert **alle Fachschemata** samt Funktionen und Triggern, aber
**keine PG-Rollen/GRANTs** (`--no-owner --no-privileges`, `pg_dump` statt
`pg_dumpall -g`). Das ist heute korrekt — genau ein Superuser (`MCN_DB_USER`)
besitzt alles. Würden je PG-Rollen/RLS eingeführt, müsste `pg_dumpall -g` dazukommen.

**Einrichtung — nicht optional für den Echtbetrieb:** `MCN_BACKUP_DIR` in der
`.env` auf eine **andere Platte / ein externes Volume** legen (nicht neben
`pgdata`) und das Verzeichnis zusätzlich **off-box** spiegeln (rsync/S3). Ein
Backup auf derselben Platte hilft beim Plattencrash nicht. Das Backup-Verzeichnis
enthält Klartext-Schlüssel — Zugriff beschränken.

Verifikation (einmal sofort sichern statt bis nachts warten):

```bash
# In der .env: MCN_BACKUP_RUN_ON_START=1  → dann:
docker compose up -d backup
docker compose logs -f backup          # „[backup] DB-Dump: …", „MinIO-Spiegel: …"
ls -lh "${MCN_BACKUP_DIR:-./backups}"/db          # der gzip-Dump liegt da
# Danach RUN_ON_START wieder auf 0.
```

### Wiederherstellung

Auf einem frischen Stack (Volumes leer). Der DB-Dump enthält Schema **und**
Trigger; er wird in eine leere Datenbank eingespielt.

```bash
cd deploy

# 0) Gesicherte .env zurückspielen (Schlüssel!) und in die Host-Shell laden, damit
#    $MCN_DB_USER/$MCN_DB_NAME/… in den folgenden Befehlen gesetzt sind. compose
#    exportiert die .env NICHT in die aufrufende Shell.
cp "${MCN_BACKUP_DIR:-./backups}/env/env-<ts>.bak" .env
set -a; . ./.env; set +a

# Nur Datenspeicher hochfahren (NICHT backend — dessen Entrypoint migriert und
# füllte damit die DB, bevor der Dump drin ist):
docker compose up -d postgres minio minio-init

# 1) Datenbank in die LEERE DB einspielen. ON_ERROR_STOP + single-transaction
#    machen den Restore atomar: bei einem Fehler (z. B. „already exists", weil die
#    DB doch nicht leer war) bricht er sichtbar ab, statt still halbfertig zu enden.
gunzip -c "${MCN_BACKUP_DIR:-./backups}/db/mcn-<ts>.sql.gz" \
  | docker compose exec -T postgres \
      psql -v ON_ERROR_STOP=1 --single-transaction -U "$MCN_DB_USER" -d "$MCN_DB_NAME"

# 2) MinIO-Belege zurückspiegeln (Backup → Bucket, ohne --remove):
docker compose run --rm --entrypoint sh backup -c '
  mc alias set dst "$MCN_MINIO_ENDPOINT" "$MCN_MINIO_ACCESS_KEY" "$MCN_MINIO_SECRET_KEY" &&
  mc mirror --overwrite /backups/minio/"$MCN_MINIO_BUCKET" dst/"$MCN_MINIO_BUCKET"'

# 3) Rest hochfahren:
docker compose up -d
```

Der Restore MUSS in eine **leere** Datenbank laufen (frisches `pgdata`-Volume):
Ist die DB schon migriert, kollidiert jedes `CREATE` — `ON_ERROR_STOP=1
--single-transaction` macht das dann als Fehler sichtbar und rollt zurück, statt
einen halben Restore zu hinterlassen. Ein `pg_dump`/`psql`-Restore ist nur so gut
wie sein letzter Test: den Ablauf einmal auf einem Wegwerf-Server durchspielen,
nicht erst im Ernstfall.

---

## 9. Nachsehen, wenn etwas klemmt

```bash
docker compose ps                       # wer ist healthy?
docker compose logs -f backend          # migrate/Seed/gunicorn
docker compose logs nginx | grep '\[nginx\]'   # TLS- und /admin/-Zustand
docker compose logs scheduler           # Fälligkeiten + login_throttle-Prune
docker compose logs backup              # nächtliches Backup
```

| Symptom | Ursache |
|---|---|
| `Bad Request (400)` | Domain fehlt in `MCN_ALLOWED_HOSTS` |
| Login sagt nichts, geht aber nicht | noch kein Zertifikat → HTTP → Secure-Cookies werden nicht gesendet (Abschnitt 4/5) |
| `CSRF verification failed` | `MCN_CSRF_TRUSTED_ORIGINS` passt nicht (mit `https://`, ohne `/` am Ende) |
| `/admin/` → 403 | IP nicht in `MCN_ADMIN_ALLOW_IPS` — so soll es sein |
| `/admin/` → 401 | Basic-Auth aktiv, Zugangsdaten fehlen/falsch |
| 502 direkt nach `up` | Backend migriert noch — `docker compose logs backend` |
| Es wird nie etwas fällig | `scheduler`-Container läuft nicht |

---

## 10. Was hier fehlt (bewusst)

**Backup gibt es jetzt** (Abschnitt 8a) — die frühere Lücke ist geschlossen.
Warum es zählt, bleibt richtig und erklärt, warum der **MinIO-Spiegel** Teil des
Backups ist (nicht nur der DB-Dump):

> Beleg-PDFs sind ersetzbar (sie werden aus dem eingefrorenen Snapshot neu
> gerendert). **Kundenunterschriften, Baustellenfotos und Atteste existieren nur
> als Datei in MinIO** — ist MinIO weg, bleibt ein versiegelter Bericht ohne die
> Unterschrift, wegen der er existiert.

**Offen am Backup:** die **Off-box-Spiegelung** (`MCN_BACKUP_DIR` auf externe
Storage + rsync/S3) ist Sache des Betreibers, und ein **Restore-Probelauf** auf
einem Wegwerf-Server sollte einmal gemacht werden, bevor echte Daten drin sind.
Ebenfalls offen: **Benutzeranlage im Leitstand** (heute nur über `/admin/`) und
**Monitoring**.
