"""Auch `pricing.article.list_price` braucht vier Nachkommastellen.

Gleicher Grund wie bei `article_supplier_reference` (Migration 0038): Der
Listenpreis eines Artikels mit DATANORM-Preiseinheit 100 liegt je Stück unter
einem Cent (Stahlhaften: 0,1290 €). Auf zwei Stellen gerundet wären das 0,13 € —
und da `list_price` als Kalkulationsbasis einer Verkaufspreisgruppe dienen kann
(`sale_price_group.calc_basis = 'LISTENPREIS'`), pflanzte sich der Fehler in
jeden daraus abgeleiteten Verkaufspreis fort.

Der Verkaufspreis selbst bleibt bei zwei Nachkommastellen — er steht auf dem
Kundenbeleg.
"""
from django.db import migrations

FORWARD_SQL = r"""
ALTER TABLE pricing.article
    ALTER COLUMN list_price TYPE numeric(15, 4);

COMMENT ON COLUMN pricing.article.list_price IS
    'Listenpreis je EINER Mengeneinheit, vier Nachkommastellen. Kann Basis einer '
    'Verkaufspreisgruppe sein (calc_basis = LISTENPREIS).';
"""

REVERSE_SQL = r"""
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pricing.article
        WHERE list_price IS NOT NULL AND list_price <> round(list_price, 2)
    ) THEN
        RAISE EXCEPTION
            'Rueckbau auf numeric(15,2) wuerde Listenpreise verfaelschen.';
    END IF;
END $$;

ALTER TABLE pricing.article ALTER COLUMN list_price TYPE numeric(15, 2);
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0038_einkaufspreis_nachkommastellen"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
