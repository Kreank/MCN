"""Der Zahlungsstatus folgt aus dem OFFENEN BETRAG — nicht aus dem Vorzeichen der
Zahlungen.

Review-Befund (MITTEL): `payment_status(paid, gross)` entschied auf `paid <= 0`,
**bevor** der offene Betrag betrachtet wurde. Bei einem Kreditbeleg ist `paid` nach
der Erstattung NEGATIV (RUECKERSTATTUNG, PAYMENT_SIGN = −1): GUTSCHRIFT −595,00 €
+ Rückerstattung 595,00 € ergibt `open_amount = 0,00 €` — der Status blieb trotzdem
„OFFEN". Der erledigte Kreditbeleg stand dauerhaft im Filter „Offen" und trug den
Stempel „Offen" bei 0,00 € offen, im Widerspruch zum eigenen Docstring.

Die Regressionsblöcke unten halten die Bestandsfälle (normale Rechnung, Storno,
Vollgutschrift) unverändert fest — der Fix darf nur den einen Fall bewegen.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.db import connection

from db_core.models import Invoice
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import buchhaltung as buchhaltung_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service
from db_core.services.buchhaltung import payment_status

_HEUTE = date.today()
_BRUTTO = Decimal("880.60")
_POS_2_BRUTTO = Decimal("595.00")
_NULL = Decimal("0.00")


def _d(s):
    return Decimal(s)


# ===========================================================================
# Regression — die Bestandsfälle bleiben Wort für Wort, wie sie waren
# ===========================================================================

@pytest.mark.parametrize(
    ("paid", "gross", "erwartet"),
    [
        # Normale Rechnung (Forderungsbetrag > 0).
        ("0.00", "1000.00", "OFFEN"),          # unbezahlt
        ("400.00", "1000.00", "TEILZAHLUNG"),  # teilweise
        ("999.99", "1000.00", "TEILZAHLUNG"),
        ("1000.00", "1000.00", "BEZAHLT"),     # voll
        ("1000.01", "1000.00", "UEBERZAHLT"),  # zu viel
        ("1200.00", "1000.00", "UEBERZAHLT"),
        # Storno/Vollgutschrift auf eine UNBEZAHLTE Rechnung: nichts mehr zu fordern,
        # nichts geflossen. (Auch der voll verrechnete Kreditbeleg landet hier.)
        ("0.00", "0.00", "AUSGEGLICHEN"),
        # Reine Funktionsgrenze: mehr geflossen, als gefordert ist. Am Original
        # entsteht das seit dem Verrechnungs-Slice nur noch durch eine ECHTE
        # Überzahlung — nicht mehr durch den Storno einer bezahlten Rechnung (dessen
        # Erstattungspflicht steht auf dem Kreditbeleg).
        ("200.00", "0.00", "UEBERZAHLT"),
    ],
)
def test_bestandsfaelle_bleiben_unveraendert(paid, gross, erwartet):
    """Die fünf Statuswerte für alle Bestandsfälle — unverändert durch den Fix."""
    assert payment_status(_d(paid), _d(gross)) == erwartet


# ===========================================================================
# BRUCHFALL — der vollständig erstattete Kreditbeleg
# ===========================================================================

def test_erstatteter_kreditbeleg_ist_nicht_mehr_offen():
    """GUTSCHRIFT −595,00 € + Rückerstattung 595,00 € (paid = −595) → offen 0,00 €.

    Gegen den alten Code: „OFFEN" bei 0,00 € offen. Der Status muss aus dem offenen
    Betrag folgen, nicht aus dem Vorzeichen von `paid`.
    """
    assert payment_status(_d("-595.00"), _d("-595.00")) != "OFFEN"


def test_kreditbeleg_ohne_erstattung_bleibt_offen():
    """Solange nichts erstattet ist, ist der Kreditbeleg offen — zugunsten des Kunden."""
    assert payment_status(_NULL, _d("-595.00")) == "OFFEN"


def test_teilweise_erstatteter_kreditbeleg_ist_teilzahlung():
    """Halb erstattet ist weder „offen" noch „erledigt"."""
    assert payment_status(_d("-200.00"), _d("-595.00")) == "TEILZAHLUNG"


def test_ueberzahlte_erstattung_ist_ueberzahlt():
    """Mehr erstattet als geschuldet — der Kunde schuldet die Differenz zurück."""
    assert payment_status(_d("-700.00"), _d("-595.00")) == "UEBERZAHLT"


# ===========================================================================
# Integration — derselbe Fall durch Service und DB (nicht nur die reine Funktion)
# ===========================================================================

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


def _spiegel(invoice_id):
    inv = buchhaltung_service.mit_zahlungsstand(
        Invoice.objects.filter(id=invoice_id)
    ).get()
    return buchhaltung_service.zahlungsspiegel(inv, heute=_HEUTE)


@pytest.mark.django_db
def test_erstatteter_kreditbeleg_end_to_end(app_user):
    """Rechnung → **voll bezahlt** → Teilgutschrift 595,00 € → Rückerstattung 595,00 €.

    Der Kreditbeleg ist danach erledigt: offen 0,00 €, Status nicht mehr „OFFEN".
    Gegen den alten Code stand er dauerhaft im Filter „Offen".

    **Die Rechnung muss bezahlt sein, damit überhaupt etwas zu erstatten ist** — das
    ist die Invariante des Verrechnungs-Slices: Eine Gutschrift auf eine noch offene
    Rechnung wird mit der Forderung VERRECHNET (nichts fließt zurück); erstattet wird
    nur, was der Kunde tatsächlich gezahlt hat. Vorher rechnete das System auf einer
    unbezahlten Rechnung eine Erstattungspflicht von 595,00 € aus — Geld, das nie
    geflossen war.
    """
    obj = property_service.create_property(
        app_user.id, name="Kreditbeleg-Objekt", property_type="WEG",
        street="Weg", house_number="1", postal_code="10115", city="Berlin",
    )
    kunde = identity_service.create_person(
        app_user.id, first_name="Klara", last_name="Kundin"
    )
    order = _gepruefter_auftrag(app_user, obj, kunde)
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="RECHNUNG",
        work_order_id=order.id,
        invoice_date=_HEUTE - timedelta(days=30),
        due_date=_HEUTE - timedelta(days=1),
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

    # Der Kunde zahlt die Rechnung vollständig — erst dadurch gibt es überhaupt Geld,
    # das erstattet werden kann.
    buchhaltung_service.record_payment(
        app_user.id, invoice_id=inv.id, amount=_BRUTTO, paid_at=_HEUTE
    )

    gutschrift = beleg_service.create_correction(
        app_user.id, invoice_id=inv.id, positions=[2]
    )
    assert gutschrift.gross_total == -_POS_2_BRUTTO

    # Das Original bleibt bei 0,00 € offen (BEZAHLT) — die Erstattungspflicht steht
    # auf dem Kreditbeleg, und zwar dort ALLEIN.
    original = _spiegel(inv.id)
    assert original["open_amount"] == _NULL
    assert original["payment_status"] == "BEZAHLT"

    # Vor der Erstattung: der Kreditbeleg ist offen — zugunsten des Kunden.
    vorher = _spiegel(gutschrift.id)
    assert vorher["verrechnet"] == _NULL, "Nichts zu verrechnen — die Rechnung ist bezahlt."
    assert vorher["open_amount"] == -_POS_2_BRUTTO
    assert vorher["zu_erstatten"] == _POS_2_BRUTTO
    assert vorher["payment_status"] == "OFFEN"
    assert vorher["ist_forderung"] is False

    # Das Haus erstattet dem Kunden die 595,00 €.
    buchhaltung_service.record_payment(
        app_user.id, invoice_id=gutschrift.id, amount=_POS_2_BRUTTO,
        paid_at=_HEUTE, payment_type="RUECKERSTATTUNG",
    )

    nachher = _spiegel(gutschrift.id)
    assert nachher["paid_total"] == -_POS_2_BRUTTO
    assert nachher["open_amount"] == _NULL
    assert nachher["erstattet"] == _POS_2_BRUTTO
    assert nachher["payment_status"] != "OFFEN", (
        "Ein erledigter Kreditbeleg mit 0,00 EUR offen darf nicht OFFEN heissen."
    )
    assert nachher["payment_status"] == "BEZAHLT"
    assert nachher["mahnbar"] is False
    # Und das Original ist weiterhin ruhig — keine zweite Erstattungspflicht.
    assert _spiegel(inv.id)["open_amount"] == _NULL



# ===========================================================================
# Befund 3 — der Referenzbeleg braucht einen Index (Migration 0096)
# ===========================================================================

@pytest.mark.django_db
def test_referenzbeleg_ist_indiziert(db):
    """Postgres indiziert FK-Spalten NICHT automatisch.

    `beleg.storniert_exists()` (EXISTS) und `buchhaltung.credit_subquery()` (Summe
    der Kreditbelege) korrelieren beide über `invoice.reference_invoice_id` — je
    Zeile der offenen Posten einmal. Ohne Index ist das ein Seq-Scan über den
    gesamten (monoton wachsenden, weil nie gelöschten) Belegbestand.
    """
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT indexdef FROM pg_indexes
            WHERE schemaname = 'invoicing' AND tablename = 'invoice'
            """
        )
        defs = [r[0] for r in cur.fetchall()]
    assert any("reference_invoice_id" in d for d in defs), (
        "Kein Index auf invoicing.invoice.reference_invoice_id: " + "; ".join(defs)
    )
