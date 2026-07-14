"""Die Verrechnung: Die Erstattungspflicht steht auf GENAU EINEM Beleg.

**INVARIANTE (User-Entscheidung):** Die Erstattung wird auf dem **Kreditbeleg**
gebucht. Das Original zeigt nach der Verrechnung **nie** einen negativen offenen
Betrag (durch Kreditbelege).

Vorher stand die Erstattungspflicht auf ZWEI Belegen: Nach dem Storno einer
bezahlten Rechnung meldete das Original „880,60 € sind dem Kunden zu erstatten"
(UEBERZAHLT) — **und** der Stornobeleg meldete dasselbe. Es gab keine Buchung, nach
der beide Zeilen ruhig waren: Buchte man die Erstattung am Storno, blieb das
Original für immer UEBERZAHLT. Wer die Liste der Erstattungen abarbeitete, zahlte
denselben Betrag zweimal aus.

Das Modell (`buchhaltung.verrechnungsvolumen` + die SQL-Hälfte in
`mit_zahlungsstand`):

    verrechnet(gesamt) = min( max(brutto − gezahlt, 0) , Σ|Kreditbelege| )
    offen(Rechnung)    = brutto − gezahlt − verrechnet
    offen(Kreditbeleg) = brutto_kredit + verrechnet_anteil − gezahlt_kredit

Ein Kreditbeleg wird also zuerst mit der **noch offenen Forderung** verrechnet; nur
was darüber hinausgeht (= Geld, das der Kunde tatsächlich gezahlt hat), bleibt als
Erstattungspflicht auf dem Kreditbeleg stehen.

**`_spiegel()` fährt bei JEDEM Aufruf die SQL-Annotation gegen die Python-Sicht.**
Das ist die Falle, in die dieses Projekt zweimal getappt ist: zwei Rechenstellen,
die auseinanderlaufen. Hier kann keine der beiden allein grün werden.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from db_core.db_context import business_transaction
from db_core.models import Invoice
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import buchhaltung as buchhaltung_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service

_HEUTE = date.today()
_NULL = Decimal("0.00")
# 100 × 2,40 € + 10 × 50,00 € = 740,00 € netto → 880,60 € brutto (19 %).
_POS_1_BRUTTO = Decimal("285.60")
_POS_2_BRUTTO = Decimal("595.00")
_BRUTTO = Decimal("880.60")


# --- Aufbau ----------------------------------------------------------------

def _gepruefter_auftrag(app_user, obj, debtor):
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Auftrag zur Rechnung"
    )
    auftrag_service.set_order_evidence(
        app_user.id, work_order_id=order.id, reference="Nachweis"
    )
    auftrag_service.confirm_responsibility(
        app_user.id, work_order_id=order.id, scope="COMMON_PROPERTY"
    )
    for role in ("PRINCIPAL", "INVOICE_DEBTOR"):
        auftrag_service.add_work_order_party(
            app_user.id, work_order_id=order.id, party_id=debtor.id,
            role=role, is_primary=True,
        )
    for to in ("FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG",
               "TECHNISCH_ABGESCHLOSSEN", "KAUFMAENNISCH_GEPRUEFT"):
        auftrag_service.advance_status(app_user.id, work_order_id=order.id, to_status=to)
    return order


def _rechnung(app_user, *, name):
    """Veröffentlichte Rechnung über 880,60 € brutto, zwei Positionen."""
    obj = property_service.create_property(
        app_user.id, name=name, property_type="WEG",
        street="Weg", house_number="1", postal_code="10115", city="Berlin",
    )
    kunde = identity_service.create_person(
        app_user.id, first_name="Klara", last_name="Kundin"
    )
    order = _gepruefter_auftrag(app_user, obj, kunde)
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="RECHNUNG",
        work_order_id=order.id,
        invoice_date=_HEUTE - timedelta(days=90),
        due_date=_HEUTE - timedelta(days=30),
        lines=[
            {"line_type": "MATERIAL", "description": "Ziegel", "quantity": 100,
             "unit": "Stk", "unit_price": "2.40", "tax_code": "DE_19"},
            {"line_type": "ARBEITSZEIT", "description": "Montage", "quantity": 10,
             "unit": "h", "unit_price": "50.00", "tax_code": "DE_19"},
        ],
    )
    for role in ("INVOICE_DEBTOR", "INVOICE_RECIPIENT"):
        beleg_service.add_invoice_party(
            app_user.id, invoice_id=inv.id, party_id=kunde.id, role=role,
            is_primary=True,
        )
    beleg_service.publish_invoice(app_user.id, invoice_id=inv.id)
    inv.refresh_from_db()
    assert inv.gross_total == _BRUTTO
    return inv


def _zahle(app_user, invoice_id, betrag, typ="ZAHLUNG"):
    return buchhaltung_service.record_payment(
        app_user.id, invoice_id=invoice_id, amount=Decimal(betrag),
        paid_at=_HEUTE, payment_type=typ,
    )


def _erstatte(app_user, invoice_id, betrag):
    return _zahle(app_user, invoice_id, betrag, typ="RUECKERSTATTUNG")


# --- Die Doppelsicherung: SQL gegen Python ---------------------------------

def _spiegel(invoice_id):
    """Der Zahlungsspiegel — **und** die Gegenprobe gegen die SQL-Annotation.

    `mit_zahlungsstand` rechnet `verrechnet`/`forderungsbetrag`/`open_amount` in
    SQL (filterbar, für Listen und Trigger-nahe Abfragen); `zahlungsspiegel`
    rechnet dieselbe Formel in Python (für die Detailsicht). Jeder Aufruf hier
    prüft, dass sie **denselben Cent** liefern. Driftet eine der beiden, fällt
    JEDER Test dieser Datei um — nicht nur ein spezieller.
    """
    inv = buchhaltung_service.mit_zahlungsstand(
        Invoice.objects.filter(id=invoice_id)
    ).get()
    s = buchhaltung_service.zahlungsspiegel(inv, heute=_HEUTE)
    for feld in ("verrechnet", "forderungsbetrag", "open_amount"):
        assert s[feld] == getattr(inv, feld), (
            f"DRIFT in '{feld}': SQL sagt {getattr(inv, feld)}, "
            f"Python sagt {s[feld]} (Beleg {inv.invoice_number or inv.id})."
        )
    # Die tragende Identität: was offen ist, ist der Rest des auszugleichenden
    # Betrags. Ohne sie wären forderungsbetrag und open_amount zwei Wahrheiten.
    assert s["open_amount"] == s["forderungsbetrag"] - s["paid_total"]
    return s


def _kette(original_id):
    """Alle Belege der Kette (Original + veröffentlichte Kreditbelege)."""
    ids = [original_id] + list(
        Invoice.objects.filter(
            reference_invoice_id=original_id,
            invoice_type__in=beleg_service.CREDIT_TYPES,
            status="VEROEFFENTLICHT",
        )
        .order_by("invoice_date", "invoice_number", "id")
        .values_list("id", flat=True)
    )
    return [_spiegel(i) for i in ids]


def _zuviel_gezahlt(spiegel_liste):
    """Was der Kunde in Summe zu viel gezahlt hat (≥ 0 = Erstattungsanspruch).

    Unabhängig von der Verrechnung gerechnet — bewusst eine **zweite, naive**
    Sicht: gezahlt (über alle Belege der Kette) minus dem, was nach allen
    Kreditbelegen überhaupt noch geschuldet ist.
    """
    gezahlt = sum((s["paid_total"] for s in spiegel_liste), _NULL)
    geschuldet = sum((s["gross_total"] for s in spiegel_liste), _NULL)
    return gezahlt - geschuldet


# ===========================================================================
# Die Wahrheitstabelle
# ===========================================================================

@pytest.mark.django_db
def test_unbezahlte_rechnung_vollstorno_erstattet_nichts(app_user):
    """Unbezahlte Rechnung 880,60 € + Vollstorno → **0,00 € auf beiden Belegen.**

    Nichts zu fordern, nichts zu erstatten: Der Kunde hat nie gezahlt. Vorher meldete
    der Stornobeleg −880,60 € „zu erstatten" — Geld, das nie geflossen war.
    """
    inv = _rechnung(app_user, name="V-Unbezahlt-Storno")
    storno = beleg_service.create_cancellation(app_user.id, invoice_id=inv.id)

    original = _spiegel(inv.id)
    assert original["open_amount"] == _NULL
    assert original["verrechnet"] == _BRUTTO
    assert original["payment_status"] == "AUSGEGLICHEN"

    kredit = _spiegel(storno.id)
    assert kredit["verrechnet"] == _BRUTTO, "Der Storno ist voll verrechnet."
    assert kredit["open_amount"] == _NULL
    assert kredit["zu_erstatten"] == _NULL
    assert kredit["payment_status"] == "AUSGEGLICHEN"


@pytest.mark.django_db
def test_voll_bezahlte_rechnung_vollstorno_erstattet_den_vollen_betrag(app_user):
    """Voll bezahlte Rechnung + Vollstorno → Original 0,00 €, Storno −880,60 €.

    Und nach der Erstattung sind **beide** Zeilen ruhig. Genau diese Buchung gab es
    vorher nicht: Das Original blieb für immer UEBERZAHLT.
    """
    inv = _rechnung(app_user, name="V-Bezahlt-Storno")
    _zahle(app_user, inv.id, _BRUTTO)
    storno = beleg_service.create_cancellation(app_user.id, invoice_id=inv.id)

    original = _spiegel(inv.id)
    assert original["open_amount"] == _NULL
    assert original["verrechnet"] == _NULL, "Es war nichts mehr offen zu verrechnen."
    assert original["payment_status"] == "BEZAHLT"

    kredit = _spiegel(storno.id)
    assert kredit["open_amount"] == -_BRUTTO
    assert kredit["zu_erstatten"] == _BRUTTO
    assert kredit["payment_status"] == "OFFEN"

    _erstatte(app_user, storno.id, _BRUTTO)

    nachher = _spiegel(storno.id)
    assert nachher["open_amount"] == _NULL
    assert nachher["zu_erstatten"] == _NULL
    assert nachher["erstattet"] == _BRUTTO
    assert nachher["payment_status"] == "BEZAHLT"
    assert _spiegel(inv.id)["open_amount"] == _NULL, (
        "Nach der Erstattung darf am Original keine zweite Erstattungspflicht stehen."
    )


@pytest.mark.django_db
def test_teilzahlung_dann_vollstorno_erstattet_genau_das_geleistete_geld(app_user):
    """Teilzahlung 300 € + Vollstorno → Storno −300,00 € (nicht −880,60 €).

    Erstattet wird, was geflossen ist — nicht der Belegbetrag. Der Rest (580,60 €)
    ist mit der noch offenen Forderung verrechnet.
    """
    inv = _rechnung(app_user, name="V-Teilzahlung-Storno")
    _zahle(app_user, inv.id, "300.00", typ="TEILZAHLUNG")
    storno = beleg_service.create_cancellation(app_user.id, invoice_id=inv.id)

    original = _spiegel(inv.id)
    assert original["verrechnet"] == Decimal("580.60")
    assert original["open_amount"] == _NULL
    assert original["payment_status"] == "BEZAHLT"

    kredit = _spiegel(storno.id)
    assert kredit["verrechnet"] == Decimal("580.60")
    assert kredit["open_amount"] == Decimal("-300.00")
    assert kredit["zu_erstatten"] == Decimal("300.00")

    _erstatte(app_user, storno.id, "300.00")
    assert _spiegel(storno.id)["open_amount"] == _NULL
    assert _spiegel(inv.id)["open_amount"] == _NULL


@pytest.mark.django_db
def test_teilgutschrift_auf_unbezahlte_rechnung_wird_verrechnet(app_user):
    """Teilgutschrift 595 € auf eine UNBEZAHLTE Rechnung → Original 285,60 € offen,
    Gutschrift 0,00 € (nichts zu erstatten)."""
    inv = _rechnung(app_user, name="V-Gutschrift-Unbezahlt")
    gs = beleg_service.create_correction(app_user.id, invoice_id=inv.id, positions=[2])

    original = _spiegel(inv.id)
    assert original["open_amount"] == _POS_1_BRUTTO
    assert original["payment_status"] == "OFFEN"
    assert original["mahnbar"] is True

    kredit = _spiegel(gs.id)
    assert kredit["verrechnet"] == _POS_2_BRUTTO
    assert kredit["open_amount"] == _NULL
    assert kredit["zu_erstatten"] == _NULL
    assert kredit["payment_status"] == "AUSGEGLICHEN"


@pytest.mark.django_db
def test_teilgutschrift_auf_voll_bezahlte_rechnung_ist_zu_erstatten(app_user):
    """Teilgutschrift 595 € auf eine VOLL BEZAHLTE Rechnung → Gutschrift −595,00 €.

    Das Original bleibt bei 0,00 € (BEZAHLT). Vorher stand es auf −595,00 €
    UEBERZAHLT — und blieb dort auch nach der Erstattung an der Gutschrift.
    """
    inv = _rechnung(app_user, name="V-Gutschrift-Bezahlt")
    _zahle(app_user, inv.id, _BRUTTO)
    gs = beleg_service.create_correction(app_user.id, invoice_id=inv.id, positions=[2])

    original = _spiegel(inv.id)
    assert original["verrechnet"] == _NULL
    assert original["open_amount"] == _NULL
    assert original["payment_status"] == "BEZAHLT"

    kredit = _spiegel(gs.id)
    assert kredit["open_amount"] == -_POS_2_BRUTTO
    assert kredit["zu_erstatten"] == _POS_2_BRUTTO


# ===========================================================================
# Mehrere Kreditbelege — deterministische Zuteilung, kein Geld erfunden
# ===========================================================================

@pytest.mark.django_db
def test_mehrere_gutschriften_teilen_das_verrechnungsvolumen_deterministisch(app_user):
    """Rechnung 880,60 €, Teilzahlung 300 €, danach ZWEI Gutschriften (595 + 285,60).

    Verrechnungsvolumen = 880,60 − 300 = 580,60 €. Die Zuteilung folgt der Belegfolge
    (Belegdatum, dann Belegnummer, dann id):

    | Beleg          | Betrag   | verrechnet | offen    |
    |----------------|----------|------------|----------|
    | Rechnung       |  880,60  |   580,60   |    0,00  |
    | Gutschrift 1   | −595,00  |   580,60   |  −14,40  |
    | Gutschrift 2   | −285,60  |     0,00   | −285,60  |

    Summe der Erstattungspflichten = 300,00 € — **exakt** das Geld, das der Kunde
    gezahlt hat. Kein Cent erfunden, keiner verloren.
    """
    inv = _rechnung(app_user, name="V-Mehrere-Gutschriften")
    _zahle(app_user, inv.id, "300.00", typ="TEILZAHLUNG")
    gs1 = beleg_service.create_correction(app_user.id, invoice_id=inv.id, positions=[2])
    gs2 = beleg_service.create_correction(app_user.id, invoice_id=inv.id, positions=[1])
    assert gs1.invoice_number < gs2.invoice_number, "Vorbedingung: gs1 kommt zuerst."

    original = _spiegel(inv.id)
    assert original["verrechnet"] == Decimal("580.60")
    assert original["open_amount"] == _NULL

    a, b = _spiegel(gs1.id), _spiegel(gs2.id)
    assert a["verrechnet"] == Decimal("580.60")
    assert a["open_amount"] == Decimal("-14.40")
    assert b["verrechnet"] == _NULL
    assert b["open_amount"] == -_POS_1_BRUTTO

    erstattungspflicht = -(a["open_amount"] + b["open_amount"])
    assert erstattungspflicht == Decimal("300.00"), (
        "Die Summe der Erstattungspflichten muss dem geleisteten Geld entsprechen."
    )

    # Beide erstattet → die ganze Kette ist ruhig.
    _erstatte(app_user, gs1.id, "14.40")
    _erstatte(app_user, gs2.id, _POS_1_BRUTTO)
    assert [s["open_amount"] for s in _kette(inv.id)] == [_NULL, _NULL, _NULL]


@pytest.mark.django_db
def test_kettensumme_ist_exakt_das_zuviel_gezahlte(app_user):
    """Der Erhaltungssatz: über die ganze Kette geht die Rechnung EXAKT auf.

        Σ offen(Kette) = Σ brutto(Kette) − Σ gezahlt(Kette)

    Hier mit dem härtesten Fall: der Kunde hat **überzahlt** (1.000 € auf 880,60 €)
    UND es gibt eine Gutschrift über 595 € — die Erstattungsansprüche übersteigen
    zusammen den Rechnungsbetrag. Das Verrechnungsvolumen kürzt sich aus der Summe
    heraus; die Zuteilung kann Geld zwischen den Zeilen verschieben, aber keins
    erfinden und keins verlieren.
    """
    inv = _rechnung(app_user, name="V-Erhaltung")
    _zahle(app_user, inv.id, "1000.00", typ="UEBERZAHLUNG")
    beleg_service.create_correction(app_user.id, invoice_id=inv.id, positions=[2])

    kette = _kette(inv.id)
    summe_offen = sum((s["open_amount"] for s in kette), _NULL)
    assert summe_offen == -_zuviel_gezahlt(kette)
    # 1.000 gezahlt, geschuldet nach Gutschrift 285,60 → 714,40 € zurück.
    assert summe_offen == Decimal("-714.40")
    # Und die Verteilung: 119,40 € echte Überzahlung am Original, 595,00 € am Kredit.
    assert [s["open_amount"] for s in kette] == [
        Decimal("-119.40"), Decimal("-595.00")
    ]


# ===========================================================================
# Die Grenzen — was NICHT verrechnet werden darf
# ===========================================================================

@pytest.mark.django_db
def test_storno_im_entwurf_verrechnet_nichts(app_user):
    """Ein Entwurf ist kein Beleg: Er nimmt nichts zurück und verrechnet nichts.

    Sonst genügte ein unfertiger Entwurf, um eine echte Forderung aus Mahnwesen und
    offenen Posten verschwinden zu lassen — der bequemste Weg, eine Mahnung zu
    unterdrücken.
    """
    inv = _rechnung(app_user, name="V-Storno-Entwurf")
    with business_transaction(app_user.id):
        entwurf = Invoice.objects.create(
            id=uuid.uuid4(),
            property_id=inv.property_id,
            invoice_type="STORNO",
            reference_invoice_id=inv.id,
            status="ENTWURF",
            invoice_date=inv.invoice_date,
            net_total=-inv.net_total,
            tax_total=-inv.tax_total,
            gross_total=-inv.gross_total,
            version=1,
        )

    original = _spiegel(inv.id)
    assert original["verrechnet"] == _NULL
    assert original["open_amount"] == _BRUTTO
    assert original["mahnbar"] is True

    kredit = _spiegel(entwurf.id)
    assert kredit["verrechnet"] == _NULL, "Ein Entwurf verrechnet nichts."
    assert kredit["open_amount"] == -_BRUTTO
    assert kredit["ist_forderung"] is False


@pytest.mark.django_db
def test_echte_ueberzahlung_bleibt_am_original(app_user):
    """Zahlt der Kunde ohne Storno zu viel, bleibt UEBERZAHLT am Original stehen.

    Das ist **keine** Kreditbeleg-Sache und darf nicht in der Verrechnung
    verschwinden: Es ist nichts zurückzunehmen, es wurde schlicht zu viel überwiesen.
    """
    inv = _rechnung(app_user, name="V-Ueberzahlung-Pur")
    _zahle(app_user, inv.id, "980.60", typ="UEBERZAHLUNG")

    s = _spiegel(inv.id)
    assert s["verrechnet"] == _NULL
    assert s["open_amount"] == Decimal("-100.00")
    assert s["zu_erstatten"] == Decimal("100.00")
    assert s["payment_status"] == "UEBERZAHLT"
    assert s["mahnbar"] is False


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("betrag", "typ", "erwartet_offen", "erwartet_status"),
    [
        (None, None, _BRUTTO, "OFFEN"),
        ("300.00", "TEILZAHLUNG", Decimal("580.60"), "TEILZAHLUNG"),
        ("880.60", "ZAHLUNG", _NULL, "BEZAHLT"),
        ("900.00", "UEBERZAHLUNG", Decimal("-19.40"), "UEBERZAHLT"),
    ],
)
def test_regression_ohne_kreditbeleg_bleibt_alles_wie_es_war(
    app_user, betrag, typ, erwartet_offen, erwartet_status
):
    """Ohne Kreditbeleg gibt es nichts zu verrechnen — jeder Bestandsfall unverändert."""
    inv = _rechnung(app_user, name=f"V-Regression-{typ or 'ohne'}")
    if betrag is not None:
        _zahle(app_user, inv.id, betrag, typ=typ)

    s = _spiegel(inv.id)
    assert s["verrechnet"] == _NULL
    assert s["forderungsbetrag"] == _BRUTTO
    assert s["open_amount"] == erwartet_offen
    assert s["payment_status"] == erwartet_status


# ===========================================================================
# Die reine Formel — cent-genau, ohne Datenbank
# ===========================================================================

@pytest.mark.parametrize(
    ("brutto", "gezahlt", "kreditsumme", "erwartet"),
    [
        # unbezahlt + Vollstorno → alles verrechnet
        ("880.60", "0.00", "880.60", "880.60"),
        # voll bezahlt + Vollstorno → nichts zu verrechnen, alles zu erstatten
        ("880.60", "880.60", "880.60", "0.00"),
        # Teilzahlung → nur der offene Rest wird verrechnet
        ("880.60", "300.00", "880.60", "580.60"),
        # Teilgutschrift auf unbezahlte Rechnung → die Gutschrift geht ganz auf
        ("880.60", "0.00", "595.00", "595.00"),
        # ohne Kreditbeleg → nichts
        ("880.60", "300.00", "0.00", "0.00"),
        # echte Überzahlung → kein negatives Verrechnungsvolumen
        ("880.60", "1000.00", "595.00", "0.00"),
        # Cent-Grenze
        ("100.00", "99.99", "100.00", "0.01"),
    ],
)
def test_verrechnungsvolumen_ist_centgenau(brutto, gezahlt, kreditsumme, erwartet):
    """`min(max(brutto − gezahlt, 0), kreditsumme)` — reine Decimal-Arithmetik.

    Es wird nur addiert, subtrahiert und verglichen; es gibt keine Division und damit
    **keine Rundung**, die einen Cent verlieren könnte. Alle Beträge tragen zwei
    Nachkommastellen (numeric(15,2)).
    """
    assert buchhaltung_service.verrechnungsvolumen(
        Decimal(brutto), Decimal(gezahlt), Decimal(kreditsumme)
    ) == Decimal(erwartet)
