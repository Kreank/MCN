"""Ein Rechnungsentwurf lässt sich verwerfen — ohne einen Schutz anzufassen.

## Warum so und nicht per DELETE

Sascha am 2026-08-02: *„Ja wir nehmen das zweite. Aber es soll auch nur mit
Entwürfen gehen. Erstellte Rechnungen können nur wie gehabt über Storno
berichtigt werden."*

Der zuvor versuchte Löschweg ist verworfen worden (siehe `ENTSCHEIDUNGEN.md`):
Er hätte den Löschschutz auf `invoicing.billing_link` lockern müssen, womit
**jede einzelne** Bindung angreifbar geworden wäre — wer eine entfernt, gibt die
Quelle wieder frei und hängt die Doppelabrechnungssperre spurlos aus.

Das Verwerfen fasst **keinen einzigen Schutztrigger an**. Es ist ein
Statuswechsel; die Bindungen werden mit der längst gebauten Mechanik **gelöst**
(`released_at` + Grund), genau wie beim Storno. Die gelöste Bindung bleibt als
Nachweis stehen, die Quelle ist wieder abrechenbar.

## Der Automat

    ENTWURF ──▶ VERWORFEN   (Sackgasse)
       └──────▶ VEROEFFENTLICHT ──▶ (Storno, B-21/B-30)

* Aus `VERWORFEN` führt kein Weg zurück. Ein wiederbelebter Entwurf könnte
  Quellen ein zweites Mal binden, die inzwischen anderswo abgerechnet sind.
* `VEROEFFENTLICHT` bleibt unberührt: Ein gestellter Beleg wird storniert, nie
  verworfen. Genau das hat der Auftraggeber verlangt.

## Was NICHT hier steht

Das Herausfiltern aus Listen — das ist Sache der Abfragen, nicht des Schemas.
Ein verworfener Entwurf bleibt vollständig lesbar; er ist nur nirgends mehr im
Weg.
"""
from django.db import migrations

FORWARD_SQL = r"""
-- ---------------------------------------------------------------------------
-- 1. Der neue Zustand
-- ---------------------------------------------------------------------------
ALTER TABLE invoicing.invoice
    DROP CONSTRAINT IF EXISTS invoice_status_check;
ALTER TABLE invoicing.invoice
    ADD CONSTRAINT invoice_status_check
    CHECK (status IN ('ENTWURF', 'VEROEFFENTLICHT', 'VERWORFEN'));

COMMENT ON COLUMN invoicing.invoice.status IS
    'ENTWURF | VEROEFFENTLICHT (gestellt, nur noch Storno) | VERWORFEN '
    '(Entwurf zurueckgezogen, aus Listen ausgeblendet, Bindungen geloest). '
    'Siehe 0147.';

-- ---------------------------------------------------------------------------
-- 2. Statusautomat: nur ENTWURF -> VERWORFEN, und von dort nirgendwohin
-- ---------------------------------------------------------------------------
CREATE FUNCTION invoicing.guard_invoice_verworfen() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status = 'VERWORFEN' AND OLD.status <> 'ENTWURF' THEN
        RAISE EXCEPTION
            'invoice %: nur ein Entwurf laesst sich verwerfen — dieser ist % '
            '(ein gestellter Beleg wird storniert, B-21/B-30)',
            OLD.id, lower(OLD.status);
    END IF;

    IF OLD.status = 'VERWORFEN' AND NEW.status <> 'VERWORFEN' THEN
        RAISE EXCEPTION
            'invoice %: ein verworfener Entwurf wird nicht wiederbelebt — er '
            'koennte Quellen binden, die inzwischen anderswo abgerechnet sind',
            OLD.id;
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_invoice_verworfen_guard
    BEFORE UPDATE OF status ON invoicing.invoice
    FOR EACH ROW EXECUTE FUNCTION invoicing.guard_invoice_verworfen();
"""

REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS trg_invoice_verworfen_guard ON invoicing.invoice;
DROP FUNCTION IF EXISTS invoicing.guard_invoice_verworfen();

-- Verworfene Entwuerfe muessten vorher von Hand einsortiert werden — der CHECK
-- weist sie sonst ab.
ALTER TABLE invoicing.invoice
    DROP CONSTRAINT IF EXISTS invoice_status_check;
ALTER TABLE invoicing.invoice
    ADD CONSTRAINT invoice_status_check
    CHECK (status IN ('ENTWURF', 'VEROEFFENTLICHT'));
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0146_angebotsentwurf_loeschbar"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
