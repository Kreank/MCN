"""Plantafel Stufe 1 (Welle A): Default-Dauer je Terminkategorie + Serientermine.

**A) `workflow.appointment_category.default_duration_minutes`**
Der Disponent legt hundertfach denselben Termintyp an („Wartung Gastherme:
90 Minuten"). Bisher tippt er Ende-Datum und Ende-Uhrzeit jedes Mal von Hand —
die häufigste Fehlerquelle im Board (ein Termin ohne Ende ist auf der Plantafel
ein Strich statt eines Balkens). Die Kategorie trägt jetzt ihre übliche Dauer;
der Termin-Dialog schlägt daraus das Ende vor.

**Vorschlag, keine Vorschrift:** Die Dauer ist NULL-bar und wird im Dialog nur
VORBELEGT, nie erzwungen. Ein konkreter Termin darf länger dauern als die
Kategorie üblich ist — die DB kennt die Kategoriedauer beim Einsatz gar nicht.
Deshalb liegt die Vorbelegung im Frontend und es gibt keinen Trigger, der
`scheduled_end` daraus ableitet: Ein Server, der die Dauer nachträglich
erzwänge, überschriebe die Entscheidung des Disponenten.

**B) `workflow.service_job.series_id`**
Serientermine (wöchentliche Baustellenbegehung, monatliche Wartungsrunde)
entstehen als **echte, eigenständige Einsätze** — je Vorkommen eine Zeile, wie
es die Fälligkeits-Engine mit ihren Folgeterminen schon macht. Kein rrule-Feld,
kein Master/Occurrence-Modell:

- Jedes Vorkommen durchläuft den normalen Statusautomaten. Ein abgesagter
  Dienstag macht den Mittwoch nicht kaputt.
- Zuweisungen, Zeitbuchungen, Berichte und Tore hängen an einer konkreten
  Zeile. Ein virtuelles Vorkommen hätte keine Identität, an der das haften
  könnte.
- Die Plantafel muss nichts über Wiederholungsregeln wissen.

`series_id` ist reine **Herkunftsklammer** (nicht-normativ): Sie sagt „diese
Termine wurden zusammen angelegt", damit das UI sie als Serie kennzeichnen und
gemeinsam finden kann. Sie ist **kein FK** auf eine Serientabelle — es gibt
keine: eine Regel, die man später ändern könnte, würde bereits geplante,
zugewiesene und teils abgearbeitete Termine rückwirkend in Frage stellen. Wer
die Serie ändern will, ändert die Termine.

**`series_anchor`** ist der Taktgeber der Reihe: der Beginn ihres ERSTEN
Vorkommens, wie er beim Anlegen galt. Jedes weitere Vorkommen wird aus ihm
gerechnet (`n`-ter Takt), nie aus seinem Vorgänger. Warum als eigene Spalte und
nicht einfach „der früheste Termin der Reihe"?

- Der Monatstag hängt daran: Der 31.01. klemmt im Februar auf den 28. — rechnete
  man aus DEM weiter, wäre der März der 28., und die Reihe wanderte übers Jahr
  nach vorn. Nur der ursprüngliche Anker weiß noch, dass „der 31." gemeint war.
- Ein einzelnes Vorkommen darf verschoben oder abgesagt werden, **ohne den Takt
  der ganzen Reihe zu kippen** (Review-Fund: wird das erste Vorkommen auf den
  Dienstag gezogen, wurde aus „jeden Montag" ab der nächsten Verlängerung
  dauerhaft „jeden Dienstag"). Der Anker überlebt beides, weil er den WILLEN
  festhält, nicht den Zustand.

Rückwärts: rein additiv (drei Spalten, ein Index), keine Datenmigration.
"""
from django.db import migrations

FORWARD_SQL = r"""
ALTER TABLE workflow.appointment_category
    ADD COLUMN default_duration_minutes integer NULL
        CHECK (default_duration_minutes IS NULL
               OR (default_duration_minutes > 0
                   AND default_duration_minutes <= 10080));   -- max. 7 Tage

ALTER TABLE workflow.service_job
    ADD COLUMN series_id     uuid        NULL,
    ADD COLUMN series_anchor timestamptz NULL;

-- Anker gibt es nur MIT Serie (und umgekehrt): ein Taktgeber ohne Reihe wäre
-- bedeutungslos, eine Reihe ohne Taktgeber nicht verlängerbar.
ALTER TABLE workflow.service_job
    ADD CONSTRAINT service_job_series_anchor_paired
        CHECK ((series_id IS NULL) = (series_anchor IS NULL));

-- Die Serie eines Termins ist eine Suche („zeig mir die ganze Reihe"), kein Join
-- über eine Elterntabelle — ein partieller Index reicht und kostet nichts bei
-- Einzelterminen.
CREATE INDEX idx_service_job_series
    ON workflow.service_job (series_id)
    WHERE series_id IS NOT NULL;

COMMENT ON COLUMN workflow.appointment_category.default_duration_minutes IS
    'Uebliche Dauer dieses Termintyps in Minuten. Nur VORSCHLAG fuer den Dialog (NULL = keiner); der Server leitet daraus nie ein scheduled_end ab.';
COMMENT ON COLUMN workflow.service_job.series_id IS
    'Herkunftsklammer: diese Termine wurden als Serie zusammen angelegt. Kein FK, keine Serientabelle - jedes Vorkommen ist ein eigenstaendiger Einsatz.';
COMMENT ON COLUMN workflow.service_job.series_anchor IS
    'Taktgeber der Reihe: Beginn des ERSTEN Vorkommens. Jeder weitere Takt wird daraus gerechnet - ein verschobenes oder abgesagtes Vorkommen kippt den Takt damit nicht.';
"""

REVERSE_SQL = r"""
DROP INDEX workflow.idx_service_job_series;
ALTER TABLE workflow.service_job
    DROP CONSTRAINT service_job_series_anchor_paired;
ALTER TABLE workflow.service_job
    DROP COLUMN series_anchor,
    DROP COLUMN series_id;
ALTER TABLE workflow.appointment_category DROP COLUMN default_duration_minutes;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0076_arbeitskosten_35a"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
