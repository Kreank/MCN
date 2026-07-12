"""Der unterzeichnete Baustellenbericht versiegelt auch sein Beweismittelbündel.

Befund aus dem Review des Slices „Bericht am freien Termin": `protect_site_report`
(0054) friert nur die **Spalten** der `site_report`-Zeile ein. Die Fotos hängen
aber nicht in der Zeile, sondern als `content.file_link` daran. Nach der
Kundenunterschrift ließen sich damit weiterhin Bilder **nachschieben** oder
**entfernen** — der Bericht behauptet „unveränderlich seit <signed_at>", während
sein aussagekräftigster Teil (das Bild vom Zustand vor Ort) weiter beweglich war.

Ein Abnahmeprotokoll ist genau das: Text **und** Bild, vom Kunden bestätigt.
Deshalb erstreckt sich die Versiegelung ab hier auf die Verknüpfungen:

    Bericht UNTERZEICHNET  ⇒  kein INSERT, kein UPDATE, kein DELETE
                              einer file_link-Zeile mit diesem site_report_id.

Die Datei selbst (`content.file`) ist ohnehin physisch unveränderlich
(`trg_file_immutable`, 0021) — geschützt werden musste die **Zuordnung**.

Der Trigger prüft OLD und NEW getrennt: Ein UPDATE, das eine Verknüpfung von
einem unterzeichneten Bericht **weg**bewegte, wäre sonst so wirksam wie ein
DELETE. `FOR SHARE` auf die Berichtszeile serialisiert gegen ein gleichzeitiges
`sign_report` (das die Zeile per UPDATE sperrt).

Bewusst NICHT geschützt: Verknüpfungen an Auftrag/Einsatz/Projekt. Sie sind keine
vom Kunden abgenommene Aussage, sondern laufende Ablage — dort ist Nachreichen
richtig. Wer ein Foto nach der Abnahme hat, hängt es an den Einsatz, nicht in das
besiegelte Protokoll.
"""
from django.db import migrations

FORWARD_SQL = r"""
CREATE FUNCTION content.protect_signed_site_report_links() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_status text;
BEGIN
    IF TG_OP IN ('INSERT', 'UPDATE') AND NEW.site_report_id IS NOT NULL THEN
        SELECT status INTO v_status
        FROM workflow.site_report WHERE id = NEW.site_report_id FOR SHARE;
        IF v_status = 'UNTERZEICHNET' THEN
            RAISE EXCEPTION
                'site_report %: Der Bericht ist unterzeichnet — Fotos und Anhänge können nicht mehr hinzugefügt oder geändert werden.',
                NEW.site_report_id;
        END IF;
    END IF;

    IF TG_OP IN ('UPDATE', 'DELETE') AND OLD.site_report_id IS NOT NULL THEN
        SELECT status INTO v_status
        FROM workflow.site_report WHERE id = OLD.site_report_id FOR SHARE;
        IF v_status = 'UNTERZEICHNET' THEN
            RAISE EXCEPTION
                'site_report %: Der Bericht ist unterzeichnet — Fotos und Anhänge können nicht mehr entfernt werden.',
                OLD.site_report_id;
        END IF;
    END IF;

    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;

CREATE TRIGGER trg_file_link_signed_report
    BEFORE INSERT OR UPDATE OR DELETE ON content.file_link
    FOR EACH ROW EXECUTE FUNCTION content.protect_signed_site_report_links();
"""

REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS trg_file_link_signed_report ON content.file_link;
DROP FUNCTION IF EXISTS content.protect_signed_site_report_links();
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0064_bericht_am_freien_termin"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
