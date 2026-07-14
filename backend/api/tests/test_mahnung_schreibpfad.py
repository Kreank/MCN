"""Die Forderungsgrenze im SCHREIBPFAD des Mahnwesens.

Liste, Filter, Mahnlauf und der UI-Knopf hielten die Grenze bereits — **der einzige
Pfad, der tatsächlich mahnt, hielt sie nicht**: `POST /invoices/{id}/dunning` stellte
auf einer vollständig stornierten Rechnung (offen 0,00 €, `mahnbar=False`) klaglos
eine Mahnstufe aus (201), und `POST /dunning-notices/{id}/send-email` schickte danach
den Text „… ist die Rechnung RE-… weiterhin offen" an den Kunden. Dasselbe auf einer
voll bezahlten Rechnung und auf einem Kreditbeleg.

Das war der letzte offene Weg zu „Mahnung über Geld, das der Kunde nicht mehr
schuldet".

Die Grenze sitzt jetzt **doppelt**:
  * im Service (`buchhaltung.mahnsperre`, gezogen aus derselben einen Rechenstelle
    `mit_zahlungsstand`/`zahlungsspiegel`) → benannter Grund als 422, und
  * im **DB-Trigger** (`invoicing.check_dunning_notice`, Migration 0097) → auch ein
    Schreiber, der am Service vorbeigeht, bekommt keine Mahnung durch.

Die Tests fahren beide Ebenen: die API-Fälle über den echten Endpunkt, die
Trigger-Fälle über einen Roh-Insert (ORM in `business_transaction`), der den
Service-Guard bewusst umgeht.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.db import DatabaseError

from db_core.db_context import business_transaction
from db_core.models import DunningNotice, Invoice
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import buchhaltung as buchhaltung_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service

_HEUTE = date.today()
_GESTERN = _HEUTE - timedelta(days=1)
# 100 × 2,40 € + 10 × 50,00 € = 740,00 € netto → 880,60 € brutto (19 %).
_BRUTTO = Decimal("880.60")

DUNNING_URL = "/api/buchhaltung/invoices/{}/dunning"


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
    """Veröffentlichte, 30 Tage überfällige Rechnung über 880,60 € brutto."""
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


def _mahnen(admin_client, invoice_id, *, level=1, issued_at=None):
    return admin_client.post(
        DUNNING_URL.format(invoice_id),
        data={"level": level, "issued_at": str(issued_at or _HEUTE)},
        content_type="application/json",
    )


def _roh_mahnung(app_user, invoice_id, *, level=1, issued_at=None):
    """Mahnstufe **am Service vorbei** direkt in die Tabelle (nur der Trigger hält)."""
    with business_transaction(app_user.id):
        DunningNotice.objects.create(
            id=uuid.uuid4(),
            invoice_id=invoice_id,
            level_id=level,
            issued_at=issued_at or _HEUTE,
            created_by_id=app_user.id,
        )


def _mahnbar(invoice_id):
    """Die EINE Rechenstelle: was sagt der Zahlungsspiegel?"""
    inv = buchhaltung_service.mit_zahlungsstand(
        Invoice.objects.filter(id=invoice_id)
    ).get()
    return buchhaltung_service.zahlungsspiegel(inv, heute=_HEUTE)


# ===========================================================================
# API — der Schreibpfad lehnt ab, was keine offene Forderung ist (422)
# ===========================================================================

@pytest.mark.django_db
def test_mahnung_auf_stornierte_rechnung_wird_abgelehnt(admin_client, app_user):
    """Vollstorno → offen 0,00 €, `mahnbar=False`. Vorher: 201 Created."""
    inv = _rechnung(app_user, name="Mahnsperre-Storno")
    beleg_service.create_cancellation(app_user.id, invoice_id=inv.id)
    spiegel = _mahnbar(inv.id)
    assert spiegel["mahnbar"] is False and spiegel["open_amount"] == Decimal("0.00")

    r = _mahnen(admin_client, inv.id)

    assert r.status_code == 422, r.content
    assert "storniert" in r.json()["detail"].lower()
    assert not DunningNotice.objects.filter(invoice_id=inv.id).exists()


@pytest.mark.django_db
def test_mahnung_auf_voll_bezahlte_rechnung_wird_abgelehnt(admin_client, app_user):
    """Voll bezahlt → nichts mehr offen, also nichts mehr zu mahnen."""
    inv = _rechnung(app_user, name="Mahnsperre-Bezahlt")
    buchhaltung_service.record_payment(
        app_user.id, invoice_id=inv.id, amount=_BRUTTO, paid_at=_HEUTE
    )
    spiegel = _mahnbar(inv.id)
    assert spiegel["payment_status"] == "BEZAHLT" and spiegel["mahnbar"] is False

    r = _mahnen(admin_client, inv.id)

    assert r.status_code == 422, r.content
    assert "offen" in r.json()["detail"].lower()
    assert not DunningNotice.objects.filter(invoice_id=inv.id).exists()


@pytest.mark.django_db
def test_mahnung_auf_kreditbeleg_wird_abgelehnt(admin_client, app_user):
    """Ein STORNO/eine GUTSCHRIFT fordert nichts — sie ist nie Mahnkandidat."""
    inv = _rechnung(app_user, name="Mahnsperre-Kreditbeleg")
    storno = beleg_service.create_cancellation(app_user.id, invoice_id=inv.id)
    assert storno.gross_total == -_BRUTTO
    assert _mahnbar(storno.id)["is_kreditbeleg"] is True

    r = _mahnen(admin_client, storno.id)

    assert r.status_code == 422, r.content
    assert "kreditbeleg" in r.json()["detail"].lower()
    assert not DunningNotice.objects.filter(invoice_id=storno.id).exists()


@pytest.mark.django_db
def test_mahnung_auf_offene_ueberfaellige_forderung_bleibt_moeglich(
    admin_client, app_user
):
    """Kein Regress: die echte offene, überfällige Forderung wird weiter gemahnt."""
    inv = _rechnung(app_user, name="Mahnsperre-Regress")
    assert _mahnbar(inv.id)["mahnbar"] is True

    r = _mahnen(admin_client, inv.id)

    assert r.status_code == 201, r.content
    assert r.json()["level"] == 1
    # Auch eine TEILzahlung lässt die Restforderung mahnbar (Stufe 2).
    buchhaltung_service.record_payment(
        app_user.id, invoice_id=inv.id, amount=Decimal("300.00"),
        paid_at=_HEUTE, payment_type="TEILZAHLUNG",
    )
    r2 = _mahnen(admin_client, inv.id, level=2)
    assert r2.status_code == 201, r2.content


@pytest.mark.django_db
def test_bestehende_mahnungen_bleiben_nach_storno_erhalten(admin_client, app_user):
    """Die Grenze verhindert die NÄCHSTE Stufe — sie löscht keine Historie (GoBD)."""
    inv = _rechnung(app_user, name="Mahnsperre-Historie")
    assert _mahnen(admin_client, inv.id, level=1).status_code == 201
    beleg_service.create_cancellation(app_user.id, invoice_id=inv.id)

    r = _mahnen(admin_client, inv.id, level=2)

    assert r.status_code == 422
    assert list(
        DunningNotice.objects.filter(invoice_id=inv.id).values_list("level", flat=True)
    ) == [1]
    # Und die Mahnliste zeigt den Fall weiter — als nicht mehr mahnbar.
    zeile = next(
        i
        for i in admin_client.get("/api/buchhaltung/dunning").json()["items"]
        if i["id"] == str(inv.id)
    )
    assert zeile["dunning_level"] == 1
    assert zeile["mahnbar"] is False


# ===========================================================================
# Die Mahnliste rechnet nicht selbst — EINE Rechenstelle für days_overdue
# ===========================================================================

@pytest.mark.django_db
def test_bezahlte_gemahnte_rechnung_ist_nicht_mehr_ueberfaellig(admin_client, app_user):
    """Bezahlt heißt nicht mehr „30 Tage überfällig".

    `api/buchhaltung.list_dunning` rechnete die Überfälligkeitstage selbst
    (`today - due_date`, sobald die Rechnung eine Forderung IST) statt sie aus dem
    Zahlungsspiegel zu nehmen — der sie nur bei tatsächlich offenem Betrag führt.
    Folge: Eine voll bezahlte, früher gemahnte Rechnung stand mit „30 Tage
    überfällig" in der Liste, direkt neben dem Hinweis „nichts mehr offen".
    """
    inv = _rechnung(app_user, name="Ueberfaellig-Bezahlt")
    assert _mahnen(admin_client, inv.id, level=1).status_code == 201
    buchhaltung_service.record_payment(
        app_user.id, invoice_id=inv.id, amount=_BRUTTO, paid_at=_HEUTE
    )

    zeile = next(
        i
        for i in admin_client.get("/api/buchhaltung/dunning").json()["items"]
        if i["id"] == str(inv.id)
    )
    assert zeile["days_overdue"] is None, "Wer nichts schuldet, ist nicht im Verzug."
    assert zeile["mahnbar"] is False
    assert zeile["is_storniert"] is False
    # Der Grund gehört als Text an die Oberfläche — „ausgeglichen" wäre hier falsch:
    # es HAT jemand gezahlt.
    assert zeile["payment_status"] == "BEZAHLT"
    assert Decimal(zeile["open_amount"]) == Decimal("0.00")


@pytest.mark.django_db
def test_offene_ueberfaellige_forderung_zeigt_ihre_tage_weiter(admin_client, app_user):
    """Gegenprobe: bei echter offener Forderung stehen die Tage weiter da."""
    inv = _rechnung(app_user, name="Ueberfaellig-Offen")
    zeile = next(
        i
        for i in admin_client.get("/api/buchhaltung/dunning").json()["items"]
        if i["id"] == str(inv.id)
    )
    assert zeile["days_overdue"] == 30
    assert zeile["payment_status"] == "OFFEN"
    assert zeile["mahnbar"] is True


@pytest.mark.django_db
def test_stornierte_gemahnte_rechnung_ist_ausgeglichen_ohne_verzugstage(
    admin_client, app_user
):
    """Storno nach Mahnung: keine Verzugstage, Status AUSGEGLICHEN (niemand zahlte)."""
    inv = _rechnung(app_user, name="Ueberfaellig-Storno")
    assert _mahnen(admin_client, inv.id, level=1).status_code == 201
    beleg_service.create_cancellation(app_user.id, invoice_id=inv.id)

    zeile = next(
        i
        for i in admin_client.get("/api/buchhaltung/dunning").json()["items"]
        if i["id"] == str(inv.id)
    )
    assert zeile["days_overdue"] is None
    assert zeile["is_storniert"] is True
    assert zeile["payment_status"] == "AUSGEGLICHEN"


# ===========================================================================
# DB-Trigger — was am Service vorbeigeht, hält die Datenbank auf
# ===========================================================================
# „Was im Service sitzt, ist umgehbar; erst was im Trigger sitzt, hält."

@pytest.mark.django_db(transaction=True)
def test_datenbank_verweigert_mahnung_auf_stornierte_rechnung(app_user):
    inv = _rechnung(app_user, name="Trigger-Storno")
    beleg_service.create_cancellation(app_user.id, invoice_id=inv.id)

    with pytest.raises(DatabaseError, match="storniert"):
        _roh_mahnung(app_user, inv.id)
    assert not DunningNotice.objects.filter(invoice_id=inv.id).exists()


@pytest.mark.django_db(transaction=True)
def test_datenbank_verweigert_mahnung_ohne_offenen_betrag(app_user):
    inv = _rechnung(app_user, name="Trigger-Bezahlt")
    buchhaltung_service.record_payment(
        app_user.id, invoice_id=inv.id, amount=_BRUTTO, paid_at=_HEUTE
    )

    with pytest.raises(DatabaseError, match="offene"):
        _roh_mahnung(app_user, inv.id)
    assert not DunningNotice.objects.filter(invoice_id=inv.id).exists()


@pytest.mark.django_db(transaction=True)
def test_datenbank_verweigert_mahnung_auf_kreditbeleg(app_user):
    inv = _rechnung(app_user, name="Trigger-Kreditbeleg")
    storno = beleg_service.create_cancellation(app_user.id, invoice_id=inv.id)

    with pytest.raises(DatabaseError, match="Kreditbeleg"):
        _roh_mahnung(app_user, storno.id)
    assert not DunningNotice.objects.filter(invoice_id=storno.id).exists()


@pytest.mark.django_db(transaction=True)
def test_datenbank_laesst_die_echte_forderung_durch(app_user):
    """Gegenprobe: der Trigger nickt nicht alles ab — die offene Forderung geht."""
    inv = _rechnung(app_user, name="Trigger-Regress")
    _roh_mahnung(app_user, inv.id, issued_at=_GESTERN)
    assert DunningNotice.objects.filter(invoice_id=inv.id).count() == 1


# ===========================================================================
# DRIFT — Service und Trigger sagen dasselbe, auch MIT Kreditbelegen
# ===========================================================================
# Der Verrechnungs-Slice hat `open_amount` der Rechnung umgestellt:
#
#     alt:  offen = brutto + kredit − gezahlt              (kann negativ werden)
#     neu:  offen = max(brutto − gezahlt − kreditsumme, 0) (nie negativ durch Kredite)
#
# Der Trigger (Migration 0097) rechnet weiterhin nach der **alten** Formel. Für sein
# einziges Prädikat — „ist noch etwas offen?" — sind beide **identisch**:
#
#     max(r − c, 0) > 0   ⟺   r − c > 0        (r = brutto − gezahlt, c = Kreditsumme)
#
# Der Trigger musste seine Formel deshalb NICHT mitziehen (keine Migration). Er
# rechnet konservativer, nie großzügiger: Wo die neue Formel 0 zeigt, zeigt die alte
# ≤ 0 — beide verweigern. Dieser Test lässt diese Behauptung nicht als Behauptung
# stehen, sondern **fährt sie**: Für jeden Fall entscheidet erst der Zahlungsspiegel,
# dann die Datenbank — und beide müssen zum selben Ergebnis kommen.

_DRIFT_FAELLE = [
    # (name, zahlung, gutschrift_positionen, storno, erwartet_mahnbar)
    ("offen", None, None, False, True),
    ("teilzahlung", "300.00", None, False, True),
    ("voll_bezahlt", "880.60", None, False, False),
    ("teilgutschrift", None, [2], False, True),
    ("vollgutschrift", None, [1, 2], False, False),
    ("bezahlt_dann_teilgutschrift", "880.60", [2], False, False),
    # 300 gezahlt, 595 gutgeschrieben → 880,60 − 300 − 595 = −14,40 → nichts mehr offen
    ("teilzahlung_und_teilgutschrift_aufgezehrt", "300.00", [2], False, False),
    # 100 gezahlt, 595 gutgeschrieben → 185,60 € bleiben offen und mahnbar
    ("teilzahlung_und_teilgutschrift_rest_offen", "100.00", [2], False, True),
    ("teilzahlung_dann_storno", "300.00", None, True, False),
    ("storno_unbezahlt", None, None, True, False),
]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("name", "zahlung", "positionen", "storno", "erwartet"),
    _DRIFT_FAELLE,
    ids=[f[0] for f in _DRIFT_FAELLE],
)
def test_service_und_trigger_sind_deckungsgleich(
    app_user, name, zahlung, positionen, storno, erwartet
):
    """Was `zahlungsspiegel()['mahnbar']` verneint, weist die DB ab — und umgekehrt."""
    inv = _rechnung(app_user, name=f"Drift-{name}")
    if zahlung is not None:
        buchhaltung_service.record_payment(
            app_user.id, invoice_id=inv.id, amount=Decimal(zahlung),
            paid_at=_HEUTE, payment_type="TEILZAHLUNG",
        )
    if positionen is not None:
        beleg_service.create_correction(
            app_user.id, invoice_id=inv.id, positions=positionen
        )
    if storno:
        beleg_service.create_cancellation(app_user.id, invoice_id=inv.id)

    spiegel = _mahnbar(inv.id)
    assert spiegel["mahnbar"] is erwartet, (
        f"Der Zahlungsspiegel irrt bei '{name}': offen {spiegel['open_amount']}."
    )

    if erwartet:
        _roh_mahnung(app_user, inv.id, issued_at=_GESTERN)
        assert DunningNotice.objects.filter(invoice_id=inv.id).count() == 1
    else:
        with pytest.raises(DatabaseError):
            _roh_mahnung(app_user, inv.id, issued_at=_GESTERN)
        assert not DunningNotice.objects.filter(invoice_id=inv.id).exists()
