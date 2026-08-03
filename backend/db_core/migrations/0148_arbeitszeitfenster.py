"""Arbeitszeitfenster im Firmenprofil (company.company_profile).

**Wozu.** Die Auslastung auf der Plantafel zählt ARBEITSZEIT, nicht die Wanduhr:
Ein Einsatz über vier Tage belegt vier Arbeitstage, nicht 81 Stunden am Stück.
Dafür braucht die Rechnung zwei Angaben, die es im Bestand nirgends gab —
**wann der Arbeitstag beginnt und endet** und **wie lang die Pause ist**.

**Warum nicht am Arbeitsvertrag.** `hr.employment_contract` trägt zwar das
Sollstunden-Raster (8,00 h für Montag …), aber keine Uhrzeiten — und der Vertrag
ist nach dem INSERT unveränderlich (Trigger `hr.enforce_contract_immutable`).
Eine Änderung der Betriebszeiten erzwänge dort für jeden Mitarbeiter einen
Folgevertrag. Die Zeiten sind aber keine Vertragsänderung, sondern eine
betriebliche Einstellung — sie gehören ins Firmenprofil, wie die
Gewährleistungsvorgabe und die Urlaubsverfallsregel daneben.

**Warum die Pausenschwelle NICHT konfigurierbar ist.** Ab wann eine Pause fällig
wird, bestimmt § 4 ArbZG (mehr als sechs Stunden), nicht der Betrieb. Wäre die
Schwelle ein Feld, ließe sich der Betrieb so einstellen, dass die Rechnung eine
gesetzwidrige Lage als normal ausweist. Konfigurierbar ist deshalb nur die
**Länge** der Pause (die Firma gibt 60 Minuten, das Gesetz verlangt 30) — die
Schwelle steht als Konstante im Service.

**Defaults sind der Regeltag der Firma:** 07:00–16:00 Anwesenheit, 60 Minuten
Pause = 8 Stunden Arbeit, 40 Stunden in der Woche. `NOT NULL` mit Default: Ein
Betrieb ohne Arbeitszeitfenster kann keine Auslastung rechnen, und ein NULL
hier hieße für die Rechnung „raten" — genau das soll aufhören.
"""
from django.db import migrations


CREATE_SQL = r"""
ALTER TABLE company.company_profile
    ADD COLUMN work_start time NOT NULL DEFAULT '07:00',
    ADD COLUMN work_end time NOT NULL DEFAULT '16:00',
    ADD COLUMN break_minutes integer NOT NULL DEFAULT 60,
    ADD CONSTRAINT company_profile_arbeitszeit_reihenfolge
        CHECK (work_start < work_end),
    -- Die Pause muss in den Arbeitstag passen und darf ihn nicht auffressen:
    -- Eine Pause, die so lang ist wie die Anwesenheit, ergaebe 0 h Arbeit und
    -- damit dauerhaft 0 % Auslastung fuer den ganzen Betrieb.
    ADD CONSTRAINT company_profile_pause_passt
        CHECK (break_minutes >= 0
               AND break_minutes < EXTRACT(EPOCH FROM (work_end - work_start)) / 60);

COMMENT ON COLUMN company.company_profile.work_start IS
    'Beginn des betrieblichen Arbeitstags (Ortszeit). Grundlage der Auslastungsrechnung auf der Plantafel: Was zwischen Feierabend und diesem Zeitpunkt liegt, ist keine Arbeitszeit.';
COMMENT ON COLUMN company.company_profile.work_end IS
    'Feierabend (Ortszeit). Siehe work_start.';
COMMENT ON COLUMN company.company_profile.break_minutes IS
    'Laenge der taeglichen Pause in Minuten (Vorgabe 60). Wird je Arbeitstag abgezogen, an dem mehr als sechs Stunden geplant sind. Die SCHWELLE von sechs Stunden steht in § 4 ArbZG und ist deshalb nicht einstellbar.';
"""

DROP_SQL = r"""
ALTER TABLE company.company_profile
    DROP CONSTRAINT IF EXISTS company_profile_arbeitszeit_reihenfolge,
    DROP CONSTRAINT IF EXISTS company_profile_pause_passt,
    DROP COLUMN IF EXISTS work_start,
    DROP COLUMN IF EXISTS work_end,
    DROP COLUMN IF EXISTS break_minutes;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0147_rechnungsentwurf_verwerfen"),
    ]

    operations = [
        migrations.RunSQL(sql=CREATE_SQL, reverse_sql=DROP_SQL),
    ]
