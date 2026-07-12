"""Baustellenberichte (workflow.site_report) + Foto-Anhang-Ziel an content.file_link.

Hand-SQL nach db/README.md: neue Fachtabelle als RunSQL mit Schutzstandard
(updated_at/Audit/No-Delete/No-Truncate/REVOKE). Muster: 0016/0019/0023.

Fachquelle: Handwerk-Baustellenbericht (Tätigkeitsnachweis vor Ort). Ein Bericht
gehört zu einem Auftrag (die Baustelle) und optional zu einem Einsatz. Er trägt
Datum, Wetter, Tätigkeiten, Arbeitsstunden und Bemerkungen; Fotos hängen als
content.file_link (site_report_id) daran; die Abnahme erfolgt über eine
Kundenunterschrift (als PNG im Objektspeicher, referenziert über
signature_file_id), die den Bericht besiegelt.

Grundsatzentscheidungen:

1. **Anker Auftrag (work_order), Einsatz optional.** Jeder Bericht gehört zu genau
   einem Auftrag (die Baustelle/Liegenschaft hängt daran); der Einsatzbezug ist
   optional (ein Bericht kann einen konkreten Vor-Ort-Termin dokumentieren oder
   den Auftrag allgemein).

2. **Signatur besiegelt (Statusautomat ENTWURF → UNTERZEICHNET).** Ein
   unterzeichneter Bericht ist unveränderlich (Trigger `protect_site_report`) —
   die Kundenunterschrift ist eine Abnahme, die nicht nachträglich verfälscht
   werden darf. Die Kohärenz „unterzeichnet ⇒ Unterschrift + Name + Zeitpunkt
   gesetzt" erzwingt ein CHECK.

3. **Foto-Ziel an content.file_link.** Ein neues nullbares `site_report_id` +
   FK; der `num_nonnulls(... ) = 1`-Exklusiv-CHECK wird um dieses Ziel erweitert,
   damit ein Foto weiterhin an GENAU EIN Objekt hängt.
"""
from django.db import migrations

FORWARD_SQL = r"""
CREATE TABLE workflow.site_report (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    work_order_id      uuid NOT NULL REFERENCES workflow.work_order (id),
    service_job_id     uuid NULL REFERENCES workflow.service_job (id),
    report_date        date NOT NULL,
    author_id          uuid NOT NULL REFERENCES security.app_user (id),
    weather            text NULL,
    activity_text      text NOT NULL CHECK (btrim(activity_text) <> ''),
    hours_worked       numeric(6,2) NULL CHECK (hours_worked IS NULL OR hours_worked >= 0),
    materials_note     text NULL,
    remarks            text NULL,
    status             text NOT NULL DEFAULT 'ENTWURF'
                       CHECK (status IN ('ENTWURF', 'UNTERZEICHNET')),
    -- Abnahme durch den Kunden (Unterschrift als content.file, PNG)
    signed_by_name     text NULL,
    signed_at          timestamptz NULL,
    signature_file_id  uuid NULL REFERENCES content.file (id),
    -- Kohärenz: unterzeichnet ⇒ Unterschrift + Name + Zeitpunkt vollständig
    CONSTRAINT site_report_signed_complete CHECK (
        status <> 'UNTERZEICHNET'
        OR (signed_by_name IS NOT NULL AND btrim(signed_by_name) <> ''
            AND signed_at IS NOT NULL AND signature_file_id IS NOT NULL)
    ),
    version            integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_site_report_work_order ON workflow.site_report (work_order_id);
CREATE INDEX idx_site_report_service_job ON workflow.site_report (service_job_id);

CREATE TRIGGER trg_site_report_updated_at
    BEFORE UPDATE ON workflow.site_report
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_site_report_audit
    AFTER UPDATE ON workflow.site_report
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_site_report_no_delete
    BEFORE DELETE ON workflow.site_report
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_site_report_no_truncate
    BEFORE TRUNCATE ON workflow.site_report
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON workflow.site_report FROM PUBLIC;

-- Ein unterzeichneter Bericht ist eingefroren: keine inhaltliche Änderung mehr
-- (nur updated_at/version durch die Trigger). Auftrag/Einsatz sind ohnehin fix.
CREATE FUNCTION workflow.protect_site_report() RETURNS trigger
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
    -- Auftrags-/Autorenbezug ist immer unveränderlich.
    IF NEW.work_order_id IS DISTINCT FROM OLD.work_order_id
       OR NEW.author_id IS DISTINCT FROM OLD.author_id THEN
        RAISE EXCEPTION 'site_report %: Auftrag/Autor sind unveränderlich', OLD.id;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_site_report_protect
    BEFORE UPDATE ON workflow.site_report
    FOR EACH ROW EXECUTE FUNCTION workflow.protect_site_report();

-- ---------------------------------------------------------------------------
-- Foto-Anhang-Ziel an content.file_link ergänzen (genau-ein-Ziel bleibt gewahrt)
-- ---------------------------------------------------------------------------
ALTER TABLE content.file_link
    ADD COLUMN site_report_id uuid NULL REFERENCES workflow.site_report (id);

ALTER TABLE content.file_link DROP CONSTRAINT file_link_check;
ALTER TABLE content.file_link ADD CONSTRAINT file_link_check CHECK (
    num_nonnulls(service_case_id, work_order_id, service_job_id, property_id,
                 unit_id, asset_id, quote_id, invoice_id, party_id,
                 communication_id, project_id, article_id, site_report_id) = 1
);
"""

REVERSE_SQL = r"""
ALTER TABLE content.file_link DROP CONSTRAINT file_link_check;
ALTER TABLE content.file_link ADD CONSTRAINT file_link_check CHECK (
    num_nonnulls(service_case_id, work_order_id, service_job_id, property_id,
                 unit_id, asset_id, quote_id, invoice_id, party_id,
                 communication_id, project_id, article_id) = 1
);
ALTER TABLE content.file_link DROP COLUMN site_report_id;
DROP FUNCTION IF EXISTS workflow.protect_site_report() CASCADE;
DROP TABLE IF EXISTS workflow.site_report;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0053_suppliercredential"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
