"""Ein Angebot darf weg, solange es keine Nummer trägt.

## Warum

Sascha am 2026-08-02: *„Bei Angebote und Rechnungen dasselbe. Entwürfe alle
löschbar. Sobald versendet oder bestätigt fest und nicht mehr änderbar."*

`0020` verbot jedes DELETE pauschal, mit der Begründung „Entwürfe werden
verworfen, indem sie nie veröffentlicht werden". In der Praxis heißt das: Sie
bleiben liegen. Wer ein Angebot dreimal ansetzt, hat drei Karteileichen, und im
vierten Jahr findet niemand mehr das echte.

## Die Grenze

Sie steht bereits im Schema und muss nicht erfunden werden: Ein Angebot bekommt
seine **Belegnummer erst ab `VERSENDET`** (CHECK „P3-01" in 0018). Solange
`quote_number IS NULL`, war das Angebot nie beim Kunden — es ist ein Entwurf,
gleich ob `ENTWURF`, `INTERN_GEPRUEFT` oder `FREIGEGEBEN`.

Deshalb prüft der Trigger die **Nummer**, nicht den Status: Sie ist das, woran
ein Beleg zum Dokument wird, und eine neue Statusstufe zwischen Freigabe und
Versand hebelte eine Statusliste aus, die Nummer aber nicht.

## Was der Trigger NICHT tun muss

* **Angebotszeilen**, auf die eine Berichtsposition zeigt
  (`site_report_line.source_quote_line_id`), hält Postgres über den
  Fremdschlüssel fest — das Löschen scheitert dort von selbst. Diese Sicherung
  bleibt bewusst stehen.
* **Abrechnungsbindungen** (`invoicing.billing_link`) zeigen ebenfalls auf
  Angebotszeilen. Auch hier greift der Fremdschlüssel. Anders als bei der
  Rechnung (siehe 0147) muss hier nichts gelöst werden: Gebunden wird erst beim
  Fakturieren, und fakturiert wird nur aus versendeten Angeboten.
"""
from django.db import migrations

FORWARD_SQL = r"""
DROP TRIGGER IF EXISTS trg_quote_no_delete ON invoicing.quote;

CREATE FUNCTION invoicing.forbid_delete_ausgestelltes_angebot() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.quote_number IS NOT NULL THEN
        RAISE EXCEPTION
            'quote %: Angebot % ist ausgestellt (%) und wird nicht geloescht — '
            'ein versendetes Angebot wird abgelehnt oder ersetzt, nicht entfernt',
            OLD.id, OLD.quote_number, lower(OLD.status);
    END IF;
    RETURN OLD;
END;
$$;

CREATE TRIGGER trg_quote_no_delete
    BEFORE DELETE ON invoicing.quote
    FOR EACH ROW EXECUTE FUNCTION invoicing.forbid_delete_ausgestelltes_angebot();
"""

REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS trg_quote_no_delete ON invoicing.quote;
DROP FUNCTION IF EXISTS invoicing.forbid_delete_ausgestelltes_angebot();

CREATE TRIGGER trg_quote_no_delete
    BEFORE DELETE ON invoicing.quote
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0145_berichtsentwurf_loeschbar"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
