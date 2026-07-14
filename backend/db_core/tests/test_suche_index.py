"""Der Suchindex greift wirklich — von PostgreSQL bestätigt, nicht behauptet.

Die globale Suche vergleicht einen **Ausdruck** (`suche.NORM_SQL`), keine Spalte.
PostgreSQL kann einen Ausdrucksindex nur benutzen, wenn der Ausdruck in der
WHERE-Klausel **denselben Parsebaum** ergibt wie der in der Indexdefinition
(Migration 0098). Weicht auch nur ein Zeichen ab — ein anderes `coalesce`, eine
umgestellte Ersetzung, ein zusätzlicher Cast —, dann passiert Folgendes:

    Die Suche liefert weiterhin **korrekte** Treffer. Nur eben per Seq-Scan.

Auf den 800.000 Artikeln des geplanten DATANORM-Vollimports sind das gemessene
**16,2 Sekunden statt 0,3 Millisekunden**, bei einer Suchpalette, die bei jedem
Tastendruck feuert. Kein fachlicher Test der Welt sieht das: Mit den zwanzig
Zeilen einer Testdatenbank ist auch der Seq-Scan sofort fertig, und alles ist
grün.

Deshalb fragt dieser Test nicht das Ergebnis, sondern den **Plan**. Damit die
Frage auf einer winzigen Tabelle überhaupt sinnvoll ist, wird der Seq-Scan
transaktionslokal verboten (`enable_seqscan = off`): Der Planer nimmt dann den
Index — **wenn er kann**. Kann er nicht, steht „Seq Scan" trotzdem im Plan (das
Verbot ist eine Kostenstrafe, kein Verbot im engen Sinn), und der Test schlägt
fehl. Genau das ist die Aussage, die wir brauchen.

Wer `NORM_SQL` ändert, braucht also eine neue Migration — dieser Test sagt es ihm.
"""
import pytest
from django.db import connection

from db_core.models import Article, Assembly
from db_core.services import suche as suche_service


def _plan(model, ausdruck_feld, spalte, token):
    """EXPLAIN für „normalisierte Spalte enthält Token", ohne Seq-Scan-Option."""
    qs = model.objects.annotate(
        **{ausdruck_feld: suche_service._norm(spalte)}
    ).filter(**{f"{ausdruck_feld}__contains": token})
    sql, params = qs.query.sql_with_params()
    with connection.cursor() as cur:
        # Transaktionslokal (der Test läuft in der Testtransaktion und rollt zurück).
        cur.execute("SET LOCAL enable_seqscan = off")
        cur.execute("EXPLAIN " + sql, params)
        return "\n".join(zeile[0] for zeile in cur.fetchall())


ARTIKEL_SPALTEN = [
    "article_number", "description", "matchcode",
    "manufacturer_name", "manufacturer_number", "gtin",
]
LEISTUNG_SPALTEN = ["assembly_number", "name", "internal_name", "description"]


@pytest.mark.django_db
@pytest.mark.parametrize("spalte", ARTIKEL_SPALTEN)
def test_artikelsuche_nutzt_den_ausdrucksindex(spalte):
    """Jede Spalte, die die Tokensuche im ODER prüft, braucht ihren Index.

    Ein ODER ist nur so schnell wie sein langsamster Zweig: Bliebe eine einzige
    Spalte ohne Index, müsste der Planer wieder jede Zeile anfassen — halb
    indiziert ist hier dasselbe wie gar nicht.
    """
    plan = _plan(Article, "n_feld", spalte, "zinkrinne")
    assert "Seq Scan" not in plan, (
        f"pricing.article/{spalte}: Der Ausdrucksindex aus Migration 0098 greift "
        f"NICHT — die Artikelsuche fällt auf einen Seq-Scan zurück (auf dem "
        f"Vollkatalog: Sekunden statt Millisekunden). Stimmt suche.NORM_SQL noch "
        f"zeichengleich mit der Migration überein?\n\n{plan}"
    )
    assert f"idx_article_{spalte}_norm_trgm" in plan, plan


@pytest.mark.django_db
@pytest.mark.parametrize("spalte", LEISTUNG_SPALTEN)
def test_leistungssuche_nutzt_den_ausdrucksindex(spalte):
    plan = _plan(Assembly, "n_feld", spalte, "dachrinne")
    assert "Seq Scan" not in plan, plan
    assert f"idx_assembly_{spalte}_norm_trgm" in plan, plan


@pytest.mark.django_db
def test_suchfenster_index_existiert():
    """Der btree auf (created_at DESC, id) ist der Ausweg für den BREITEN Begriff.

    Ein Trigramm-Index ist stark, solange der Begriff selten ist. Passt ein Wort
    auf Zehntausende Artikel, muss der Bitmap-Scan sie alle anfassen, nur um die
    25 jüngsten auszugeben (gemessen: 2,0 s). Mit diesem Index kann der Planer
    stattdessen in Fensterreihenfolge laufen und nach 25 Treffern aufhören
    (gemessen: 1,7 ms). Welchen Weg er nimmt, entscheidet er selbst — wir sorgen
    nur dafür, dass er die Wahl hat.
    """
    with connection.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM pg_indexes "
            "WHERE schemaname = 'pricing' AND tablename = 'article' "
            "AND indexname = 'idx_article_suchfenster'"
        )
        assert cur.fetchone(), "Index idx_article_suchfenster fehlt (Migration 0098)."
