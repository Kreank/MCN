"""Der dritte Berichtszustand: ABGESCHLOSSEN.

## Warum

Ein Bericht kannte bisher zwei Zustände — `ENTWURF` und `UNTERZEICHNET`. Die
Abrechnung zieht ausschließlich aus unterzeichneten Berichten, mit der
Begründung „ein nicht abgenommener Nachweis ist keine Abrechnungsgrundlage".

Das trifft die Wirklichkeit dieses Betriebs nicht. Sascha am 2026-08-02:

    „Tatsache unterschreiben eher wenige Kunden … ca. 80 % unserer Berichte sind
    ohne Unterschrift. Die Vorgabe/Regel ist also eher kontraproduktiv für uns.“

Der Fehler lag nicht in der Regel, sondern im fehlenden Zustand: Ein fertiger
Bericht, den niemand unterschrieben hat, musste als `ENTWURF` liegenbleiben —
in einem Topf mit Berichten, an denen der Monteur noch tippt. Die Abrechnung
sperrte diesen Topf zu Recht aus und traf damit den Normalfall mit.

## Der Automat

    ENTWURF ──▶ ABGESCHLOSSEN ──▶ UNTERZEICHNET
       │                              ▲
       └──────────────────────────────┘

* `ENTWURF` — in Arbeit. Bleibt von der Abrechnung ausgeschlossen; daran ändert
  sich nichts, und das ist der Punkt: Ein halbfertiger Bericht gehört in keine
  Rechnung.
* `ABGESCHLOSSEN` — fertig, ohne Unterschrift. **Voll abrechenbar.** Der
  Normalfall dieses Betriebs.
* `UNTERZEICHNET` — fertig und vom Kunden bestätigt. Abrechenbar wie zuvor, nur
  zusätzlich beweiskräftig.

Die Unterschrift ist damit vom **Tor** zum **Merkmal** geworden. Wird ein Posten
später bestritten, ist weiterhin auf einen Blick erkennbar, welche Berichte
bestätigt sind — nur hängt die Rechnungsstellung nicht mehr daran.

**Kein Rückweg.** `ABGESCHLOSSEN → ENTWURF` ist gesperrt, ebenso jede Änderung
am Inhalt. Sonst ließe sich die Grundlage einer gestellten Rechnung nachträglich
verschieben. Wer sich vertan hat, schreibt einen weiteren Bericht — dieselbe
Logik wie bei Belegen (Korrektur nur als Folgedokument, B-21).

## Bestandsdaten

Bleiben unangetastet. Vorhandene Entwürfe sind Testdaten („vollkommen
irrelevant", Sascha 2026-08-02); sie pauschal auf ABGESCHLOSSEN zu heben würde
auch halbfertige mitnehmen und wäre eine Behauptung über Daten, die niemand
geprüft hat.
"""
from django.db import migrations

FORWARD_SQL = r"""
-- ---------------------------------------------------------------------------
-- 1. Der neue Zustand im CHECK
-- ---------------------------------------------------------------------------
ALTER TABLE workflow.site_report
    DROP CONSTRAINT IF EXISTS site_report_status_check;
ALTER TABLE workflow.site_report
    ADD CONSTRAINT site_report_status_check
    CHECK (status IN ('ENTWURF', 'ABGESCHLOSSEN', 'UNTERZEICHNET'));

COMMENT ON COLUMN workflow.site_report.status IS
    'ENTWURF (in Arbeit, nicht abrechenbar) | ABGESCHLOSSEN (fertig ohne '
    'Unterschrift, abrechenbar) | UNTERZEICHNET (fertig und vom Kunden '
    'bestaetigt). Die Unterschrift ist ein Merkmal, kein Tor — siehe 0144.';

-- ---------------------------------------------------------------------------
-- 2. Statusautomat + Versiegelung
-- ---------------------------------------------------------------------------
-- Ersetzt workflow.protect_site_report() aus 0054. Neu daran: ABGESCHLOSSEN
-- friert den Inhalt genauso ein wie UNTERZEICHNET (der Bericht ist ab hier
-- Abrechnungsgrundlage), laesst aber den einen erlaubten Weg offen — die
-- nachtraegliche Unterschrift.
CREATE OR REPLACE FUNCTION workflow.protect_site_report() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    -- ACHTUNG: `header_snapshot` gehoert mit hierher. Er kam mit 0132 dazu (der
    -- eingefrorene Briefkopf) und wurde dort in genau diese Funktion
    -- eingereiht. Wer sie ersetzt, ohne ihn mitzunehmen, entfernt den Schutz
    -- stillschweigend — beim ersten Versuch hier genau so passiert, gefunden
    -- hat es test_bericht_kopf_einfrieren.
    inhalt_geaendert boolean := (
           NEW.report_date     IS DISTINCT FROM OLD.report_date
        OR NEW.weather         IS DISTINCT FROM OLD.weather
        OR NEW.activity_text   IS DISTINCT FROM OLD.activity_text
        OR NEW.hours_worked    IS DISTINCT FROM OLD.hours_worked
        OR NEW.materials_note  IS DISTINCT FROM OLD.materials_note
        OR NEW.remarks         IS DISTINCT FROM OLD.remarks
        OR NEW.header_snapshot IS DISTINCT FROM OLD.header_snapshot
    );
BEGIN
    -- Unterzeichnet = Endzustand. Unveraendert gegenueber 0054.
    IF OLD.status = 'UNTERZEICHNET' THEN
        IF NEW.status IS DISTINCT FROM OLD.status
           OR inhalt_geaendert
           OR NEW.signed_by_name IS DISTINCT FROM OLD.signed_by_name
           OR NEW.signed_at IS DISTINCT FROM OLD.signed_at
           OR NEW.signature_file_id IS DISTINCT FROM OLD.signature_file_id THEN
            RAISE EXCEPTION
                'site_report %: unterzeichnete Berichte sind unveränderlich', OLD.id;
        END IF;
    END IF;

    -- Abgeschlossen: Inhalt fest, genau ein Ausgang (die Unterschrift).
    IF OLD.status = 'ABGESCHLOSSEN' THEN
        IF NEW.status NOT IN ('ABGESCHLOSSEN', 'UNTERZEICHNET') THEN
            RAISE EXCEPTION
                'site_report %: ein abgeschlossener Bericht wird nicht wieder '
                'geöffnet (%). Korrektur nur als weiterer Bericht',
                OLD.id, NEW.status;
        END IF;
        IF inhalt_geaendert THEN
            RAISE EXCEPTION
                'site_report %: abgeschlossene Berichte sind inhaltlich '
                'unveränderlich — sie sind Abrechnungsgrundlage', OLD.id;
        END IF;
    END IF;

    -- Kein Sprung rueckwaerts aus einem Endzustand heraus.
    IF OLD.status = 'ENTWURF' AND NEW.status NOT IN
       ('ENTWURF', 'ABGESCHLOSSEN', 'UNTERZEICHNET') THEN
        RAISE EXCEPTION 'site_report %: unbekannter Zielzustand %', OLD.id, NEW.status;
    END IF;

    -- Auftrags-/Autorenbezug ist immer unveraenderlich (wie 0054).
    IF NEW.work_order_id IS DISTINCT FROM OLD.work_order_id
       OR NEW.author_id IS DISTINCT FROM OLD.author_id THEN
        RAISE EXCEPTION
            'site_report %: Auftrag und Autor sind unveränderlich', OLD.id;
    END IF;
    RETURN NEW;
END;
$$;

-- ---------------------------------------------------------------------------
-- 3. Die Positionen versiegeln mit
-- ---------------------------------------------------------------------------
-- Ohne diesen Teil bliebe die Tuer offen, die Punkt 2 gerade zugemacht hat: Der
-- Kopf waere fest, die POSITIONEN aber weiter aenderbar (0080 prueft allein auf
-- UNTERZEICHNET). Dann liesse sich ein Bericht abschliessen, abrechnen — und
-- anschliessend die Menge umschreiben, auf der die Rechnung fusst. Der Wortlaut
-- der Meldung nennt den Status, damit im Zweifel klar ist, welcher Zustand
-- gerade sperrt.
CREATE OR REPLACE FUNCTION workflow.protect_site_report_lines() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_status text;
BEGIN
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        SELECT status INTO v_status
        FROM workflow.site_report WHERE id = NEW.site_report_id FOR SHARE;
        IF v_status IN ('ABGESCHLOSSEN', 'UNTERZEICHNET') THEN
            RAISE EXCEPTION
                'site_report %: Der Bericht ist % — seine Positionen können nicht mehr angelegt oder geändert werden.',
                NEW.site_report_id, lower(v_status);
        END IF;
    END IF;

    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        SELECT status INTO v_status
        FROM workflow.site_report WHERE id = OLD.site_report_id FOR SHARE;
        IF v_status IN ('ABGESCHLOSSEN', 'UNTERZEICHNET') THEN
            RAISE EXCEPTION
                'site_report %: Der Bericht ist % — seine Positionen können nicht mehr entfernt werden.',
                OLD.site_report_id, lower(v_status);
        END IF;
    END IF;

    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;
"""

REVERSE_SQL = r"""
-- Zurueck auf den Stand von 0054/0080. Berichte im Zustand ABGESCHLOSSEN
-- muessten vorher von Hand einsortiert werden — der CHECK weist sie sonst ab.
CREATE OR REPLACE FUNCTION workflow.protect_site_report_lines() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_status text;
BEGIN
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        SELECT status INTO v_status
        FROM workflow.site_report WHERE id = NEW.site_report_id FOR SHARE;
        IF v_status = 'UNTERZEICHNET' THEN
            RAISE EXCEPTION
                'site_report %: Der Bericht ist unterzeichnet — seine Positionen können nicht mehr angelegt oder geändert werden.',
                NEW.site_report_id;
        END IF;
    END IF;

    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        SELECT status INTO v_status
        FROM workflow.site_report WHERE id = OLD.site_report_id FOR SHARE;
        IF v_status = 'UNTERZEICHNET' THEN
            RAISE EXCEPTION
                'site_report %: Der Bericht ist unterzeichnet — seine Positionen können nicht mehr entfernt werden.',
                OLD.site_report_id;
        END IF;
    END IF;

    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;
CREATE OR REPLACE FUNCTION workflow.protect_site_report() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status = 'UNTERZEICHNET' THEN
        IF NEW.status IS DISTINCT FROM OLD.status
           OR NEW.report_date IS DISTINCT FROM OLD.report_date
           OR NEW.weather IS DISTINCT FROM OLD.weather
           OR NEW.activity_text IS DISTINCT FROM OLD.activity_text
           OR NEW.hours_worked IS DISTINCT FROM OLD.hours_worked
           OR NEW.materials_note IS DISTINCT FROM OLD.materials_note
           OR NEW.remarks IS DISTINCT FROM OLD.remarks
           OR NEW.signed_by_name IS DISTINCT FROM OLD.signed_by_name
           OR NEW.signed_at IS DISTINCT FROM OLD.signed_at
           OR NEW.signature_file_id IS DISTINCT FROM OLD.signature_file_id THEN
            RAISE EXCEPTION
                'site_report %: unterzeichnete Berichte sind unveränderlich', OLD.id;
        END IF;
    END IF;
    IF NEW.work_order_id IS DISTINCT FROM OLD.work_order_id
       OR NEW.author_id IS DISTINCT FROM OLD.author_id THEN
        RAISE EXCEPTION
            'site_report %: Auftrag und Autor sind unveränderlich', OLD.id;
    END IF;
    RETURN NEW;
END;
$$;

ALTER TABLE workflow.site_report
    DROP CONSTRAINT IF EXISTS site_report_status_check;
ALTER TABLE workflow.site_report
    ADD CONSTRAINT site_report_status_check
    CHECK (status IN ('ENTWURF', 'UNTERZEICHNET'));
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0143_merge_azubi_und_material"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
