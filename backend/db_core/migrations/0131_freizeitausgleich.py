"""Abwesenheitsart FREIZEITAUSGLEICH (Befund E6, Runde 2).

Sascha: „Urlaubsanträge, Krankheit-Anträge, **Überstundenausgleich**
beantragen usw."

Die ersten beiden gab es; der dritte hatte keine Art. Ein Monteur, der seine
Überstunden abfeiert, musste das als „Sonderurlaub" oder „unbezahlt" buchen —
beides falsch: Sonderurlaub ist der bezahlte Anlassfall (§ 616 BGB, Umzug,
Todesfall), unbezahlt ist gar keine Vergütung. Freizeitausgleich ist bezahlte
Zeit, die aus dem **Arbeitszeitkonto** kommt.

Warum das genügt und nichts weiter zu rechnen ist
-------------------------------------------------
Das Urlaubskonto (`hr.vacation_account`, `used_days`) zählt ausschließlich
Abwesenheiten der Art `URLAUB` (`services/mitarbeiter.py`). Eine neue Art fällt
dort also von selbst heraus — Freizeitausgleich darf den Urlaubsanspruch nicht
mindern, und genau das passiert ohne weiteres Zutun.

Verhältnis zum Arbeitszeitkonto (Migration 0072)
------------------------------------------------
`hr.time_adjustment` kennt die Ausgleichsart `FREIZEITAUSGLEICH` bereits — das
ist aber die **Gegenbuchung**, nicht der Antrag. Sie ist ausdrücklich eine
Führungsentscheidung: `zeiterfassung.ausgleich_buchen` verbietet die Buchung
auf dem eigenen Konto (Vier-Augen-Prinzip).

Beide Seiten gehören zusammen und ersetzen einander nicht:

* **Antrag** (hier): „Ich möchte am Freitag frei und das über Überstunden
  ausgleichen." Läuft durch den Genehmigungsweg, steht in der
  Abwesenheitsplanung, sperrt den Zeitraum gegen Doppelbelegung.
* **Kontobuchung** (0072): der tatsächliche Minutenabzug, den die Führung bei
  der Genehmigung vornimmt.

Die beiden **automatisch** zu koppeln wäre hier verfrüht: Die Minuten aus einem
Abwesenheitszeitraum abzuleiten hieße, das Sollstunden-Raster als Wahrheit über
den Ausgleich zu setzen, und der Abzug entstünde ohne die Begründung, die 0072
für jede Kontobewegung verlangt. Der Abzug bleibt deshalb ein bewusster,
begründeter Schritt der Führung.
"""
from django.db import migrations

FORWARD_SQL = r"""
ALTER TABLE hr.absence DROP CONSTRAINT absence_absence_type_check;
ALTER TABLE hr.absence ADD CONSTRAINT absence_absence_type_check
    CHECK (absence_type IN ('URLAUB', 'KRANKHEIT', 'ELTERNZEIT',
                            'SONDERURLAUB', 'UNBEZAHLT', 'FORTBILDUNG',
                            'FREIZEITAUSGLEICH'));
"""

REVERSE_SQL = r"""
-- Rückwärts nur, solange die neue Art nirgends gebucht ist — sonst bricht der
-- CHECK an den vorhandenen Zeilen ab. Das ist gewollt: Ein stiller Datenverlust
-- (Umbuchen auf eine andere Art) wäre schlimmer als eine scheiternde Rücknahme.
ALTER TABLE hr.absence DROP CONSTRAINT absence_absence_type_check;
ALTER TABLE hr.absence ADD CONSTRAINT absence_absence_type_check
    CHECK (absence_type IN ('URLAUB', 'KRANKHEIT', 'ELTERNZEIT',
                            'SONDERURLAUB', 'UNBEZAHLT', 'FORTBILDUNG'));
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0130_eigene_abwesenheitsantraege"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
