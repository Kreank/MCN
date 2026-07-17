"""Beleg am Vorgang verankern: invoicing.quote/invoice bekommen service_case_id.

Ein Angebot/eine Rechnung entstand bisher am **Auftrag** (work_order) und am
**Projekt**, aber nie direkt am **Vorgang** (service_case). Wird ein Beleg aus
einem Vorgang heraus angelegt, ging der Bezug verloren; und die Aufstufung
Vorgang→Projekt (`promote_service_case_to_project`) konnte die Belege nicht
mitziehen, weil ihnen die Spalte fehlte. Diese Migration ergänzt den fehlenden
Bezug — analog zum bestehenden Auftragsbezug (Migration 0018):

* `workflow.service_case` bekommt `UNIQUE (id, property_id)` — das Ziel für die
  zusammengesetzten Beleg-FKs (Muster wie `workflow.work_order`, 0013).
* `invoicing.quote` und `invoicing.invoice` bekommen je
  `service_case_id uuid NULL REFERENCES workflow.service_case (id)` **und** den
  zusammengesetzten FK `(service_case_id, property_id)` gegen den Vorgang —
  „Beleg und Vorgang gehören zur selben Liegenschaft", exakt die Regel P3-12,
  die 0018 schon für den Auftrag zieht. Dazu ein partieller Index.

Warum Hand-SQL als **Django-RunSQL** und NICHT als db/migrations/0044.sql:
`db_core/migrations/0001_baseline.py` liest zur Laufzeit ALLE `db/migrations/*.sql`
per glob und führt sie aus. Eine neue 0044.sql würde auf einer frischen (Test-)DB
doppelt angewandt (einmal durch die Baseline, einmal durch diese Migration) und
bräche den Aufbau — genau die Grenze, die schon 0042 dokumentiert.
`db/migrations/` ist der eingefrorene Baseline-Stand 0001–0043; jede spätere
Fachschema-Änderung lebt ausschließlich hier als RunSQL.

Schutzstandard: reine ADD-COLUMN-/Constraint-Erweiterungen. Die No-Delete-/Audit-/
No-Truncate-Trigger von quote/invoice (0020) und service_case (0015) bestehen
bereits und decken die neue Spalte mit ab.
"""
from django.db import migrations

FORWARD_SQL = r"""
-- ---------------------------------------------------------------------------
-- workflow.service_case — Ziel für die zusammengesetzten Beleg-FKs
-- (Liegenschaftskonsistenz, P3-12; Muster wie work_order in 0013).
-- ---------------------------------------------------------------------------
ALTER TABLE workflow.service_case
    ADD CONSTRAINT uq_service_case_id_property UNIQUE (id, property_id);

-- ---------------------------------------------------------------------------
-- invoicing.quote — Vorgangsbezug (analog work_order_id in 0018)
-- ---------------------------------------------------------------------------
ALTER TABLE invoicing.quote
    ADD COLUMN service_case_id uuid NULL REFERENCES workflow.service_case (id),
    -- P3-12: Beleg und Vorgang gehören zur selben Liegenschaft
    ADD CONSTRAINT quote_service_case_property_fk
        FOREIGN KEY (service_case_id, property_id)
        REFERENCES workflow.service_case (id, property_id);

CREATE INDEX idx_quote_service_case ON invoicing.quote (service_case_id)
    WHERE service_case_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- invoicing.invoice — identisch
-- ---------------------------------------------------------------------------
ALTER TABLE invoicing.invoice
    ADD COLUMN service_case_id uuid NULL REFERENCES workflow.service_case (id),
    ADD CONSTRAINT invoice_service_case_property_fk
        FOREIGN KEY (service_case_id, property_id)
        REFERENCES workflow.service_case (id, property_id);

CREATE INDEX idx_invoice_service_case ON invoicing.invoice (service_case_id)
    WHERE service_case_id IS NOT NULL;
"""

# Rückwärtsstrategie (nur solange keine Fachdaten am neuen Bezug entstanden sind):
# Spalten fallen lassen — das räumt die Einzel-/Verbund-FKs und den Index gleich mit —
# und die Verbund-Unique am Vorgang entfernen.
REVERSE_SQL = r"""
ALTER TABLE invoicing.invoice DROP COLUMN IF EXISTS service_case_id;
ALTER TABLE invoicing.quote DROP COLUMN IF EXISTS service_case_id;
ALTER TABLE workflow.service_case DROP CONSTRAINT IF EXISTS uq_service_case_id_property;
"""


class Migration(migrations.Migration):
    dependencies = [("db_core", "0112_anlagenart_shk_codeliste")]
    operations = [migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL)]
