"""Index auf `invoicing.invoice.reference_invoice_id` (Review-Befund, NIEDRIG).

Postgres legt für eine FK-Spalte **keinen** Index an — nur die referenzierte Seite
(der Primärschlüssel) ist indiziert. `reference_invoice_id` ist aber genau die
Spalte, über die die Forderungsgrenze korreliert:

  * `beleg.storniert_exists()` — EXISTS auf einen veröffentlichten STORNO je
    Rechnung (steckt in `forderungen()` und damit in offenen Posten, Mahnwesen,
    Mahnlauf, Dossier).
  * `buchhaltung.credit_subquery()` — Summe der veröffentlichten Kreditbelege je
    Rechnung.

Beide laufen als korrelierte Subquery über `WHERE reference_invoice_id = <pk>` und
lagen ohne Index auf einem Seq-Scan über die gesamte Belegtabelle — je Zeile der
Ergebnisliste einmal. Das skaliert mit der Belegzahl gegen die Wand: Der Bestand
wächst monoton (GoBD — es wird nichts gelöscht), die Liste der offenen Posten wird
aber täglich geöffnet.

Partiell auf `NOT NULL`: Nur Kreditbelege (STORNO/GUTSCHRIFT) und Schlussrechnungen
tragen einen Referenzbeleg; für die große Mehrheit der Zeilen ist die Spalte NULL
und gehört nicht in den Index. Damit bleibt er klein — und die Anfragen der
Forderungsgrenze suchen ohnehin ausschließlich nach *gesetzten* Werten.

Reines DDL (kein Modellzustand) → `makemigrations --check` bleibt unberührt.
"""
from django.db import migrations

FORWARD_SQL = """
CREATE INDEX IF NOT EXISTS ix_invoice_reference_invoice
    ON invoicing.invoice (reference_invoice_id)
    WHERE reference_invoice_id IS NOT NULL;
"""

REVERSE_SQL = """
DROP INDEX IF EXISTS invoicing.ix_invoice_reference_invoice;
"""


class Migration(migrations.Migration):

    dependencies = [("db_core", "0095_wandflaeche_nie_negativ_beim_insert")]

    operations = [migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL)]
