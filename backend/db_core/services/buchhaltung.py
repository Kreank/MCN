"""Buchhaltungs-Service: Zahlungen erfassen/stornieren und Mahnstufen erzeugen.

Baut auf den veröffentlichten Rechnungen aus dem Beleg-Slice auf. Wie die übrigen
Services laufen alle Writes über business_transaction; die fachlichen DB-Tore
(Zahlung nur auf veröffentlichte Rechnung B-23; Mahnung nur auf veröffentlichte,
fällige Rechnung mit lückenlos aufsteigender Stufe B-22) prüft die DB als Trigger
und wird über as_business_error in 422 übersetzt.

**Zahlungs-Vorzeichenkonvention (App-seitig, die DB erzwingt kein Vorzeichen):**
Beträge werden stets als positiver Betrag erfasst; das Vorzeichen für den offenen
Posten ergibt sich aus dem payment_type — Geldeingänge reduzieren den offenen
Betrag, Rückerstattungen/Storno-Buchungen erhöhen ihn wieder. `PAYMENT_SIGN`
ist die eine Quelle dieser Konvention (auch die API-Ableitung nutzt sie).

**Storno einer Zahlung:** `invoicing.payment` ist append-only — eine Zahlung wird
nie gelöscht, sondern durch eine Gegenbuchung (payment_type='STORNO_BUCHUNG')
neutralisiert. Ein FK auf die Ursprungszahlung fehlt im Schema; die Verknüpfung
wird über external_reference ('STORNO:<id>') hergestellt.
"""
import uuid
from datetime import date
from decimal import Decimal

from django.db.models import (
    Case,
    DecimalField,
    ExpressionWrapper,
    F,
    Max,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Coalesce, Greatest, Least

from db_core.db_context import business_transaction
from db_core.gate_errors import as_business_error
from db_core.models import DunningLevel, DunningNotice, Invoice, Payment
from db_core.services import beleg as beleg_service
from db_core.services._validation import ensure_exists

# Beitrag je payment_type zum bezahlten Betrag (+1 = Geldeingang reduziert den
# offenen Posten, -1 = Rückfluss/Storno erhöht ihn wieder). Einzige Quelle der
# Vorzeichenkonvention — die API-Ableitung importiert diese Tabelle.
PAYMENT_SIGN = {
    "ZAHLUNG": 1,
    "TEILZAHLUNG": 1,
    "UEBERZAHLUNG": 1,
    "RUECKERSTATTUNG": -1,
    "STORNO_BUCHUNG": -1,
}
PAYMENT_TYPES = tuple(PAYMENT_SIGN)

# Typen, die über record_payment erfassbar sind. STORNO_BUCHUNG fehlt bewusst:
# sie entsteht nur als Gegenbuchung in reverse_payment (Recht STORNIEREN) und
# ist dort an die Ursprungszahlung gekoppelt.
RECORDABLE_PAYMENT_TYPES = tuple(t for t in PAYMENT_SIGN if t != "STORNO_BUCHUNG")

# ---------------------------------------------------------------------------
# Der abgeleitete Zahlungsstand — EINE Rechenstelle
# ---------------------------------------------------------------------------
# Weder der bezahlte Betrag noch der offene Posten stehen in der Datenbank: beides
# wird aus der vorzeichenbehafteten Summe der Zahlungen abgeleitet (PAYMENT_SIGN).
# Diese Ableitung lag bisher **dreimal** im Repo (api/buchhaltung, mahnlauf und —
# beim Bau der Dossiers — beinahe ein viertes Mal). Sie steht deshalb jetzt hier,
# neben der Vorzeichenkonvention, die sie benutzt. Alle Aufrufer importieren von
# hier; es gibt keine zweite Definition von „offen".

_ZERO = Decimal("0.00")
_GELD = DecimalField(max_digits=15, decimal_places=2)
_POSITIVE_TYPES = tuple(t for t, s in PAYMENT_SIGN.items() if s > 0)
_NEGATIVE_TYPES = tuple(t for t, s in PAYMENT_SIGN.items() if s < 0)


def _null():
    return Value(_ZERO, output_field=_GELD)


def _geld(expr):
    return ExpressionWrapper(expr, output_field=_GELD)


def _signierte_zahlung():
    """CASE-Ausdruck: der vorzeichenbehaftete Beitrag EINER Zahlung (PAYMENT_SIGN)."""
    return Case(
        When(payment_type__in=_POSITIVE_TYPES, then=F("amount")),
        When(payment_type__in=_NEGATIVE_TYPES, then=-F("amount")),
        default=Value(0),
        output_field=_GELD,
    )


def _zahlungssumme_zu(ref):
    """Subquery: vorzeichenbehaftete Zahlungssumme zu der Rechnung, auf die `ref`
    im äußeren Queryset zeigt ('pk' = diese Rechnung, 'reference_invoice_id' = die
    Ursprungsrechnung eines Kreditbelegs).

    Eigene Aggregation (statt eines Joins), damit kein Kreuzprodukt mit anderen
    Relationen entsteht — sonst zählte jede Zahlung so oft, wie die Rechnung
    andere Kindzeilen hat.
    """
    return Subquery(
        Payment.objects.filter(invoice_id=OuterRef(ref))
        .values("invoice_id")
        .annotate(s=Sum(_signierte_zahlung()))
        .values("s"),
        output_field=_GELD,
    )


def paid_subquery():
    """Subquery: vorzeichenbehaftete Summe der Zahlungen je Rechnung."""
    return _zahlungssumme_zu("pk")


def dunning_level_subquery():
    """Subquery: höchste erreichte Mahnstufe je Rechnung (None = nie gemahnt)."""
    return Subquery(
        DunningNotice.objects.filter(invoice_id=OuterRef("pk"))
        .values("invoice_id")
        .annotate(m=Max("level"))
        .values("m")
    )


def credit_subquery():
    """Subquery: Summe der **veröffentlichten** Kreditbelege je Rechnung (≤ 0).

    STORNO/GUTSCHRIFT verweisen über `reference_invoice_id` auf ihren Ursprung und
    tragen negative Summen. Ein Kreditbeleg im **ENTWURF** zählt nicht mit — er hat
    nichts zurückgenommen (dieselbe Regel wie in `beleg._erteilte_gutschriften`).
    """
    return _kreditsumme_zu("pk", vorzeichen=1)


def _kreditsumme_zu(ref, *, vorzeichen=-1, nur_vorher=False):
    """Subquery über die veröffentlichten Kreditbelege EINER Ursprungsrechnung.

    `ref` benennt die Spalte des äußeren Querysets, die auf die Ursprungsrechnung
    zeigt: `'pk'` (die Rechnung selbst) oder `'reference_invoice_id'` (die
    Ursprungsrechnung, gesehen aus einem Kreditbeleg heraus).

    `vorzeichen=-1` dreht die (negativen) Belegsummen in einen **positiven** Betrag
    („wie viel wurde zurückgenommen?"); `vorzeichen=1` liefert sie roh (≤ 0).

    `nur_vorher=True` zählt nur die Kreditbelege, die in der **Zuteilungsreihenfolge
    VOR dem äußeren Beleg** stehen (Belegdatum, dann Belegnummer, dann id — eine
    totale, stabile Ordnung; veröffentlichte Belege tragen Datum und Nummer immer).
    Daraus fällt die deterministische Aufteilung des Verrechnungsvolumens auf
    mehrere Kreditbelege: Der erste zehrt die offene Forderung auf, der nächste den
    Rest davon — kein Geld wird erfunden, keins verloren.
    """
    qs = Invoice.objects.filter(
        reference_invoice_id=OuterRef(ref),
        invoice_type__in=beleg_service.CREDIT_TYPES,
        status="VEROEFFENTLICHT",
    )
    if nur_vorher:
        qs = qs.filter(
            Q(invoice_date__lt=OuterRef("invoice_date"))
            | Q(
                invoice_date=OuterRef("invoice_date"),
                invoice_number__lt=OuterRef("invoice_number"),
            )
            | Q(
                invoice_date=OuterRef("invoice_date"),
                invoice_number=OuterRef("invoice_number"),
                id__lt=OuterRef("id"),
            )
        )
    return Subquery(
        qs.values("reference_invoice_id")
        .annotate(s=_geld(Sum("gross_total") * Value(vorzeichen)))
        .values("s"),
        output_field=_GELD,
    )


# ---------------------------------------------------------------------------
# Die Verrechnung — die EINE Formel (SQL und Python rechnen sie identisch)
# ---------------------------------------------------------------------------
# **INVARIANTE: Die Erstattungspflicht steht auf GENAU EINEM Beleg — dem
# Kreditbeleg. Das Original zeigt nach der Verrechnung NIE einen negativen offenen
# Betrag (durch Kreditbelege).**
#
# Vorher stand sie auf ZWEIEN: Nach dem Storno einer bezahlten Rechnung meldete das
# Original „880,60 € sind dem Kunden zu erstatten" (UEBERZAHLT) — **und** der
# Stornobeleg meldete dasselbe. Es gab keine Buchung, nach der beide Zeilen ruhig
# waren: Bucht man die Erstattung am Storno, bleibt das Original für immer
# UEBERZAHLT. Das ist die Einladung zur Doppelerstattung. Der User hat entschieden:
# **die Erstattung wird auf dem Kreditbeleg gebucht.**
#
# Daraus folgt das Rechenmodell — ein Kreditbeleg wird zuerst mit der **noch offenen
# Forderung** der Ursprungsrechnung VERRECHNET; nur was darüber hinausgeht (= Geld,
# das der Kunde bereits gezahlt hat), bleibt als **Erstattungspflicht auf dem
# Kreditbeleg** stehen:
#
#     verrechnungsvolumen = min( max(brutto − gezahlt, 0) , Σ|Kreditbelege| )
#     offen(Rechnung)     = brutto − gezahlt − verrechnungsvolumen
#     offen(Kreditbeleg)  = brutto_kredit + verrechnet_anteil − gezahlt_kredit
#
# (`brutto_kredit` ist negativ, `gezahlt_kredit` nach einer RUECKERSTATTUNG
# ebenfalls — PAYMENT_SIGN = −1.) Die Kette bleibt dabei **exakt** ausgeglichen:
#
#     Σ offen = brutto − Σ|Kredit| − (gezahlt + Σ gezahlt_kredit)
#
# Das Verrechnungsvolumen kürzt sich heraus — die Aufteilung auf mehrere
# Kreditbelege verschiebt Geld zwischen den Zeilen, sie erfindet keins und verliert
# keins.
#
# **Eine echte Überzahlung bleibt am Original stehen:** Zahlt der Kunde ohne Storno
# zu viel, ist `max(brutto − gezahlt, 0) = 0`, es wird nichts verrechnet, und
# `offen` bleibt negativ → UEBERZAHLT. Das ist keine Kreditbeleg-Sache und
# verschwindet nicht.


def verrechnungsvolumen(brutto, gezahlt, kreditsumme):
    """Wie viel des Kreditvolumens deckt die noch OFFENE Forderung ab (≥ 0)?

    Die Python-Hälfte der einen Formel. Die SQL-Hälfte steht in `mit_zahlungsstand`
    (`Least(Greatest(...))`) und rechnet dasselbe; ein Drift-Test fährt beide
    gegeneinander (`db_core/tests/test_verrechnung.py`).
    """
    return min(max(brutto - gezahlt, _ZERO), kreditsumme)


def _verrechnungsvolumen_sql(brutto, gezahlt, kreditsumme):
    return Least(Greatest(_geld(brutto - gezahlt), _null()), kreditsumme)


def mit_zahlungsstand(qs):
    """Annotiert ein Invoice-Queryset mit dem vollständigen Geldstand.

    * `paid_total`   — vorzeichenbehaftete Summe der Zahlungen (nie NULL)
    * `credit_total` — Summe der veröffentlichten Kreditbelege (≤ 0, nie NULL)
    * `storniert`    — trägt einen veröffentlichten STORNO (aus `beleg`)
    * `verrechnet`   — was zwischen diesem Beleg und seinem Gegenbeleg verrechnet
      ist (≥ 0, siehe Kommentarblock oben)
    * `forderungsbetrag` — der über DIESEN Beleg noch auszugleichende Betrag
      (Brutto, gemindert um das Verrechnete). Es gilt immer:
      `open_amount = forderungsbetrag − paid_total`.
    * `open_amount`  — offener Betrag. Auf einer Rechnung: was der Kunde noch
      schuldet (negativ nur bei echter Überzahlung). Auf einem Kreditbeleg: negativ,
      solange noch zu erstatten ist.
    * `dunning_level` — höchste erreichte Mahnstufe

    `open_amount` ist die EINE Antwort auf „wie viel ist offen?" und in SQL
    filterbar. Wer sie neu ausrechnet, baut eine zweite Wahrheit.
    """
    kredit = _kreditbeleg_q()
    brutto = Coalesce(F("gross_total"), _null())
    return (
        qs.annotate(
            paid_total=Coalesce(paid_subquery(), _null()),
            credit_total=Coalesce(credit_subquery(), _null()),
            storniert=beleg_service.storniert_exists(),
            dunning_level=dunning_level_subquery(),
            # Nur für Kreditbelege belegt (sonst NULL → 0): der Geldstand der
            # Ursprungsrechnung und die Kreditbelege, die vor diesem an der Reihe sind.
            origin_gross=Coalesce(
                Subquery(
                    Invoice.objects.filter(
                        pk=OuterRef("reference_invoice_id")
                    ).values("gross_total")[:1],
                    output_field=_GELD,
                ),
                _null(),
            ),
            origin_paid=Coalesce(_zahlungssumme_zu("reference_invoice_id"), _null()),
            origin_credit_sum=Coalesce(
                _kreditsumme_zu("reference_invoice_id"), _null()
            ),
            prior_credit_sum=Coalesce(
                _kreditsumme_zu("reference_invoice_id", nur_vorher=True), _null()
            ),
        )
        .annotate(
            verrechnet=Case(
                # Kreditbeleg: sein ANTEIL am Verrechnungsvolumen der Ursprungs-
                # rechnung — das, was die vorher zugeteilten Kreditbelege übrig
                # gelassen haben, höchstens sein eigener Betrag.
                When(
                    kredit & Q(status="VEROEFFENTLICHT"),
                    then=Least(
                        Greatest(
                            _geld(
                                _verrechnungsvolumen_sql(
                                    F("origin_gross"),
                                    F("origin_paid"),
                                    F("origin_credit_sum"),
                                )
                                - F("prior_credit_sum")
                            ),
                            _null(),
                        ),
                        _geld(Value(_ZERO, output_field=_GELD) - brutto),
                    ),
                ),
                # Kreditbeleg im ENTWURF: er hat nichts zurückgenommen und nichts
                # verrechnet (dieselbe Regel wie `credit_subquery`).
                When(kredit, then=_null()),
                # Rechnung: das gesamte Volumen ihrer Kreditbelege, gedeckelt auf
                # die noch offene Forderung.
                default=_verrechnungsvolumen_sql(
                    brutto,
                    F("paid_total"),
                    _geld(Value(_ZERO, output_field=_GELD) - F("credit_total")),
                ),
                output_field=_GELD,
            )
        )
        .annotate(
            forderungsbetrag=Case(
                When(kredit, then=_geld(brutto + F("verrechnet"))),
                default=_geld(brutto - F("verrechnet")),
                output_field=_GELD,
            )
        )
        .annotate(
            open_amount=_geld(F("forderungsbetrag") - F("paid_total")),
        )
    )


def _kreditbeleg_q():
    return Q(invoice_type__in=beleg_service.CREDIT_TYPES)


# ---------------------------------------------------------------------------
# Die EINE Grenze: FORDERUNG — was schuldet der Kunde?
# ---------------------------------------------------------------------------
# Diese Frage wurde im Repo an vier Stellen beantwortet, und zwar **verschieden**:
# Dossier, offene Posten, Mahnwesen und Mahnlauf. Mahnlauf und offene Posten
# filterten nur auf `VEROEFFENTLICHT AND gross_total > paid_total` — eine durch
# einen veröffentlichten STORNO aufgehobene Rechnung blieb damit offener Posten UND
# Mahnkandidat: Der Kunde bekam eine Mahnung über Geld, das er nicht mehr schuldet.
# Deshalb steht die Grenze jetzt genau EINMAL hier; alle Aufrufer ziehen von hier.
#
# Eine Rechnung ist eine **Forderung**, wenn sie
#   * veröffentlicht ist (ein Entwurf fordert nichts — auch ein Storno-ENTWURF
#     hebt nichts auf),
#   * kein Kreditbeleg ist (STORNO/GUTSCHRIFT tragen negative Summen und haben
#     kein Zahlungsziel gegen den Kunden — sie sind nie Mahnkandidat),
#   * nicht storniert ist (`beleg.stornierte_belege`): der Storno hebt sie auf.
#
# Die **Gutschrift** nimmt die Rechnung dagegen NICHT aus der Menge — sie MINDERT
# den geforderten Betrag (`credit_total`). Das ist die bestehende Projektgrenze
# *Storno löst, Gutschrift nicht*: Eine Kulanz heißt nicht, dass nicht gearbeitet
# wurde; die Leistung bleibt abgerechnet (die Abrechnungsbindung bleibt bestehen).
# Eine **Vollgutschrift** (nur auf ungebundenen Rechnungen überhaupt erlaubt,
# `beleg._vollgutschrift_sperre_pruefen`) führt damit auf `open_amount = 0`: nichts
# mehr offen, nichts mehr zu mahnen — ohne dass die Rechnung zum Storno umgedeutet
# würde.


def forderungen(qs=None):
    """Die Rechnungen, die tatsächlich Geld fordern (siehe Kommentarblock oben)."""
    qs = Invoice.objects.all() if qs is None else qs
    return (
        qs.filter(status="VEROEFFENTLICHT")
        .exclude(invoice_type__in=beleg_service.CREDIT_TYPES)
        .filter(~beleg_service.storniert_exists())
    )


def offene_forderungen(qs=None, *, stichtag=None):
    """Forderungen mit offenem Betrag (> 0,00 €), optional nur die überfälligen.

    Grundmenge für offene Posten, Mahnwesen und Mahnlauf. `stichtag` schaltet den
    Überfälligkeitsfilter dazu (fällig VOR dem Stichtag).
    """
    qs = mit_zahlungsstand(forderungen(qs)).filter(open_amount__gt=_ZERO)
    if stichtag is not None:
        qs = qs.filter(due_date__lt=stichtag)
    return qs


def payment_status(paid, gross):
    """OFFEN | TEILZAHLUNG | BEZAHLT | UEBERZAHLT | AUSGEGLICHEN.

    `gross` ist der **Forderungsbetrag** (Brutto abzüglich des Verrechneten), nicht
    zwingend `gross_total` — sonst stünde eine stornierte Rechnung als „offen" da,
    obwohl sie nichts mehr fordert.

    **Der Status folgt aus dem OFFENEN BETRAG** (`gross - paid`), nicht aus dem
    Vorzeichen von `paid`. Genau daran scheiterte die frühere Fassung: Sie entschied
    zuerst auf `paid <= 0`. Bei einem Kreditbeleg ist `paid` nach der Erstattung
    aber NEGATIV (RUECKERSTATTUNG, PAYMENT_SIGN = −1) — GUTSCHRIFT −595,00 € plus
    Rückerstattung 595,00 € ergibt `open_amount = 0,00 €`, und der erledigte
    Kreditbeleg stand trotzdem dauerhaft als „OFFEN" im Filter und trug den Stempel
    „Offen" bei 0,00 € offen.

    Die Forderung hat eine **Richtung** (das Vorzeichen von `gross`); „offen" heißt
    immer: in dieser Richtung ist noch etwas zu bewegen.

    * OFFEN — es floss noch nichts in Richtung der Forderung. Bei einem
      **Kreditbeleg** (negativer Betrag) heißt das: offen zugunsten des Kunden,
      bis erstattet ist.
    * TEILZAHLUNG — teilweise beglichen (bzw. teilweise erstattet).
    * BEZAHLT — nichts mehr offen, und es floss Geld (auch die vollständige
      Erstattung eines Kreditbelegs landet hier: er ist erledigt).
    * AUSGEGLICHEN — es ist **nichts mehr zu fordern** (Forderungsbetrag exakt
      0,00 €, durch Storno/Gutschrift verrechnet) und es floss kein Geld. Das ist
      NICHT „bezahlt" (niemand hat gezahlt) und erst recht nicht „offen". Ein
      **Kreditbeleg**, der vollständig mit der offenen Forderung verrechnet ist,
      landet hier: nichts zu erstatten, weil nie Geld geflossen ist.
    * UEBERZAHLT — es floss mehr, als (noch) gefordert ist. Das ist die **echte**
      Überzahlung (der Kunde hat schlicht zu viel überwiesen) — sie steht am
      Original und verschwindet nicht.
      **NICHT** mehr hierher gehört der Storno einer bezahlten Rechnung: Die
      Erstattungspflicht steht seit dem Verrechnungs-Slice auf genau EINEM Beleg,
      dem Kreditbeleg (siehe Kommentarblock „Die Verrechnung"). Vorher stand sie auf
      beiden — und blieb am Original für immer stehen, auch nachdem am Storno
      erstattet worden war. Das war die Einladung zur Doppelerstattung.
    """
    offen = gross - paid
    if offen == _ZERO:
        # Nichts mehr offen. Ob das „bezahlt" oder „ausgeglichen" heißt, entscheidet
        # allein, ob Geld geflossen ist.
        return "BEZAHLT" if paid != _ZERO else "AUSGEGLICHEN"
    if gross == _ZERO:
        # Nichts zu fordern, aber Geld geflossen (Storno nach Zahlung): der Betrag
        # ist dem Kunden zu erstatten. Ein negatives `paid` ohne Forderung wäre eine
        # zu erstattende Auszahlung ohne Grund — sie bleibt sichtbar offen.
        return "UEBERZAHLT" if paid > _ZERO else "OFFEN"
    # Es ist noch etwas offen. Floss bisher nichts in Richtung der Forderung
    # (`paid` hat das andere Vorzeichen oder ist 0), ist sie unberührt OFFEN.
    if (paid <= _ZERO) if gross > _ZERO else (paid >= _ZERO):
        return "OFFEN"
    # Es floss in Richtung der Forderung. Zeigt der Rest weiter in dieselbe
    # Richtung, ist er eine Teilzahlung; hat er gedreht, floss zu viel.
    rest_in_forderungsrichtung = (offen > _ZERO) if gross > _ZERO else (offen < _ZERO)
    return "TEILZAHLUNG" if rest_in_forderungsrichtung else "UEBERZAHLT"


def zahlungsspiegel(inv, *, heute=None):
    """Der abgeleitete Geldstand EINER (mit `mit_zahlungsstand` annotierten)
    Rechnung — die eine Wahrheit für alle Oberflächen.

    Rechnet die **Verrechnung** (Kommentarblock oben) in Python nach — dieselbe
    Formel, die `mit_zahlungsstand` in SQL annotiert. Beide Hälften werden in
    `db_core/tests/test_verrechnung.py` über eine Fallmatrix gegeneinander gefahren;
    das ist die Falle, in die dieses Projekt zweimal getappt ist.

    Ein **Kreditbeleg** trägt die Erstattungspflicht — und zwar als EINZIGER Beleg:
    Sein `open_amount` ist der Teil seines Betrags, der NICHT mit der noch offenen
    Forderung der Ursprungsrechnung verrechnet werden konnte (= das Geld, das der
    Kunde tatsächlich gezahlt hat), abzüglich bereits geleisteter Erstattungen.
    Ist nichts zu erstatten, steht er auf 0,00 € (AUSGEGLICHEN — verrechnet); ist
    erstattet, ebenfalls (BEZAHLT — erledigt). Er ist nie eine Forderung
    (`ist_forderung=False`) und damit nie überfällig und nie mahnbar.

    Erwartet die Annotationen aus `mit_zahlungsstand`.
    """
    heute = heute or date.today()
    gross = inv.gross_total or _ZERO
    paid = inv.paid_total if inv.paid_total is not None else _ZERO
    credit = inv.credit_total or _ZERO
    ist_kreditbeleg = inv.invoice_type in beleg_service.CREDIT_TYPES

    if ist_kreditbeleg:
        if inv.status == "VEROEFFENTLICHT":
            volumen = verrechnungsvolumen(
                inv.origin_gross or _ZERO,
                inv.origin_paid or _ZERO,
                inv.origin_credit_sum or _ZERO,
            )
            verrechnet = min(
                max(volumen - (inv.prior_credit_sum or _ZERO), _ZERO), -gross
            )
        else:
            # Ein Entwurf hat nichts zurückgenommen und nichts verrechnet.
            verrechnet = _ZERO
        forderungsbetrag = gross + verrechnet
    else:
        verrechnet = verrechnungsvolumen(gross, paid, -credit)
        forderungsbetrag = gross - verrechnet

    offen = forderungsbetrag - paid
    ist_forderung = not ist_kreditbeleg and not inv.storniert
    ueberfaellig = bool(
        ist_forderung and inv.due_date and inv.due_date < heute and offen > _ZERO
    )
    return {
        "gross_total": gross,
        "paid_total": paid,
        "credit_total": credit,
        # Was zwischen diesem Beleg und seinem Gegenbeleg verrechnet ist (≥ 0).
        "verrechnet": verrechnet,
        "forderungsbetrag": forderungsbetrag,
        "open_amount": offen,
        # Klartext für die Oberfläche — nie nur eine Farbe:
        # was ist noch zu erstatten, was wurde bereits erstattet?
        "zu_erstatten": max(-offen, _ZERO),
        "erstattet": max(-paid, _ZERO),
        "payment_status": payment_status(paid, forderungsbetrag),
        "is_storniert": bool(inv.storniert),
        "is_kreditbeleg": ist_kreditbeleg,
        "ist_forderung": ist_forderung,
        "is_overdue": ueberfaellig,
        "days_overdue": (heute - inv.due_date).days if ueberfaellig else None,
        "dunning_level": inv.dunning_level,
        # Weiter mahnen lässt sich nur eine Forderung mit offenem Betrag.
        "mahnbar": bool(ist_forderung and offen > _ZERO),
    }


def mahnsperre(invoice_id):
    """Der benannte Grund, warum diese Rechnung NICHT (mehr) gemahnt werden darf —
    oder None, wenn sie eine offene Forderung ist.

    Zieht die Antwort aus derselben einen Rechenstelle wie Liste, Filter, Mahnwesen
    und Mahnlauf (`mit_zahlungsstand`/`zahlungsspiegel`); es entsteht keine vierte
    Wahrheit. Der Aufrufer übersetzt den Text in ein 422 — der Kunde erfährt, WARUM
    nicht gemahnt wird, statt eine Mahnung über Geld zu bekommen, das er nicht mehr
    schuldet.

    Die Grenze steht zusätzlich als DB-Trigger (Migration 0097): Der Service-Guard
    ist die gute Fehlermeldung, der Trigger ist die Garantie.

    **Zum Verrechnungs-Slice — musste der Trigger seine Formel mitziehen? Nein.**
    Er rechnet `offen := brutto + kredit − gezahlt` (kann negativ werden), der
    Service jetzt `offen := max(brutto − gezahlt − kreditsumme, 0)`. Für das einzige
    Prädikat des Triggers („ist noch etwas offen?") sind beide **identisch**:

        max(r − c, 0) > 0   ⟺   r − c > 0     (r = brutto − gezahlt, c = Kreditsumme)

    Wo die neue Formel 0 zeigt, zeigt die alte ≤ 0 — beide verweigern. Der Trigger ist
    nie großzügiger als der Service, nur konservativer. Das ist **gefahren, nicht
    behauptet**: `api/tests/test_mahnung_schreibpfad.py::test_service_und_trigger_sind_deckungsgleich`
    schickt zehn Fälle (mit Zahlung, Teil-/Vollgutschrift, Storno, aufgezehrter
    Restforderung) erst durch den Zahlungsspiegel und dann per Roh-Insert an die DB.
    Deshalb gibt es zu diesem Slice **keine Migration**.
    """
    inv = mit_zahlungsstand(Invoice.objects.filter(id=invoice_id)).first()
    if inv is None:
        return "Rechnung nicht gefunden."
    nummer = inv.invoice_number or "Entwurf"
    if inv.status != "VEROEFFENTLICHT":
        return (
            f"Beleg {nummer} ist nicht veröffentlicht (Status {inv.status}) — "
            "ein Entwurf fordert nichts und wird nicht gemahnt (B-22)."
        )
    s = zahlungsspiegel(inv)
    if s["is_kreditbeleg"]:
        return (
            f"Beleg {nummer} ist ein Kreditbeleg ({inv.invoice_type}) — er fordert "
            "kein Geld vom Kunden und ist nie Mahnfall."
        )
    if s["is_storniert"]:
        return (
            f"Rechnung {nummer} ist storniert — der Kunde schuldet daraus nichts "
            "mehr. Bereits ausgestellte Mahnungen bleiben erhalten (GoBD), eine "
            "weitere Stufe wird nicht ausgestellt."
        )
    if not s["mahnbar"]:
        return (
            f"Rechnung {nummer} hat keine offene Forderung mehr "
            f"(offen: {s['open_amount']} €, Status {s['payment_status']}) — gemahnt "
            "wird nur, was der Kunde noch schuldet."
        )
    return None


def record_payment(
    actor_app_user_id,
    *,
    invoice_id,
    amount,
    paid_at,
    payment_type="ZAHLUNG",
    import_source="MANUAL",
    external_reference=None,
    currency="EUR",
):
    """Erfasst eine (Teil-)Zahlung zu einer veröffentlichten Rechnung.

    amount ist immer ein positiver Betrag (das Vorzeichen ergibt sich aus dem
    payment_type, siehe PAYMENT_SIGN). Die DB verlangt eine veröffentlichte
    Rechnung (B-23) und Idempotenz über UNIQUE(import_source, external_reference);
    fehlt external_reference, wird bei manueller Erfassung eine synthetische
    Referenz vergeben.

    `STORNO_BUCHUNG` ist hier **nicht** erfassbar: Eine Stornobuchung entsteht
    ausschließlich über `reverse_payment`, das sie an die Ursprungszahlung
    koppelt (`STORNO:<id>`) und doppeltes Stornieren verhindert. Ohne diese
    Sperre könnte ein Konto mit dem Recht AENDERN eine Stornobuchung erzeugen und
    damit das Recht STORNIEREN aushebeln — und die Buchung hinge an nichts.
    """
    if payment_type not in RECORDABLE_PAYMENT_TYPES:
        raise ValueError(
            f"Ungültiger payment_type '{payment_type}'. "
            f"Erlaubt: {', '.join(RECORDABLE_PAYMENT_TYPES)}."
        )
    if amount is None or amount <= 0:
        raise ValueError("amount muss ein positiver Betrag sein.")
    ensure_exists(Invoice, invoice_id, "Rechnung")
    ref = external_reference or f"{import_source}:{uuid.uuid4()}"
    with as_business_error():
        with business_transaction(actor_app_user_id):
            payment = Payment.objects.create(
                id=uuid.uuid4(),
                invoice_id=invoice_id,
                payment_type=payment_type,
                amount=amount,
                currency=currency,
                paid_at=paid_at,
                import_source=import_source,
                external_reference=ref,
            )
    return payment


def reverse_payment(actor_app_user_id, *, payment_id, paid_at=None):
    """Storniert eine Zahlung durch eine Gegenbuchung (STORNO_BUCHUNG).

    Keine physische Löschung (append-only). Eine bereits stornierende Buchung
    (STORNO_BUCHUNG) kann nicht storniert werden, und eine Zahlung wird nicht
    doppelt storniert (Prüfung über die external_reference 'STORNO:<id>').
    """
    original = Payment.objects.filter(id=payment_id).first()
    if original is None:
        raise ValueError("Zahlung nicht gefunden.")
    # Die Gegenbuchung ist stets eine negative STORNO_BUCHUNG; sie neutralisiert
    # nur eingehende (positiv gewertete) Zahlungen. Eine bereits negative Buchung
    # (RUECKERSTATTUNG/STORNO_BUCHUNG) würde dadurch doppelt statt aufgehoben.
    if PAYMENT_SIGN[original.payment_type] < 0:
        raise ValueError(
            "Nur eingehende Zahlungen können storniert werden "
            "(Rückerstattungen/Storno-Buchungen nicht)."
        )
    storno_ref = f"STORNO:{original.id}"
    if Payment.objects.filter(
        invoice_id=original.invoice_id, external_reference=storno_ref
    ).exists():
        raise ValueError("Diese Zahlung wurde bereits storniert.")
    with as_business_error():
        with business_transaction(actor_app_user_id):
            storno = Payment.objects.create(
                id=uuid.uuid4(),
                invoice_id=original.invoice_id,
                payment_type="STORNO_BUCHUNG",
                amount=original.amount,
                currency=original.currency,
                paid_at=paid_at or date.today(),
                import_source="MANUAL",
                external_reference=storno_ref,
            )
    return storno


def issue_dunning_notice(
    actor_app_user_id, *, invoice_id, level, issued_at, note=None, document_id=None
):
    """Erzeugt eine Mahnstufe (Zahlungserinnerung/Mahnung) zu einer Rechnung.

    **Gemahnt wird nur, was eine offene Forderung IST** (`mahnsperre`, gezogen aus
    der einen Rechenstelle): kein Kreditbeleg, nicht storniert, offener Betrag nach
    Gutschriften und Zahlungen > 0. Ohne diesen Guard stellte dieser Pfad — der
    einzige, der tatsächlich mahnt — auch auf einer stornierten oder voll bezahlten
    Rechnung klaglos eine Stufe aus, und der Mahnungsversand schickte dem Kunden
    „… ist die Rechnung weiterhin offen". Dieselbe Grenze sitzt seit Migration 0097
    im DB-Trigger; hier steht sie für den benannten Grund (422 statt roher
    Trigger-Meldung).

    Die DB erzwingt zusätzlich: veröffentlichte, zum issued_at bereits fällige
    Rechnung, und die nächste lückenlose Stufe (max+1). Verstöße werden als 422
    übersetzt. Das Mahndokument ist optional (die Ausfertigung ist reine Ausgabe,
    keine Vorbedingung).
    """
    if level is None or level <= 0:
        raise ValueError("level muss eine positive Stufennummer sein.")
    # Eine deaktivierte Stufe wird nicht ausgestellt (Konfig-Ebene, Migration
    # 0025 db_core). Die lückenlose Eskalation (max+1) erzwingt zusätzlich der
    # DB-Trigger.
    lvl = DunningLevel.objects.filter(level=level).first()
    if lvl is None:
        raise ValueError(f"Mahnstufe {level} existiert nicht.")
    if not lvl.active:
        raise ValueError(f"Mahnstufe {level} ist deaktiviert und kann nicht ausgestellt werden.")
    ensure_exists(Invoice, invoice_id, "Rechnung")
    grund = mahnsperre(invoice_id)
    if grund is not None:
        raise ValueError(grund)
    with as_business_error():
        with business_transaction(actor_app_user_id):
            notice = DunningNotice.objects.create(
                id=uuid.uuid4(),
                invoice_id=invoice_id,
                level_id=level,
                issued_at=issued_at,
                document_id=document_id,
                note=note,
                created_by_id=actor_app_user_id,
            )
    return notice
