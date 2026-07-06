# MCN Backend — Django auf database-first PostgreSQL

Django 5 + django-ninja. Die Datenbank (Schema, Trigger, Statusautomaten,
Audit) ist die Quelle der Wahrheit und liegt in `../db/migrations/*.sql` —
Django führt sie aus, generiert sie aber nie.

## Setup

Voraussetzungen: [uv](https://docs.astral.sh/uv/), laufende PostgreSQL-16-Instanz.

```powershell
cd backend
uv sync

# Verbindung (Defaults: localhost:55432, DB mitra_crm_dev, User postgres)
$env:MCN_DB_PASSWORD = "..."          # weitere: MCN_DB_NAME/USER/HOST/PORT

# Leere Datenbank: baut ALLES auf (43 SQL-Dateien + Django-Tabellen)
uv run python manage.py migrate

# ODER: Datenbank hat das Fachschema bereits
uv run python manage.py migrate db_core 0001_baseline --fake
uv run python manage.py migrate

uv run python manage.py createsuperuser
uv run python manage.py runserver          # API-Doku: http://localhost:8000/api/docs
uv run uvicorn config.asgi:application --reload   # ASGI, für SSE/KI-Streaming
```

## Schemaänderung während der Entwicklung (der Alltagsfall)

1. **SQL-Migration schreiben** — als neue Django-Migration mit `RunSQL`:

   ```python
   # db_core/migrations/0002_projekt_farbe.py
   from django.db import migrations

   class Migration(migrations.Migration):
       dependencies = [("db_core", "0001_baseline")]
       operations = [
           migrations.RunSQL(
               sql="ALTER TABLE workflow.project ADD COLUMN farbe text;",
               # reverse_sql nur solange keine Fachdaten entstanden sind,
               # sonst migrations.RunSQL.noop (Politik aus db/README.md)
               reverse_sql="ALTER TABLE workflow.project DROP COLUMN farbe;",
           ),
       ]
   ```

2. **Model nachziehen** (`db_core/models.py`, managed = False).
   `uv run python manage.py inspectdb <tabelle>` liefert einen Startpunkt.
3. `uv run python manage.py migrate` — bringt jede Umgebung auf Stand.

Regeln:
- Fachtabellen: immer `managed = False`, `db_table = 'schema"."tabelle'`.
  `makemigrations` erzeugt für sie nichts — gewollt.
- Neue Tabellen bekommen den Schutzstandard des Repos (No-Delete/Audit/
  No-Truncate) im selben `RunSQL` mit — Muster in `../db/migrations/0036_*.sql`.
- Unkritische Neubauten ohne Schutzregeln (UI-Präferenzen, KI-Konversationen
  o. Ä.) dürfen als normale `managed = True`-Models in einer eigenen App mit
  `makemigrations` leben. Der geschützte Kern bleibt SQL.
- Historische Basis (0001–0043) bleibt in `../db/migrations/`; neue Änderungen
  entstehen als Django-Migrationen. Ein Verzeichnis, eine Kette, `migrate`
  kann alles von leer bis aktuell.

## Fachliche Schreibvorgänge: `db_core.db_context`

Jede schreibende Operation läuft durch `business_transaction` — sie setzt
transaktionslokal `app.current_user_id` (und optional `app.status_reason`),
wie `../db/README.md` es verbindlich fordert:

```python
from db_core.db_context import business_transaction, run_business_transaction

with business_transaction(request.user.app_user_id):
    projekt.save()

# begründungspflichtiger Status-Rücksprung
with business_transaction(uid, status_reason="Kunde hat storniert"):
    vorgang_zuruecksetzen(...)

# mit automatischem Retry bei Deadlock/Serialisierungskonflikt (40P01/40001)
run_business_transaction(uid, lambda: auftrag_freigeben(auftrag_id))
```

**Kein** `ATOMIC_REQUESTS`, **keine** SET-LOCAL-Middleware: Middleware läuft
außerhalb der View-Transaktion, das `SET LOCAL` verpuffte dort wirkungslos —
deshalb Service-Schicht statt Middleware. Views (ninja-Endpoints) bleiben
dünn und rufen Service-Funktionen, die intern `business_transaction` nutzen.
Dieselben Service-Funktionen nutzt später der KI-Agent (`ai_proposal`-Fluss):
die KI bekommt keinen Sonderweg an den DB-Toren vorbei.

## Auth-Verdrahtung

`accounts.User` (Login, Sessions, Passwort — Djangos Welt, Schema `public`)
↔ `security.app_user` (fachliche Identität, Audit-Referenzziel) über
`User.app_user_id`. Beim Anlegen eines Mitarbeiters: erst `app_user`-Zeile
(fachlich), dann Django-User mit dessen UUID. Konten ohne `app_user_id`
können lesen, aber nicht fachlich schreiben (`db_context` lehnt ab).

## Admin

Nur für Djangos eigene Verwaltung (Benutzer/Gruppen) und allenfalls
Read-only-Sichten auf Stammdaten. Fachliche Writes (Statuswechsel, Belege)
laufen ausschließlich über die Service-Schicht — der Admin kennt die
Statusautomaten nicht und produziert an den Triggern nur kryptische Fehler.

## Tests

`pytest` + `pytest-django`. Der Test-Runner baut die Test-DB über die
Migrationskette auf — inklusive Baseline, also mit allen echten Triggern und
Statusautomaten. Backend-Tests laufen damit gegen die harten DB-Regeln.
Die SQL-Akzeptanztests (`../db/tests/`) bleiben davon unberührt und laufen
weiter direkt per psql.

## Betriebsannahmen (aus db/README.md, hier umgesetzt)

- Isolationsstufe READ COMMITTED — nirgends anheben, auch nicht im Pool.
- `SET LOCAL` statt Session-`SET` (Connection-Pooling, NR2-02) →
  `set_config(..., true)` in `db_context`.
- Sperrenkonflikte sind wiederholbare Fehler → `run_business_transaction`.
- Transaktionen, die Belegnummern ziehen, kurz halten.
