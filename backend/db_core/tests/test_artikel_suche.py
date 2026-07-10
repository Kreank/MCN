"""Tests der Artikelsuche mit Hero-Operatoren (`+` UND, `|` ODER, `*` Platzhalter).

Der Parser `build_article_search_q` wird gegen die echte Postgres-Test-DB
gefiltert — das prüft die Semantik end-to-end (icontains/iregex, Feld-OR über
Nummer/Bezeichnung/Matchcode) und beweist, dass Sonderzeichen im Suchbegriff
nicht als Regex ausbrechen (re.escape).
"""
import pytest

from db_core.models import Article
from db_core.services import artikel as artikel_service


def _mk(app_user, number, description, matchcode=None):
    return artikel_service.create_article(
        app_user.id, article_number=number, description=description, unit="Stk",
        matchcode=matchcode,
    )


def _find(needle):
    q = artikel_service.build_article_search_q(needle)
    qs = Article.objects.all()
    if q is not None:
        qs = qs.filter(q)
    return {a.article_number for a in qs}


def test_leere_suche_kein_filter():
    # Leere Suche (oder nur Trennzeichen) → None → kein Filter → alle Artikel.
    assert artikel_service.build_article_search_q(None) is None
    assert artikel_service.build_article_search_q("") is None
    assert artikel_service.build_article_search_q("   ") is None
    assert artikel_service.build_article_search_q("+") is None
    assert artikel_service.build_article_search_q(" | + ") is None


@pytest.mark.django_db
def test_leere_suche_liefert_alle(app_user):
    _mk(app_user, "L-1", "Irgendwas")
    _mk(app_user, "L-2", "Anderes")
    assert _find("") == {"L-1", "L-2"}
    assert _find("   ") == {"L-1", "L-2"}


@pytest.mark.django_db
def test_und_operator(app_user):
    _mk(app_user, "U-1", "Rohr DN100 Kupfer")
    _mk(app_user, "U-2", "Rohr DN200")
    # Beide Teilbegriffe müssen vorkommen.
    assert _find("Rohr+DN100") == {"U-1"}
    assert _find("Rohr+DN999") == set()


@pytest.mark.django_db
def test_oder_operator(app_user):
    _mk(app_user, "O-1", "Fitting Kupfer")
    _mk(app_user, "O-2", "Fitting Messing")
    _mk(app_user, "O-3", "Fitting Stahl")
    assert _find("Kupfer|Messing") == {"O-1", "O-2"}


@pytest.mark.django_db
def test_wildcard_operator(app_user):
    _mk(app_user, "W-1", "aFOOb")
    _mk(app_user, "W-3", "aXb")
    _mk(app_user, "W-4", "bXa")          # falsche Reihenfolge
    treffer = _find("a*b")               # „a", dann beliebig, dann „b"
    assert "W-1" in treffer              # matcht „aXXXb"
    assert "W-3" in treffer
    assert "W-4" not in treffer
    # Hero-Beispiel: Rohr*15 = „Rohr" … „15".
    _mk(app_user, "W-5", "Rohrbogen 15 mm")
    _mk(app_user, "W-6", "Rohrbogen 22 mm")
    assert _find("Rohr*15") == {"W-5"}


@pytest.mark.django_db
def test_kombination_und_oder(app_user):
    _mk(app_user, "K-1", "Rohr DN100")       # a UND b
    _mk(app_user, "K-2", "Rohr DN200")       # nur a
    _mk(app_user, "K-3", "Ventil Messing")   # c
    # (Rohr UND DN100) ODER Messing
    assert _find("Rohr+DN100|Messing") == {"K-1", "K-3"}


@pytest.mark.django_db
def test_matchcode_wird_durchsucht(app_user):
    # Der Matchcode (Hero-Kurzsuchbegriff) gehört in die Suche, auch wenn er
    # nicht in der Bezeichnung steht.
    _mk(app_user, "MC-1", "Absperrschieber", matchcode="XZKURZ")
    _mk(app_user, "MC-2", "Anderes Teil")
    assert _find("XZKURZ") == {"MC-1"}
    # Auch als UND-/Wildcard-Term über den Matchcode.
    assert _find("XZ*KURZ") == {"MC-1"}


@pytest.mark.django_db
def test_sonderzeichen_brechen_nicht_aus(app_user):
    """`%`, `_`, `.`, `(`, `\\` im Suchbegriff dürfen nicht als Regex greifen."""
    _mk(app_user, "SZ-1", "Kabel 3x1,5 (NYM) 50%_ .end")
    _mk(app_user, "SZ-2", "KabelXend")   # kein literaler Punkt vor „end"

    # icontains-Terme mit Sonderzeichen (kein *): literal, kein Ausbruch/Fehler.
    assert _find("(NYM)") == {"SZ-1"}
    assert _find("50%_") == {"SZ-1"}

    # Wildcard-Term mit Klammern: re.escape maskiert `(` und `)` → literal.
    assert _find("Kabel*(NYM)*.end") == {"SZ-1"}

    # Der Punkt ist LITERAL (escaped), nicht „jedes Zeichen": „KabelXend" hat
    # keinen „.end" und darf nicht matchen.
    treffer = _find("Kabel*.end")
    assert "SZ-1" in treffer
    assert "SZ-2" not in treffer

    # Backslash bricht nicht aus (re.escape → literal), kein Regex-Fehler.
    assert _find("Kabel*\\ende") == set()
