"""Einkaufs- und Listenpreis mit vier Nachkommastellen.

Anlass: der DATANORM-Import. Preise stehen dort als Ganzzahl in Cent, und darauf
wirkt die **Preiseinheit** (0 = je 1, 1 = je 10, 2 = je 100, 3 = je 1000
Mengeneinheiten). Der gespeicherte Preis gilt laut Schema-Kommentar immer je EINER
Mengeneinheit — also muss vor dem Speichern durch die Preiseinheit geteilt werden.

Dabei entstehen echte Werte unterhalb eines Cents:

    Stahlhaften 20 cm: Listenpreis 12,90 € für 100 Stück = 0,1290 €/Stück
                       minus 40 % Rabatt                  = 0,0774 €/Stück

`numeric(15,2)` könnte davon nur 0,08 € speichern — 3,4 % zu viel, auf jeder
Position mit Kleinteilen. In der bestehenden Datenbank haben 40.550 von 285.232
Lieferantenreferenzen eine Preiseinheit ungleich 1; der Fehler ist also nicht
theoretisch.

Vier Nachkommastellen sind der Kompromiss: sie decken „je 1000" bei
Cent-Artikeln ab (0,0372 €) und bleiben schmal genug, dass die Spalte lesbar
bleibt.

**Nur der EK-Bereich wird erweitert.** `unit_price` und `net_amount` auf den
Belegzeilen bleiben bei zwei Nachkommastellen: Sie stehen auf dem Kundenbeleg,
sind GoBD-relevant und werden vom DB-CHECK
`net_amount = round(quantity * unit_price, 2)` erzwungen. Der Einkaufspreis ist
dagegen ein interner Kalkulationswert.

Die Erweiterung der Skala ist verlustfrei (numeric(15,2) → numeric(15,4)): jeder
vorhandene Wert bleibt exakt erhalten, es kommen nur zwei Nullen hinten dran.
"""
from django.db import migrations

FORWARD_SQL = r"""
ALTER TABLE pricing.article_supplier_reference
    ALTER COLUMN last_purchase_price TYPE numeric(15, 4),
    ALTER COLUMN list_price          TYPE numeric(15, 4);

COMMENT ON COLUMN pricing.article_supplier_reference.last_purchase_price IS
    'Einkaufspreis je EINER Mengeneinheit, vier Nachkommastellen. Netto '
    '(DATANORM-Preiskennzeichen 2) oder Liste*(1-Rabatt). NULL = unbekannt, '
    'nie 0. Zwei Nachkommastellen genuegen nicht: bei Preiseinheit 100/1000 '
    'liegen echte Stueckpreise unter einem Cent.';
COMMENT ON COLUMN pricing.article_supplier_reference.list_price IS
    'Haendler-Listenpreis je EINER Mengeneinheit, vier Nachkommastellen '
    '(DATANORM-Preiskennzeichen 1).';
"""

REVERSE_SQL = r"""
-- Rueckwaerts ist NICHT verlustfrei: vorhandene Werte mit mehr als zwei
-- Nachkommastellen wuerden gerundet. Deshalb bewusst als Fehler.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pricing.article_supplier_reference
        WHERE last_purchase_price IS NOT NULL
          AND last_purchase_price <> round(last_purchase_price, 2)
    ) THEN
        RAISE EXCEPTION
            'Rueckbau auf numeric(15,2) wuerde Einkaufspreise verfaelschen '
            '(Werte mit mehr als zwei Nachkommastellen vorhanden).';
    END IF;
END $$;

ALTER TABLE pricing.article_supplier_reference
    ALTER COLUMN last_purchase_price TYPE numeric(15, 2),
    ALTER COLUMN list_price          TYPE numeric(15, 2);
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0037_belegrubrik"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
