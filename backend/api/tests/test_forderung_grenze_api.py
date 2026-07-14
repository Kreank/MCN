"""Die EINE Grenze „was schuldet der Kunde?" (Forderung) — Bruchfälle.

Vor diesem Slice gab es dafür **zwei** Wahrheiten: Das Dossier zog die Grenze
richtig (veröffentlicht ∧ kein Kreditbeleg ∧ nicht storniert), Mahnlauf und offene
Posten dagegen nur über `status='VEROEFFENTLICHT' AND gross_total > paid_total` —
eine **stornierte** Rechnung blieb dort offener Posten UND Mahnkandidat. Der Kunde
bekam eine Mahnung über Geld, das er nicht mehr schuldet.

Diese Tests fixieren die Grenze an allen Aufrufern (Dossier, offene Posten,
Mahnwesen, Mahnlauf). Jeder von ihnen war gegen den alten Code ROT.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from db_core.db_context import business_transaction
from db_core.models import DunningNotice, Invoice
from db_core.services import auftrag as auftrag_service
from db_core.services import auswertungen as auswertungen_service
from db_core.services import beleg as beleg_service
from db_core.services import buchhaltung as buchhaltung_service
from db_core.services import datev as datev_service
from db_core.services import firma as firma_service
from db_core.services import identity as identity_service
from db_core.services import mahnlauf as mahnlauf_service
from db_core.services import property as property_service

_HEUTE = date.today()

# Positionen: 240,00 € + 500,00 € netto = 740,00 € → 880,60 € brutto (19 %).
_POS_1_BRUTTO = Decimal("285.60")
_POS_2_BRUTTO = Decimal("595.00")
_BRUTTO = Decimal("880.60")
_NULL = Decimal("0.00")


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


def _veroeffentlichte_rechnung(app_user, *, name="Forderungs-Objekt"):
    """Veröffentlichte, 30 Tage überfällige Rechnung mit ZWEI Positionen.

    Zwei Positionen, damit sich Teil- und Vollgutschrift unterscheiden lassen.
    """
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
    return {"inv": inv, "kunde": kunde, "obj": obj, "order": order}


@pytest.fixture
def fall(app_user):
    """Die überfällige Rechnung mit ausgestellter Mahnstufe 1.

    → Nächste fällige Stufe ist 2 (Frist 14 Tage, 30 Tage überfällig).
    """
    daten = _veroeffentlichte_rechnung(app_user)
    buchhaltung_service.issue_dunning_notice(
        app_user.id, invoice_id=daten["inv"].id, level=1,
        issued_at=_HEUTE - timedelta(days=1), note="Erste Erinnerung",
    )
    return daten


# --- Helfer ----------------------------------------------------------------

def _kandidaten_ids(stichtag=None):
    return {c["invoice_id"] for c in mahnlauf_service.list_candidates(stichtag=stichtag)}


def _posten(admin_client, invoice_id):
    """Die Zeile der Rechnung aus der Liste der offenen Posten (oder None)."""
    body = admin_client.get("/api/buchhaltung/invoices?page_size=100").json()
    return next((i for i in body["items"] if i["id"] == str(invoice_id)), None)


def _ueberfaellig_ids(admin_client):
    body = admin_client.get(
        "/api/buchhaltung/invoices?overdue=true&page_size=100"
    ).json()
    return {i["id"] for i in body["items"]}


def _mahnzeile(admin_client, invoice_id):
    body = admin_client.get("/api/buchhaltung/dunning").json()
    return next((i for i in body["items"] if i["id"] == str(invoice_id)), None)


def _max_level(invoice_id):
    return (
        DunningNotice.objects.filter(invoice_id=invoice_id)
        .order_by("-level")
        .values_list("level", flat=True)
        .first()
    )


# ===========================================================================
# BRUCHFALL 1 — Vollstorno: weder offener Posten noch Mahnkandidat
# ===========================================================================

@pytest.mark.django_db
def test_vollstorno_ist_weder_offener_posten_noch_mahnkandidat(
    admin_client, fall, app_user
):
    """Der veröffentlichte STORNO hebt die Rechnung auf — sie fordert nichts mehr.

    Vorher stand die stornierte Rechnung mit 880,60 € als überfälliger offener
    Posten in der Liste UND als Mahnkandidat im Mahnlauf.
    """
    inv = fall["inv"]
    assert inv.id in _kandidaten_ids(), "Vorbedingung: ohne Storno ist sie Kandidat."

    beleg_service.create_cancellation(app_user.id, invoice_id=inv.id)

    assert inv.id not in _kandidaten_ids()
    # Der Beleg bleibt SICHTBAR (kein Löschen im Projekt), fordert aber nichts mehr.
    zeile = _posten(admin_client, inv.id)
    assert zeile is not None, "Der Beleg verschwindet nicht — er ist nur keine Forderung."
    assert Decimal(zeile["open_amount"]) == _NULL
    assert zeile["payment_status"] == "AUSGEGLICHEN"
    assert zeile["is_overdue"] is False
    assert zeile["is_storniert"] is True
    assert str(inv.id) not in _ueberfaellig_ids(admin_client)


# ===========================================================================
# BRUCHFALL 2 — Storno im ENTWURF hebt nichts auf
# ===========================================================================

@pytest.mark.django_db
def test_storno_im_entwurf_hebt_die_forderung_nicht_auf(admin_client, fall, app_user):
    """Ein Entwurf ist kein Beleg: die Forderung besteht unverändert weiter.

    Die Grenze prüft `status='VEROEFFENTLICHT'` am Kreditbeleg — sonst genügte ein
    unfertiger Entwurf, um eine echte Forderung aus Mahnwesen und offenen Posten
    verschwinden zu lassen (der bequemste Weg, eine Mahnung zu unterdrücken).
    """
    inv = fall["inv"]
    with business_transaction(app_user.id):
        Invoice.objects.create(
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

    assert inv.id in _kandidaten_ids()
    zeile = _posten(admin_client, inv.id)
    assert Decimal(zeile["open_amount"]) == _BRUTTO
    assert zeile["payment_status"] == "OFFEN"
    assert zeile["is_overdue"] is True
    assert zeile["is_storniert"] is False
    assert str(inv.id) in _ueberfaellig_ids(admin_client)


# ===========================================================================
# BRUCHFALL 3 — Teilgutschrift mindert die Forderung, hebt sie nicht auf
# ===========================================================================

@pytest.mark.django_db
def test_teilgutschrift_mindert_die_forderung_hebt_sie_aber_nicht_auf(
    admin_client, fall, app_user
):
    """Eine Kulanz heißt nicht, dass nicht geliefert wurde (Projektgrenze:
    *Storno löst, Gutschrift nicht*).

    Die Rechnung bleibt Forderung und Mahnkandidat — aber nur über den
    **geminderten** Betrag. Vorher wurde der volle Bruttobetrag gemahnt, obwohl
    595,00 € davon längst gutgeschrieben waren.
    """
    inv = fall["inv"]
    credit = beleg_service.create_correction(
        app_user.id, invoice_id=inv.id, positions=[2]
    )
    assert credit.gross_total == -_POS_2_BRUTTO

    row = next(c for c in mahnlauf_service.list_candidates() if c["invoice_id"] == inv.id)
    assert row["open_amount"] == _POS_1_BRUTTO

    zeile = _posten(admin_client, inv.id)
    assert Decimal(zeile["open_amount"]) == _POS_1_BRUTTO
    assert Decimal(zeile["credit_total"]) == -_POS_2_BRUTTO
    assert zeile["payment_status"] == "OFFEN"
    assert zeile["is_overdue"] is True
    assert zeile["is_storniert"] is False


@pytest.mark.django_db
def test_teilgutschrift_und_restzahlung_ist_bezahlt(admin_client, fall, app_user):
    """Zahlt der Kunde nach der Teilgutschrift den Rest, ist die Rechnung BEZAHLT.

    Vorher meldete das System TEILZAHLUNG und mahnte 595,00 € weiter an — den
    Betrag, den das Haus selbst erlassen hatte.
    """
    inv = fall["inv"]
    beleg_service.create_correction(app_user.id, invoice_id=inv.id, positions=[2])
    buchhaltung_service.record_payment(
        app_user.id, invoice_id=inv.id, amount=_POS_1_BRUTTO, paid_at=_HEUTE
    )

    assert inv.id not in _kandidaten_ids()
    zeile = _posten(admin_client, inv.id)
    assert Decimal(zeile["open_amount"]) == _NULL
    assert zeile["payment_status"] == "BEZAHLT"
    assert zeile["is_overdue"] is False


# ===========================================================================
# BRUCHFALL 4 — Vollgutschrift (betragsgleich) auf eine UNGEBUNDENE Rechnung
# ===========================================================================

@pytest.mark.django_db
def test_vollgutschrift_beendet_die_forderung_ohne_die_rechnung_aufzuheben(
    admin_client, fall, app_user
):
    """Vollgutschrift = der Kunde schuldet nichts mehr. Die Rechnung bleibt aber.

    Auf einer **gebundenen** Rechnung ist die Vollgutschrift verboten (verkappter
    Storno, `_vollgutschrift_sperre_pruefen`); auf einer ungebundenen ist sie
    erlaubt. Dann gilt: Forderung = Brutto − Gutschriften = 0,00 € → kein offener
    Posten, kein Mahnkandidat.

    Die Rechnung fällt dabei **nicht** aus der Grundmenge (anders als beim Storno):
    Sie bleibt eine Forderung mit Betrag 0. Nur so bleiben Abrechnungsbindung und
    Zahlungsverhalten unverändert — die Leistung WURDE abgerechnet, sie wurde nur
    vollständig erlassen.
    """
    inv = fall["inv"]
    credit = beleg_service.create_correction(
        app_user.id, invoice_id=inv.id, positions=[1, 2]
    )
    assert credit.gross_total == -_BRUTTO

    assert inv.id not in _kandidaten_ids()
    zeile = _posten(admin_client, inv.id)
    assert Decimal(zeile["open_amount"]) == _NULL
    assert zeile["payment_status"] == "AUSGEGLICHEN"
    assert zeile["is_overdue"] is False
    assert zeile["is_storniert"] is False  # kein Storno — nur ausgeglichen
    assert str(inv.id) not in _ueberfaellig_ids(admin_client)


# ===========================================================================
# BRUCHFALL 5 — der Kreditbeleg selbst ist nie Mahnkandidat
# ===========================================================================

@pytest.mark.django_db
def test_kreditbeleg_ist_niemals_mahnkandidat(admin_client, fall, app_user):
    """STORNO und GUTSCHRIFT fordern nichts — sie haben kein Zahlungsziel gegen den
    Kunden. Sie dürfen weder im Mahnlauf noch im Mahnwesen auftauchen."""
    gutschrift = beleg_service.create_correction(
        app_user.id, invoice_id=fall["inv"].id, positions=[2]
    )
    # Storno an einer zweiten Rechnung (Storno NACH Gutschrift ist verboten).
    zweiter = _veroeffentlichte_rechnung(app_user, name="Storno-Objekt")
    storno = beleg_service.create_cancellation(
        app_user.id, invoice_id=zweiter["inv"].id
    )

    kandidaten = _kandidaten_ids()
    ueberfaellig = _ueberfaellig_ids(admin_client)
    for kredit in (gutschrift, storno):
        assert kredit.invoice_type in ("GUTSCHRIFT", "STORNO")
        assert kredit.gross_total < 0
        assert kredit.id not in kandidaten
        assert _mahnzeile(admin_client, kredit.id) is None
        assert str(kredit.id) not in ueberfaellig


# ===========================================================================
# BRUCHFALL 6 — Teilzahlung auf eine später stornierte Rechnung
# ===========================================================================

@pytest.mark.django_db
def test_teilzahlung_auf_stornierte_rechnung_wird_nicht_verschluckt(
    admin_client, fall, app_user
):
    """Nach dem Storno wird nicht gemahnt — die geleistete Zahlung bleibt sichtbar.

    Der Kunde hat 200 € auf eine Rechnung gezahlt, die danach storniert wurde: Er
    schuldet nichts mehr, das Haus schuldet IHM 200 €. Die Zahlung verschwindet
    nicht stillschweigend, sie wird zur Erstattung.

    **Sie steht aber auf GENAU EINEM Beleg — dem STORNO** (User-Entscheidung: die
    Erstattung wird auf dem Kreditbeleg gebucht). Vorher meldeten BEIDE Belege die
    Erstattungspflicht, und am Original blieb sie auch nach der Erstattung für immer
    stehen (UEBERZAHLT, −200,00 €) — die Einladung zur Doppelerstattung.

    Das Original zeigt hier: 880,60 € gefordert, davon 680,60 € mit dem Storno
    **verrechnet**, 200,00 € bezahlt → 0,00 € offen. Kein negativer Betrag.
    """
    inv = fall["inv"]
    buchhaltung_service.record_payment(
        app_user.id, invoice_id=inv.id, amount=Decimal("200.00"),
        paid_at=_HEUTE - timedelta(days=5), payment_type="TEILZAHLUNG",
    )
    storno = beleg_service.create_cancellation(app_user.id, invoice_id=inv.id)

    assert inv.id not in _kandidaten_ids()
    detail = admin_client.get(f"/api/buchhaltung/invoices/{inv.id}").json()
    assert Decimal(detail["paid_total"]) == Decimal("200.00")
    assert Decimal(detail["verrechnet"]) == _BRUTTO - Decimal("200.00")
    assert Decimal(detail["open_amount"]) == _NULL, (
        "Das Original darf durch einen Kreditbeleg NIE negativ offen stehen."
    )
    assert detail["payment_status"] == "BEZAHLT"
    assert detail["is_overdue"] is False
    assert [p["payment_type"] for p in detail["payments"]] == ["TEILZAHLUNG"]

    # Die Erstattungspflicht steht auf dem STORNO — und nur dort.
    kredit = admin_client.get(f"/api/buchhaltung/invoices/{storno.id}").json()
    assert Decimal(kredit["gross_total"]) == -_BRUTTO
    assert Decimal(kredit["verrechnet"]) == _BRUTTO - Decimal("200.00")
    assert Decimal(kredit["open_amount"]) == Decimal("-200.00")
    assert Decimal(kredit["zu_erstatten"]) == Decimal("200.00")
    assert kredit["payment_status"] == "OFFEN"

    # Und nach der Erstattung sind BEIDE Zeilen ruhig — genau das war vorher
    # unmöglich.
    buchhaltung_service.record_payment(
        app_user.id, invoice_id=storno.id, amount=Decimal("200.00"),
        paid_at=_HEUTE, payment_type="RUECKERSTATTUNG",
    )
    kredit = admin_client.get(f"/api/buchhaltung/invoices/{storno.id}").json()
    assert Decimal(kredit["open_amount"]) == _NULL
    assert Decimal(kredit["zu_erstatten"]) == _NULL
    assert Decimal(kredit["erstattet"]) == Decimal("200.00")
    assert kredit["payment_status"] == "BEZAHLT"
    original = admin_client.get(f"/api/buchhaltung/invoices/{inv.id}").json()
    assert Decimal(original["open_amount"]) == _NULL


# ===========================================================================
# BRUCHFALL 7 — Auswertungen und DATEV bleiben unverändert
# ===========================================================================

@pytest.mark.django_db
def test_umsatz_und_datev_summieren_ueber_alle_belege(fall, app_user):
    """„Offener Posten" und „Umsatz" sind ZWEI Fragen — die Grenze gilt nur für die
    erste.

    Der Umsatz summiert über **alle** Belege; der Kreditbeleg trägt negative Summen
    und mindert ihn dort korrekt. Dieser Test hält fest, dass die Forderungsgrenze
    Auswertungen und DATEV-Buchungsstapel NICHT anfasst: nach dem Storno ist der
    Umsatz 0,00 € (Rechnung + Storno), und der Stapel führt weiterhin BEIDE Belege
    (GoBD: kein Beleg verschwindet).
    """
    inv = fall["inv"]
    vorher = auswertungen_service.umsatz_projektuebersicht_summary()["revenue"]
    assert Decimal(vorher["net_total"]) == Decimal("740.00")

    storno = beleg_service.create_cancellation(app_user.id, invoice_id=inv.id)

    nachher = auswertungen_service.umsatz_projektuebersicht_summary()["revenue"]
    assert Decimal(nachher["net_total"]) == _NULL
    assert Decimal(nachher["gross_total"]) == _NULL
    assert nachher["invoice_count"] == vorher["invoice_count"]
    assert nachher["credit_count"] == vorher["credit_count"] + 1

    firma_service.update_company_profile(
        app_user.id,
        company_name="Mitra Sanitär GmbH",
        datev_consultant_number="12345",
        datev_client_number="1001",
        datev_chart_of_accounts="SKR03",
        datev_account_length=4,
        datev_fiscal_year_start_month=1,
    )
    _dateiname, inhalt = datev_service.build_datev_export(inv.invoice_date, _HEUTE)
    csv = inhalt.decode("cp1252")
    assert inv.invoice_number in csv
    assert storno.invoice_number in csv


# ===========================================================================
# BRUCHFALL 8 — Mahnstufen-Fortschreibung endet mit dem Storno
# ===========================================================================

@pytest.mark.django_db
def test_stornierte_rechnung_eskaliert_keine_weitere_mahnstufe(
    admin_client, fall, app_user
):
    """Stufe 2 ist ausgestellt, dann wird storniert → keine Stufe 3.

    Auch nicht, wenn der Lauf mit der Rechnung im Stapel angestoßen wird: er prüft
    jede Zeile erneut gegen die Forderungsgrenze und überspringt sie. Die bereits
    erzeugten Mahnungen bleiben erhalten (kein Löschen); das Mahnwesen zeigt die
    Rechnung weiter — aber ausdrücklich als nicht mehr mahnbar.
    """
    inv = fall["inv"]
    buchhaltung_service.issue_dunning_notice(
        app_user.id, invoice_id=inv.id, level=2, issued_at=_HEUTE - timedelta(days=1)
    )
    beleg_service.create_cancellation(app_user.id, invoice_id=inv.id)

    ergebnis = mahnlauf_service.run(
        app_user.id, items=[{"invoice_id": inv.id, "level": 3}], send_email=False
    )
    assert ergebnis["issued"] == 0
    assert ergebnis["skipped"] == 1
    assert _max_level(inv.id) == 2

    zeile = _mahnzeile(admin_client, inv.id)
    assert zeile is not None, "Die Mahnhistorie wird nicht gelöscht."
    assert zeile["dunning_level"] == 2
    assert zeile["is_storniert"] is True
    assert zeile["mahnbar"] is False
    assert Decimal(zeile["open_amount"]) == _NULL


@pytest.mark.django_db
def test_ungemahnte_stornierte_rechnung_ist_kein_mahnfall(admin_client, app_user):
    """Ohne Mahnhistorie verschwindet die stornierte Rechnung ganz aus dem
    Mahnwesen — sie ist kein Mahnfall, nicht einmal ein stiller."""
    inv = _veroeffentlichte_rechnung(app_user, name="Ungemahnt-Objekt")["inv"]
    assert _mahnzeile(admin_client, inv.id) is not None, "Vorbedingung: überfällig."
    beleg_service.create_cancellation(app_user.id, invoice_id=inv.id)
    assert _mahnzeile(admin_client, inv.id) is None
