"""Baseline: führt alle SQL-Migrationen aus db/migrations/ in Reihenfolge aus.

- Die SQL-Dateien bleiben die Quelle der Wahrheit und liegen unverändert im
  Repo; diese Migration liest sie zur Laufzeit ein.
- atomic = False, weil die Dateien ihre Transaktionen selbst steuern
  (BEGIN/COMMIT). Bricht eine Datei ab, sind die davor bereits committet —
  Wiederaufsetzen: Ursache beheben, migrate erneut ausführen ist NICHT
  idempotent. Deshalb Baseline nur gegen leere Datenbanken fahren.
- Auf einer Datenbank, die bereits auf Stand ist (z. B. bestehende Dev-DB):
      python manage.py migrate db_core 0001_baseline --fake
- Rückwärts: bewusst noop — Rückwärtsstrategie laut db/README.md nur über
  vorwärtsgerichtete Korrekturmigrationen.
- run_before stellt sicher, dass das Fachschema vor allen Django-eigenen
  Tabellen existiert (deterministische Reihenfolge, auch beim Test-DB-Aufbau).
"""
from pathlib import Path

from django.conf import settings
from django.db import migrations


def apply_sql_baseline(apps, schema_editor):
    sql_dir = Path(settings.SQL_MIGRATIONS_DIR)
    files = sorted(sql_dir.glob("*.sql"))
    if not files:
        raise RuntimeError(f"Keine SQL-Migrationen gefunden in {sql_dir}")
    with schema_editor.connection.cursor() as cursor:
        for f in files:
            try:
                cursor.execute(f.read_text(encoding="utf-8"))
            except Exception as exc:
                raise RuntimeError(f"Baseline-Fehler in {f.name}: {exc}") from exc


class Migration(migrations.Migration):
    initial = True
    atomic = False
    dependencies = []
    run_before = [("contenttypes", "0001_initial")]
    operations = [
        migrations.RunPython(apply_sql_baseline, migrations.RunPython.noop),
    ]
