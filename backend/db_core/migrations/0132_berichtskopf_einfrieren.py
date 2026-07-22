"""Der Briefkopf eines unterschriebenen Berichts wird eingefroren (Befund B9).

Das Problem
-----------
Der Baustellenbericht führt seit dem Briefkopf-Slice Auftraggeber, Anschrift,
Mieter, Wohnungslage und Eigentümer — **alles live aus den Stammdaten
gelesen**. Für einen Entwurf ist das richtig: Ändert sich etwas, soll der
Bericht es zeigen.

Für einen **unterschriebenen** Bericht ist es falsch. Der Mieter unterschreibt
ein Dokument mit seinem Namen darauf; zieht er drei Monate später aus und der
Nachmieter wird erfasst, stünde plötzlich **dessen** Name auf dem Papier, das
der Vormieter unterschrieben hat. Dasselbe bei einem Eigentümerwechsel oder
einer Umbenennung der Einheit.

Das ist derselbe Gedanke, den `invoicing.invoice.billing_snapshot` für den
Beleg umsetzt (B-30, GoBD) — nur dass der Bericht bisher gar nichts einfror.
Der Kommentar in `services/site_report.kopfdaten` sagt das ausdrücklich und
verweist auf diesen Befund.

Warum ein Snapshot und keine Fremdschlüssel-Historie
----------------------------------------------------
Man könnte die Beteiligten zum Zeitpunkt der Unterschrift auch über
Gültigkeitszeiträume rekonstruieren (`belegung` führt sie ja). Der Snapshot ist
trotzdem richtig:

* Die **Anschrift** einer Partei ist nicht historisiert — eine korrigierte
  Hausnummer wirkt rückwirkend, und genau das soll auf dem Papier nicht
  passieren.
* Ein Bericht muss auch dann noch zeigen, was er zeigte, wenn ein Kontakt
  später **gelöscht** oder umbenannt wird.
* Die Rekonstruktion wäre bei jedem Abruf zu leisten und müsste die
  Zeitraumlogik von damals nachbilden — der Snapshot ist die Antwort, nicht
  die Frage.

Was der Trigger durchsetzt
--------------------------
`header_snapshot` reiht sich in die Felder ein, die `protect_site_report` an
einem unterzeichneten Bericht sperrt. Ein einmal gesetzter Kopf lässt sich
danach weder ändern noch leeren — die Datenbank setzt das durch, nicht der
Dienst. Solange der Bericht Entwurf ist, bleibt die Spalte frei beschreibbar
(und in aller Regel NULL: Erst das Unterschreiben friert ein).

Altbestand
----------
Bereits unterschriebene Berichte tragen NULL und behalten es — der Trigger
verbietet ja gerade das nachträgliche Setzen. `kopfdaten` fällt für sie auf die
Live-Auflösung zurück, wie bisher. Ein nachträgliches Befüllen wäre eine
erfundene Aussage über einen Zeitpunkt, an dem niemand hingesehen hat.
"""
from django.db import migrations

FORWARD_SQL = r"""
-- Der eingefrorene Briefkopf. NULL = nicht (mehr) einzufrieren gewesen:
-- Entwürfe und Altbestand.
ALTER TABLE workflow.site_report
    ADD COLUMN header_snapshot jsonb NULL;

COMMENT ON COLUMN workflow.site_report.header_snapshot IS
    'Briefkopf zum Zeitpunkt der Unterschrift (Auftraggeber, Anschrift, Mieter, '
    'Eigentümer, Wohnungslage). Wird beim Unterzeichnen gesetzt und ist danach '
    'unveränderlich. NULL bei Entwürfen und bei Berichten, die vor Migration '
    '0132 unterschrieben wurden.';

-- ---------------------------------------------------------------------------
-- Der Kopf gehört zum versiegelten Bestand
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
           OR NEW.signature_file_id IS DISTINCT FROM OLD.signature_file_id
           -- Neu (0132): Der eingefrorene Briefkopf ist Teil des Dokuments.
           -- Ohne diese Zeile ließe sich nachträglich ein anderer Mieter oder
           -- Auftraggeber auf ein unterschriebenes Blatt schreiben.
           OR NEW.header_snapshot IS DISTINCT FROM OLD.header_snapshot THEN
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
-- Trigger auf den Stand vor 0132 (ohne header_snapshot) zurücksetzen, DANN die
-- Spalte entfernen — umgekehrt referenzierte die Funktion kurzzeitig ein Feld,
-- das es nicht mehr gibt.
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
    IF NEW.work_order_id IS DISTINCT FROM OLD.work_order_id
       OR NEW.author_id IS DISTINCT FROM OLD.author_id THEN
        RAISE EXCEPTION 'site_report %: Auftrag/Autor sind unveränderlich', OLD.id;
    END IF;
    IF OLD.work_order_id IS NULL
       AND NEW.service_job_id IS DISTINCT FROM OLD.service_job_id THEN
        RAISE EXCEPTION
            'site_report %: Der Bericht hängt am freien Termin — sein Einsatzbezug ist unveränderlich.',
            OLD.id;
    END IF;
    RETURN NEW;
END;
$$;

ALTER TABLE workflow.site_report DROP COLUMN header_snapshot;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0131_freizeitausgleich"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
