"""Härtung der Abrechnungsbindung (0084): der Entwurf bleibt bearbeitbar.

Zwei Korrekturen an den Triggern aus Migration 0084. Kein neues Schema, keine
neue Tabelle — nur die Grenzen dort ziehen, wo sie hingehören.

## 1. `protect_billed_invoice_lines`: sperren, was gebunden ist — nicht den Beleg

0084 sperrte bei **irgendeiner** aktiven Bindung an der Rechnung **jede**
Positionsänderung, auch INSERT. Der Effekt war ein praktisch unveränderlicher
Entwurf: Eine Anfahrtspauschale, eine Rabattzeile oder ein Tippfehler in einer
frisch hinzugefügten Zeile ließen sich nicht mehr anbringen. Der einzige Ausweg
war `abrechnung.bindungen_loesen` — die **Notbremse**, die alle gebundenen
Positionen verwirft. Bei einem Beleg aus 30 Berichtspositionen hieße das: von
vorn. Eine Notbremse, die zum Normalweg wird, ist keine Notbremse mehr; sie wird
zur Gewohnheit, und dann fällt niemandem mehr auf, wenn sie eine Sperre löst.

Der Schutz greift ab hier nur noch dort, wo er etwas schützt: **UPDATE und DELETE
einer gebundenen Zeile**

    EXISTS (SELECT 1 FROM invoicing.billing_link
             WHERE invoice_line_id = OLD.id AND released_at IS NULL)

Das **INSERT einer neuen Zeile** ist erlaubt: Eine Zeile, die es noch nicht gibt,
kann keine Bindung tragen — sie gefährdet die Doppelabrechnungssperre nicht.

Die Sperre selbst bleibt vollständig scharf:

* Der Beleg-Editor (`beleg.update_invoice`) ersetzt den **ganzen** Positionssatz
  per Delete+Insert. Das DELETE trifft die gebundenen Zeilen → weiterhin 422.
  Genau das ist der Fall, um den es 0084 ging.
* Eine gebundene Zeile umzuschreiben (Menge, Preis) bleibt verboten: Sie ist der
  Beleg **dieser** Berichtsposition/Zeitbuchung.
* `bindungen_loesen` funktioniert unverändert: Es setzt erst `released_at` und
  `invoice_line_id := NULL` — danach ist die Zeile nicht mehr gebunden und darf
  gelöscht werden.

## 2. `release_billing_links_on_cancel`: auch beim INSERT

0084 hängte den Trigger an `AFTER UPDATE OF status`. Das ist für den heutigen
Pfad richtig (`beleg._create_credit` legt den STORNO als ENTWURF an,
`publish_invoice` flippt den Status per UPDATE). Ein künftiger Pfad jedoch, der
einen STORNO **direkt** mit `status = 'VEROEFFENTLICHT'` INSERTet — ein Import,
eine Migration, ein KI-Agent —, umginge den Trigger **stillschweigend**: Der
Storno stünde da, die Bindungen blieben aktiv, und die stornierte Leistung wäre
für immer verbrannt (die veröffentlichte Rechnungsposition ist unveränderlich,
B-21 — es gäbe keinen Weg zurück).

Ein Trigger, der von der Reihenfolge der Schreibvorgänge des Aufrufers abhängt,
ist keine physische Garantie. Also: derselbe Rumpf, `AFTER INSERT OR UPDATE OF
status`. `OLD` ist beim INSERT nicht zugewiesen — der Zugriff darauf würde
scheitern; deshalb entscheidet `TG_OP` **vor** dem Vergleich (PL/pgSQL garantiert
keine Kurzschlussauswertung in SQL-Ausdrücken).
"""
from django.db import migrations

FORWARD_SQL = r"""
-- ---------------------------------------------------------------------------
-- 1) Nur die GEBUNDENE Zeile ist gesperrt. Neue Zeilen darf der Entwurf tragen.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION invoicing.protect_billed_invoice_lines()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    -- Nur UPDATE/DELETE (der Trigger ist nicht mehr fürs INSERT registriert):
    -- OLD ist hier stets zugewiesen.
    IF EXISTS (
        SELECT 1 FROM invoicing.billing_link
         WHERE invoice_line_id = OLD.id AND released_at IS NULL
    ) THEN
        RAISE EXCEPTION
            'Rechnungsposition % (Beleg %): Sie ist an eine Berichtsposition, eine '
            'Zeitbuchung oder eine Angebotsposition gebunden '
            '(Doppelabrechnungssperre) und kann weder geändert noch gelöscht '
            'werden. Bindungen lösen oder Beleg stornieren.',
            OLD.position_number, OLD.invoice_id
            USING ERRCODE = 'raise_exception';
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;

DROP TRIGGER IF EXISTS trg_invoice_line_billed ON invoicing.invoice_line;
CREATE TRIGGER trg_invoice_line_billed
    BEFORE UPDATE OR DELETE ON invoicing.invoice_line
    FOR EACH ROW EXECUTE FUNCTION invoicing.protect_billed_invoice_lines();

COMMENT ON FUNCTION invoicing.protect_billed_invoice_lines() IS
    'Eine GEBUNDENE Rechnungsposition ist unveraenderlich (UPDATE/DELETE -> 422). '
    'Das INSERT einer neuen, ungebundenen Zeile bleibt erlaubt: Sie traegt keine '
    'Bindung und gefaehrdet die Doppelabrechnungssperre nicht. Der Beleg-Editor '
    '(update_invoice) scheitert weiterhin, weil er den ganzen Positionssatz per '
    'DELETE ersetzt.';

-- ---------------------------------------------------------------------------
-- 2) Der Storno loest die Bindungen — auch, wenn er direkt veroeffentlicht
--    INSERTet wird. Ein Trigger, der von der Schreibreihenfolge des Aufrufers
--    abhaengt, ist keine physische Garantie.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION invoicing.release_billing_links_on_cancel()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    -- Wird der Beleg JETZT veroeffentlicht? Beim INSERT immer; beim UPDATE nur,
    -- wenn er es vorher nicht war. TG_OP entscheidet VOR dem OLD-Zugriff: beim
    -- INSERT ist OLD nicht zugewiesen, und PL/pgSQL wertet SQL-Ausdruecke nicht
    -- garantiert von links nach rechts aus.
    v_wird_veroeffentlicht boolean;
BEGIN
    IF TG_OP = 'INSERT' THEN
        v_wird_veroeffentlicht := true;
    ELSE
        v_wird_veroeffentlicht := OLD.status IS DISTINCT FROM 'VEROEFFENTLICHT';
    END IF;

    IF NEW.invoice_type = 'STORNO'
       AND NEW.status = 'VEROEFFENTLICHT'
       AND v_wird_veroeffentlicht
       AND NEW.reference_invoice_id IS NOT NULL THEN
        UPDATE invoicing.billing_link
           SET released_at = now(),
               released_reason =
                   'Storno ' || coalesce(NEW.invoice_number, NEW.id::text)
         WHERE invoice_id = NEW.reference_invoice_id
           AND released_at IS NULL;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_invoice_release_billing_links ON invoicing.invoice;
CREATE TRIGGER trg_invoice_release_billing_links
    AFTER INSERT OR UPDATE OF status ON invoicing.invoice
    FOR EACH ROW EXECUTE FUNCTION invoicing.release_billing_links_on_cancel();
"""

REVERSE_SQL = r"""
-- Zurueck auf den Stand von 0084.
CREATE OR REPLACE FUNCTION invoicing.protect_billed_invoice_lines()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_invoice uuid := CASE WHEN TG_OP = 'DELETE'
                           THEN OLD.invoice_id ELSE NEW.invoice_id END;
BEGIN
    IF EXISTS (
        SELECT 1 FROM invoicing.billing_link
         WHERE invoice_id = v_invoice AND released_at IS NULL
    ) THEN
        RAISE EXCEPTION
            'Rechnung %: Die Positionen sind an Berichtspositionen, Zeitbuchungen '
            'oder Angebotspositionen gebunden (Doppelabrechnungssperre) und können '
            'nicht geändert werden. Bindungen lösen oder Beleg stornieren.',
            v_invoice
            USING ERRCODE = 'raise_exception';
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;

DROP TRIGGER IF EXISTS trg_invoice_line_billed ON invoicing.invoice_line;
CREATE TRIGGER trg_invoice_line_billed
    BEFORE INSERT OR UPDATE OR DELETE ON invoicing.invoice_line
    FOR EACH ROW EXECUTE FUNCTION invoicing.protect_billed_invoice_lines();

CREATE OR REPLACE FUNCTION invoicing.release_billing_links_on_cancel()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.invoice_type = 'STORNO'
       AND NEW.status = 'VEROEFFENTLICHT'
       AND OLD.status IS DISTINCT FROM 'VEROEFFENTLICHT'
       AND NEW.reference_invoice_id IS NOT NULL THEN
        UPDATE invoicing.billing_link
           SET released_at = now(),
               released_reason =
                   'Storno ' || coalesce(NEW.invoice_number, NEW.id::text)
         WHERE invoice_id = NEW.reference_invoice_id
           AND released_at IS NULL;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_invoice_release_billing_links ON invoicing.invoice;
CREATE TRIGGER trg_invoice_release_billing_links
    AFTER UPDATE OF status ON invoicing.invoice
    FOR EACH ROW EXECUTE FUNCTION invoicing.release_billing_links_on_cancel();
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0085_billinglink"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
