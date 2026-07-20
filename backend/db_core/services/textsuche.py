"""Bausteine der normalisierten Tokensuche — **eine** Wahrheit für alle Sucher.

Diese Datei war ursprünglich der private Unterbau von `services/suche.py` (der
globalen Spotlight-Suche). Mit der **Dublettenvermeidung bei der Erfassung**
brauchen auch die Listen (`api/property.py::list_properties`,
`api/identity.py::list_parties`) und der Adress-Dubletten-Endpunkt exakt dieselbe
Normalisierung. Zwei Formulierungen derselben Regel wären zwei Suchen, die sich
langsam auseinanderentwickeln — deshalb steht sie hier, einmal.

## Die Normalisierung (beide Seiten, in Python UND in SQL)

Kleinschreibung → Umlaut-/ß-Entfaltung (ä→ae, ö→oe, ü→ue, ß→ss) → Entfernen
aller Nicht-Alphanumerischen. Damit ist „Badensche Straße" ≡ „badensche strasse"
≡ „badenschestr." (als Teilstring) und „030 79085327" ≡ „03079085327".

Für Kontaktwege gibt es zusätzlich eine reine **Ziffernform** (`ziffern`): Der
Nutzer tippt „0170 1234567", gespeichert ist „+49 170 1234567" — ohne diesen
zweiten Vergleich findet er seinen eigenen Kontakt nicht.

## Die Straßenform (`strassen_norm`) — neu mit dem Dubletten-Slice

Zusätzlich zur allgemeinen Normalisierung wird das **Straßen-Suffix** vereinheit-
licht: ein abschließendes `strasse` oder `str` wird zu `str`. „Albrechtstraße",
„Albrechtstr.", „Albrecht Str" ergeben damit alle `albrechtstr` und sind
**gleich** — nicht nur „ähnlich".

Das ist der Unterschied zwischen Suchen und Vergleichen: Die Listensuche kommt
mit Teilstrings aus (`albrechtstr` steckt in `albrechtstrasse`), der
Dublettenabgleich braucht **Gleichheit** in beide Richtungen — sonst findet
„Albrechtstraße 30" die bereits erfasste „Albrechtstr. 30" nicht, und genau
diese Dublette soll der Slice verhindern.

## Tokens: UND über Tokens, ODER über Felder

Der Begriff wird an Leerzeichen zerlegt. **Jedes** Token muss **irgendwo** in der
Entität vorkommen (UND); innerhalb eines Tokens zählt jedes Suchfeld (ODER). Nur
so findet „Albrechtstraße 30" die Liegenschaft: „albrechtstrasse" trifft die
Straße, „30" die Hausnummer — kein einzelnes Feld enthält alles.
"""
import re

from django.db.models import Exists, F, Func, OuterRef, Q, TextField, Value
from django.db.models.functions import Coalesce

from db_core.models import ContactPoint

UMLAUTE = (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"))

# Obergrenze der Tokenzahl. Jedes Token erzeugt eine eigene UND-Gruppe aus
# LIKE-Prädikaten und korrelierten EXISTS — ohne Grenze könnte ein einziger
# GET mit 500 Wörtern einen Worker minutenlang binden (die Ausdrücke sind nicht
# indexierbar). Mehr als acht Tokens grenzen keine Suche mehr ein, sie quälen nur
# die Datenbank; die überzähligen werden verworfen.
MAX_TOKENS = 8


# ---------------------------------------------------------------------------
# Normalisierung — dieselbe Regel in Python und in SQL
# ---------------------------------------------------------------------------

def normalisieren(text):
    """Kleinschreibung, Umlaute/ß entfaltet, alles Nicht-Alphanumerische raus."""
    t = (text or "").lower()
    for umlaut, ersatz in UMLAUTE:
        t = t.replace(umlaut, ersatz)
    return re.sub(r"[^a-z0-9]", "", t)


def nur_ziffern(text):
    """Reine Ziffernform (Telefonnummern: „030 790-853" → „030790853")."""
    return re.sub(r"\D", "", text or "")


def normalisieren_strasse(text):
    """Normalform einer Straße mit vereinheitlichtem Suffix.

    „Albrechtstraße" / „Albrechtstr." / „Albrecht Str" → alle `albrechtstr`.
    Ohne diesen Schritt wäre der Dublettenabgleich richtungsabhängig — die
    kürzere Schreibweise fände die längere (Teilstring), die längere die kürzere
    aber nie.
    """
    return re.sub(r"(strasse|str)$", "str", normalisieren(text))


# Der Normalisierungsausdruck als EIN Template mit ausschließlich literalen
# Konstanten — kein Bind-Parameter, keine verschachtelten Func-Objekte.
#
# Das ist kein Stil, sondern die Bedingung dafür, dass der GIN-Trigramm-Index aus
# Migration 0098 überhaupt greifen kann: PostgreSQL erkennt einen
# Ausdrucksindex nur wieder, wenn der Ausdruck in der WHERE-Klausel **derselbe
# Parsebaum** ist. Steht in `NORM_SQL` etwas anderes als im Index, fällt die
# Artikelsuche stillschweigend auf einen Seq-Scan über 800.000 Zeilen zurück —
# und keiner merkt es, weil die Tests mit 20 Zeilen grün bleiben.
#
# ==> Ändert jemand diesen String, MUSS er die Migration 0098 mitziehen.
#     `db_core/tests/test_suche_index.py` schlägt sonst fehl (EXPLAIN-Prüfung).
NORM_SQL = (
    "regexp_replace("
    "replace(replace(replace(replace("
    "lower(coalesce(%(expressions)s, ''))"
    ", 'ä', 'ae'), 'ö', 'oe'), 'ü', 'ue'), 'ß', 'ss')"
    ", '[^a-z0-9]', '', 'g')"
)

# Straßenform in SQL: die allgemeine Normalform, danach das Suffix vereinheit-
# licht. POSIX-Alternation matcht „leftmost-longest", `strasse` gewinnt also vor
# `str` — aus `albrechtstrasse` wird `albrechtstr`, aus `albrechtstr` ebenfalls.
#
# Dieser Ausdruck ist bewusst **nicht** derselbe wie `NORM_SQL` und darf deshalb
# NICHT für die Artikelsuche verwendet werden: Er ist von keinem Index gedeckt.
STRASSE_NORM_SQL = "regexp_replace(" + NORM_SQL + ", '(strasse|str)$', 'str')"


class Normalisiert(Func):
    """SQL-Ausdruck: `normalisieren()` auf einer Spalte (auch über Joins).

    NULL wird zu '' (coalesce) — ein Angebot im ENTWURF trägt keine quote_number,
    und daran darf die Suche nicht scheitern.
    """

    template = NORM_SQL
    output_field = TextField()


class NormalisierteStrasse(Func):
    """SQL-Ausdruck: `normalisieren_strasse()` auf einer Spalte."""

    template = STRASSE_NORM_SQL
    output_field = TextField()


def norm(pfad):
    """Normalisierte Form des Feldes hinter `pfad` (ORM-Pfad)."""
    return Normalisiert(F(pfad))


def strassen_norm(pfad):
    """Normalisierte **Straßenform** des Feldes hinter `pfad`."""
    return NormalisierteStrasse(F(pfad))


def ziffern(pfad):
    """SQL-Ausdruck: reine Ziffernform einer Spalte."""
    return Func(
        Coalesce(F(pfad), Value("")), Value(r"\D"), Value(""), Value("g"),
        function="regexp_replace", output_field=TextField(),
    )


def tokenisieren(begriff):
    """Begriff → Liste normalisierter Tokens (leere fallen weg, gekappt bei MAX_TOKENS)."""
    tokens = [t for t in (normalisieren(w) for w in (begriff or "").split()) if t]
    return tokens[:MAX_TOKENS]


# ---------------------------------------------------------------------------
# Bausteine für die Querys
# ---------------------------------------------------------------------------

def feld_q(felder, token):
    """ODER über alle (annotierten) Felder einer Entität für EIN Token."""
    q = Q()
    for feld in felder:
        q |= Q(**{f"{feld}__contains": token})
    return q


def tokens_q(felder, tokens, exists_je_token=None):
    """UND über Tokens, ODER über Felder — plus optionale Exists-Zweige.

    `exists_je_token` ist eine Funktion token → Liste von Exists()-Ausdrücken;
    sie hängt die Beziehungssuche (Kontaktwege, Beteiligte, Liegenschaften) in
    denselben ODER-Zweig, ohne die Ergebnismenge durch Joins zu vervielfachen.
    """
    gesamt = None
    for token in tokens:
        oder = feld_q(felder, token)
        for exists in (exists_je_token(token) if exists_je_token else []):
            oder |= exists
        gesamt = oder if gesamt is None else (gesamt & oder)
    return gesamt if gesamt is not None else Q(pk__isnull=True)


def adresse_annotationen(praefix, alias):
    """Normalisierte Adressfelder über einen Pfad (Liegenschaft → Adresse)."""
    return {
        f"{alias}_street": norm(f"{praefix}street"),
        f"{alias}_hn": norm(f"{praefix}house_number"),
        f"{alias}_plz": norm(f"{praefix}postal_code"),
        f"{alias}_city": norm(f"{praefix}city"),
    }


def kontaktwege_q(pfad, token, outer="pk"):
    """Exists über Kontaktwege einer Party (Text- UND Ziffernform).

    Die Ziffernform ist der Grund, aus dem dieser Helfer existiert: Wer „0170
    1234567" tippt, sucht die Nummer, die als „+49 170 1234567" gespeichert ist.
    Ein reiner Textvergleich fände sie nie.
    """
    sub = (
        ContactPoint.objects.filter(**{pfad: OuterRef(outer)})
        .annotate(n_wert=norm("value"), z_wert=ziffern("value"))
        .filter(Q(n_wert__contains=token) | Q(z_wert__contains=token))
    )
    return Exists(sub)
