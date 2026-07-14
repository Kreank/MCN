"""Globale Suche über alle Entitäten — rein lesend, rechtegefiltert, ranggeordnet.

Diese Suche ist die Antwort auf vier konkrete Beschwerden über das Altsystem.
Jede davon ist hier eine Konstruktionsregel, kein Feature:

**1. „Wir finden Projekte nicht, obwohl wir den genauen Straßennamen angeben."**
Das Altsystem sucht nur in den Feldern der Entität selbst — die Straße hängt aber
an der Liegenschaft, nicht am Projekt. Deshalb sucht MCN **über Beziehungen**:
Der Suchraum einer Entität umfasst die Adressen der verknüpften Liegenschaften,
die Namen der Beteiligten und deren Kontaktwege. Welche Felder das je Entität
sind, steht an genau zwei Stellen — in der Kategorie-Query (`_projekte`,
`_auftraege`, …) und in `_gruppen` (dieselben Felder für Rang und Begründung).

**2. „Eine Mail, die darin vorkommt, findet er nicht."**
Kontaktwege (E-Mail/Telefon) der Beteiligten gehören zum Suchraum der Entität,
an der sie hängen: Vorgang (Melder), Auftrag, Rechnung (Schuldner) und
Liegenschaft (Eigentümergemeinschaft) — nicht nur zum Kontakt selbst. Gerade die
Rechnung hat kein Titelfeld; die Mail des Schuldners ist einer der wenigen
sprechenden Wege zu ihr.

**3. „Alles über 3 Ziffern wird quasi ignoriert."**
Zahlen sind hier **erstklassig**: keine Mindestlänge je Token, keine Stoppwörter,
keine Volltext-Lexeme. Eine nackte Ziffernfolge („42") trifft Belegnummern
(…000042), Hausnummern und Telefonnummern gleichermaßen.

**4. „Selbst wenn ich die genaue Angebotsnummer angebe, findet er sie nicht oder
listet eine elend lange Liste."**
Der **Direkttreffer** ist ein eigener, vorgelagerter Pfad (`_direkttreffer`), der
sich mit der Ähnlichkeitssuche **nie vermischt**: Wer eine Kennung eingibt
(AN-2026-000042, OBJ-00001, MA-00007, eine GTIN, eine Artikelnummer), bekommt
genau diesen Datensatz auf Rang 0 an Position 1. Die Ähnlichkeitssuche läuft
zusätzlich, kann den Direkttreffer aber weder verdrängen noch verwässern.

## Normalisierung (beide Seiten, in SQL)

Verglichen wird eine normalisierte Form von Suchbegriff **und** Feldinhalt:
Kleinschreibung → Umlaut-/ß-Entfaltung (ä→ae, ö→oe, ü→ue, ß→ss) → Entfernen aller
Nicht-Alphanumerischen. Damit ist „Badensche Straße" ≡ „badensche strasse" ≡
„badenschestr." (Teilstring!) — und „030 79085327" ≡ „03079085327".
Für Kontaktwege gibt es zusätzlich eine reine Ziffernform (Telefonnummern).

Es gibt **keine gespeicherte Normalspalte** und dieser Slice legt auch keine an
(kein DDL): Die Normalisierung ist ein SQL-Ausdruck zur Abfragezeit
(`_norm` → `lower`/`replace`/`regexp_replace`). Das ist der Preis dafür, dass die
Suche ohne Migration auskommt — und die eingebaute Grenze dieses Slices (siehe
„Grenzen").

## Tokens: UND über Tokens, ODER über Felder

Der Begriff wird an Leerzeichen zerlegt. **Jedes** Token muss **irgendwo** in der
Entität vorkommen (UND); innerhalb eines Tokens zählt jedes Suchfeld (ODER). Nur
so findet „Badensche Straße 53" das Projekt: „badensche"/„strasse" treffen die
Straße der Liegenschaft, „53" die Hausnummer — kein einzelnes Feld enthält alles.

## Rang

| Rang | Bedeutung |
|---|---|
| 0 | **Direkttreffer** — Kennung exakt (eigener Pfad, immer Position 1) |
| 1 | alle Tokens im Primärfeld (Name/Titel/Nummer), am **Wortanfang** |
| 2 | alle Tokens im Primärfeld, als Teilstring |
| 3 | mindestens ein Token nur **über eine Beziehung** (Adresse, Beteiligter, Kontaktweg) |

Bei gleichem Rang: erst Kategorie (feste Reihenfolge), dann **neueste zuerst**
(`created_at` absteigend), dann `id` als Tiebreak. Begründung: In einem CRM ist
Aktualität der bessere Relevanzhinweis als das Alphabet — die Straße, an der
gerade gearbeitet wird, ist die gesuchte. Die `id` als letzte Stufe garantiert
eine **deterministische** Reihenfolge (kein Flackern bei gleicher Sekunde).

## Rechte (fail-closed, aber ohne die Suche zu töten)

Jede Kategorie hängt an ihrem Modul (`Sicht`). Fehlt das Recht, wird die
Kategorie **komplett weggelassen** — es gibt kein 403 auf die Gesamtsuche.

**Die Suche ZEIGT nichts, was die übrige API demselben Konto verwehrt** — sonst
wäre der eine Endpunkt, den jeder aufruft, das bequemste Schlupfloch:

  * **Straßenadresse** im Untertitel nur mit `property/LESEN` (`_adresse_text`).
    Ohne das Recht bleibt es bei Ort und Objektnummer — genau wie in den Listen
    von `api/auftrag.py` und `api/planung.py`, die Straße/Hausnummer ebenfalls
    nicht herausgeben.
  * **Kontaktwege** (E-Mail/Telefon) werden nur mit `identity/LESEN` überhaupt
    durchsucht, und **Personennamen** stehen nur dann im Untertitel. Andernfalls
    wäre die Suche ein Auskunftsdienst („E-Mail rein, Name raus") für ein Konto,
    das Kontaktdaten gar nicht lesen darf.

**Ein Feld, das nicht angezeigt wird, kann trotzdem MATCHEN** — und tut es:
Straße/Hausnummer bleiben auch ohne `property/LESEN` durchsuchbar. Wer „Badensche
Straße 53" eingibt, findet den Auftrag also, sieht die Adresse aber nicht im
Untertitel. Das ist **bewusst** so: Genau daran scheiterte das Vorgängersystem
(„wir finden das Projekt nicht, obwohl wir den genauen Straßennamen angeben"). Der
Preis ist ein Raten-und-Bestätigen-Orakel ohne Wertpreisgabe — man muss die Adresse
bereits kennen, um sie zu bestätigen. Diese Abwägung ist getroffen, nicht übersehen.

### `row_scope='EIGENE'` — die Objektsicht (Migration 0099)

Bis zu diesem Slice galt: „Der Monteur findet seine Einsätze und sonst nichts." Das
war zu wenig. Wer zur Meldung „Heizkörper kalt" fährt, muss das **Objekt** finden —
und daran den Vorgang von vorgestern, den Auftrag der Kollegin, die Nummer des
Mieters. Die Zeilenbegrenzung ist jetzt für **jede** Kategorie definiert, an genau
einer Stelle (`db_core/services/objektsicht.py`):

| Kategorie | Begrenzung bei EIGENE |
|---|---|
| LIEGENSCHAFT | meine Objekte |
| PROJEKT | Projekte mit mindestens einer meiner Liegenschaften |
| VORGANG · AUFTRAG | an meinen Objekten |
| KONTAKT | Parties, die an einem meiner Objekte hängen |
| EINSATZ | **unverändert: eigene Zuweisung** — nicht das Objekt |
| ANGEBOT | **versendete/angenommene** Angebote an meinen Objekten (Migration 0102) |
| RECHNUNG · ARTIKEL · LEISTUNG · MITARBEITER | **keine Zeilen** |

Der **EINSATZ** bleibt bewusst an der Zuweisung: Sonst würde ein freier Termin
(Begehung, Beratung) für jeden auffindbar, der einmal am Objekt war. Über das
Objekt-Dossier sieht der Monteur die Einsätze der Kollegen ohnehin — dort ist es
eine Objekthistorie, hier wäre es eine Terminliste.

### ANGEBOT ja, RECHNUNG nein (Migration 0102)

Seit 0102 trägt MONTEUR `invoicing/LESEN` mit Scope EIGENE. Die Kategorie **ANGEBOT**
ist damit für ihn offen — begrenzt durch `objektsicht.eigene_angebote` (meine Objekte,
Status VERSENDET/ANGENOMMEN). Die Kategorie **RECHNUNG** bleibt zu: Sie hängt an
`sicht.invoicing` (Scope ALLE) und hat **bewusst keine** EIGENE-Variante.

Zwei Dinge halten das dicht, und beide muss man kennen, bevor man hier etwas ändert:

  * **Der Untertitel des Angebots trägt keinen Betrag** (`_titel_untertitel`:
    Nummer · Adresse · Status) — und `grund` nennt nur Feldnamen. Ein Treffer, der
    „14.814,72 €" in den Untertitel schriebe, machte die Suche zum Preisleck an der
    preisfreien Beleg-API vorbei.
  * **Der Direkttreffer-Pfad zieht aus `_basis_qs`**, also aus derselben begrenzten
    Grundmenge: Die exakte Angebotsnummer eines fremden Objekts (oder eines Entwurfs)
    findet **nichts**. Sonst wäre die Kennung der bequemste Nebeneingang.

Und **die Adresse**: Sie hing an `sicht.property`; jetzt an `sicht.darf_property()`.
Der Monteur darf die Straße seines Objekts natürlich sehen — genau das war der
Auslöser des Objektsicht-Slices.

## Grenzen (ehrlich benannt)

* **Index nur auf dem Artikelstamm.** `regexp_replace(...) LIKE '%…%'` braucht
  einen Ausdrucksindex, sonst ist jede Kategorie ein Seq-Scan. Migration 0098
  legt GIN-Trigramm-Indizes auf genau diesen Ausdruck — aber nur für
  `pricing.article`/`pricing.assembly`, die einzigen Tabellen, die je
  Hunderttausende Zeilen tragen (DATANORM-Vollimport). Gemessen an 800.000
  Artikeln: 8 Tokens **16,2 s → 0,3 ms**, breiter Einzelbegriff **2,0 s → 1,7 ms**.
  Die Bewegungsdaten (Aufträge, Belege, Kontakte) laufen weiter ohne Index über
  das LIMIT-Fenster; bei ihren Größenordnungen (Tausende Zeilen) ist das auf
  Jahre unkritisch. Wächst eine davon in die Hunderttausende, gehört sie in
  dieselbe Migrationslogik.
* **Zwei Zeichen finden keinen Artikel.** Ein Trigramm braucht drei (siehe
  `TRIGRAMM_MIN`). „42" trifft weiterhin Belegnummern, Hausnummern und
  Telefonnummern — nur nicht den Artikelstamm.
* Fremdakzente (é, ç …) fallen bei der Normalisierung weg statt entfaltet zu
  werden (kein `unblock`/`unaccent`-Modul vorausgesetzt).
* Telefonnummern werden nicht kanonisiert: „+49 30 …" und „030 …" bezeichnen
  dieselbe Nummer, teilen aber keine gemeinsame Ziffernfolge.
* Der Rang wird im **Fenster** (`_FENSTER` Zeilen je Kategorie) berechnet, nicht
  über die Gesamtmenge. Ein Rang-1-Treffer jenseits des Fensters kann fehlen —
  für den Fall der exakten Kennung fängt das der Direkttreffer-Pfad ab.
"""
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Count, Exists, F, Func, OuterRef, Q, TextField, Value
from django.db.models.functions import Coalesce, Lower, Replace

from db_core.models import (
    Article,
    Assembly,
    ContactPoint,
    Employee,
    Invoice,
    InvoiceParty,
    JobAssignment,
    Party,
    PartyAddress,
    Project,
    ProjectProperty,
    Property,
    PropertyPartyRole,
    Quote,
    ServiceCase,
    ServiceJob,
    WorkOrder,
    WorkOrderParty,
)
from db_core.services import objektsicht
from db_core.services.artikel import build_article_search_q

# Zeilen, die je Kategorie aus der DB geholt werden, bevor in Python gerangt und
# auf `pro_kategorie` gekürzt wird. Größer als die Ausgabemenge, damit das Ranking
# echte Auswahl trifft und nicht nur die zufällig jüngsten fünf sortiert.
_FENSTER = 25
PRO_KATEGORIE = 5
GESAMT_MAX = 30

# Ab dieser Länge (normalisiert) wird gesucht: mindestens EIN Token muss so lang
# sein. Ein einzelnes Zeichen trifft fast jede Zeile und ist keine Suche, sondern
# eine Liste — dafür gibt es die Listen.
MIN_LAENGE = 2

# Obergrenze der Tokenzahl. Jedes Token erzeugt eine eigene UND-Gruppe aus
# LIKE-Prädikaten und korrelierten EXISTS — ohne Grenze könnte ein einziger
# GET mit 500 Wörtern einen Worker minutenlang binden (die Ausdrücke sind nicht
# indexierbar). Mehr als acht Tokens grenzen keine Suche mehr ein, sie quälen nur
# die Datenbank; die überzähligen werden verworfen.
MAX_TOKENS = 8

# Ein Suchbegriff ist eine Eingabe, kein Dokument. Wer einen ganzen Absatz
# hineinkopiert, bekommt ihn stillschweigend gekürzt statt eines 422 — die Suche
# soll nicht meckern, sondern suchen.
MAX_BEGRIFF = 200

UMLAUTE = (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"))

# Feste Kategoriereihenfolge bei gleichem Rang (Leitstand-Logik: erst die Akte,
# dann der Beleg, dann der Stamm).
TYPEN = (
    "KONTAKT",
    "LIEGENSCHAFT",
    "PROJEKT",
    "VORGANG",
    "AUFTRAG",
    "EINSATZ",
    "ANGEBOT",
    "RECHNUNG",
    "ARTIKEL",
    "LEISTUNG",
    "MITARBEITER",
)
_TYP_RANG = {typ: i for i, typ in enumerate(TYPEN)}

# Kategorie → Modul der Rechtematrix (Dokumentation + Grundlage der `Sicht`).
MODUL_JE_TYP = {
    "KONTAKT": "identity",
    "LIEGENSCHAFT": "property",
    "PROJEKT": "workflow",
    "VORGANG": "workflow",
    "AUFTRAG": "workflow",
    "EINSATZ": "workflow",
    "ANGEBOT": "invoicing",
    "RECHNUNG": "invoicing",
    "ARTIKEL": "pricing",
    "LEISTUNG": "pricing",
    "MITARBEITER": "hr",
}


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


class Normalisiert(Func):
    """SQL-Ausdruck: `normalisieren()` auf einer Spalte (auch über Joins).

    NULL wird zu '' (coalesce) — ein Angebot im ENTWURF trägt keine quote_number,
    und daran darf die Suche nicht scheitern.
    """

    template = NORM_SQL
    output_field = TextField()


def _norm(pfad):
    return Normalisiert(F(pfad))


def _ziffern(pfad):
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
# Ergebnisformen
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Sicht:
    """Was der Suchende sehen darf — aus der Rechtematrix, nicht aus der Suche.

    Je Modul zwei Ebenen:

    * `identity`/`property`/`workflow` = row_scope **ALLE** (das ganze Haus).
    * `*_eigene` = row_scope **EIGENE**, die **Objektsicht**: dieselben Kategorien,
      aber nur auf meinen Objekten (`services/objektsicht.py`). `actor_id` ist dann
      Pflicht — ohne Akteur gibt es keine „eigenen" Zeilen, also gar keine.

    `workflow_eigene` deckt PROJEKT/VORGANG/AUFTRAG (objektbegrenzt) **und** EINSATZ
    ab — der Einsatz aber weiterhin über die **Zuweisung**, nicht über das Objekt
    (siehe Modul-Docstring).

    `invoicing_eigene` (Migration 0102) öffnet **ausschließlich die Kategorie
    ANGEBOT** — objektbegrenzt und nur versendet/angenommen. Die Kategorie RECHNUNG
    hängt weiter allein an `invoicing` (Scope ALLE): Sie ist die eine Ausnahme von
    „der Monteur darf alles sehen".

    `pricing` und `hr` haben **keine** EIGENE-Variante: Preise und Personaldaten sind
    nicht die Welt des Monteurs.
    """

    identity: bool = False
    property: bool = False
    workflow: bool = False
    identity_eigene: bool = False
    property_eigene: bool = False
    workflow_eigene: bool = False
    invoicing: bool = False
    invoicing_eigene: bool = False
    pricing: bool = False
    hr: bool = False
    actor_id: uuid.UUID | None = None

    def darf_identity(self):
        return self.identity or self.identity_eigene

    def darf_property(self):
        return self.property or self.property_eigene

    def darf_workflow(self):
        return self.workflow or self.workflow_eigene

    def darf_angebote(self):
        """ANGEBOT — mit Preisen (ALLE) oder objektbegrenzt (EIGENE).

        **Absichtlich ohne Gegenstück `darf_rechnungen()`.** Die Rechnung fragt
        `sicht.invoicing` direkt ab; eine Methode, die beide Ebenen zusammenzieht,
        wäre genau die Zeile, die jemand versehentlich auch für die Rechnung
        benutzt.
        """
        return self.invoicing or self.invoicing_eigene

    def hat_recht(self):
        """Darf der Suchende überhaupt irgendetwas sehen?

        Die leere Sicht ist der Fall „Konto ohne jede Rolle". Der Service liefert
        dann schlicht nichts (er kennt keine HTTP-Antworten); die API macht daraus
        ein 403 — nichts sehen dürfen ist etwas anderes als nichts finden.
        """
        return any((
            self.identity, self.property, self.workflow,
            self.identity_eigene, self.property_eigene, self.workflow_eigene,
            self.invoicing, self.invoicing_eigene, self.pricing, self.hr,
        ))


@dataclass
class Treffer:
    typ: str
    id: uuid.UUID
    titel: str
    untertitel: str
    status: str | None
    rang: int
    grund: str
    ist_direkttreffer: bool = False
    created_at: datetime | None = None


@dataclass
class Kategorie:
    typ: str
    anzahl: int
    mehr_vorhanden: bool


@dataclass
class Ergebnis:
    """Flache, ranggeordnete Trefferliste — bewusst nicht gruppiert.

    Eine Spotlight-Suche beantwortet „was meinst du?", nicht „welche Tabelle?".
    Der beste Treffer muss oben stehen, auch wenn er aus der letzten Kategorie
    kommt — eine Gruppierung würde den Rang der Kategorie unterordnen und genau
    die „elend lange Liste" erzeugen, über die sich der Nutzer beschwert. Wer
    gruppiert anzeigen will, gruppiert im Frontend nach `typ`; `kategorien`
    liefert dafür Anzahl und `mehr_vorhanden` je Kategorie.
    """

    begriff: str
    treffer: list = field(default_factory=list)
    direkttreffer: Treffer | None = None
    kategorien: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Rang und Begründung (Python-Seite, auf den geladenen Zeilen)
# ---------------------------------------------------------------------------
#
# Die DB entscheidet, WAS ein Treffer ist; hier wird entschieden, WARUM und WIE
# GUT. Beides benutzt dieselbe Normalisierung, also dieselbe Wahrheit — die
# Python-Seite arbeitet nur auf den ohnehin geladenen (max. `_FENSTER`) Zeilen.

@dataclass
class Feldgruppe:
    """Ein benanntes Bündel Textwerte einer Entität (für Rang + Begründung).

    `primaer=True` heißt: Feld der Entität selbst (Name/Titel/Nummer).
    `primaer=False` heißt: über eine Beziehung erreicht (Adresse, Beteiligter,
    Kontaktweg) — solche Treffer sind Rang 3.

    `ziffern=True` nur für Kontaktwege: Dort — und NUR dort — vergleicht auch die
    Datenbank zusätzlich die reine Ziffernform. Die Python-Seite muss denselben
    Zuschnitt haben, sonst begründet sie einen Treffer mit einem Feld, über das
    die DB gar nicht getroffen hat.
    """

    label: str
    werte: list
    primaer: bool = False
    ziffern: bool = False


def _trifft(gruppe, token):
    """(trifft, am_wortanfang) — Teilstring auf normalisierter Form."""
    trifft = False
    for wert in gruppe.werte:
        if not wert:
            continue
        norm = normalisieren(wert)
        if not norm:
            continue
        if token in norm:
            trifft = True
            # Wortanfang: irgendein Wort des Feldes beginnt mit dem Token.
            # („Badensche Straße" → Wörter [badensche, strasse]).
            for wort in re.split(r"[^0-9A-Za-zÄÖÜäöüß]+", wert):
                if wort and normalisieren(wort).startswith(token):
                    return True, True
        elif gruppe.ziffern and token.isdigit() and token in nur_ziffern(wert):
            # Telefonnummer: reine Ziffernform als zweiter Vergleich (genau wie
            # in `_kontaktwege_q` auf der SQL-Seite).
            trifft = True
    return trifft, False


def _rang_und_grund(gruppen, tokens):
    """Rang (1|2|3) und kurze Begründung („Adresse der Liegenschaft").

    Fällt eine Begründung leer aus, hat die DB über einen Weg getroffen, den die
    Feldgruppen nicht abbilden — praktisch nur bei den Hero-Operatoren der
    Artikelsuche („Zink*rinne"). Dann bleibt es bei „Treffer" und Rang 3: lieber
    eine ehrliche vage Auskunft als eine erfundene präzise.
    """
    alle_primaer = True
    alle_wortanfang = True
    labels = []
    for token in tokens:
        primaer_getroffen = False
        wortanfang = False
        for gruppe in gruppen:
            trifft, anfang = _trifft(gruppe, token)
            if not trifft:
                continue
            if gruppe.label not in labels:
                labels.append(gruppe.label)
            if gruppe.primaer:
                primaer_getroffen = True
                wortanfang = wortanfang or anfang
        if not primaer_getroffen:
            alle_primaer = False
        if not wortanfang:
            alle_wortanfang = False

    if alle_primaer and alle_wortanfang:
        rang = 1
    elif alle_primaer:
        rang = 2
    else:
        rang = 3
    grund = " · ".join(labels[:3]) if labels else "Treffer"
    return rang, grund


# ---------------------------------------------------------------------------
# Bausteine für die Querys
# ---------------------------------------------------------------------------

def _feld_q(felder, token):
    """ODER über alle (annotierten) Felder einer Entität für EIN Token."""
    q = Q()
    for feld in felder:
        q |= Q(**{f"{feld}__contains": token})
    return q


def _tokens_q(felder, tokens, exists_je_token=None):
    """UND über Tokens, ODER über Felder — plus optionale Exists-Zweige.

    `exists_je_token` ist eine Funktion token → Liste von Exists()-Ausdrücken;
    sie hängt die Beziehungssuche (Kontaktwege, Beteiligte, Liegenschaften) in
    denselben ODER-Zweig, ohne die Ergebnismenge durch Joins zu vervielfachen.
    """
    gesamt = None
    for token in tokens:
        oder = _feld_q(felder, token)
        for exists in (exists_je_token(token) if exists_je_token else []):
            oder |= exists
        gesamt = oder if gesamt is None else (gesamt & oder)
    return gesamt if gesamt is not None else Q(pk__isnull=True)


def _adresse_annotationen(praefix, alias):
    """Normalisierte Adressfelder über einen Pfad (Liegenschaft → Adresse)."""
    return {
        f"{alias}_street": _norm(f"{praefix}street"),
        f"{alias}_hn": _norm(f"{praefix}house_number"),
        f"{alias}_plz": _norm(f"{praefix}postal_code"),
        f"{alias}_city": _norm(f"{praefix}city"),
    }


def _adresse_text(adresse, sicht):
    """Adresse für den Untertitel — **getort auf `property/LESEN`**.

    Mit dem Recht: „Badensche Straße 53, 10825 Berlin".
    Ohne das Recht: „10825 Berlin" — Straße und Hausnummer bleiben weg.

    Die Suche zieht hier **keine neue Grenze**, sie hält sich an die bestehende:
    Die Listen der übrigen API (`api/auftrag.py`, `api/planung.py`) liefern zu
    einem Auftrag ebenfalls nur Objektnummer/-name und Ort — die volle
    Straßenadresse steht ausschließlich hinter den `property`-/`identity`-getorten
    Schemata. Ein Untertitel, der sie trotzdem zeigte, wäre der bequemste Weg, an
    genau der Toren vorbei zu lesen (und die Suche ist ein Endpunkt, den jeder
    aufruft).

    **Objektsicht:** `darf_property()`, nicht `property` — der Monteur SOLL die
    Straße seines Objekts sehen. Genau daran scheiterte er vorher: Er fand seinen
    Einsatz, aber nicht die Adresse, zu der er fahren musste. Die Zeilen, die er
    überhaupt sieht, sind bereits auf seine Objekte begrenzt (`_basis_qs`) — die
    Adresse verrät ihm also nichts, was er nicht ohnehin lesen darf.
    """
    if adresse is None:
        return None
    ort = " ".join(x for x in (adresse.postal_code, adresse.city) if x)
    if not sicht.darf_property():
        return ort or None
    strasse = " ".join(x for x in (adresse.street, adresse.house_number) if x)
    return ", ".join(x for x in (strasse, ort) if x) or None


def _untertitel(*teile):
    return " · ".join(str(t) for t in teile if t)


def _kontaktwege_q(pfad, token):
    """Exists über Kontaktwege einer Party (Text- UND Ziffernform)."""
    sub = (
        ContactPoint.objects.filter(**{pfad: OuterRef("pk")})
        .annotate(n_wert=_norm("value"), z_wert=_ziffern("value"))
        .filter(Q(n_wert__contains=token) | Q(z_wert__contains=token))
    )
    return Exists(sub)


def _fenster(qs):
    """Fenster laden (Reihenfolge deterministisch: neueste zuerst, dann id)."""
    return list(qs.order_by("-created_at", "id")[:_FENSTER])


# ---------------------------------------------------------------------------
# Direkttreffer — der eigene, vorgelagerte Pfad
# ---------------------------------------------------------------------------
#
# Nummernformate (von der DB vergeben):
#   OBJ-#####  MA-#####            → Präfix + Zähler
#   P|V|AU|E|AN|RE|GS -JJJJ-NNNNNN → Präfix + Jahr + Zähler
# Toleranz: Groß/Klein egal, Trennzeichen egal, führende Nullen egal.
# „an 2026 42", „AN-2026-42", „an2026000042" → AN-2026-000042.

_KENNUNG_EINFACH = {"OBJ": "LIEGENSCHAFT", "MA": "MITARBEITER"}
_KENNUNG_JAHR = {
    "P": "PROJEKT",
    "V": "VORGANG",
    "AU": "AUFTRAG",
    "E": "EINSATZ",
    "AN": "ANGEBOT",
    "RE": "RECHNUNG",
    "GS": "RECHNUNG",
}
_NUMMERNFELD = {
    "LIEGENSCHAFT": "property_number",
    "MITARBEITER": "employee_number",
    "PROJEKT": "project_number",
    "VORGANG": "case_number",
    "AUFTRAG": "order_number",
    "EINSATZ": "job_number",
    "ANGEBOT": "quote_number",
    "RECHNUNG": "invoice_number",
}


def kennung_parsen(begriff):
    """Erkennt eine Kennung im Begriff → (typ, kanonische Nummer) oder None.

    Es zählt nur der VOLLSTÄNDIGE Begriff: „AN-2026-42" ist eine Kennung,
    „Angebot AN-2026-42 für Meier" ist Volltext (und findet den Beleg dort
    ebenfalls, nur ohne den Rang-0-Sprung).
    """
    teile = [t for t in re.split(r"[^0-9A-Za-z]+", (begriff or "").strip()) if t]
    if len(teile) < 2:
        return None
    praefix = teile[0].upper()
    zahlen = teile[1:]
    if not all(z.isdigit() for z in zahlen):
        return None

    if praefix in _KENNUNG_EINFACH and len(zahlen) == 1:
        return _KENNUNG_EINFACH[praefix], f"{praefix}-{int(zahlen[0]):05d}"
    if praefix in _KENNUNG_JAHR and len(zahlen) == 2:
        jahr, zaehler = zahlen
        if len(jahr) != 4:
            return None
        return _KENNUNG_JAHR[praefix], f"{praefix}-{jahr}-{int(zaehler):06d}"
    return None


def _direkttreffer(begriff, sicht):
    """Kennung/GTIN/Artikelnummer exakt → Treffer auf Rang 0 (oder leere Liste).

    Läuft VOR der Ähnlichkeitssuche und völlig getrennt von ihr: Ein Direkttreffer
    kann nicht durch Ähnlichkeitsrauschen verdrängt werden. Die Rechte gelten
    unverändert — ohne `property/LESEN` gibt es auch auf „OBJ-00001" nichts.
    """
    treffer = []
    kennung = kennung_parsen(begriff)
    if kennung:
        typ, nummer = kennung
        qs = _basis_qs(typ, sicht)
        if qs is not None:
            # Bewusst `exact`, nicht `iexact`: `kennung_parsen` liefert bereits die
            # kanonische Form (Großbuchstaben, aufgefüllte Nullen) — genau die, die
            # die DB vergeben hat. `iexact` würde daraus `UPPER(spalte) = …` machen
            # und damit den UNIQUE-Index auf der Nummernspalte unbrauchbar.
            feld = _NUMMERNFELD[typ]
            obj = qs.filter(**{feld: nummer}).first()
            if obj is not None:
                treffer.append(_bauen(typ, obj, sicht, rang=0,
                                      grund="Kennung exakt", direkt=True))

    # Artikel-/Leistungsnummer und GTIN sind nutzergesetzt und haben kein Muster —
    # sie werden exakt verglichen (nicht normalisiert: eine GTIN ist eine GTIN).
    # Artikel-/Leistungsnummer und GTIN sind nutzergesetzt und haben kein Muster.
    #
    # KEIN `iexact`: Auf 800.000 Artikeln (DATANORM-Vollimport) wird daraus
    # `UPPER(article_number) = …` — der UNIQUE-Index ist damit tot, und JEDER
    # Tastendruck in der Suchpalette kostete einen Seq-Scan (gemessen: 0,6 s).
    # Stattdessen die realistischen Schreibweisen als indexgestützte
    # Gleichheitssuche (`IN`). Wer eine Artikelnummer in einer exotischen
    # Mischschreibung tippt, verliert nur den Rang-0-Sprung — die normalisierte
    # Tokensuche (indexgestützt) findet den Artikel trotzdem.
    #
    # Eindeutig sind article_number/gtin nur case-SENSITIV; die Nummer der einen
    # Zeile kann die GTIN einer anderen sein. `order_by("id")` macht die Wahl
    # deterministisch — sonst entschiede Postgres, wohin das Frontend springt.
    roh = (begriff or "").strip()
    if roh and sicht.pricing:
        varianten = {roh, roh.upper(), roh.lower()}
        artikel = Article.objects.filter(
            Q(article_number__in=varianten) | Q(gtin__in=varianten)
        ).order_by("id").first()
        if artikel is not None:
            treffer.append(_bauen("ARTIKEL", artikel, sicht, rang=0,
                                  grund="Artikelnummer/GTIN exakt", direkt=True))
        leistung = Assembly.objects.filter(
            assembly_number__in=varianten).order_by("id").first()
        if leistung is not None:
            treffer.append(_bauen("LEISTUNG", leistung, sicht, rang=0,
                                  grund="Leistungsnummer exakt", direkt=True))
    return treffer


def _basis_qs(typ, sicht):
    """Grundmenge einer Kategorie MIT Rechtefilter — None heißt „darf nicht".

    **Die einzige Stelle**, an der eine Kategorie ihre Zeilen bekommt: der
    Direkttreffer-Pfad zieht von hier genauso wie die Ähnlichkeitssuche. Deshalb
    öffnet eine exakte Kennung (`OBJ-00002`) keine Tür, die die Ähnlichkeitssuche
    verschlossen hält — der Bruchfall, an dem so etwas üblicherweise scheitert.
    """
    if typ == "KONTAKT" and sicht.darf_identity():
        # MERGED-Parties sind fachlich verschwunden (Dublette) — nie ein Treffer.
        qs = Party.objects.exclude(status="MERGED")
        if not sicht.identity:
            if sicht.actor_id is None:
                return None
            qs = qs.filter(objektsicht.eigene_party_q(sicht.actor_id)).distinct()
        return qs
    if typ == "LIEGENSCHAFT" and sicht.darf_property():
        # Die Einheitenzahl gehört zum Untertitel — sie hängt an der Grundmenge,
        # nicht an der Ähnlichkeitssuche, sonst zeigte der Direkttreffer
        # („OBJ-00001") sie als einziger Weg nicht an.
        qs = Property.objects.select_related("address").annotate(
            einheiten=Count("units", distinct=True)
        )
        if not sicht.property:
            if sicht.actor_id is None:
                return None
            qs = objektsicht.begrenzen(qs, "EIGENE", sicht.actor_id, "id")
        return qs
    if typ == "PROJEKT" and sicht.darf_workflow():
        qs = Project.objects.all()
        if not sicht.workflow:
            if sicht.actor_id is None:
                return None
            qs = objektsicht.begrenzen(
                qs, "EIGENE", sicht.actor_id, "property_links__property_id"
            ).distinct()
        return qs
    if typ == "VORGANG" and sicht.darf_workflow():
        qs = ServiceCase.objects.select_related(
            "property__address", "reported_by_party"
        )
        if not sicht.workflow:
            if sicht.actor_id is None:
                return None
            qs = objektsicht.begrenzen(qs, "EIGENE", sicht.actor_id, "property_id")
        return qs
    if typ == "AUFTRAG" and sicht.darf_workflow():
        qs = WorkOrder.objects.select_related("property__address")
        if not sicht.workflow:
            if sicht.actor_id is None:
                return None
            qs = objektsicht.begrenzen(qs, "EIGENE", sicht.actor_id, "property_id")
        return qs
    if typ == "EINSATZ" and (sicht.workflow or sicht.workflow_eigene):
        qs = ServiceJob.objects.select_related(
            "work_order", "property__address", "work_order__property__address"
        )
        if not sicht.workflow:
            # row_scope EIGENE: ausschließlich Einsätze mit eigener Zuweisung
            # (Muster: api/planung.py::list_einsaetze). Ohne Akteur keine Zeile.
            if sicht.actor_id is None:
                return None
            qs = qs.filter(
                Exists(JobAssignment.objects.filter(
                    service_job=OuterRef("pk"), assignee_id=sicht.actor_id,
                ))
            )
        return qs
    if typ == "ANGEBOT" and sicht.darf_angebote():
        qs = Quote.objects.select_related("property__address")
        if not sicht.invoicing:
            # Objektsicht: meine Objekte, nur versendet/angenommen — dieselbe
            # Grenze wie `GET /invoicing/quotes/mengen`, aus derselben Quelle.
            # Ohne Akteur keine Zeile (fail-closed).
            if sicht.actor_id is None:
                return None
            qs = objektsicht.angebote_begrenzen(qs, "EIGENE", sicht.actor_id)
        return qs
    if typ == "RECHNUNG" and sicht.invoicing:
        # KEINE EIGENE-Variante — die Rechnung ist die eine Ausnahme (0102).
        return Invoice.objects.select_related("property__address")
    if typ == "ARTIKEL" and sicht.pricing:
        return Article.objects.all()
    if typ == "LEISTUNG" and sicht.pricing:
        return Assembly.objects.all()
    if typ == "MITARBEITER" and sicht.hr:
        return Employee.objects.select_related("party")
    return None


# ---------------------------------------------------------------------------
# Treffer bauen: Titel, Untertitel MIT KONTEXT, Feldgruppen für Rang/Begründung
# ---------------------------------------------------------------------------
#
# Der Untertitel ist die eigentliche Arbeit dieser Suche: „Meier" allein hilft
# niemandem — „AU-2026-000012 · Badensche Straße 53 · IN_ARBEIT" beantwortet die
# Frage, ob es der gesuchte Datensatz ist, ohne ihn zu öffnen.

def _subtyp(obj, name):
    """Umgekehrte 1:1-Beziehung (person/organization) — fehlend heißt None.

    Ein einfaches `getattr(obj, "person", None)` reicht NICHT: Django wirft bei
    einer fehlenden umgekehrten OneToOne-Beziehung `RelatedObjectDoesNotExist`
    (auch mit select_related), und getattr fängt keine Exception ab. Eine
    Organisation hätte die Suche damit zum Absturz gebracht.
    """
    try:
        return getattr(obj, name)
    except ObjectDoesNotExist:
        return None


def _kontaktweg_gruppe(label, werte, sicht):
    """Kontaktweg-Feldgruppe — **nur mit `identity/LESEN`**, sonst leer.

    Ohne das Recht wird über Kontaktwege gar nicht erst gematcht (die
    Exists-Zweige in den Querys entfallen). Die Gruppe hier muss denselben
    Zuschnitt haben: Sonst begründete die Antwort einen Treffer mit einem
    „Kontaktweg des Beteiligten", über den die DB nie gesucht hat — und verriete
    damit genau das, was das fehlende Recht schützen soll.

    `darf_identity()`: Die Objektsicht darf über die Kontaktwege ihrer Objekt-Parties
    suchen — das ist der Sinn (die Nummer des Mieters). Die Grundmenge ist in
    `_basis_qs` bereits begrenzt.
    """
    return Feldgruppe(label, werte if sicht.darf_identity() else [], ziffern=True)


def _gruppen(typ, obj, sicht):
    """Feldgruppen einer geladenen Zeile (Grundlage von Rang und Begründung)."""
    if typ == "KONTAKT":
        person = _subtyp(obj, "person")
        orga = _subtyp(obj, "organization")
        gruppen = [Feldgruppe("Name", [obj.display_name], primaer=True)]
        if person is not None:
            gruppen.append(Feldgruppe(
                "Name", [person.first_name, person.last_name], primaer=True))
        if orga is not None:
            gruppen.append(Feldgruppe("Firmenname", [orga.legal_name], primaer=True))
            gruppen.append(Feldgruppe(
                "USt-IdNr./Registernummer",
                [orga.vat_id, orga.registration_number], primaer=True))
        gruppen.append(Feldgruppe(
            "Kontaktweg", [cp.value for cp in obj.contact_points.all()],
            ziffern=True))
        gruppen.append(Feldgruppe("Adresse des Kontakts", [
            teil
            for pa in obj.addresses.all()
            for teil in (pa.address.street, pa.address.house_number,
                         pa.address.postal_code, pa.address.city)
        ]))
        return gruppen

    if typ == "LIEGENSCHAFT":
        beteiligte = [r.party for r in obj.party_roles.all()]
        return [
            Feldgruppe("Objektnummer", [obj.property_number], primaer=True),
            Feldgruppe("Name", [obj.name], primaer=True),
            Feldgruppe("Adresse", [obj.address.street, obj.address.house_number,
                                   obj.address.postal_code, obj.address.city]),
            Feldgruppe("Beteiligter", [p.display_name for p in beteiligte]),
            _kontaktweg_gruppe("Kontaktweg des Beteiligten", [
                cp.value for p in beteiligte for cp in p.contact_points.all()],
                sicht),
        ]

    if typ == "PROJEKT":
        namen, adressen = [], []
        for link in obj.property_links.all():
            adr = link.property.address
            namen += [link.property.name, link.property.property_number]
            adressen += [adr.street, adr.house_number, adr.postal_code, adr.city]
        return [
            Feldgruppe("Projektnummer", [obj.project_number], primaer=True),
            Feldgruppe("Name", [obj.name], primaer=True),
            Feldgruppe("Adresse der Liegenschaft", adressen),
            Feldgruppe("Liegenschaft", namen),
        ]

    if typ == "VORGANG":
        adr = obj.property.address
        melder = obj.reported_by_party
        return [
            Feldgruppe("Vorgangsnummer", [obj.case_number], primaer=True),
            Feldgruppe("Betreff", [obj.subject], primaer=True),
            Feldgruppe("Adresse der Liegenschaft", [
                adr.street, adr.house_number, adr.postal_code, adr.city]),
            Feldgruppe("Melder", [melder.display_name] if melder else []),
            _kontaktweg_gruppe("Kontaktweg des Melders", [
                cp.value for cp in melder.contact_points.all()] if melder else [],
                sicht),
        ]

    if typ == "AUFTRAG":
        adr = obj.property.address
        beteiligte = [p.party for p in obj.parties.all()]
        return [
            Feldgruppe("Auftragsnummer", [obj.order_number], primaer=True),
            Feldgruppe("Titel", [obj.title], primaer=True),
            Feldgruppe("Adresse der Liegenschaft", [
                adr.street, adr.house_number, adr.postal_code, adr.city]),
            Feldgruppe("Beteiligter", [p.display_name for p in beteiligte]),
            _kontaktweg_gruppe("Kontaktweg des Beteiligten", [
                cp.value for p in beteiligte for cp in p.contact_points.all()],
                sicht),
        ]

    if typ == "EINSATZ":
        prop = obj.property or (obj.work_order.property if obj.work_order else None)
        adr = prop.address if prop else None
        auftrag = obj.work_order
        return [
            Feldgruppe("Einsatznummer", [obj.job_number], primaer=True),
            Feldgruppe("Titel", [obj.title], primaer=True),
            Feldgruppe("Auftrag", [auftrag.order_number, auftrag.title]
                       if auftrag else []),
            Feldgruppe("Adresse der Liegenschaft", [
                adr.street, adr.house_number, adr.postal_code, adr.city]
                if adr else []),
        ]

    if typ == "ANGEBOT":
        adr = obj.property.address
        return [
            Feldgruppe("Angebotsnummer", [obj.quote_number], primaer=True),
            Feldgruppe("Titel", [obj.title], primaer=True),
            Feldgruppe("Adresse der Liegenschaft", [
                adr.street, adr.house_number, adr.postal_code, adr.city]),
        ]

    if typ == "RECHNUNG":
        adr = obj.property.address
        beteiligte = [p.party for p in obj.parties.all()]
        return [
            Feldgruppe("Rechnungsnummer", [obj.invoice_number], primaer=True),
            Feldgruppe("Adresse der Liegenschaft", [
                adr.street, adr.house_number, adr.postal_code, adr.city]),
            Feldgruppe("Beteiligter", [p.display_name for p in beteiligte]),
            _kontaktweg_gruppe("Kontaktweg des Beteiligten", [
                cp.value for p in beteiligte for cp in p.contact_points.all()],
                sicht),
        ]

    if typ == "ARTIKEL":
        return [
            Feldgruppe("Artikelnummer", [obj.article_number], primaer=True),
            Feldgruppe("Bezeichnung", [obj.description], primaer=True),
            Feldgruppe("Matchcode", [obj.matchcode], primaer=True),
            Feldgruppe("Hersteller", [obj.manufacturer_name,
                                      obj.manufacturer_number]),
            Feldgruppe("GTIN", [obj.gtin]),
        ]

    if typ == "LEISTUNG":
        return [
            Feldgruppe("Leistungsnummer", [obj.assembly_number], primaer=True),
            Feldgruppe("Bezeichnung", [obj.name, obj.internal_name], primaer=True),
        ]

    if typ == "MITARBEITER":
        return [
            Feldgruppe("Personalnummer", [obj.employee_number], primaer=True),
            Feldgruppe("Name", [obj.party.first_name, obj.party.last_name],
                       primaer=True),
        ]
    raise ValueError(f"Unbekannter Trefferttyp '{typ}'.")


def _name_wenn_erlaubt(name, sicht):
    """Personen-/Beteiligtenname im Untertitel — nur mit `identity/LESEN`.

    Sonst entstünde aus Suche + Untertitel ein Auskunftsdienst: „E-Mail rein,
    Name raus" — für ein Konto, das identity gerade NICHT lesen darf.
    """
    return name if sicht.darf_identity() else None


def _titel_untertitel(typ, obj, sicht):
    """(titel, untertitel, status) — der Untertitel trägt den KONTEXT.

    Was darin steht, hängt am Recht: die Straßenadresse nur mit `property`
    (`_adresse_text`), Personennamen nur mit `identity` (`_name_wenn_erlaubt`).
    Ohne diese Rechte bleibt der Untertitel sachlich (Nummer, Ort, Status) — er
    wird nie zum Schlupfloch an den Toren der übrigen API vorbei.
    """
    if typ == "KONTAKT":
        art = "Person" if obj.party_type == "PERSON" else "Organisation"
        adresse = next(
            (_adresse_text(pa.address, sicht) for pa in obj.addresses.all()), None)
        weg = next((cp.value for cp in obj.contact_points.all()), None)
        return obj.display_name, _untertitel(art, adresse, weg), obj.status

    if typ == "LIEGENSCHAFT":
        einheiten = getattr(obj, "einheiten", None)
        einheiten_text = (
            f"{einheiten} Einheit{'en' if einheiten != 1 else ''}"
            if einheiten else None
        )
        return (
            obj.name,
            _untertitel(obj.property_type, _adresse_text(obj.address, sicht),
                        einheiten_text, obj.property_number),
            obj.status,
        )

    if typ == "PROJEKT":
        adressen = [
            _adresse_text(link.property.address, sicht)
            for link in list(obj.property_links.all())[:2]
        ]
        return (
            obj.name,
            _untertitel(obj.project_number, *adressen),
            obj.status,
        )

    if typ == "VORGANG":
        melder = obj.reported_by_party.display_name if obj.reported_by_party else None
        return (
            obj.subject,
            _untertitel(obj.case_number,
                        _adresse_text(obj.property.address, sicht),
                        _name_wenn_erlaubt(melder, sicht), obj.status),
            obj.status,
        )

    if typ == "AUFTRAG":
        return (
            obj.title,
            _untertitel(obj.order_number,
                        _adresse_text(obj.property.address, sicht), obj.status),
            obj.status,
        )

    if typ == "EINSATZ":
        auftrag = obj.work_order
        prop = obj.property or (auftrag.property if auftrag else None)
        titel = obj.title or (auftrag.title if auftrag else None) or obj.job_number
        termin = (
            obj.scheduled_start.strftime("%d.%m.%Y %H:%M")
            if obj.scheduled_start else "ohne Termin"
        )
        return (
            titel,
            _untertitel(obj.job_number, auftrag.order_number if auftrag else None,
                        _adresse_text(prop.address, sicht) if prop else None,
                        termin, obj.status),
            obj.status,
        )

    if typ == "ANGEBOT":
        return (
            obj.title,
            _untertitel(obj.quote_number or "ohne Nummer (Entwurf)",
                        _adresse_text(obj.property.address, sicht), obj.status),
            obj.status,
        )

    if typ == "RECHNUNG":
        empfaenger = next(
            (p.party.display_name for p in obj.parties.all()), None)
        return (
            f"{obj.invoice_type} {obj.invoice_number or '(Entwurf)'}",
            _untertitel(_adresse_text(obj.property.address, sicht),
                        _name_wenn_erlaubt(empfaenger, sicht), obj.status),
            obj.status,
        )

    if typ == "ARTIKEL":
        return (
            obj.description,
            _untertitel(obj.article_number, obj.manufacturer_name, obj.unit,
                        obj.status),
            obj.status,
        )

    if typ == "LEISTUNG":
        return (
            obj.name,
            _untertitel(obj.assembly_number, obj.unit, obj.status),
            obj.status,
        )

    if typ == "MITARBEITER":
        return (
            f"{obj.party.first_name} {obj.party.last_name}",
            _untertitel(obj.employee_number, obj.status),
            obj.status,
        )
    raise ValueError(f"Unbekannter Trefferttyp '{typ}'.")


def _bauen(typ, obj, sicht, *, rang, grund, direkt=False):
    titel, untertitel, status = _titel_untertitel(typ, obj, sicht)
    return Treffer(
        typ=typ,
        id=obj.id,
        titel=titel or "(ohne Bezeichnung)",
        untertitel=untertitel,
        status=status,
        rang=rang,
        grund=grund,
        ist_direkttreffer=direkt,
        created_at=getattr(obj, "created_at", None),
    )


# ---------------------------------------------------------------------------
# Die Kategorie-Querys
# ---------------------------------------------------------------------------

def _kontakte(tokens, sicht):
    qs = _basis_qs("KONTAKT", sicht)
    if qs is None:
        return []
    felder = ("n_display", "n_first", "n_last", "n_legal", "n_vat", "n_reg")
    qs = qs.annotate(
        n_display=_norm("display_name"),
        n_first=_norm("person__first_name"),
        n_last=_norm("person__last_name"),
        n_legal=_norm("organization__legal_name"),
        n_vat=_norm("organization__vat_id"),
        n_reg=_norm("organization__registration_number"),
    )

    def beziehungen(token):
        adresse = (
            PartyAddress.objects.filter(party=OuterRef("pk"))
            .annotate(**_adresse_annotationen("address__", "a"))
            .filter(_feld_q(("a_street", "a_hn", "a_plz", "a_city"), token))
        )
        return [_kontaktwege_q("party", token), Exists(adresse)]

    qs = qs.filter(_tokens_q(felder, tokens, beziehungen)).select_related(
        "person", "organization"
    ).prefetch_related("contact_points", "addresses__address")
    return _fenster(qs)


def _liegenschaften(tokens, sicht):
    qs = _basis_qs("LIEGENSCHAFT", sicht)
    if qs is None:
        return []
    felder = ("n_nummer", "n_name", "a_street", "a_hn", "a_plz", "a_city")
    qs = qs.annotate(
        n_nummer=_norm("property_number"),
        n_name=_norm("name"),
        **_adresse_annotationen("address__", "a"),
    )

    def beteiligte(token):
        namen = (
            PropertyPartyRole.objects.filter(property=OuterRef("pk"))
            .annotate(n_party=_norm("party__display_name"))
            .filter(n_party__contains=token)
        )
        if not sicht.darf_identity():
            return [Exists(namen)]
        # Auch der Kontaktweg der Eigentümergemeinschaft findet ihre Liegenschaft
        # („eine Mail, die darin vorkommt") — aber nur mit `identity/LESEN`.
        wege = (
            ContactPoint.objects
            .filter(party__property_roles__property=OuterRef("pk"))
            .annotate(n_wert=_norm("value"), z_wert=_ziffern("value"))
            .filter(Q(n_wert__contains=token) | Q(z_wert__contains=token))
        )
        return [Exists(namen), Exists(wege)]

    qs = qs.filter(_tokens_q(felder, tokens, beteiligte)).prefetch_related(
        "party_roles__party__contact_points"
    )
    return _fenster(qs)


def _projekte(tokens, sicht):
    qs = _basis_qs("PROJEKT", sicht)
    if qs is None:
        return []
    felder = ("n_nummer", "n_name")
    qs = qs.annotate(n_nummer=_norm("project_number"), n_name=_norm("name"))

    def liegenschaften(token):
        sub = (
            ProjectProperty.objects.filter(project=OuterRef("pk"))
            .annotate(
                n_pname=_norm("property__name"),
                n_pnummer=_norm("property__property_number"),
                **_adresse_annotationen("property__address__", "a"),
            )
            .filter(_feld_q(
                ("n_pname", "n_pnummer", "a_street", "a_hn", "a_plz", "a_city"),
                token,
            ))
        )
        return [Exists(sub)]

    qs = qs.filter(_tokens_q(felder, tokens, liegenschaften)).prefetch_related(
        "property_links__property__address"
    )
    return _fenster(qs)


def _vorgaenge(tokens, sicht):
    qs = _basis_qs("VORGANG", sicht)
    if qs is None:
        return []
    felder = ("n_nummer", "n_subject", "n_melder",
              "a_street", "a_hn", "a_plz", "a_city")
    qs = qs.annotate(
        n_nummer=_norm("case_number"),
        n_subject=_norm("subject"),
        n_melder=_norm("reported_by_party__display_name"),
        **_adresse_annotationen("property__address__", "a"),
    )

    # Kontaktwege des Melders: Exists über die Kontaktwege genau seiner Party.
    # („Eine Mail, die darin vorkommt, findet er nicht" — hier findet sie er.)
    # Nur mit `identity/LESEN`: Sonst wäre die Suche ein Auskunftsdienst, der aus
    # einer E-Mail-Adresse den zugehörigen Vorgang (und über den Untertitel den
    # Namen) auflöst — für ein Konto, das Kontaktdaten gar nicht lesen darf.
    def beziehungen(token):
        if not sicht.darf_identity():
            return []
        sub = (
            ContactPoint.objects.filter(party=OuterRef("reported_by_party"))
            .annotate(n_wert=_norm("value"), z_wert=_ziffern("value"))
            .filter(Q(n_wert__contains=token) | Q(z_wert__contains=token))
        )
        return [Exists(sub)]

    qs = qs.filter(_tokens_q(felder, tokens, beziehungen)).prefetch_related(
        "reported_by_party__contact_points"
    )
    return _fenster(qs)


def _auftraege(tokens, sicht):
    qs = _basis_qs("AUFTRAG", sicht)
    if qs is None:
        return []
    felder = ("n_nummer", "n_title", "a_street", "a_hn", "a_plz", "a_city")
    qs = qs.annotate(
        n_nummer=_norm("order_number"),
        n_title=_norm("title"),
        **_adresse_annotationen("property__address__", "a"),
    )

    def beteiligte(token):
        namen = (
            WorkOrderParty.objects.filter(work_order=OuterRef("pk"))
            .annotate(n_party=_norm("party__display_name"))
            .filter(n_party__contains=token)
        )
        if not sicht.darf_identity():
            return [Exists(namen)]
        wege = (
            ContactPoint.objects
            .filter(party__work_order_roles__work_order=OuterRef("pk"))
            .annotate(n_wert=_norm("value"), z_wert=_ziffern("value"))
            .filter(Q(n_wert__contains=token) | Q(z_wert__contains=token))
        )
        return [Exists(namen), Exists(wege)]

    qs = qs.filter(_tokens_q(felder, tokens, beteiligte)).prefetch_related(
        "parties__party__contact_points"
    )
    return _fenster(qs)


def _einsaetze(tokens, sicht):
    qs = _basis_qs("EINSATZ", sicht)
    if qs is None:
        return []
    felder = (
        "n_nummer", "n_title", "n_ordernr", "n_ordertitle",
        "a_street", "a_hn", "a_plz", "a_city",
        "o_street", "o_hn", "o_plz", "o_city",
    )
    qs = qs.annotate(
        n_nummer=_norm("job_number"),
        n_title=_norm("title"),
        n_ordernr=_norm("work_order__order_number"),
        n_ordertitle=_norm("work_order__title"),
        # Adresse: am Einsatz selbst ODER über den Auftrag (freier Termin hat
        # oft keine eigene Liegenschaft, ein auftragsgebundener Einsatz oft
        # keine eigene property_id).
        **_adresse_annotationen("property__address__", "a"),
        **_adresse_annotationen("work_order__property__address__", "o"),
    )
    qs = qs.filter(_tokens_q(felder, tokens))
    return _fenster(qs)


def _angebote(tokens, sicht):
    qs = _basis_qs("ANGEBOT", sicht)
    if qs is None:
        return []
    felder = ("n_nummer", "n_title", "a_street", "a_hn", "a_plz", "a_city")
    qs = qs.annotate(
        # quote_number ist im ENTWURF NULL — Coalesce in `_norm` fängt das ab.
        n_nummer=_norm("quote_number"),
        n_title=_norm("title"),
        **_adresse_annotationen("property__address__", "a"),
    )
    qs = qs.filter(_tokens_q(felder, tokens))
    return _fenster(qs)


def _rechnungen(tokens, sicht):
    qs = _basis_qs("RECHNUNG", sicht)
    if qs is None:
        return []
    # Die Rechnung hat KEIN Titelfeld — ihre Identität sind Nummer, Typ und
    # Beteiligte. Im ENTWURF ist auch die Nummer NULL; dann bleibt allein der
    # Weg über Adresse und Beteiligte. Genau dafür ist die Beziehungssuche da.
    felder = ("n_nummer", "a_street", "a_hn", "a_plz", "a_city")
    qs = qs.annotate(
        n_nummer=_norm("invoice_number"),
        **_adresse_annotationen("property__address__", "a"),
    )

    def beteiligte(token):
        namen = (
            InvoiceParty.objects.filter(invoice=OuterRef("pk"))
            .annotate(n_party=_norm("party__display_name"))
            .filter(n_party__contains=token)
        )
        if not sicht.darf_identity():
            return [Exists(namen)]
        # Die Rechnung hat keinen Titel — der Kontaktweg des Schuldners ist einer
        # der wenigen sprechenden Wege zu ihr. Genau die Beschwerde des Nutzers.
        wege = (
            ContactPoint.objects
            .filter(party__invoice_roles__invoice=OuterRef("pk"))
            .annotate(n_wert=_norm("value"), z_wert=_ziffern("value"))
            .filter(Q(n_wert__contains=token) | Q(z_wert__contains=token))
        )
        return [Exists(namen), Exists(wege)]

    qs = qs.filter(_tokens_q(felder, tokens, beteiligte)).prefetch_related(
        "parties__party__contact_points"
    )
    return _fenster(qs)


HERO_OPERATOREN = ("+", "|", "*")

# Ein Trigramm-Index kann erst ab DREI Zeichen greifen — das ist keine Einstellung,
# das ist die Definition eines Trigramms. Ein zweistelliges Token („a1") ist damit
# auf `pricing.article` unindizierbar: gemessen 1,1 s auf 800.000 Zeilen, weil die
# Datenbank jede Zeile anfassen muss. Deshalb durchsucht die **Artikel**-Kategorie
# (die einzige, die je Hunderttausende Zeilen trägt) nur Begriffe, die mindestens
# ein trigrammfähiges Token enthalten. „42" findet weiterhin Belegnummern,
# Hausnummern und Telefonnummern — nur eben keine Artikel, in deren Nummer
# irgendwo eine 42 steckt. Das wäre ohnehin kein Suchergebnis, sondern Rauschen.
TRIGRAMM_MIN = 3


def _artikel(tokens, sicht, begriff):
    """Artikel: ENTWEDER Hero-Operatoren (`+ | *`) ODER normalisierte Tokensuche.

    **Entweder/oder, nicht beides — das ist eine Performance-Entscheidung mit
    Messwerten.** Ursprünglich waren beide Zweige mit `|` verknüpft („der Nutzer
    bekommt beides"). Ein ODER über zwei Zweige ist aber nur so schnell wie sein
    langsamster: Der Planer muss jede Zeile prüfen, für die auch nur EIN Zweig
    nicht indexgestützt ist. Gemessen an 800.000 Artikeln (dem realen Umfang des
    geplanten DATANORM-Vollimports): **8 Tokens = 16,2 s, Seq Scan**. Die
    Suchpalette feuert alle 200 ms — das ist kein langsamer Endpunkt, das ist ein
    DoS-Hebel gegen die eigene Datenbank.

    Jetzt:
      * Begriff MIT Operator (`Zink*rinne`, `Rohr+DN100`) → ausschließlich
        `build_article_search_q` (die bestehende, getestete Hero-Suche; sie nutzt
        die Trigramm-Indizes aus Migration 0038).
      * Begriff OHNE Operator (der Regelfall) → ausschließlich die normalisierte
        Tokensuche, die auf den Ausdrucksindizes aus Migration 0098 läuft.

    Der Nutzer verliert nichts: Ein Begriff ohne Operatoren hätte im Hero-Zweig
    ohnehin nur denselben Teilstring gesucht — bloß ohne Umlaut-/Leerzeichen-
    Normalisierung und ohne UND über mehrere Wörter.
    """
    qs = _basis_qs("ARTIKEL", sicht)
    if qs is None:
        return []
    if any(op in begriff for op in HERO_OPERATOREN):
        hero = build_article_search_q(begriff)
        return _fenster(qs.filter(hero)) if hero is not None else []

    # Kein trigrammfähiges Token → die Abfrage wäre auf dem Vollkatalog ein
    # Vollscan (siehe TRIGRAMM_MIN). Die exakte Artikelnummer/GTIN findet
    # weiterhin der Direkttreffer-Pfad, der über den UNIQUE-Index läuft.
    if max(len(t) for t in tokens) < TRIGRAMM_MIN:
        return []

    felder = ("n_nummer", "n_desc", "n_match", "n_hersteller", "n_hnummer",
              "n_gtin")
    qs = qs.annotate(
        n_nummer=_norm("article_number"),
        n_desc=_norm("description"),
        n_match=_norm("matchcode"),
        n_hersteller=_norm("manufacturer_name"),
        n_hnummer=_norm("manufacturer_number"),
        n_gtin=_norm("gtin"),
    )
    return _fenster(qs.filter(_tokens_q(felder, tokens)))


def _leistungen(tokens, sicht):
    qs = _basis_qs("LEISTUNG", sicht)
    if qs is None:
        return []
    felder = ("n_nummer", "n_name", "n_intern", "n_desc")
    qs = qs.annotate(
        n_nummer=_norm("assembly_number"),
        n_name=_norm("name"),
        n_intern=_norm("internal_name"),
        n_desc=_norm("description"),
    )
    return _fenster(qs.filter(_tokens_q(felder, tokens)))


def _mitarbeiter(tokens, sicht):
    qs = _basis_qs("MITARBEITER", sicht)
    if qs is None:
        return []
    felder = ("n_nummer", "n_first", "n_last")
    qs = qs.annotate(
        n_nummer=_norm("employee_number"),
        n_first=_norm("party__first_name"),
        n_last=_norm("party__last_name"),
    )
    return _fenster(qs.filter(_tokens_q(felder, tokens)))


_SUCHRAUM = {
    "KONTAKT": _kontakte,
    "LIEGENSCHAFT": _liegenschaften,
    "PROJEKT": _projekte,
    "VORGANG": _vorgaenge,
    "AUFTRAG": _auftraege,
    "EINSATZ": _einsaetze,
    "ANGEBOT": _angebote,
    "RECHNUNG": _rechnungen,
    "LEISTUNG": _leistungen,
    "MITARBEITER": _mitarbeiter,
}


# ---------------------------------------------------------------------------
# Einstieg
# ---------------------------------------------------------------------------

def suche(begriff, *, sicht, pro_kategorie=PRO_KATEGORIE, gesamt_max=GESAMT_MAX):
    """Globale Suche. Rein lesend — kein `business_transaction`, kein Write.

    Ablauf: (1) Direkttreffer-Pfad (Kennung/GTIN, Rang 0, Position 1),
    (2) Ähnlichkeitssuche je Kategorie (nur die erlaubten), (3) Rang + Begründung
    auf den geladenen Zeilen, (4) Sortierung und Kürzung.

    Ein leerer oder zu kurzer Begriff liefert ein leeres Ergebnis — keinen Fehler.
    """
    begriff = (begriff or "").strip()[:MAX_BEGRIFF]
    tokens = tokenisieren(begriff)
    # Mindestens EIN Token muss tragen. Sonst („a", „a b") wäre jede Bedingung ein
    # `LIKE '%a%'` über jede Tabelle — das ist keine Suche, das ist ein Scan.
    if not tokens or max(len(t) for t in tokens) < MIN_LAENGE:
        return Ergebnis(begriff=begriff)

    direkte = _direkttreffer(begriff, sicht)
    gesehen = {(t.typ, t.id) for t in direkte}

    treffer = list(direkte)
    mehr = {}
    for typ in TYPEN:
        if typ == "ARTIKEL":
            zeilen = _artikel(tokens, sicht, begriff)
        else:
            zeilen = _SUCHRAUM[typ](tokens, sicht)
        if not zeilen:
            continue
        gerangt = []
        for obj in zeilen:
            if (typ, obj.id) in gesehen:
                continue  # steht schon als Direkttreffer in der Liste
            rang, grund = _rang_und_grund(_gruppen(typ, obj, sicht), tokens)
            gerangt.append(_bauen(typ, obj, sicht, rang=rang, grund=grund))
        gerangt.sort(key=_sortierschluessel)
        # `mehr_vorhanden`: Im Fenster lagen mehr Zeilen, als ausgeliefert werden —
        # ODER das Fenster war voll, dann liegt hinter ihm womöglich noch mehr.
        mehr[typ] = len(gerangt) > pro_kategorie or len(zeilen) >= _FENSTER
        treffer.extend(gerangt[:pro_kategorie])

    treffer.sort(key=_sortierschluessel)
    vor_grenze = {typ: sum(1 for t in treffer if t.typ == typ) for typ in TYPEN}
    treffer = treffer[:gesamt_max]

    # Die Zählung entsteht aus der AUSGELIEFERTEN Liste (also NACH der
    # Gesamtgrenze) — sonst verspräche `anzahl` Treffer, die gar nicht mitkommen.
    # Kürzt die Gesamtgrenze eine Kategorie (bis auf null), bleibt sie trotzdem in
    # der Liste stehen: `anzahl=0, mehr_vorhanden=True` sagt „hier gäbe es etwas,
    # es hat nur nicht mehr hineingepasst". Ein stilles Weglassen wäre von „nichts
    # gefunden" nicht zu unterscheiden.
    kategorien = []
    for typ in TYPEN:
        if not vor_grenze[typ]:
            continue
        anzahl = sum(1 for t in treffer if t.typ == typ)
        kategorien.append(Kategorie(
            typ=typ,
            anzahl=anzahl,
            mehr_vorhanden=mehr.get(typ, False) or anzahl < vor_grenze[typ],
        ))
    return Ergebnis(
        begriff=begriff,
        treffer=treffer,
        direkttreffer=direkte[0] if len(direkte) == 1 else None,
        kategorien=kategorien,
    )


def _sortierschluessel(t):
    """(Rang, Kategorie, neueste zuerst, id) — vollständig deterministisch."""
    stempel = -t.created_at.timestamp() if t.created_at else 0.0
    return (t.rang, _TYP_RANG[t.typ], stempel, str(t.id))
