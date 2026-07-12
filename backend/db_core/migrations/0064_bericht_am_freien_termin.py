"""Baustellenbericht am freien Termin (workflow.site_report).

Fachlicher Hintergrund
----------------------
Seit Migration 0062 gibt es den **freien Termin**: ein Einsatz ohne Auftrag
(Begehung/Besichtigung/Beratung — die Tätigkeit, die *vor* der Beauftragung
stattfindet). Genau dort entsteht das **Begehungsprotokoll**: Zustand vor Ort,
Fotos, Kundenunterschrift. `site_report.work_order_id` war NOT NULL (0054) — der
Bericht am freien Termin war damit physisch unmöglich. Diese Migration öffnet ihn.

Vier Entscheidungen
-------------------
**1. `work_order_id` wird NULLABLE, aber der Bericht braucht einen Anker.**
Ein Bericht darf nicht im Leeren hängen (er ist ein Nachweis, kein Notizzettel):
`CHECK (work_order_id IS NOT NULL OR service_job_id IS NOT NULL)`. Entweder er
hängt am Auftrag (Baustelle) oder mindestens am Einsatz (Termin) — oder an
beidem. Ein Bericht ohne jeden Bezug ist nicht speicherbar.

**2. Konsistenz Einsatz ↔ Auftrag als TRIGGER, nicht als zusammengesetzter FK.**
Bisher galt implizit „ist der Einsatz gesetzt, gehört er zu genau diesem Auftrag"
(Service-Prüfung). Das wird jetzt physisch:

    Einsatz gesetzt  ⇒  site_report.work_order_id  IS NOT DISTINCT FROM
                        service_job.work_order_id

Ein zusammengesetzter FK `(service_job_id, work_order_id) → service_job(id,
work_order_id)` — das Muster aus 0062 — kann das **nicht** leisten:
  * MATCH SIMPLE (Default) prüft nur, wenn **alle** FK-Spalten belegt sind. Genau
    der neue Fall (Bericht am freien Termin: service_job_id gesetzt,
    work_order_id NULL) liefe damit **ungeprüft** durch — und ebenso der
    gefährlichere Fall „Bericht am auftragsgebundenen Einsatz, aber ohne dessen
    Auftrag": er wäre über die Auftragsliste unsichtbar, obwohl er zur Baustelle
    gehört.
  * MATCH FULL verbietet das Mischen von NULL und NOT NULL in einem
    mehrspaltigen FK — der Bericht am freien Termin wäre damit wieder gesperrt,
    also das genaue Gegenteil des Slices.
Der Trigger deckt beide Richtungen ab (Einsatz eines anderen Auftrags **und**
fehlende/überzählige Auftragsangabe) und ist die **einzige** Stelle der Regel —
kein zweites, halb greifendes Mechanismus daneben.

In 0062 war der FK dagegen richtig: dort war die zu prüfende Kombination genau
die „beide gesetzt"-Zelle, und die NULL-Fälle sollten bewusst offen bleiben.

**3. KEIN eigenes `property_id` am Bericht.** Naheliegend, aber falsch: Die
Liegenschaft ist am Anker bereits eindeutig hinterlegt — am Einsatz
(`service_job.property_id`, seit 0062) bzw. am Auftrag (`work_order.property_id`,
NOT NULL). Eine dritte Kopie am Bericht wäre eine zweite Quelle der Wahrheit, die
mit einem weiteren zusammengesetzten FK gegen Anker-Auftrag UND Anker-Einsatz
abgeglichen werden müsste — Aufwand ohne Zugewinn. Ist die Liegenschaft am freien
Termin nicht gepflegt (die Begehung eines Objekts, das noch nicht im System
steht), trägt der Bericht sie ebenfalls nicht: das ist die ehrliche Aussage, kein
Verlust. Die Ableitung ist `report → service_job.property → work_order.property`.

**4. Der Anker ist unveränderlich.** `work_order_id` war schon bisher fix
(`protect_site_report`). Neu: Ist der Bericht **allein** am Einsatz verankert
(work_order_id IS NULL), ist auch `service_job_id` fix. Sonst ließe sich das
Protokoll einer Begehung nachträglich auf einen anderen Termin umhängen — eine
Verfälschung des Nachweises; und ein Leeren des Feldes verletzte den Anker-CHECK
(SQLSTATE 23514 → 500 statt einer Fachmeldung). Für auftragsgebundene Berichte
bleibt der Einsatzbezug im ENTWURF änderbar wie bisher — die neue
Konsistenzprüfung hält ihn innerhalb desselben Auftrags.

Trigger-Inventur (0054): `protect_site_report` ist der einzige Trigger, der
`work_order_id` liest, und er vergleicht ausschließlich (`IS DISTINCT FROM`) —
NULL-sicher. Es gibt auf `site_report` **kein** Tor gegen den Auftragsstatus
(Berichte dürfen auch nach der Abrechnung noch als Nachweis existieren), also
auch nichts, was bei NULL ins Leere liefe. Der Rest (updated_at, Audit,
No-Delete, No-Truncate) ist spaltenunabhängig.

Rückwärts: nur solange kein Bericht ohne Auftrag existiert (sonst verlöre
`SET NOT NULL` Fachdaten).
"""
from django.db import migrations

FORWARD_SQL = r"""
-- ---------------------------------------------------------------------------
-- 1. Auftragsbezug wird optional; der Anker bleibt Pflicht
-- ---------------------------------------------------------------------------
ALTER TABLE workflow.site_report
    ALTER COLUMN work_order_id DROP NOT NULL;

ALTER TABLE workflow.site_report
    ADD CONSTRAINT site_report_braucht_anker
        CHECK (work_order_id IS NOT NULL OR service_job_id IS NOT NULL);

COMMENT ON COLUMN workflow.site_report.work_order_id IS
    'Auftrag des Berichts. NULL = Bericht am freien Termin (Begehungsprotokoll); dann ist service_job_id der Anker.';

-- ---------------------------------------------------------------------------
-- 2. Konsistenz: der Bericht trägt den Auftrag SEINES Einsatzes — oder keinen
-- ---------------------------------------------------------------------------
CREATE FUNCTION workflow.check_site_report_anchor() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_job_order uuid;
    v_found     boolean;
BEGIN
    IF NEW.service_job_id IS NULL THEN
        RETURN NEW;   -- reiner Auftragsbericht: der Anker-CHECK genügt
    END IF;

    SELECT j.work_order_id, true INTO v_job_order, v_found
    FROM workflow.service_job j
    WHERE j.id = NEW.service_job_id
    FOR SHARE;

    IF NOT COALESCE(v_found, false) THEN
        -- Der FK auf service_job feuert erst nach diesem BEFORE-Trigger.
        RAISE EXCEPTION 'site_report: Der angegebene Einsatz % existiert nicht.',
            NEW.service_job_id;
    END IF;

    IF NEW.work_order_id IS DISTINCT FROM v_job_order THEN
        IF v_job_order IS NULL THEN
            RAISE EXCEPTION
                'site_report: Der Einsatz % ist ein freier Termin (ohne Auftrag) — ein Bericht daran darf keinen Auftrag tragen.',
                NEW.service_job_id;
        ELSE
            RAISE EXCEPTION
                'site_report: Der Einsatz % gehört zum Auftrag %, nicht zu %.',
                NEW.service_job_id, v_job_order, NEW.work_order_id;
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_site_report_anchor
    BEFORE INSERT OR UPDATE ON workflow.site_report
    FOR EACH ROW EXECUTE FUNCTION workflow.check_site_report_anchor();

-- ---------------------------------------------------------------------------
-- 3. Der Anker ist unveränderlich (Erweiterung von protect_site_report)
-- ---------------------------------------------------------------------------
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
           OR NEW.service_job_id IS DISTINCT FROM OLD.service_job_id
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
    -- Hängt der Bericht ALLEIN am Einsatz (freier Termin), ist der Einsatz sein
    -- einziger Anker: ein Umhängen verfälschte den Nachweis, ein Leeren risse den
    -- Anker-CHECK auf.
    IF OLD.work_order_id IS NULL
       AND NEW.service_job_id IS DISTINCT FROM OLD.service_job_id THEN
        RAISE EXCEPTION
            'site_report %: Der Bericht hängt am freien Termin — sein Einsatzbezug ist unveränderlich.',
            OLD.id;
    END IF;
    RETURN NEW;
END;
$$;
"""

REVERSE_SQL = r"""
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
        RAISE EXCEPTION 'site_report %: Auftrag/Autor sind unveränderlich', OLD.id;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_site_report_anchor ON workflow.site_report;
DROP FUNCTION IF EXISTS workflow.check_site_report_anchor();

ALTER TABLE workflow.site_report
    DROP CONSTRAINT IF EXISTS site_report_braucht_anker;

ALTER TABLE workflow.site_report
    ALTER COLUMN work_order_id SET NOT NULL;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0063_datev_anzahlungskonto"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
