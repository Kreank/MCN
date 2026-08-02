"""Ein Berichtsentwurf darf weg — ab ABGESCHLOSSEN nicht mehr.

## Warum

Sascha am 2026-08-02: *„Warum können wir Entwürfe nicht löschen? Finde ich blöd,
das müllt das System zu. Berichte, aus denen Rechnungen erstellt werden oder halt
bestätigt sind — das man die nicht mehr löschen kann, ok. Aber Entwürfe …"*

Er hat recht, und die bisherige Sperre war zu grob: `util.forbid_mutation()`
verbietet jedes DELETE, **ohne den Status anzusehen**. Die GoBD verlangt
Unveränderlichkeit ab dem Zeitpunkt, an dem ein Beleg entsteht — nicht während
des Tippens. Ein Berichtsentwurf trägt keine Nummer, war nie beim Kunden und
begründet keine Forderung; ihn aufzubewahren dokumentiert nichts, es sammelt nur
Müll an, in dem später niemand den echten Nachweis findet.

## Was gilt

* `ENTWURF` — löschbar, samt Positionen und Dateiverknüpfungen.
* `ABGESCHLOSSEN` / `UNTERZEICHNET` — **nie**. Ab hier ist der Bericht
  Abrechnungsgrundlage bzw. abgenommener Nachweis (siehe 0144).

## Der Fallstrick, der hier NICHT auftreten kann

Ein Rechnungsentwurf hält Abrechnungsbindungen auf Berichtspositionen
(`invoicing.billing_link`). Beim Bericht ist das unkritisch, weil abgerechnet nur
wird, was mindestens `ABGESCHLOSSEN` ist (0144) — und das ist ohnehin gesperrt.
Ein *Entwurf* kann also gar nicht gebunden sein. Der Fremdschlüssel von
`billing_link` auf die Berichtsposition würde ein Löschen zusätzlich verhindern;
diese Sicherung bleibt bewusst stehen, statt sie wegzuräumen.

Für **Angebot und Rechnung** gilt das nicht — dort müssen beim Löschen die
Bindungen gelöst werden. Siehe `docs/ENTSCHEIDUNGEN.md`, „Entwürfe sind löschbar".
"""
from django.db import migrations

FORWARD_SQL = r"""
-- ---------------------------------------------------------------------------
-- Bericht: DELETE nur im Entwurf
-- ---------------------------------------------------------------------------
DROP TRIGGER IF EXISTS trg_site_report_no_delete ON workflow.site_report;

CREATE FUNCTION workflow.forbid_delete_ausgestellter_bericht() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status <> 'ENTWURF' THEN
        RAISE EXCEPTION
            'site_report %: nur ein Entwurf laesst sich loeschen — dieser ist % '
            '(ab hier ist er Abrechnungsgrundlage bzw. abgenommener Nachweis)',
            OLD.id, lower(OLD.status);
    END IF;
    RETURN OLD;
END;
$$;

CREATE TRIGGER trg_site_report_no_delete
    BEFORE DELETE ON workflow.site_report
    FOR EACH ROW EXECUTE FUNCTION workflow.forbid_delete_ausgestellter_bericht();

-- ---------------------------------------------------------------------------
-- Berichtspositionen: dieselbe Grenze
-- ---------------------------------------------------------------------------
-- 0080 sperrte das Entfernen ueber protect_site_report_lines(), das seit 0144
-- ABGESCHLOSSEN und UNTERZEICHNET abweist und den Entwurf durchlaesst. Damit ist
-- hier nichts weiter zu tun: Loescht jemand den Berichtskopf, raeumt der Dienst
-- die Positionen in derselben Transaktion mit ab.
--
-- Die Fremdschluessel von invoicing.billing_link auf die Berichtsposition
-- bleiben, wie sie sind: Sollte je eine Bindung an einer Entwurfsposition
-- haengen, verweigert Postgres das Loeschen — eine Sicherung mehr, kostenlos.
"""

REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS trg_site_report_no_delete ON workflow.site_report;
DROP FUNCTION IF EXISTS workflow.forbid_delete_ausgestellter_bericht();

CREATE TRIGGER trg_site_report_no_delete
    BEFORE DELETE ON workflow.site_report
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0144_bericht_abgeschlossen"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
