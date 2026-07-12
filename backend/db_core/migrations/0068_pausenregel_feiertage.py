"""Pausenregel (hr.break_rule), Feiertage (hr.holiday), automatische Pausen,
Rechtematrix fuer die Selbsterfassung.

1) hr.break_rule — Firmeneinstellung, genau eine Zeile (Muster
   company.company_profile: `is_singleton` UNIQUE + CHECK).
   * KEINE         — der Betrieb schneidet keine Pause ein (Pausen werden
                     ausschliesslich gestempelt).
   * GESETZLICH    — ArbZG § 4: mehr als 6 h → 30 min, mehr als 9 h → 45 min.
   * FESTE_ZEITEN  — feste Fenster (z. B. 12:00–12:30) aus `fixed_breaks`.
   Die Wertepruefung der JSONB-Fenster liegt im Service (Von < Bis, HH:MM);
   die DB erzwingt nur, dass FESTE_ZEITEN nicht ohne Fenster existieren kann.

2) hr.holiday — Feiertagskalender. Bisher eine bewusste Luecke („Feiertage
   zaehlen als Arbeitstage, wenn der Vertrag ein Soll ausweist", HANDOFF). Fuer
   den Soll-Ist-Vergleich der Zeiterfassung ist das nicht haltbar: ein
   Feiertag, an dem niemand arbeitet, erzeugte sonst jede Woche ein
   Minus-Saldo. `region` = Bundesland-Code, NULL = bundesweit. Der massgebliche
   Bundeslandcode steht im Firmenprofil (`company.company_profile.state_code`,
   existiert seit 0023) — der Service filtert `region IS NULL OR region =
   state_code`.

   Seed: das laufende und das kommende Jahr. Die beweglichen Feste werden aus
   dem Ostersonntag abgeleitet (anonyme gregorianische Osterformel, hier in
   Python berechnet und als statische INSERT-Liste ausgegeben — die Migration
   bleibt damit deterministisch und ohne Laufzeit-Abhaengigkeit).
   Fortschreibung: `hr.holiday` ist eine gewoehnliche Stammdatentabelle, weitere
   Jahre werden gepflegt (UI/Command), nicht migriert.

3) workflow.time_entry.auto_generated — eine vom System eingesetzte Pause ist
   im UI als solche zu kennzeichnen. Eine automatische Pause, die aussieht wie
   eine gestempelte, waere eine Falschaussage ueber die Aufzeichnung.

4) Rechtematrix: MONTEUR erhaelt `hr/LESEN` und `hr/AENDERN` mit row_scope
   **EIGENE**. Ohne das koennte der Monteur seine eigene Zeit nicht erfassen —
   und `hr` gehoerte bisher allein ADMINISTRATION/GESCHAEFTSFUEHRUNG. `EIGENE`
   ist fail-closed: alle `hr`-Endpunkte, die mit `require` gesichert sind
   (Personalliste, fremde Abwesenheiten, Vertraege), antworten fuer diese Rolle
   weiterhin mit 403. Nur die ausdruecklich auf den Akteur gefilterten
   Endpunkte (`/hr/self`, Stempeluhr, eigene Tage) sind erreichbar.
   `hr/FREIGEBEN` bleibt bei ADMINISTRATION/GESCHAEFTSFUEHRUNG (row_scope ALLE)
   — das Bestaetigen ist Fuehrungsaufgabe und faellt zusammen mit dem
   Vier-Augen-Trigger aus 0067.
"""
from datetime import date, timedelta

import django.db.models.functions.datetime
from django.db import migrations, models

# Das laufende und das kommende Jahr (Stand des Slices).
_YEARS = (2026, 2027)

_BUNDESLAENDER = (
    "BW", "BY", "BE", "BB", "HB", "HH", "HE", "MV",
    "NI", "NW", "RP", "SL", "SN", "ST", "SH", "TH",
)


def _ostersonntag(year):
    """Anonyme gregorianische Osterformel."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _buss_und_bettag(year):
    """Der Mittwoch vor dem 23. November."""
    d = date(year, 11, 22)
    while d.weekday() != 2:  # Mittwoch
        d -= timedelta(days=1)
    return d


def _holiday_rows():
    rows = []
    for year in _YEARS:
        ostern = _ostersonntag(year)
        # (Datum, Name, Regionen — leer = bundesweit)
        entries = [
            (date(year, 1, 1), "Neujahr", ()),
            (date(year, 1, 6), "Heilige Drei Könige", ("BW", "BY", "ST")),
            (date(year, 3, 8), "Internationaler Frauentag", ("BE", "MV")),
            (ostern - timedelta(days=2), "Karfreitag", ()),
            (ostern, "Ostersonntag", ("BB",)),
            (ostern + timedelta(days=1), "Ostermontag", ()),
            (date(year, 5, 1), "Tag der Arbeit", ()),
            (ostern + timedelta(days=39), "Christi Himmelfahrt", ()),
            (ostern + timedelta(days=49), "Pfingstsonntag", ("BB",)),
            (ostern + timedelta(days=50), "Pfingstmontag", ()),
            (ostern + timedelta(days=60), "Fronleichnam",
             ("BW", "BY", "HE", "NW", "RP", "SL")),
            (date(year, 8, 15), "Mariä Himmelfahrt", ("SL",)),
            (date(year, 10, 3), "Tag der Deutschen Einheit", ()),
            (date(year, 9, 20), "Weltkindertag", ("TH",)),
            (date(year, 10, 31), "Reformationstag",
             ("BB", "HB", "HH", "MV", "NI", "SN", "ST", "SH", "TH")),
            (date(year, 11, 1), "Allerheiligen", ("BW", "BY", "NW", "RP", "SL")),
            (_buss_und_bettag(year), "Buß- und Bettag", ("SN",)),
            (date(year, 12, 25), "1. Weihnachtstag", ()),
            (date(year, 12, 26), "2. Weihnachtstag", ()),
        ]
        for day, name, regionen in entries:
            if not regionen:
                rows.append((day, name, None))
            else:
                for r in regionen:
                    rows.append((day, name, r))
    return rows


def _holiday_values():
    parts = []
    for day, name, region in _holiday_rows():
        safe = name.replace("'", "''")
        reg = "NULL" if region is None else f"'{region}'"
        parts.append(f"    ('{day.isoformat()}', '{safe}', {reg})")
    return ",\n".join(parts)


CREATE_SQL = (
    r"""
-- ---------------------------------------------------------------------------
-- hr.break_rule — Pausenregel des Betriebs (Singleton)
-- ---------------------------------------------------------------------------
CREATE TABLE hr.break_rule (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    is_singleton  boolean NOT NULL DEFAULT true CHECK (is_singleton),
    mode          text NOT NULL DEFAULT 'GESETZLICH'
                  CHECK (mode IN ('KEINE', 'GESETZLICH', 'FESTE_ZEITEN')),
    -- [{"von": "12:00", "bis": "12:30"}, ...] — Form prueft der Service.
    fixed_breaks  jsonb NOT NULL DEFAULT '[]'::jsonb
                  CHECK (jsonb_typeof(fixed_breaks) = 'array'),
    version       integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT break_rule_singleton UNIQUE (is_singleton),
    CONSTRAINT break_rule_fixed_needs_windows
        CHECK (mode <> 'FESTE_ZEITEN' OR jsonb_array_length(fixed_breaks) > 0)
);

CREATE TRIGGER trg_break_rule_updated_at
    BEFORE UPDATE ON hr.break_rule
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_break_rule_audit
    AFTER UPDATE ON hr.break_rule
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_break_rule_no_delete
    BEFORE DELETE ON hr.break_rule
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_break_rule_no_truncate
    BEFORE TRUNCATE ON hr.break_rule
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON hr.break_rule FROM PUBLIC;

-- Default: die gesetzliche Regel (ArbZG § 4). Ein Betrieb, der nichts
-- einstellt, haelt damit das Gesetz ein statt gar nichts.
INSERT INTO hr.break_rule (mode) VALUES ('GESETZLICH');

-- ---------------------------------------------------------------------------
-- hr.holiday — Feiertagskalender (region NULL = bundesweit)
-- ---------------------------------------------------------------------------
CREATE TABLE hr.holiday (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    day         date NOT NULL,
    name        text NOT NULL CHECK (btrim(name) <> ''),
    region      text NULL CHECK (region IS NULL OR region IN
                ('BW', 'BY', 'BE', 'BB', 'HB', 'HH', 'HE', 'MV',
                 'NI', 'NW', 'RP', 'SL', 'SN', 'ST', 'SH', 'TH')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    -- NULLS NOT DISTINCT (PG 15+): „bundesweit" ist EIN Wert, nicht beliebig
    -- oft wiederholbar.
    CONSTRAINT holiday_unique UNIQUE NULLS NOT DISTINCT (day, region)
);

CREATE INDEX idx_holiday_day ON hr.holiday (day);

CREATE TRIGGER trg_holiday_updated_at
    BEFORE UPDATE ON hr.holiday
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_holiday_audit
    AFTER UPDATE ON hr.holiday
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_holiday_no_truncate
    BEFORE TRUNCATE ON hr.holiday
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE TRUNCATE ON hr.holiday FROM PUBLIC;
-- Bewusst KEIN No-Delete: ein falsch gepflegter Feiertag ist keine
-- GoBD-relevante Historie, sondern ein Kalenderfehler. Die Aenderung wird
-- auditiert.

INSERT INTO hr.holiday (day, name, region) VALUES
"""
    + _holiday_values()
    + r"""
ON CONFLICT DO NOTHING;

-- ---------------------------------------------------------------------------
-- workflow.time_entry.auto_generated — vom System eingesetzte Pause
-- ---------------------------------------------------------------------------
ALTER TABLE workflow.time_entry
    ADD COLUMN auto_generated boolean NOT NULL DEFAULT false;

-- ---------------------------------------------------------------------------
-- Rechtematrix: MONTEUR darf die EIGENE Zeit erfassen und lesen.
-- ---------------------------------------------------------------------------
UPDATE security.role_permission
SET allowed = true, row_scope = 'EIGENE'
WHERE role_code = 'MONTEUR' AND module = 'hr' AND action IN ('LESEN', 'AENDERN');
"""
)

DROP_SQL = r"""
UPDATE security.role_permission
SET allowed = false, row_scope = 'ALLE'
WHERE role_code = 'MONTEUR' AND module = 'hr' AND action IN ('LESEN', 'AENDERN');
ALTER TABLE workflow.time_entry DROP COLUMN auto_generated;
DROP TABLE IF EXISTS hr.holiday;
DROP TABLE IF EXISTS hr.break_rule;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0067_arbeitstag"),
    ]

    operations = [
        migrations.RunSQL(sql=CREATE_SQL, reverse_sql=DROP_SQL),
        # State-only (managed=False ⇒ kein DDL).
        migrations.CreateModel(
            name="BreakRule",
            fields=[
                ("id", models.UUIDField(primary_key=True, serialize=False)),
                ("is_singleton", models.BooleanField(db_default=True)),
                ("mode", models.TextField(db_default=models.Value("GESETZLICH"))),
                ("fixed_breaks", models.JSONField(db_default=models.Value("[]"))),
                ("version", models.IntegerField(db_default=models.Value(1))),
                (
                    "created_at",
                    models.DateTimeField(
                        db_default=django.db.models.functions.datetime.Now()
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        db_default=django.db.models.functions.datetime.Now()
                    ),
                ),
            ],
            options={"db_table": 'hr"."break_rule', "managed": False},
        ),
        migrations.CreateModel(
            name="Holiday",
            fields=[
                ("id", models.UUIDField(primary_key=True, serialize=False)),
                ("day", models.DateField()),
                ("name", models.TextField()),
                ("region", models.TextField(blank=True, null=True)),
                (
                    "created_at",
                    models.DateTimeField(
                        db_default=django.db.models.functions.datetime.Now()
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        db_default=django.db.models.functions.datetime.Now()
                    ),
                ),
            ],
            options={"db_table": 'hr"."holiday', "managed": False},
        ),
    ]
