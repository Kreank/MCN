"""Katalog-interne Artikelnummer an der Lieferantenreferenz (pricing).

**Wozu.** Der B-Satz eines DATANORM-Katalogs trägt in Feld 4 eine „alternative
Artikelnummer". Was dort steht, entscheidet der Absender — und B&O füllt das Feld
mit einer EIGENEN Nummer (`ZRB2071510`, `TZZ459CH`, zu 55,8 % das Muster
`XXXSRT<laufende Zahl>`). Über alle 2.043.336 B&O-Artikel ist jeder dieser Werte
genau einmal vergeben: eine laufende Katalognummer, keine Herstellernummer.

Bis heute landete dieser Wert in `pricing.article.manufacturer_number`. Damit
stand im Artikelstamm unter „Hersteller-Nr." eine Nummer, die es außerhalb von
B&O nirgends gibt — nicht beim Hersteller, nicht im Großhandelsshop. Wer damit
nachbestellte, suchte ins Leere. Das Alt-System (HERO) lässt dieselbe Angabe bei
B&O-Ware korrekt leer.

**Warum hier und nicht am Artikel.** Die Nummer beschreibt nicht den Artikel,
sondern seine Stellung IM KATALOG EINES LIEFERANTEN. Derselbe Bosch-Ersatzteil
(TTNR 87183125010) liegt sowohl im B&O- als auch im Bosch-Katalog — mit
verschiedenen internen Nummern. Am Artikel könnte nur eine davon stehen; an der
Referenz trägt jede Lieferantenbeziehung ihre eigene.

**Warum nicht einfach wegwerfen.** Die Nummer ist der Rückkanal zu B&O: Für
Rückfragen, Reklamationen und den Abgleich künftiger Katalogstände ist sie die
Sprache des Lieferanten. Sie ist nur nichts, was man als Herstellernummer
ausgeben darf.

Nullable ohne Default: Herstellerkataloge (Vaillant, Bosch) führen kein solches
Feld, dort bleibt es leer. Die Spalte gehört NICHT zur Referenzidentität und
wird deshalb von `pricing.protect_supplier_ref()` nicht eingefroren — ein
Katalogstand darf seine interne Nummer korrigieren.
"""
from django.db import migrations

CREATE_SQL = r"""
ALTER TABLE pricing.article_supplier_reference
    ADD COLUMN supplier_catalog_id text;

COMMENT ON COLUMN pricing.article_supplier_reference.supplier_catalog_id IS
    'Katalog-interne Nummer des Lieferanten (DATANORM B-Satz Feld 4), z. B. B&Os ZRB2071510/XXXSRT-Nummern. Rueckkanal zum Lieferanten - KEINE Herstellernummer und nicht als solche anzuzeigen.';
"""

DROP_SQL = r"""
ALTER TABLE pricing.article_supplier_reference
    DROP COLUMN IF EXISTS supplier_catalog_id;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0149_automatische_nummernvergabe"),
    ]

    operations = [
        migrations.RunSQL(sql=CREATE_SQL, reverse_sql=DROP_SQL),
    ]
