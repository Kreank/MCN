"""Am Einsatz gebuchtes Material wird abrechenbar — Artikelbezug + vierte
Herkunft der Abrechnungsbindung.

**Der Befund (2026-07-31).** Der Monteur hat am Einsatz zwei „Material"-Wege vor
sich: die **Materialbuchung** (`workflow.material_entry`, seit 0017) und die
**Berichtsposition** (`workflow.site_report_line`, seit 0080). Nur der zweite
führte zu Geld. Die Materialbuchung kam in `abrechnung.py` nirgends vor — sie
war ein Datensatz ohne Ausgang. Wer sein Material dort erfasste, hatte es
erfasst; er hatte es nur nicht berechnet. Das ist der teuerste stille Fehler
dieses Systems: eine plausibel aussehende Rechnung, die zu niedrig ist.

Diese Migration macht den Weg auf. Sie tut dafür **genau zwei** Dinge:

1. `workflow.material_entry.source_article_id` — die Buchung darf sagen, WELCHER
   Artikel verbraucht wurde. Ohne Artikel gibt es keine Preisermittlung; mit ihm
   läuft sie über dieselbe eine Rechenstelle wie überall
   (`aufschlagsmatrix.vk_vorschlag`).

2. `invoicing.billing_link.material_entry_id` + `source_kind = 'MATERIALBUCHUNG'`
   — die vierte Herkunft, nach exakt demselben Muster wie die drei bestehenden.
   Damit liegt die Doppelabrechnungssperre auch für Materialbuchungen **in der
   Datenbank** (vierter partieller UNIQUE `WHERE released_at IS NULL`), nicht im
   Service. Und der Storno löst sie automatisch mit: Der Trigger
   `invoicing.release_billing_links_on_cancel` arbeitet je Rechnung, nicht je
   Quellart — nach einem Storno ist dieselbe Materialbuchung wieder abrechenbar.

**KEINE Preisspalte an der Materialbuchung — ausdrücklich.** Dieselbe Regel wie
beim Baustellenbericht (Invariante Kap. 3, Migration 0080): Die Erfassung vor Ort
liefert die **Menge**, das Belegwesen den **Preis**. Ein Monteur, der auf der
Baustelle Preise erfasst, schließt eine Preisvereinbarung ab; die Aufschlagsmatrix
und die Mindestmarge wären damit umgangen. Der Schema-Test
`test_erfassung_fuehrt_keine_geldspalte` durchsucht `information_schema` und hält
die Regel auch gegen künftige Migrationen — er erfasst seit diesem Slice
**beide** Tabellen.

**KEIN Lager, kein Bestand, keine Mengenfortschreibung** (Beschluss B-26,
`db/migrations/0028_artikelstamm.sql`: „KEINE Bestandsführung — Artikelstamm ist
Stammdaten, kein Lager"). `source_article_id` ist ein **Verweis auf die
Identität** des Verbrauchten, kein Lagerbuchungssatz. B-26 trifft über die
Rechnungsbindung keine Aussage und steht dem hier nicht entgegen.

**Das B-28-Korrekturfenster bleibt unberührt und gilt für die neue Spalte
automatisch.** `workflow.guard_entry_correction` ist ein Zeilen-Trigger ohne
Spaltenliste: Jedes UPDATE auf `material_entry` geht durch dasselbe Tor — auch
das nachträgliche Zuordnen eines Artikels. Nach kaufmännischer Freigabe des
Auftrags ist damit auch die Artikelzuordnung gesperrt, nach Einsatzabschluss
verlangt sie `SET LOCAL app.correction_reason`. Genau richtig: Eine nachträgliche
Artikelzuordnung ist eine **inhaltliche** Änderung — sie entscheidet über den
Preis.

Rückwärtsstrategie: `reverse_sql` stellt den Stand vor der Migration her,
solange keine Materialbindung entstanden ist. Danach gilt die Politik aus
`db/README.md`: nur noch vorwärts.
"""
from django.db import migrations

FORWARD_SQL = r"""
-- ---------------------------------------------------------------------------
-- 1. workflow.material_entry.source_article_id
--
-- Nullable und ohne Default: Die Freitextbuchung („Dichtung aus dem Fahrzeug")
-- bleibt zulässig — sie ist der Bestand und der Alltag. Sie hat dann nur keinen
-- ermittelbaren Preis; der Abrechnungslauf schickt sie in die Klärung
-- (`MATERIAL_OHNE_ARTIKEL`), statt sie mit 0,00 € durchzulassen oder
-- stillschweigend wegzulassen.
--
-- KEINE Preisspalte. Nicht „noch nicht", sondern grundsätzlich (siehe Modulkopf).
-- ---------------------------------------------------------------------------
ALTER TABLE workflow.material_entry
    ADD COLUMN source_article_id uuid NULL REFERENCES pricing.article (id);

COMMENT ON COLUMN workflow.material_entry.source_article_id IS
    'Optionaler Verweis auf den verbrauchten Artikel (Identität, KEIN Lagerbestand '
    '— B-26). Nur mit ihm ist der Preis ermittelbar (aufschlagsmatrix.vk_vorschlag); '
    'ohne ihn geht die Buchung in die Preisklärung. Die Buchung fuehrt selbst '
    'KEINEN Preis: sie liefert die Menge, das Belegwesen den Preis.';

-- Der heiße Pfad ist die Abrechnung: „welche Buchungen dieses Auftrags tragen
-- welchen Artikel?" — und die Identitätsgrenze des Geld-Wächters.
CREATE INDEX idx_material_entry_article
    ON workflow.material_entry (source_article_id)
    WHERE source_article_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 2. invoicing.billing_link — die vierte Herkunft
--
-- Muster 1:1 wie die drei bestehenden (Migration 0084). Kein Sonderweg: Wer eine
-- Materialbuchung abrechnet, bindet sie genauso, wie eine Zeitbuchung gebunden
-- wird — und der partielle UNIQUE macht die zweite Rechnung darüber physisch
-- unmöglich.
-- ---------------------------------------------------------------------------
ALTER TABLE invoicing.billing_link
    ADD COLUMN material_entry_id uuid NULL REFERENCES workflow.material_entry (id);

COMMENT ON COLUMN invoicing.billing_link.material_entry_id IS
    'Quelle: am Einsatz gebuchtes Material (workflow.material_entry). Vierte '
    'Herkunft neben Berichtsposition, Zeitbuchung und Angebotsposition.';

-- Die Codeliste der Quellart um MATERIALBUCHUNG erweitern. Der CHECK entstand in
-- 0084 als Spalten-CHECK; Postgres hat ihn `billing_link_source_kind_check`
-- genannt. Er wird ersetzt, nicht ergänzt — zwei CHECKs auf derselben Spalte
-- wären zwei Wahrheiten.
ALTER TABLE invoicing.billing_link
    DROP CONSTRAINT billing_link_source_kind_check;
ALTER TABLE invoicing.billing_link
    ADD CONSTRAINT billing_link_source_kind_check CHECK (source_kind IN
        ('BERICHTSPOSITION', 'ZEITBUCHUNG', 'ANGEBOTSPOSITION', 'MATERIALBUCHUNG'));

-- Genau EINE Quellspalte ist gesetzt — jetzt über vier Spalten.
ALTER TABLE invoicing.billing_link
    DROP CONSTRAINT billing_link_eine_quelle;
ALTER TABLE invoicing.billing_link
    ADD CONSTRAINT billing_link_eine_quelle CHECK (
        num_nonnulls(site_report_line_id, time_entry_id, quote_line_id,
                     material_entry_id) = 1
    );

-- Die Art muss zur gesetzten Quellspalte passen — sonst behauptete die Zeile
-- etwas anderes, als sie referenziert.
ALTER TABLE invoicing.billing_link
    DROP CONSTRAINT billing_link_quelle_passt_zur_art;
ALTER TABLE invoicing.billing_link
    ADD CONSTRAINT billing_link_quelle_passt_zur_art CHECK (
        (source_kind = 'BERICHTSPOSITION' AND site_report_line_id IS NOT NULL)
     OR (source_kind = 'ZEITBUCHUNG'      AND time_entry_id       IS NOT NULL)
     OR (source_kind = 'ANGEBOTSPOSITION' AND quote_line_id       IS NOT NULL)
     OR (source_kind = 'MATERIALBUCHUNG'  AND material_entry_id   IS NOT NULL)
    );

-- DIE DOPPELABRECHNUNGSSPERRE für die neue Quelle — exakt das Muster der drei
-- bestehenden: je Materialbuchung höchstens EINE aktive Bindung. Der Storno löst
-- sie (released_at IS NOT NULL) und gibt die Buchung wieder frei.
CREATE UNIQUE INDEX uq_billing_link_material_entry
    ON invoicing.billing_link (material_entry_id)
    WHERE material_entry_id IS NOT NULL AND released_at IS NULL;
"""

REVERSE_SQL = r"""
DROP INDEX IF EXISTS invoicing.uq_billing_link_material_entry;

ALTER TABLE invoicing.billing_link
    DROP CONSTRAINT IF EXISTS billing_link_quelle_passt_zur_art;
ALTER TABLE invoicing.billing_link
    ADD CONSTRAINT billing_link_quelle_passt_zur_art CHECK (
        (source_kind = 'BERICHTSPOSITION' AND site_report_line_id IS NOT NULL)
     OR (source_kind = 'ZEITBUCHUNG'      AND time_entry_id       IS NOT NULL)
     OR (source_kind = 'ANGEBOTSPOSITION' AND quote_line_id       IS NOT NULL)
    );

ALTER TABLE invoicing.billing_link
    DROP CONSTRAINT IF EXISTS billing_link_eine_quelle;
ALTER TABLE invoicing.billing_link
    ADD CONSTRAINT billing_link_eine_quelle CHECK (
        num_nonnulls(site_report_line_id, time_entry_id, quote_line_id) = 1
    );

ALTER TABLE invoicing.billing_link
    DROP CONSTRAINT IF EXISTS billing_link_source_kind_check;
ALTER TABLE invoicing.billing_link
    ADD CONSTRAINT billing_link_source_kind_check CHECK (source_kind IN
        ('BERICHTSPOSITION', 'ZEITBUCHUNG', 'ANGEBOTSPOSITION'));

ALTER TABLE invoicing.billing_link DROP COLUMN IF EXISTS material_entry_id;

DROP INDEX IF EXISTS workflow.idx_material_entry_article;
ALTER TABLE workflow.material_entry DROP COLUMN IF EXISTS source_article_id;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0138_notification_taskcomment"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
