"""Audit-Spur auf accounting.receipt_line nachziehen (Schutzstandard).

Befund aus dem Review von 0031: `receipt_line` erhielt zwar `updated_at`,
`protect_receipt_lines` und `no_truncate`, aber — anders als die etablierten
Beleg-Positionstabellen `invoicing.invoice_line`/`quote_line` (db/migrations/
0020_invoicing_schutz.sql) — KEINEN Audit-Trigger auf UPDATE und DELETE.

Das ist GoBD-relevant: `belegerfassung.update_receipt` ersetzt die Positionen
eines Entwurfs durch DELETE + Neuanlage. Ohne Audit-Trigger blieben diese
Änderungen an einem Beleg ohne Spur. Der Kopf `accounting.receipt` ist korrekt
auditiert (trg_receipt_audit) und per no_delete gesperrt; nur die Kindtabelle
fiel durch.

Positionsänderungen sind im Entwurf (ERFASST/GEPRUEFT) fachlich zulässig — der
Freeze-Trigger greift ab FREIGEGEBEN. Zulässig heißt aber nicht spurlos: genau
dafür sind audit_row_update/audit_row_delete da.
"""
from django.db import migrations

FORWARD_SQL = r"""
CREATE TRIGGER trg_receipt_line_audit AFTER UPDATE ON accounting.receipt_line
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_receipt_line_delete_audit AFTER DELETE ON accounting.receipt_line
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_delete();
"""

REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS trg_receipt_line_delete_audit ON accounting.receipt_line;
DROP TRIGGER IF EXISTS trg_receipt_line_audit ON accounting.receipt_line;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0034_costcenter_ledgeraccount_receipt_receiptline"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
