"""Trigramm-Indizes auf dem Normalisierungsausdruck der globalen Suche.

Die globale Suche (`db_core/services/suche.py`) vergleicht eine **normalisierte**
Form beider Seiten: `lower` → Umlaut-/ß-Entfaltung → alles Nicht-Alphanumerische
raus. Das ist ein Ausdruck zur Abfragezeit; ohne Index bedeutet jeder Suchtoken
`LIKE '%…%'` über die ganze Tabelle.

Bei den Bewegungsdaten (Aufträge, Belege, Kontakte) ist das heute wie auf Jahre
belanglos — dort stehen Tausende Zeilen. Der **Artikelstamm** ist der Ausreißer:
Der geplante DATANORM-Vollimport bringt rund **800.000 Artikel**. Gemessen auf
genau dieser Menge, ohne diese Indizes:

    2 Tokens →  4,7 s · 8 Tokens → 16,2 s   (Seq Scan, 800.000 Zeilen verworfen)

Die Suchpalette feuert bei jedem Tastendruck. Das ist kein langsamer Endpunkt,
das ist ein Selbst-DoS. Mit den Indizes: **Millisekunden** (Bitmap-Index-Scan).

## Warum ein Ausdrucksindex — und was daran zerbrechlich ist

PostgreSQL erkennt einen Ausdrucksindex nur wieder, wenn der Ausdruck in der
WHERE-Klausel **denselben Parsebaum** ergibt. Der Ausdruck ist hier bewusst
**eingefroren ausgeschrieben** und nicht aus `suche.NORM_SQL` importiert: Eine
Migration ist Geschichte, sie darf sich nicht rückwirkend mitverändern, wenn
jemand den Servicecode anfasst.

Der Preis dieser Entkopplung ist, dass Code und Index auseinanderlaufen KÖNNEN —
und zwar **stillschweigend**: Die Suche liefert weiter richtige Treffer, nur eben
über einen Seq-Scan. Grüne Tests, unbenutzbares Produkt. Genau dagegen steht
`db_core/tests/test_suche_index.py`: Er lässt PostgreSQL den echten Plan ausgeben
und schlägt fehl, sobald darin ein „Seq Scan on article" auftaucht. Wer `NORM_SQL`
ändert, braucht also eine neue Migration — der Test sagt es ihm.

`lower`, `replace`, `regexp_replace` und `coalesce` sind IMMUTABLE — der Ausdruck
ist damit indizierbar.

## Umfang

Indiziert werden genau die Felder, die die Tokensuche in einem ODER prüft
(`_artikel`, `_leistungen`). Ein ODER ist nur so schnell wie sein langsamster
Zweig: Bliebe auch nur ein Feld ohne Index, müsste der Planer wieder jede Zeile
anfassen — halb indizieren ist hier dasselbe wie gar nicht.

`pricing.assembly` (Leistungen) bleibt klein; die Indizes kosten dort fast nichts
und halten den Code symmetrisch.

## Rückwärtsstrategie

Reines Lesewerkzeug, keine Fachdaten: vollständig rückwärts (DROP INDEX).
"""
from django.db import migrations

# Eingefroren: zeichengleich mit `db_core.services.suche.NORM_SQL`
# (dort `%(expressions)s` statt `{spalte}`). Nicht importieren — siehe Docstring.
NORM = (
    "regexp_replace("
    "replace(replace(replace(replace("
    "lower(coalesce({spalte}, ''))"
    ", 'ä', 'ae'), 'ö', 'oe'), 'ü', 'ue'), 'ß', 'ss')"
    ", '[^a-z0-9]', '', 'g')"
)

ARTIKEL_FELDER = (
    "article_number",
    "description",
    "matchcode",
    "manufacturer_name",
    "manufacturer_number",
    "gtin",
)
LEISTUNG_FELDER = ("assembly_number", "name", "internal_name", "description")


def _index(tabelle, spalte):
    schema, tab = tabelle.split(".")
    name = f"idx_{tab}_{spalte}_norm_trgm"
    return (
        f"CREATE INDEX IF NOT EXISTS {name} ON {tabelle} "
        f"USING gin (({NORM.format(spalte=spalte)}) gin_trgm_ops);",
        f"DROP INDEX IF EXISTS {schema}.{name};",
    )


_paare = [_index("pricing.article", f) for f in ARTIKEL_FELDER]
_paare += [_index("pricing.assembly", f) for f in LEISTUNG_FELDER]

# Der Ausweg für den BREITEN Begriff.
#
# Ein Trigramm-Index ist stark, wenn der Begriff selten ist. Bei einem sehr
# häufigen Wort („rohr") passen aber Zehntausende Zeilen — dann muss der
# Bitmap-Scan sie alle anfassen und der Ausdruck für jede erneut geprüft werden,
# nur um am Ende die 25 jüngsten auszugeben (gemessen: ~2 s bei 114.000 Treffern).
#
# Mit einem btree auf der Sortierordnung des Suchfensters (created_at DESC, id)
# bekommt der Planer eine zweite Möglichkeit: Er kann die Tabelle in genau dieser
# Reihenfolge durchlaufen und nach den ersten 25 passenden Zeilen aufhören. Bei
# einem breiten Begriff ist das nach ein paar Hundert Zeilen erledigt. Welchen Weg
# er nimmt, entscheidet er anhand der Selektivität — genau dafür ist er da. Wir
# geben ihm nur die Wahl.
_paare.append((
    "CREATE INDEX IF NOT EXISTS idx_article_suchfenster "
    "ON pricing.article (created_at DESC, id);",
    "DROP INDEX IF EXISTS pricing.idx_article_suchfenster;",
))

SQL = "\n".join(vor for vor, _ in _paare)
REVERSE = "\n".join(zurueck for _, zurueck in _paare)


class Migration(migrations.Migration):
    dependencies = [("db_core", "0097_mahnung_nur_auf_offene_forderung")]

    operations = [
        migrations.RunSQL(
            # pg_trgm kommt schon aus 0038; defensiv, damit diese Migration für
            # sich allein lesbar und lauffähig bleibt.
            sql="CREATE EXTENSION IF NOT EXISTS pg_trgm;\n" + SQL,
            reverse_sql=REVERSE,
        ),
    ]
