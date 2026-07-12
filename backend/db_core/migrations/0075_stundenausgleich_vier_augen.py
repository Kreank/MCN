"""Vier-Augen auf dem Arbeitszeitkonto — physisch, nicht nur im Service.

Review-Befund A1 (HOCH, per HTTP reproduziert)
----------------------------------------------
`hr.time_adjustment` (Migration 0072) verbot im Service das **Buchen** auf das
eigene Konto, nicht aber das **Stornieren**. Ein Storno ist jedoch eine
Ausgleichsbuchung wie jede andere: eigene Zeile in derselben Tabelle, negierte
Minuten, wirkt auf denselben abgeleiteten Saldo. Wer −30 h auf seinem Konto nicht
buchen darf, konnte die Buchung eines Kollegen auf seinem Konto stornieren und
sich damit +30 h gutschreiben. Betroffen waren genau die Rollen, gegen die das
Tor gedacht ist: ADMINISTRATION und GESCHAEFTSFUEHRUNG (row_scope ALLE, zugleich
mit eigenem Personalsatz).

Der Service prüft das jetzt in **einer** Funktion für **beide** Schreibpfade
(`_kein_eigenes_konto`). Diese Migration zieht die Regel zusätzlich in die
Datenbank — dorthin, wo das Repo seine Regeln hält:

    Der Akteur (`app.current_user_id`) darf keine Zeile auf hr.employee schreiben,
    die auf SEINEN eigenen `app_user` zeigt.

Damit ist die Umgehung nicht mehr eine Frage der Sorgfalt in der Service-Schicht,
sondern physisch ausgeschlossen — auch für einen künftigen zweiten Schreibpfad,
für den KI-Agenten und für ein Skript, das an der Anwendung vorbei schreibt
(gleiche Linie wie `workflow.enforce_work_day`, das den eigenen Arbeitstag
sperrt).

Fehlt `app.current_user_id`, greift die Prüfung nicht — genau wie überall sonst
im Repo. Fachliche Writes laufen ausnahmslos durch `business_transaction`, das
den Kontext setzt; Migrationen und Seeds nicht, und die sollen hier nicht
blockiert werden.
"""
from django.db import migrations

FORWARD_SQL = r"""
CREATE OR REPLACE FUNCTION hr.enforce_time_adjustment() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_orig    hr.time_adjustment%ROWTYPE;
    v_actor   uuid;
    v_owner   uuid;
BEGIN
    IF TG_OP = 'INSERT' THEN
        -- ------------------------------------------------------------------
        -- Vier-Augen: niemand bewegt sein EIGENES Arbeitszeitkonto.
        -- Gilt fuer die Buchung UND fuer das Storno (ein Storno ist eine
        -- Buchung mit negierten Minuten auf demselben Konto).
        -- ------------------------------------------------------------------
        v_actor := NULLIF(current_setting('app.current_user_id', true), '')::uuid;
        IF v_actor IS NOT NULL THEN
            SELECT e.app_user_id INTO v_owner
            FROM hr.employee e WHERE e.id = NEW.employee_id;
            IF v_owner = v_actor THEN
                RAISE EXCEPTION
                    'Stundenausgleich: das eigene Arbeitszeitkonto kann nicht selbst ausgeglichen oder storniert werden (Vier-Augen-Prinzip)'
                    USING ERRCODE = 'raise_exception';
            END IF;
        END IF;

        IF NEW.reversal_of_id IS NOT NULL THEN
            SELECT * INTO v_orig FROM hr.time_adjustment
            WHERE id = NEW.reversal_of_id FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'Stundenausgleich: die zu stornierende Buchung % existiert nicht',
                    NEW.reversal_of_id USING ERRCODE = 'raise_exception';
            END IF;
            IF v_orig.reversal_of_id IS NOT NULL THEN
                RAISE EXCEPTION
                    'Stundenausgleich: eine Storno-Buchung kann nicht storniert werden'
                    USING ERRCODE = 'raise_exception';
            END IF;
            IF v_orig.status <> 'GEBUCHT' THEN
                RAISE EXCEPTION
                    'Stundenausgleich: die Buchung ist bereits storniert'
                    USING ERRCODE = 'raise_exception';
            END IF;
            IF NEW.employee_id <> v_orig.employee_id THEN
                RAISE EXCEPTION
                    'Stundenausgleich: ein Storno gehoert demselben Mitarbeiter wie die Buchung'
                    USING ERRCODE = 'raise_exception';
            END IF;
            IF NEW.minutes <> -v_orig.minutes THEN
                RAISE EXCEPTION
                    'Stundenausgleich: ein Storno traegt exakt die negierten Minuten der Buchung (% statt %)',
                    NEW.minutes, -v_orig.minutes USING ERRCODE = 'raise_exception';
            END IF;
            IF NEW.status <> 'GEBUCHT' THEN
                RAISE EXCEPTION
                    'Stundenausgleich: eine Storno-Buchung wird im Status GEBUCHT angelegt'
                    USING ERRCODE = 'raise_exception';
            END IF;
            UPDATE hr.time_adjustment SET status = 'STORNIERT'
            WHERE id = v_orig.id;
        ELSIF NEW.status <> 'GEBUCHT' THEN
            RAISE EXCEPTION
                'Stundenausgleich: eine neue Buchung wird im Status GEBUCHT angelegt'
                USING ERRCODE = 'raise_exception';
        END IF;
        RETURN NEW;
    END IF;

    -- UPDATE: alles unveraenderlich ausser dem Uebergang GEBUCHT -> STORNIERT.
    IF NEW.employee_id IS DISTINCT FROM OLD.employee_id
       OR NEW.adjustment_type IS DISTINCT FROM OLD.adjustment_type
       OR NEW.effective_on IS DISTINCT FROM OLD.effective_on
       OR NEW.minutes IS DISTINCT FROM OLD.minutes
       OR NEW.reason IS DISTINCT FROM OLD.reason
       OR NEW.reversal_of_id IS DISTINCT FROM OLD.reversal_of_id
       OR NEW.created_by IS DISTINCT FROM OLD.created_by THEN
        RAISE EXCEPTION
            'Stundenausgleich %: eine gebuchte Ausgleichsbuchung ist unveraenderlich — eine Fehlbuchung wird storniert, nicht umgeschrieben',
            OLD.id USING ERRCODE = 'raise_exception';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status
       AND NOT (OLD.status = 'GEBUCHT' AND NEW.status = 'STORNIERT') THEN
        RAISE EXCEPTION
            'Stundenausgleich %: Statuswechsel % -> % ist nicht zulaessig',
            OLD.id, OLD.status, NEW.status USING ERRCODE = 'raise_exception';
    END IF;
    RETURN NEW;
END;
$$;
"""

# Rueckwaerts: die Fassung aus 0072 (ohne die Vier-Augen-Pruefung).
REVERSE_SQL = r"""
CREATE OR REPLACE FUNCTION hr.enforce_time_adjustment() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_orig hr.time_adjustment%ROWTYPE;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.reversal_of_id IS NOT NULL THEN
            SELECT * INTO v_orig FROM hr.time_adjustment
            WHERE id = NEW.reversal_of_id FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'Stundenausgleich: die zu stornierende Buchung % existiert nicht',
                    NEW.reversal_of_id USING ERRCODE = 'raise_exception';
            END IF;
            IF v_orig.reversal_of_id IS NOT NULL THEN
                RAISE EXCEPTION
                    'Stundenausgleich: eine Storno-Buchung kann nicht storniert werden'
                    USING ERRCODE = 'raise_exception';
            END IF;
            IF v_orig.status <> 'GEBUCHT' THEN
                RAISE EXCEPTION
                    'Stundenausgleich: die Buchung ist bereits storniert'
                    USING ERRCODE = 'raise_exception';
            END IF;
            IF NEW.employee_id <> v_orig.employee_id THEN
                RAISE EXCEPTION
                    'Stundenausgleich: ein Storno gehoert demselben Mitarbeiter wie die Buchung'
                    USING ERRCODE = 'raise_exception';
            END IF;
            IF NEW.minutes <> -v_orig.minutes THEN
                RAISE EXCEPTION
                    'Stundenausgleich: ein Storno traegt exakt die negierten Minuten der Buchung (% statt %)',
                    NEW.minutes, -v_orig.minutes USING ERRCODE = 'raise_exception';
            END IF;
            IF NEW.status <> 'GEBUCHT' THEN
                RAISE EXCEPTION
                    'Stundenausgleich: eine Storno-Buchung wird im Status GEBUCHT angelegt'
                    USING ERRCODE = 'raise_exception';
            END IF;
            UPDATE hr.time_adjustment SET status = 'STORNIERT'
            WHERE id = v_orig.id;
        ELSIF NEW.status <> 'GEBUCHT' THEN
            RAISE EXCEPTION
                'Stundenausgleich: eine neue Buchung wird im Status GEBUCHT angelegt'
                USING ERRCODE = 'raise_exception';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.employee_id IS DISTINCT FROM OLD.employee_id
       OR NEW.adjustment_type IS DISTINCT FROM OLD.adjustment_type
       OR NEW.effective_on IS DISTINCT FROM OLD.effective_on
       OR NEW.minutes IS DISTINCT FROM OLD.minutes
       OR NEW.reason IS DISTINCT FROM OLD.reason
       OR NEW.reversal_of_id IS DISTINCT FROM OLD.reversal_of_id
       OR NEW.created_by IS DISTINCT FROM OLD.created_by THEN
        RAISE EXCEPTION
            'Stundenausgleich %: eine gebuchte Ausgleichsbuchung ist unveraenderlich — eine Fehlbuchung wird storniert, nicht umgeschrieben',
            OLD.id USING ERRCODE = 'raise_exception';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status
       AND NOT (OLD.status = 'GEBUCHT' AND NEW.status = 'STORNIERT') THEN
        RAISE EXCEPTION
            'Stundenausgleich %: Statuswechsel % -> % ist nicht zulaessig',
            OLD.id, OLD.status, NEW.status USING ERRCODE = 'raise_exception';
    END IF;
    RETURN NEW;
END;
$$;
"""


class Migration(migrations.Migration):

    # Haengt am aktuellen Blatt (0074, Parallel-Slice Fälligkeiten) — die Kette
    # bleibt linear, es entsteht kein zweites Blatt.
    dependencies = [
        ("db_core", "0074_faelligkeiten_models"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
