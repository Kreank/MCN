"""Slice „Abrechnung": aus Angebot bzw. Bericht + Zeiten wird eine Rechnung.

Das Versprechen, das hier geprüft wird, ist ein einziges:

    **Dieselbe Leistung kann physisch nicht zweimal abgerechnet werden.**

„Physisch" heißt: nicht, weil der Service aufpasst, sondern weil die Datenbank es
nicht zulässt. `test_db_sperrt_die_zweite_bindung_am_service_vorbei` geht deshalb
bewusst am Service vorbei und schreibt direkt über das ORM.

Der wichtigste Test des Slices ist `test_storno_loest_die_bindung`: Er ist der
ganze Grund für das Bindungs-Design. Eine veröffentlichte Rechnungsposition ist
unveränderlich (B-21) — ohne die Freigabe durch den Storno wären stornierte
Stunden für immer verbrannt.

Daneben die zweite Leitplanke: **Fehlt der Preis, wird NICHT mit 0 € abgerechnet
und auch nichts weggelassen.** Der Vorgang scheitert mit einer strukturierten
Klärungsliste — und derselbe Aufruf nimmt den vom Menschen genannten Preis
entgegen. Ein Fehler ohne Ausweg wird sonst irgendwann umgangen, und der Umweg
wäre die 0-€-Position.
"""
import inspect
import uuid
from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal

import pytest
from django.db import IntegrityError

from db_core.db_context import business_transaction
from db_core.models import (
    BillingLink,
    Invoice,
    InvoiceLine,
    SiteReportLine,
    WorkOrder,
)
from db_core.services import abrechnung as abrechnung_service
from db_core.services import artikel as artikel_service
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import einsatz as einsatz_service
from db_core.services import identity as identity_service
from db_core.services import lohngruppe as lohngruppe_service
from db_core.services import mitarbeiter as mitarbeiter_service
from db_core.services import property as property_service
from db_core.services import site_report as report_service
from db_core.services import zeiterfassung as zeit_service
from db_core.services.abrechnung import AbrechnungError, PreisUnbekannt

PNG_1x1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
    b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)

T0 = datetime(2026, 7, 6, 6, 0, tzinfo=dt_timezone.utc)


class FakeStorage:
    def ensure_bucket(self):
        pass

    def put_object(self, key, data, content_type="application/octet-stream"):
        return None

    def get_object(self, key):
        raise KeyError(key)

    def remove_object(self, key):
        pass


@pytest.fixture
def fake_storage(monkeypatch):
    from db_core import storage as storage_module

    monkeypatch.setattr(storage_module, "get_storage", lambda: FakeStorage())


# --- Szenario ---------------------------------------------------------------

@pytest.fixture
def szenario(app_user):
    obj = property_service.create_property(
        app_user.id, name="Regie-Objekt", property_type="WEG",
        street="Baustelle", house_number="1", postal_code="10115", city="Berlin",
    )
    kunde = identity_service.create_person(
        app_user.id, first_name="Karla", last_name="Kundin"
    )
    return {"user": app_user, "obj": obj, "kunde": kunde}


def _auftrag(szenario, *, bis="KAUFMAENNISCH_GEPRUEFT", billing_mode=None):
    """Auftrag mit erfüllten Freigabe-Toren, hochgefahren bis `bis`.

    Default KAUFMAENNISCH_GEPRUEFT — das Tor B-08 verlangt es für die
    Veröffentlichung einer RECHNUNG. Wer Zeiten erfassen will, muss vorher bei
    IN_AUSFUEHRUNG stehenbleiben: Nach der kaufmännischen Prüfung ist das
    B-28-Korrekturfenster zu (kein INSERT mehr auf `time_entry`).
    """
    app_user, obj, kunde = szenario["user"], szenario["obj"], szenario["kunde"]
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Heizung erneuern"
    )
    auftrag_service.set_order_evidence(
        app_user.id, work_order_id=order.id, reference="Auftrag per Mail"
    )
    auftrag_service.confirm_responsibility(
        app_user.id, work_order_id=order.id, scope="COMMON_PROPERTY"
    )
    for role in ("PRINCIPAL", "INVOICE_DEBTOR"):
        auftrag_service.add_work_order_party(
            app_user.id, work_order_id=order.id, party_id=kunde.id,
            role=role, is_primary=True,
        )
    if billing_mode:
        abrechnung_service.set_billing_mode(
            app_user.id, work_order_id=order.id, billing_mode=billing_mode
        )
    for to in ("FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG",
               "TECHNISCH_ABGESCHLOSSEN", "KAUFMAENNISCH_GEPRUEFT"):
        auftrag_service.advance_status(
            app_user.id, work_order_id=order.id, to_status=to
        )
        if to == bis:
            break
    order.refresh_from_db()
    return order


def _kg(szenario, order):
    """Auftrag auf KAUFMAENNISCH_GEPRUEFT bringen (Tor B-08 für die RECHNUNG)."""
    for to in ("TECHNISCH_ABGESCHLOSSEN", "KAUFMAENNISCH_GEPRUEFT"):
        if order.status != to:
            auftrag_service.advance_status(
                szenario["user"].id, work_order_id=order.id, to_status=to
            )
    order.refresh_from_db()
    return order


def _beteiligte(szenario, invoice):
    for role in ("INVOICE_DEBTOR", "INVOICE_RECIPIENT"):
        beleg_service.add_invoice_party(
            szenario["user"].id, invoice_id=invoice.id,
            party_id=szenario["kunde"].id, role=role, is_primary=True,
        )


def _artikel(szenario, nummer, *, vk=None, beschreibung="Kupferrohr 18"):
    """Artikel — mit Festpreis (Server kennt den VK) oder ohne (VK unbekannt).

    OHNE VK-Variante und ohne Lieferanten-EK liefert `vk_vorschlag` `sale_price =
    None` = **unbekannt, nicht 0**. Genau der Fall, um den es geht.
    """
    art = artikel_service.create_article(
        szenario["user"].id, article_number=nummer, description=beschreibung,
        unit="m", line_type="MATERIAL",
    )
    if vk is not None:
        artikel_service.set_article_sale_price(
            szenario["user"].id, article_id=art.id,
            fixed_price=Decimal(vk), is_standard=True,
        )
    return art


def _artikel_mit_vk_gruppe(szenario, nummer, ek, *, aufschlag="50",
                           beschreibung="Rohr aus dem Import"):
    """Artikel mit Lieferanten-EK **und** VK-Gruppe (Basis EK) — der DATANORM-Weg.

    Mit `ek="0.00"` ist das der Importfehler, um den es geht: Der EK-CHECK erlaubt
    `>= 0`, die VK-Gruppe rechnet ihre Formel brav auf der 0-Basis durch und
    liefert 0,00 € — eine Zahl, die wie ein Preis aussieht und keiner ist.
    """
    art = artikel_service.create_article(
        szenario["user"].id, article_number=nummer, description=beschreibung,
        unit="m", line_type="MATERIAL",
    )
    lieferant = identity_service.create_person(
        szenario["user"].id, first_name="Groß", last_name=f"Handel {nummer}"
    )
    artikel_service.set_primary_supplier(
        szenario["user"].id, article_id=art.id, supplier_party_id=lieferant.id,
        supplier_article_number=nummer, last_purchase_price=Decimal(ek),
    )
    gruppe = artikel_service.create_sale_price_group(
        szenario["user"].id, name=f"VK-Gruppe {nummer}", calc_basis="EK",
        operator="AUFSCHLAG", percent_change=Decimal(aufschlag),
    )
    artikel_service.set_article_sale_price(
        szenario["user"].id, article_id=art.id, sale_price_group_id=gruppe.id,
        is_standard=True,
    )
    return art


def _bericht(szenario, order, lines, *, signieren=True, datum="2026-07-06"):
    report = report_service.create_report(
        szenario["user"].id, work_order_id=order.id, report_date=datum,
        activity_text="Rohre verlegt.",
    )
    if lines:
        report_service.set_report_lines(
            szenario["user"].id, report_id=report.id, lines=lines
        )
    if signieren:
        report_service.sign_report(
            szenario["user"].id, report_id=report.id,
            signed_by_name="Karla Kundin", signature_png=PNG_1x1,
        )
    report.refresh_from_db()
    return report


def _monteur(szenario, name, *, stundensatz="65.00", kostensatz="42.00",
             mit_lohngruppe=True):
    """Ein Mitarbeiter mit Vertrag — die Lohngruppe hängt am Vertrag."""
    from db_core.models import AppUser

    app_user = AppUser.objects.create(
        id=uuid.uuid4(), display_name=name, status="ACTIVE", version=1
    )
    person = identity_service.create_person(
        szenario["user"].id, first_name=name, last_name="Monteur"
    )
    wg = None
    if mit_lohngruppe:
        wg = lohngruppe_service.create_wage_group(
            szenario["user"].id, name=f"Lohngruppe {name}", kind="LOHN",
            hourly_rate=Decimal(stundensatz), cost_rate=Decimal(kostensatz),
        )
    employee = mitarbeiter_service.create_employee(
        szenario["user"].id, app_user_id=app_user.id, party_id=person.id,
        hired_on=date(2026, 1, 1),
    )
    mitarbeiter_service.create_contract(
        szenario["user"].id, employee_id=employee.id, valid_from=date(2026, 1, 1),
        hours={f"hours_{d}": 8 for d in ("monday", "tuesday", "wednesday",
                                         "thursday", "friday")},
        vacation_days_per_year=30,
        wage_group_id=wg.id if wg else None,
    )
    return {"app_user": app_user, "employee": employee, "wage_group": wg}


def _job(szenario, order):
    return einsatz_service.create_service_job(
        szenario["user"].id, work_order_id=order.id
    )


def _zeit(szenario, monteur, job, *, von, bis):
    return zeit_service.zeiteintrag_anlegen(
        szenario["user"].id,
        user_id=monteur["app_user"].id,
        category_id=zeit_service.standard_kategorie().id,
        service_job_id=job.id,
        started_at=von,
        ended_at=bis,
    )


def _angebot(szenario, order, lines, *, versenden=True):
    quote = beleg_service.create_quote(
        szenario["user"].id, property_id=szenario["obj"].id,
        title="Angebot Heizung", work_order_id=order.id, lines=lines,
    )
    if versenden:
        beleg_service.send_quote(szenario["user"].id, quote_id=quote.id)
    quote.refresh_from_db()
    return quote


def _pos(desc, qty, preis, *, kind="NORMAL", typ="MATERIAL", unit="m"):
    return {
        "line_type": typ, "line_kind": kind, "description": desc,
        "quantity": qty, "unit": unit, "unit_price": preis, "tax_code": "DE_19",
    }


# ---------------------------------------------------------------------------
# Angebot → Rechnung (PAUSCHAL)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_angebot_wird_wertgleich_kopiert(szenario):
    """Die Rechnung trägt die Werte des Angebots — nicht die von heute."""
    order = _auftrag(szenario)
    quote = _angebot(szenario, order, [
        _pos("Rohr DN20", "10", "12.50"),
        _pos("Montage", "4", "65.00", typ="ARBEITSZEIT", unit="h"),
    ])
    invoice = abrechnung_service.rechnung_aus_angebot(
        szenario["user"].id, quote_id=quote.id
    )
    lines = list(invoice.lines.all().order_by("position_number"))
    assert [l.description for l in lines] == ["Rohr DN20", "Montage"]
    assert [l.unit_price for l in lines] == [Decimal("12.50"), Decimal("65.00")]
    assert invoice.net_total == quote.net_total
    assert invoice.gross_total == quote.gross_total
    assert invoice.status == "ENTWURF"
    assert invoice.work_order_id == order.id
    # Je Betragsposition eine Bindung.
    assert BillingLink.objects.filter(
        invoice_id=invoice.id, source_kind="ANGEBOTSPOSITION"
    ).count() == 2


@pytest.mark.django_db
def test_alternativ_und_bedarf_werden_nicht_abgerechnet(szenario):
    """Optionen sind keine Beauftragung — sie stehen nicht auf der Rechnung."""
    order = _auftrag(szenario)
    quote = _angebot(szenario, order, [
        _pos("Rohr DN20", "10", "12.50"),
        _pos("Alternativ: Edelstahl", "10", "30.00", kind="ALTERNATIV"),
        _pos("Bedarf: Zusatzventil", "1", "80.00", kind="BEDARF"),
    ])
    invoice = abrechnung_service.rechnung_aus_angebot(
        szenario["user"].id, quote_id=quote.id
    )
    beschreibungen = [l.description for l in invoice.lines.all()]
    assert beschreibungen == ["Rohr DN20"]
    assert invoice.net_total == Decimal("125.00")


@pytest.mark.django_db
def test_zweiter_lauf_ueber_dasselbe_angebot_scheitert(szenario):
    """Doppelabrechnung — der Kernfall."""
    order = _auftrag(szenario)
    quote = _angebot(szenario, order, [_pos("Rohr DN20", "10", "12.50")])
    abrechnung_service.rechnung_aus_angebot(szenario["user"].id, quote_id=quote.id)

    with pytest.raises(AbrechnungError) as exc:
        abrechnung_service.rechnung_aus_angebot(
            szenario["user"].id, quote_id=quote.id
        )
    assert "bereits abgerechnet" in str(exc.value)
    assert "Rohr DN20" in str(exc.value)
    assert Invoice.objects.count() == 1


@pytest.mark.django_db
def test_angebot_im_entwurf_ist_keine_vereinbarung(szenario):
    order = _auftrag(szenario)
    quote = _angebot(szenario, order, [_pos("Rohr", "1", "10.00")], versenden=False)
    with pytest.raises(AbrechnungError, match="keine Vereinbarung"):
        abrechnung_service.rechnung_aus_angebot(
            szenario["user"].id, quote_id=quote.id
        )


# ---------------------------------------------------------------------------
# DIE SPERRE LIEGT IN DER DATENBANK
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_db_sperrt_die_zweite_bindung_am_service_vorbei(szenario):
    """Am Service vorbei, direkt über das ORM: die DB weist es ab.

    Der Beweis, dass die Doppelabrechnungssperre **physisch** ist. Wäre sie nur
    eine Service-Regel, genügte ein zweiter Schreibpfad (KI-Agent, Skript,
    künftiger Endpunkt), um sie zu umgehen.
    """
    order = _auftrag(szenario)
    quote = _angebot(szenario, order, [_pos("Rohr DN20", "10", "12.50")])
    invoice = abrechnung_service.rechnung_aus_angebot(
        szenario["user"].id, quote_id=quote.id
    )
    line = invoice.lines.first()
    quote_line = quote.lines.first()

    with pytest.raises(IntegrityError):
        with business_transaction(szenario["user"].id):
            BillingLink.objects.create(
                id=uuid.uuid4(),
                invoice_id=invoice.id,
                invoice_line_id=line.id,
                source_kind="ANGEBOTSPOSITION",
                quote_line_id=quote_line.id,
            )


@pytest.mark.django_db
def test_bindung_kann_nicht_geloescht_werden(szenario):
    """Kein DELETE: Ein gelöschter Link machte die Sperre spurlos rückgängig."""
    order = _auftrag(szenario)
    quote = _angebot(szenario, order, [_pos("Rohr", "1", "10.00")])
    invoice = abrechnung_service.rechnung_aus_angebot(
        szenario["user"].id, quote_id=quote.id
    )
    with pytest.raises(Exception):
        with business_transaction(szenario["user"].id):
            BillingLink.objects.filter(invoice_id=invoice.id).delete()


# ---------------------------------------------------------------------------
# DER WICHTIGSTE TEST: der Storno löst die Bindung
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_storno_loest_die_bindung(szenario):
    """Nach dem Storno ist dieselbe Leistung wieder abrechenbar.

    **Der ganze Grund für dieses Design.** Die Rechnungsposition ist nach dem
    Veröffentlichen unveränderlich — sie lässt sich nicht entwerten. Die Bindung
    liegt daneben und wird gelöst; die Quelle wird frei.
    """
    order = _auftrag(szenario)
    quote = _angebot(szenario, order, [_pos("Rohr DN20", "10", "12.50")])
    invoice = abrechnung_service.rechnung_aus_angebot(
        szenario["user"].id, quote_id=quote.id
    )
    _beteiligte(szenario, invoice)
    beleg_service.publish_invoice(szenario["user"].id, invoice_id=invoice.id)

    # Vor dem Storno: gesperrt.
    with pytest.raises(AbrechnungError, match="bereits abgerechnet"):
        abrechnung_service.rechnung_aus_angebot(
            szenario["user"].id, quote_id=quote.id
        )

    storno = beleg_service.create_cancellation(
        szenario["user"].id, invoice_id=invoice.id
    )
    assert storno.invoice_type == "STORNO"

    link = BillingLink.objects.get(invoice_id=invoice.id)
    assert link.released_at is not None
    assert storno.invoice_number in link.released_reason
    # Die Position bleibt am gelösten Link stehen (der Beleg existiert weiter).
    assert link.invoice_line_id is not None

    # Und jetzt: wieder abrechenbar.
    neu = abrechnung_service.rechnung_aus_angebot(
        szenario["user"].id, quote_id=quote.id
    )
    assert neu.id != invoice.id
    assert neu.net_total == Decimal("125.00")
    assert BillingLink.objects.filter(
        invoice_id=neu.id, released_at__isnull=True
    ).count() == 1


@pytest.mark.django_db
def test_gutschrift_loest_die_bindung_NICHT(szenario):
    """Die Teilkorrektur hebt den Beleg nicht auf — die Leistung bleibt berechnet.

    Dieselbe Grenze zieht das Belegmodul schon bei den Abschlägen: nur das STORNO
    macht einen angerechneten Abschlag wieder frei, die Gutschrift nicht. Zwei
    verschiedene Auffassungen von „aufgehoben" im selben Modul wären eine
    Fehlerquelle.
    """
    order = _auftrag(szenario)
    quote = _angebot(szenario, order, [
        _pos("Rohr DN20", "10", "12.50"),
        _pos("Ventil", "1", "80.00"),
    ])
    invoice = abrechnung_service.rechnung_aus_angebot(
        szenario["user"].id, quote_id=quote.id
    )
    _beteiligte(szenario, invoice)
    beleg_service.publish_invoice(szenario["user"].id, invoice_id=invoice.id)

    beleg_service.create_correction(
        szenario["user"].id, invoice_id=invoice.id, positions=[2]
    )
    assert BillingLink.objects.filter(
        invoice_id=invoice.id, released_at__isnull=True
    ).count() == 2
    with pytest.raises(AbrechnungError, match="bereits abgerechnet"):
        abrechnung_service.rechnung_aus_angebot(
            szenario["user"].id, quote_id=quote.id
        )


@pytest.mark.django_db
def test_vollgutschrift_auf_gebundene_rechnung_ist_verboten(szenario):
    """Die Vollgutschrift ist ein verkappter Storno — und wird abgelehnt.

    Sie hätte das genaue Gegenteil dessen bewirkt, was ihr Urheber meint: eine
    Rechnung über 0 €, deren Leistung **für immer** als abgerechnet gilt (die
    Gutschrift löst die Bindung nicht) und die nie wieder in Rechnung gestellt
    werden kann. Der zweideutige Fall wird verboten, nicht interpretiert.
    """
    order = _auftrag(szenario)
    quote = _angebot(szenario, order, [
        _pos("Rohr DN20", "10", "12.50"),
        _pos("Ventil", "1", "80.00"),
    ])
    invoice = abrechnung_service.rechnung_aus_angebot(
        szenario["user"].id, quote_id=quote.id
    )
    _beteiligte(szenario, invoice)
    beleg_service.publish_invoice(szenario["user"].id, invoice_id=invoice.id)

    with pytest.raises(ValueError) as exc:
        beleg_service.create_correction(
            szenario["user"].id, invoice_id=invoice.id, positions=[1, 2]
        )
    assert "STORNIEREN" in str(exc.value)
    assert not Invoice.objects.filter(invoice_type="GUTSCHRIFT").exists()

    # Der genannte Weg führt zum Ziel: Der Storno geht — und gibt die Leistung frei.
    beleg_service.create_cancellation(szenario["user"].id, invoice_id=invoice.id)
    assert BillingLink.objects.filter(
        invoice_id=invoice.id, released_at__isnull=True
    ).count() == 0


@pytest.mark.django_db
def test_zwei_teilgutschriften_die_zusammen_ausschoepfen_werden_gesperrt(szenario):
    """Die Grenze liegt am **Betrag**, nicht an der Positionsliste.

    Sonst erreichte man den verkappten Storno einfach über zwei Teilgutschriften.
    """
    order = _auftrag(szenario)
    quote = _angebot(szenario, order, [
        _pos("Rohr DN20", "10", "12.50"),
        _pos("Ventil", "1", "80.00"),
    ])
    invoice = abrechnung_service.rechnung_aus_angebot(
        szenario["user"].id, quote_id=quote.id
    )
    _beteiligte(szenario, invoice)
    beleg_service.publish_invoice(szenario["user"].id, invoice_id=invoice.id)

    # Erste Teilgutschrift (80,00 € netto von 205,00 €): zulässig — eine Kulanz
    # heißt nicht, dass nicht gearbeitet wurde. Die Bindung bleibt.
    beleg_service.create_correction(
        szenario["user"].id, invoice_id=invoice.id, positions=[2]
    )
    assert BillingLink.objects.filter(
        invoice_id=invoice.id, released_at__isnull=True
    ).count() == 2

    # Die zweite schöpft den Rest aus → zusammen der volle Betrag → gesperrt.
    with pytest.raises(ValueError) as exc:
        beleg_service.create_correction(
            szenario["user"].id, invoice_id=invoice.id, positions=[1]
        )
    assert "STORNIEREN" in str(exc.value)
    assert Invoice.objects.filter(invoice_type="GUTSCHRIFT").count() == 1


# ---------------------------------------------------------------------------
# Regie: Bericht + Zeiten
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_regie_rechnet_bericht_und_zeiten(szenario, fake_storage):
    """Der Regieweg: Berichtsposition (Server-Preis) + Stunden je Lohngruppe."""
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG", billing_mode="REGIE")
    artikel = _artikel(szenario, "AB-1", vk="15.00")
    _bericht(szenario, order, [{
        "line_type": "MATERIAL", "description": "Kupferrohr 18",
        "quantity": "12", "unit": "m", "source_article_id": str(artikel.id),
    }])
    job = _job(szenario, order)
    m1 = _monteur(szenario, "Anton", stundensatz="65.00", kostensatz="42.00")
    m2 = _monteur(szenario, "Berta", stundensatz="65.00", kostensatz="42.00")
    # Anton: 2 h + 1 h 30, Berta: 3 h — Anton und Berta haben VERSCHIEDENE
    # Lohngruppen, es entstehen also zwei Sammelpositionen.
    _zeit(szenario, m1, job, von=T0, bis=T0 + timedelta(hours=2))
    _zeit(szenario, m1, job, von=T0 + timedelta(hours=3),
          bis=T0 + timedelta(hours=4, minutes=30))
    _zeit(szenario, m2, job, von=T0, bis=T0 + timedelta(hours=3))
    _kg(szenario, order)

    invoice = abrechnung_service.rechnung_aus_auftrag(
        szenario["user"].id, work_order_id=order.id, tax_code="DE_19"
    )
    lines = list(invoice.lines.all().order_by("position_number"))
    assert len(lines) == 3
    material = lines[0]
    assert material.quantity == Decimal("12.000")
    assert material.unit_price == Decimal("15.00")   # aus vk_vorschlag, nicht geraten
    assert material.net_amount == Decimal("180.00")

    zeit = {l.description: l for l in lines[1:]}
    assert zeit["Arbeitszeit Lohngruppe Anton"].quantity == Decimal("3.500")
    assert zeit["Arbeitszeit Lohngruppe Anton"].unit_price == Decimal("65.00")
    assert zeit["Arbeitszeit Lohngruppe Anton"].unit_cost == Decimal("42.00")
    assert zeit["Arbeitszeit Lohngruppe Berta"].quantity == Decimal("3.000")
    # Je ZEITBUCHUNG eine Bindung (nicht je Sammelposition) — sonst ließe sich
    # eine einzelne Stunde später doch noch ein zweites Mal abrechnen.
    assert BillingLink.objects.filter(
        invoice_id=invoice.id, source_kind="ZEITBUCHUNG"
    ).count() == 3
    assert BillingLink.objects.filter(
        invoice_id=invoice.id, source_kind="BERICHTSPOSITION"
    ).count() == 1


@pytest.mark.django_db
def test_gleiche_lohngruppe_wird_zu_einer_position(szenario, fake_storage):
    """Zwei Mitarbeiter derselben Lohngruppe → EINE Sammelposition.

    Und: **erst summieren, dann in Stunden umrechnen.** 20 min sind 0,333… h;
    würde je Buchung gerundet, summierten sich die Fehler.
    """
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG", billing_mode="REGIE")
    job = _job(szenario, order)
    m1 = _monteur(szenario, "Anton", stundensatz="65.00")
    m2 = _monteur(szenario, "Berta", mit_lohngruppe=False)
    # Berta bekommt Antons Lohngruppe (über ihren Vertrag → neuer Vertrag).
    mitarbeiter_service.create_contract(
        szenario["user"].id, employee_id=m2["employee"].id, valid_from=date(2026, 6, 1),
        hours={f"hours_{d}": 8 for d in ("monday", "tuesday", "wednesday",
                                         "thursday", "friday")},
        vacation_days_per_year=30, wage_group_id=m1["wage_group"].id,
    )
    # 3 × 20 Minuten = 1 h exakt. Je Buchung gerundet (0,333) ergäbe 0,999 h.
    _zeit(szenario, m1, job, von=T0, bis=T0 + timedelta(minutes=20))
    _zeit(szenario, m1, job, von=T0 + timedelta(hours=1),
          bis=T0 + timedelta(hours=1, minutes=20))
    _zeit(szenario, m2, job, von=T0, bis=T0 + timedelta(minutes=20))
    _kg(szenario, order)

    invoice = abrechnung_service.rechnung_aus_auftrag(
        szenario["user"].id, work_order_id=order.id, tax_code="DE_19",
        mit_berichten=False,
    )
    lines = list(invoice.lines.all())
    assert len(lines) == 1
    assert lines[0].quantity == Decimal("1.000")     # nicht 0.999
    assert lines[0].net_amount == Decimal("65.00")
    assert BillingLink.objects.filter(invoice_id=invoice.id).count() == 3


@pytest.mark.django_db
def test_entwurfsberichte_fliessen_nicht_ein_und_werden_benannt(szenario, fake_storage):
    """Ein nicht abgenommener Nachweis ist keine Abrechnungsgrundlage —
    aber er wird **benannt**, nicht verschwiegen."""
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG", billing_mode="REGIE")
    artikel = _artikel(szenario, "AB-1", vk="15.00")
    _bericht(szenario, order, [{
        "line_type": "MATERIAL", "description": "Kupferrohr 18", "quantity": "5",
        "unit": "m", "source_article_id": str(artikel.id),
    }], signieren=True)
    _bericht(szenario, order, [{
        "line_type": "MATERIAL", "description": "Kupferrohr 18", "quantity": "99",
        "unit": "m", "source_article_id": str(artikel.id),
    }], signieren=False, datum="2026-07-07")
    _kg(szenario, order)

    offen = abrechnung_service.offene_abrechnung(order.id)
    assert [p["quantity"] for p in offen["berichtspositionen"]] == [Decimal("5.000")]
    assert len(offen["nicht_unterzeichnete_berichte"]) == 1
    assert offen["nicht_unterzeichnete_berichte"][0]["status"] == "ENTWURF"

    invoice = abrechnung_service.rechnung_aus_auftrag(
        szenario["user"].id, work_order_id=order.id, tax_code="DE_19",
    )
    assert [l.quantity for l in invoice.lines.all()] == [Decimal("5.000")]


@pytest.mark.django_db
def test_regie_laeuft_nicht_zweimal(szenario, fake_storage):
    """Der zweite Lauf findet nichts mehr — nicht: rechnet dasselbe nochmal ab."""
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG", billing_mode="REGIE")
    artikel = _artikel(szenario, "AB-1", vk="15.00")
    _bericht(szenario, order, [{
        "line_type": "MATERIAL", "description": "Rohr", "quantity": "5",
        "unit": "m", "source_article_id": str(artikel.id),
    }])
    _kg(szenario, order)
    abrechnung_service.rechnung_aus_auftrag(
        szenario["user"].id, work_order_id=order.id, tax_code="DE_19"
    )
    with pytest.raises(AbrechnungError, match="nichts abzurechnen"):
        abrechnung_service.rechnung_aus_auftrag(
            szenario["user"].id, work_order_id=order.id, tax_code="DE_19"
        )
    assert Invoice.objects.filter(invoice_type="RECHNUNG").count() == 1


# ---------------------------------------------------------------------------
# PAUSCHAL: Zeiten und Berichtspositionen werden NICHT zusätzlich fakturiert
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_pauschal_faktura_aus_bericht_ist_gesperrt(szenario, fake_storage):
    """Das Angebot enthält die Leistung bereits — beides zu fakturieren hieße,
    doppelt zu kassieren."""
    order = _auftrag(szenario)          # Default PAUSCHAL
    assert order.billing_mode == "PAUSCHAL"
    artikel = _artikel(szenario, "AB-1", vk="15.00")
    _bericht(szenario, order, [{
        "line_type": "MATERIAL", "description": "Rohr", "quantity": "5",
        "unit": "m", "source_article_id": str(artikel.id),
    }])
    with pytest.raises(AbrechnungError) as exc:
        abrechnung_service.rechnung_aus_auftrag(
            szenario["user"].id, work_order_id=order.id, tax_code="DE_19"
        )
    assert "PAUSCHAL" in str(exc.value)
    assert Invoice.objects.count() == 0

    # Aber sichtbar bleibt der Nachweis — als Nachweis, nicht als Rechnungsposten.
    offen = abrechnung_service.offene_abrechnung(order.id)
    assert offen["abrechenbar"] is False
    assert len(offen["berichtspositionen"]) == 1
    assert "NICHT zusätzlich fakturiert" in offen["hinweis"]


# ---------------------------------------------------------------------------
# Fehlender Preis: 422 mit Klärungsliste — niemals 0 €, niemals weglassen
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_artikel_ohne_ek_ergibt_klaerung_statt_null_euro(szenario, fake_storage):
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG", billing_mode="REGIE")
    artikel = _artikel(szenario, "AB-OHNE", vk=None, beschreibung="Rohr ohne EK")
    _bericht(szenario, order, [{
        "line_type": "MATERIAL", "description": "Rohr ohne EK", "quantity": "5",
        "unit": "m", "source_article_id": str(artikel.id),
    }])
    _kg(szenario, order)

    with pytest.raises(PreisUnbekannt) as exc:
        abrechnung_service.rechnung_aus_auftrag(
            szenario["user"].id, work_order_id=order.id, tax_code="DE_19"
        )
    positionen = exc.value.positionen
    assert len(positionen) == 1
    p = positionen[0]
    assert p["quelle_art"] == "BERICHTSPOSITION"
    assert p["menge"] == Decimal("5.000")
    assert p["grund"] in ("EK_FEHLT", "KEINE_VK_REGEL")
    assert "Rohr ohne EK" in p["bezeichnung"]
    # Kein Beleg entstanden — und ganz sicher keine 0-€-Position.
    assert Invoice.objects.count() == 0
    assert InvoiceLine.objects.count() == 0


@pytest.mark.django_db
def test_offene_abrechnung_zeigt_den_fehlenden_preis_sofort(szenario, fake_storage):
    """Der Hebel: geklärt wird, BEVOR jemand fakturieren will."""
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG", billing_mode="REGIE")
    ohne = _artikel(szenario, "AB-OHNE", vk=None, beschreibung="Rohr ohne EK")
    mit = _artikel(szenario, "AB-MIT", vk="15.00", beschreibung="Rohr mit VK")
    _bericht(szenario, order, [
        {"line_type": "MATERIAL", "description": "Rohr ohne EK", "quantity": "5",
         "unit": "m", "source_article_id": str(ohne.id)},
        {"line_type": "MATERIAL", "description": "Rohr mit VK", "quantity": "2",
         "unit": "m", "source_article_id": str(mit.id)},
    ])
    offen = abrechnung_service.offene_abrechnung(order.id)
    status = {p["description"]: p for p in offen["berichtspositionen"]}
    assert status["Rohr ohne EK"]["preis_status"] == "UNBEKANNT"
    assert status["Rohr ohne EK"]["einzelpreis"] is None      # unbekannt, NICHT 0
    assert status["Rohr ohne EK"]["grund_text"]
    assert status["Rohr mit VK"]["preis_status"] == "BEKANNT"
    assert status["Rohr mit VK"]["einzelpreis"] == Decimal("15.00")


@pytest.mark.django_db
def test_genannter_preis_macht_die_rechnung_moeglich(szenario, fake_storage):
    """Der Ausweg: Ein Mensch nennt den Einzelpreis — der Server rechnet die Summe."""
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG", billing_mode="REGIE")
    artikel = _artikel(szenario, "AB-OHNE", vk=None)
    report = _bericht(szenario, order, [{
        "line_type": "MATERIAL", "description": "Rohr ohne EK", "quantity": "5",
        "unit": "m", "source_article_id": str(artikel.id),
    }])
    _kg(szenario, order)
    line = SiteReportLine.objects.get(site_report_id=report.id)

    invoice = abrechnung_service.rechnung_aus_auftrag(
        szenario["user"].id, work_order_id=order.id, tax_code="DE_19",
        preise={str(line.id): "19.90"},
    )
    pos = invoice.lines.get()
    assert pos.unit_price == Decimal("19.90")
    assert pos.net_amount == Decimal("99.50")        # 5 × 19,90 — vom SERVER
    assert invoice.net_total == Decimal("99.50")
    assert invoice.tax_total == Decimal("18.91")     # 19 % — vom SERVER


@pytest.mark.django_db
def test_genannter_preis_fuer_bekannte_position_wird_abgelehnt(szenario, fake_storage):
    """Sonst ließe sich die eine Rechenstelle über „Preis nennen" unterlaufen."""
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG", billing_mode="REGIE")
    artikel = _artikel(szenario, "AB-MIT", vk="15.00")
    report = _bericht(szenario, order, [{
        "line_type": "MATERIAL", "description": "Rohr", "quantity": "5",
        "unit": "m", "source_article_id": str(artikel.id),
    }])
    _kg(szenario, order)
    line = SiteReportLine.objects.get(site_report_id=report.id)

    with pytest.raises(AbrechnungError) as exc:
        abrechnung_service.rechnung_aus_auftrag(
            szenario["user"].id, work_order_id=order.id, tax_code="DE_19",
            preise={str(line.id): "1.00"},
        )
    assert "obwohl der Server einen Preis kennt" in str(exc.value)
    assert Invoice.objects.count() == 0


@pytest.mark.django_db
def test_genannter_preis_null_wird_abgelehnt(szenario, fake_storage):
    """Ein leeres Eingabefeld darf nicht stillschweigend zur Gratisleistung werden."""
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG", billing_mode="REGIE")
    artikel = _artikel(szenario, "AB-OHNE", vk=None)
    report = _bericht(szenario, order, [{
        "line_type": "MATERIAL", "description": "Rohr", "quantity": "5",
        "unit": "m", "source_article_id": str(artikel.id),
    }])
    _kg(szenario, order)
    line = SiteReportLine.objects.get(site_report_id=report.id)
    with pytest.raises(AbrechnungError, match="größer als 0"):
        abrechnung_service.rechnung_aus_auftrag(
            szenario["user"].id, work_order_id=order.id, tax_code="DE_19",
            preise={str(line.id): "0"},
        )


@pytest.mark.django_db
def test_mitarbeiter_ohne_lohngruppe_ergibt_klaerung(szenario, fake_storage):
    """Kein geratener Stundensatz — Klärung statt 0 €."""
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG", billing_mode="REGIE")
    job = _job(szenario, order)
    m = _monteur(szenario, "Ohne", mit_lohngruppe=False)
    _zeit(szenario, m, job, von=T0, bis=T0 + timedelta(hours=2))
    _kg(szenario, order)

    with pytest.raises(PreisUnbekannt) as exc:
        abrechnung_service.rechnung_aus_auftrag(
            szenario["user"].id, work_order_id=order.id, tax_code="DE_19"
        )
    p = exc.value.positionen[0]
    assert p["quelle_art"] == "ZEITGRUPPE"
    assert p["quelle_id"] == m["app_user"].id
    assert p["grund"] == "LOHNGRUPPE_FEHLT"
    assert p["menge"] == Decimal("2.000")

    # Und mit genanntem Satz geht es durch.
    invoice = abrechnung_service.rechnung_aus_auftrag(
        szenario["user"].id, work_order_id=order.id, tax_code="DE_19",
        preise={str(m["app_user"].id): "70.00"},
    )
    pos = invoice.lines.get()
    assert pos.quantity == Decimal("2.000")
    assert pos.unit_price == Decimal("70.00")
    assert pos.net_amount == Decimal("140.00")


@pytest.mark.django_db
def test_freitextposition_ohne_herkunft_ergibt_klaerung(szenario, fake_storage):
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG", billing_mode="REGIE")
    _bericht(szenario, order, [{
        "line_type": "PAUSCHALE", "description": "Sonderarbeit XY",
        "quantity": "1", "unit": "psch",
    }])
    _kg(szenario, order)
    with pytest.raises(PreisUnbekannt) as exc:
        abrechnung_service.rechnung_aus_auftrag(
            szenario["user"].id, work_order_id=order.id, tax_code="DE_19"
        )
    assert exc.value.positionen[0]["grund"] == "KEINE_HERKUNFT"


# ---------------------------------------------------------------------------
# DIE STILLE NULL: 0,00 € ist kein Preis, sondern eine Lücke
#
# Der teuerste stille Fehler, den dieses System machen kann: Die Position landet
# mit 0,00 € auf der Rechnung, die Vorschau nickt sie als BEKANNT ab, und der
# Klärungsweg ist zu („der Server kennt ja einen Preis"). Die Rechnung sieht
# plausibel aus und ist um den vollen Positionsbetrag zu niedrig.
#
# Die DB lässt die Null überall zu (die CHECKs sagen `>= 0`): Einkaufspreis,
# Festpreis, Stundensatz. Also zieht der Abrechnungslauf die Grenze — und zwar
# an JEDEM der drei Wege.
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ek_null_ergibt_klaerung_statt_null_euro_position(szenario, fake_storage):
    """EK 0,00 € (Importfehler) + VK-Gruppe → 0,00 € VK. Das ist KEIN Preis."""
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG", billing_mode="REGIE")
    artikel = _artikel_mit_vk_gruppe(szenario, "EK-NULL", "0.00")
    report = _bericht(szenario, order, [{
        "line_type": "MATERIAL", "description": "Rohr aus dem Import",
        "quantity": "12", "unit": "m", "source_article_id": str(artikel.id),
    }])
    _kg(szenario, order)

    # 1) Die Vorschau meldet UNBEKANNT — nicht „BEKANNT: 0,00 €".
    offen = abrechnung_service.offene_abrechnung(order.id)
    pos = offen["berichtspositionen"][0]
    assert pos["preis_status"] == "UNBEKANNT"
    assert pos["einzelpreis"] is None
    assert pos["grund"] == "VK_NULL"
    assert "0,00" in pos["grund_text"]

    # 2) Der Lauf scheitert mit Klärung statt einer 0-€-Position.
    with pytest.raises(PreisUnbekannt) as exc:
        abrechnung_service.rechnung_aus_auftrag(
            szenario["user"].id, work_order_id=order.id, tax_code="DE_19"
        )
    assert exc.value.positionen[0]["grund"] == "VK_NULL"
    assert Invoice.objects.count() == 0
    assert InvoiceLine.objects.count() == 0

    # 3) Und der Klärungsweg GREIFT: Der genannte Preis wird angenommen (früher
    #    lehnte ihn `_genannter_preis` ab — „der Server kennt einen Preis (0,00)").
    line = SiteReportLine.objects.get(site_report_id=report.id)
    invoice = abrechnung_service.rechnung_aus_auftrag(
        szenario["user"].id, work_order_id=order.id, tax_code="DE_19",
        preise={str(line.id): "19.90"},
    )
    rechnungsposition = invoice.lines.get()
    assert rechnungsposition.unit_price == Decimal("19.90")
    assert rechnungsposition.net_amount == Decimal("238.80")   # 12 × 19,90
    assert invoice.net_total == Decimal("238.80")


@pytest.mark.django_db
def test_festpreis_null_ergibt_klaerung_statt_null_euro_position(szenario, fake_storage):
    """`article_sale_price.fixed_price` erlaubt `>= 0` — ein Festpreis 0,00 ist ein
    Tippfehler, keine Gratisleistung."""
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG", billing_mode="REGIE")
    artikel = _artikel(szenario, "FP-NULL", vk="0.00", beschreibung="Ventil")
    report = _bericht(szenario, order, [{
        "line_type": "MATERIAL", "description": "Ventil", "quantity": "3",
        "unit": "m", "source_article_id": str(artikel.id),
    }])
    _kg(szenario, order)

    with pytest.raises(PreisUnbekannt) as exc:
        abrechnung_service.rechnung_aus_auftrag(
            szenario["user"].id, work_order_id=order.id, tax_code="DE_19"
        )
    assert exc.value.positionen[0]["grund"] == "VK_NULL"

    line = SiteReportLine.objects.get(site_report_id=report.id)
    invoice = abrechnung_service.rechnung_aus_auftrag(
        szenario["user"].id, work_order_id=order.id, tax_code="DE_19",
        preise={str(line.id): "80.00"},
    )
    assert invoice.lines.get().net_amount == Decimal("240.00")


@pytest.mark.django_db
def test_lohngruppe_mit_satz_null_ergibt_klaerung(szenario, fake_storage):
    """`wage_group.hourly_rate` erlaubt `>= 0` — 0,00 €/h ist kein Stundensatz.

    Die 0-€-Arbeitszeitposition ist der heimtückischste Fall: Sie sieht auf der
    Rechnung völlig unauffällig aus und verschenkt die gesamte Arbeitszeit.
    """
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG", billing_mode="REGIE")
    job = _job(szenario, order)
    m = _monteur(szenario, "Null", stundensatz="0.00", kostensatz="42.00")
    _zeit(szenario, m, job, von=T0, bis=T0 + timedelta(hours=2))
    _kg(szenario, order)

    # Die Vorschau sagt es sofort.
    offen = abrechnung_service.offene_abrechnung(order.id)
    gruppe = offen["zeitgruppen"][0]
    assert gruppe["preis_status"] == "UNBEKANNT"
    assert gruppe["einzelpreis"] is None
    assert gruppe["grund"] == "LOHNSATZ_NULL"
    # Und schlägt die 0-Lohngruppe nicht als Satz vor.
    assert all(v["betrag"] > 0 for v in gruppe["vorschlaege"])

    with pytest.raises(PreisUnbekannt) as exc:
        abrechnung_service.rechnung_aus_auftrag(
            szenario["user"].id, work_order_id=order.id, tax_code="DE_19"
        )
    p = exc.value.positionen[0]
    assert p["grund"] == "LOHNSATZ_NULL"
    assert p["quelle_id"] == m["wage_group"].id
    assert Invoice.objects.count() == 0

    # Der genannte Satz führt zur richtigen Rechnung.
    invoice = abrechnung_service.rechnung_aus_auftrag(
        szenario["user"].id, work_order_id=order.id, tax_code="DE_19",
        preise={str(m["wage_group"].id): "70.00"},
    )
    pos = invoice.lines.get()
    assert pos.quantity == Decimal("2.000")
    assert pos.unit_price == Decimal("70.00")
    assert pos.net_amount == Decimal("140.00")


# ---------------------------------------------------------------------------
# Das Tor ist beidseitig: REGIE sperrt die Angebotskopie
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_regie_auftrag_laesst_sich_nicht_aus_dem_angebot_fakturieren(szenario):
    """Sonst stünde dieselbe Leistung auf zwei Rechnungen — jede sauber gebunden.

    Die Doppelabrechnungssperre finge das NICHT ab: Der Angebotsweg bindet
    Angebotspositionen, der Regieweg Berichtspositionen und Zeitbuchungen. Zwei
    verschiedene Quellen, zwei gültige Bindungen, doppelt kassiert.
    """
    order = _auftrag(szenario, billing_mode="REGIE")
    quote = _angebot(szenario, order, [_pos("Rohr DN20", "10", "12.50")])
    with pytest.raises(AbrechnungError) as exc:
        abrechnung_service.rechnung_aus_angebot(
            szenario["user"].id, quote_id=quote.id
        )
    assert "REGIE" in str(exc.value)
    assert Invoice.objects.count() == 0

    # Auf PAUSCHAL umgestellt geht derselbe Lauf durch.
    abrechnung_service.set_billing_mode(
        szenario["user"].id, work_order_id=order.id, billing_mode="PAUSCHAL"
    )
    invoice = abrechnung_service.rechnung_aus_angebot(
        szenario["user"].id, quote_id=quote.id
    )
    assert invoice.net_total == Decimal("125.00")


# ---------------------------------------------------------------------------
# Der gebundene Entwurf bleibt bearbeitbar (Migration 0088)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_neue_ungebundene_zeile_am_gebundenen_entwurf_ist_erlaubt(szenario):
    """Anfahrtspauschale, Rabattzeile, Nachtrag: Eine NEUE Zeile trägt keine
    Bindung und gefährdet die Sperre nicht.

    Sperrte der Schutz auch das INSERT, bliebe als einziger Ausweg die Notbremse
    `bindungen_loesen` — die alle gebundenen Positionen verwirft. Bei 30
    Berichtspositionen hieße das: von vorn. Eine Notbremse, die zum Normalweg
    wird, ist keine mehr.
    """
    order = _auftrag(szenario)
    quote = _angebot(szenario, order, [_pos("Rohr", "10", "12.50")])
    invoice = abrechnung_service.rechnung_aus_angebot(
        szenario["user"].id, quote_id=quote.id
    )
    with business_transaction(szenario["user"].id):
        InvoiceLine.objects.create(
            id=uuid.uuid4(), invoice_id=invoice.id, position_number=2,
            line_type="PAUSCHALE", line_kind="NORMAL",
            description="Anfahrtspauschale", quantity=Decimal("1.000"),
            unit="psch", unit_price=Decimal("45.00"),
            net_amount=Decimal("45.00"), tax_code_id="DE_19",
            tax_rate_percent=Decimal("19.00"),
        )
    assert invoice.lines.count() == 2
    # Die Bindung der ersten Zeile ist unberührt geblieben.
    assert BillingLink.objects.filter(
        invoice_id=invoice.id, released_at__isnull=True
    ).count() == 1


@pytest.mark.django_db
def test_gebundene_zeile_laesst_sich_nicht_aendern_oder_loeschen(szenario):
    """Der Schutz greift dort, wo er etwas schützt: an der GEBUNDENEN Zeile.

    Sie ist der Beleg genau dieser Angebots-/Berichtsposition; sie umzuschreiben
    oder zu löschen risse die Doppelabrechnungssperre auf.
    """
    order = _auftrag(szenario)
    quote = _angebot(szenario, order, [_pos("Rohr", "10", "12.50")])
    invoice = abrechnung_service.rechnung_aus_angebot(
        szenario["user"].id, quote_id=quote.id
    )
    line = invoice.lines.get()

    with pytest.raises(Exception, match="gebunden"):
        with business_transaction(szenario["user"].id):
            InvoiceLine.objects.filter(id=line.id).update(
                unit_price=Decimal("1.00")
            )
    with pytest.raises(Exception, match="gebunden"):
        with business_transaction(szenario["user"].id):
            InvoiceLine.objects.filter(id=line.id).delete()

    line.refresh_from_db()
    assert line.unit_price == Decimal("12.50")
    assert invoice.lines.count() == 1


# ---------------------------------------------------------------------------
# Tore, die scharf bleiben
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_b08_rechnung_vor_kaufmaennisch_geprueft_wird_abgelehnt(szenario, fake_storage):
    """B-08 bleibt scharf: Der Entwurf entsteht, die Veröffentlichung scheitert."""
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG", billing_mode="REGIE")
    artikel = _artikel(szenario, "AB-1", vk="15.00")
    _bericht(szenario, order, [{
        "line_type": "MATERIAL", "description": "Rohr", "quantity": "5",
        "unit": "m", "source_article_id": str(artikel.id),
    }])
    invoice = abrechnung_service.rechnung_aus_auftrag(
        szenario["user"].id, work_order_id=order.id, tax_code="DE_19"
    )
    _beteiligte(szenario, invoice)
    assert WorkOrder.objects.get(id=order.id).status == "IN_AUSFUEHRUNG"

    with pytest.raises(ValueError):
        beleg_service.publish_invoice(szenario["user"].id, invoice_id=invoice.id)
    invoice.refresh_from_db()
    assert invoice.status == "ENTWURF"


@pytest.mark.django_db
def test_editor_kann_gebundene_positionen_nicht_ersetzen(szenario):
    """Sonst verschwände die Doppelabrechnungssperre mit einem Klick auf
    „Speichern" — still, und niemand merkte es."""
    order = _auftrag(szenario)
    quote = _angebot(szenario, order, [_pos("Rohr", "10", "12.50")])
    invoice = abrechnung_service.rechnung_aus_angebot(
        szenario["user"].id, quote_id=quote.id
    )
    with pytest.raises(ValueError) as exc:
        beleg_service.update_invoice(
            szenario["user"].id, invoice_id=invoice.id,
            lines=[_pos("Ganz was anderes", "1", "1.00")],
        )
    assert "gebunden" in str(exc.value)
    invoice.refresh_from_db()
    assert invoice.net_total == Decimal("125.00")


@pytest.mark.django_db
def test_bindungen_loesen_gibt_die_quelle_frei_und_raeumt_den_entwurf(szenario):
    """Die Notbremse: Die Quelle wird frei, WEIL der Entwurf sie nicht mehr
    berechnet. Beides in einer Transaktion — die Sperre bleibt lückenlos."""
    order = _auftrag(szenario)
    quote = _angebot(szenario, order, [_pos("Rohr", "10", "12.50")])
    invoice = abrechnung_service.rechnung_aus_angebot(
        szenario["user"].id, quote_id=quote.id
    )
    abrechnung_service.bindungen_loesen(
        szenario["user"].id, invoice_id=invoice.id, reason="Falscher Auftrag erwischt",
    )
    invoice.refresh_from_db()
    # Der Entwurf stellt die Leistung nicht mehr in Rechnung …
    assert invoice.lines.count() == 0
    assert invoice.net_total == Decimal("0.00")
    link = BillingLink.objects.get(quote_line_id=quote.lines.first().id)
    assert link.released_at is not None
    assert link.invoice_line_id is None
    # … und genau deshalb ist die Quelle wieder frei.
    neu = abrechnung_service.rechnung_aus_angebot(
        szenario["user"].id, quote_id=quote.id
    )
    assert neu.net_total == Decimal("125.00")


@pytest.mark.django_db
def test_bindungen_loesen_verlangt_eine_begruendung(szenario):
    order = _auftrag(szenario)
    quote = _angebot(szenario, order, [_pos("Rohr", "10", "12.50")])
    invoice = abrechnung_service.rechnung_aus_angebot(
        szenario["user"].id, quote_id=quote.id
    )
    with pytest.raises(AbrechnungError, match="begründungspflichtig"):
        abrechnung_service.bindungen_loesen(
            szenario["user"].id, invoice_id=invoice.id, reason="  "
        )


@pytest.mark.django_db
def test_veroeffentlichte_rechnung_wird_nicht_entbunden_sondern_storniert(szenario):
    order = _auftrag(szenario)
    quote = _angebot(szenario, order, [_pos("Rohr", "10", "12.50")])
    invoice = abrechnung_service.rechnung_aus_angebot(
        szenario["user"].id, quote_id=quote.id
    )
    _beteiligte(szenario, invoice)
    beleg_service.publish_invoice(szenario["user"].id, invoice_id=invoice.id)
    with pytest.raises(AbrechnungError, match="STORNO"):
        abrechnung_service.bindungen_loesen(
            szenario["user"].id, invoice_id=invoice.id, reason="Doch nicht"
        )


# ---------------------------------------------------------------------------
# Der Abrechnungslauf fasst den Artikelstamm NICHT an
# ---------------------------------------------------------------------------

def test_abrechnung_schreibt_niemals_in_den_artikelstamm():
    """Statisch: kein Schreibpfad von der Abrechnung nach `pricing.article`.

    Der einzige Weg vom Beleg in den Stamm ist der eigene, mit `pricing/AENDERN`
    getorte Vorgang `POST /pricing/articles/{id}/stammdaten-uebernehmen`. Ein
    genannter Einzelpreis gilt für DIESEN Beleg — sonst schriebe ein
    Abrechnungslauf den Stammdatensatz um, den alle anderen Belege mitbenutzen.
    """
    from db_core.services import abrechnung

    quelle = inspect.getsource(abrechnung)
    for verboten in (
        "Article.objects.create", "Article.objects.update", "article.save",
        "ArticleSalePrice", "WageGroup.objects.create", "wage_group.save",
    ):
        assert verboten not in quelle, (
            f"abrechnung.py schreibt in den Stamm ('{verboten}'). Ein genannter "
            "Preis gilt für diesen Beleg, nicht für den Artikelstamm."
        )


@pytest.mark.django_db
def test_genannter_preis_aendert_den_artikel_nicht(szenario, fake_storage):
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG", billing_mode="REGIE")
    artikel = _artikel(szenario, "AB-OHNE", vk=None)
    report = _bericht(szenario, order, [{
        "line_type": "MATERIAL", "description": "Rohr", "quantity": "5",
        "unit": "m", "source_article_id": str(artikel.id),
    }])
    _kg(szenario, order)
    line = SiteReportLine.objects.get(site_report_id=report.id)
    abrechnung_service.rechnung_aus_auftrag(
        szenario["user"].id, work_order_id=order.id, tax_code="DE_19",
        preise={str(line.id): "19.90"},
    )
    from db_core.models import ArticleSalePrice

    assert not ArticleSalePrice.objects.filter(article_id=artikel.id).exists()
    artikel.refresh_from_db()
    assert artikel.list_price is None


# ---------------------------------------------------------------------------
# Moduswechsel nach der Abrechnung — Review-Befund H-2
# ---------------------------------------------------------------------------
# Der Wechsel PAUSCHAL ⇄ REGIE ist das einzige Tor, an dem dieselbe Leistung
# zweimal in Rechnung gehen kann: Die beiden Wege binden **disjunkte** Quellen
# (Angebotsposition vs. Berichtsposition/Zeitbuchung), die drei partiellen
# UNIQUE-Indizes auf `billing_link` können das per Konstruktion nicht sehen.

def _pauschal_abgerechnet(szenario, *, publizieren=True):
    """Auftrag PAUSCHAL, Angebot, Rechnung aus dem Angebot — optional publiziert."""
    order = _auftrag(szenario)
    quote = _angebot(szenario, order, [
        _pos("Rohr DN20", "10", "12.50"),
        _pos("Montage", "1", "25.00", typ="ARBEITSZEIT", unit="h"),
    ])
    invoice = abrechnung_service.rechnung_aus_angebot(
        szenario["user"].id, quote_id=quote.id
    )
    if publizieren:
        _beteiligte(szenario, invoice)
        beleg_service.publish_invoice(szenario["user"].id, invoice_id=invoice.id)
        invoice.refresh_from_db()
    return order, quote, invoice


def _modus_am_service_vorbei(szenario, order, modus):
    """Setzt `billing_mode` direkt über das ORM — am Servicetor vorbei.

    Damit lässt sich prüfen, dass die **zweite** Sperre (in den beiden
    Rechnungswegen selbst) auch dann hält, wenn der Modus auf irgendeinem anderen
    Weg umgestellt wurde. Die Sperre in `set_billing_mode` ist die erste Instanz,
    nicht die einzige.
    """
    with business_transaction(szenario["user"].id):
        WorkOrder.objects.filter(id=order.id).update(billing_mode=modus)
    order.refresh_from_db()
    return order


@pytest.mark.django_db
def test_moduswechsel_nach_veroeffentlichter_rechnung_ist_verboten(szenario):
    """Der Repro-Fall: 178,50 € auf zwei veröffentlichten Rechnungen.

    PAUSCHAL → Rechnung aus Angebot → veröffentlicht → Wechsel auf REGIE →
    Rechnung aus Bericht/Zeiten → veröffentlicht. Der Statusvergleich auf
    ABGERECHNET greift nicht (nichts im System setzt diesen Status je), also
    hängt die Sperre an dem, was wirklich passiert ist: aktive Bindungen bzw.
    wirksame veröffentlichte Rechnungen.
    """
    order, _quote, invoice = _pauschal_abgerechnet(szenario)
    with pytest.raises(AbrechnungError) as exc:
        abrechnung_service.set_billing_mode(
            szenario["user"].id, work_order_id=order.id, billing_mode="REGIE"
        )
    assert "nicht mehr ändern" in str(exc.value)
    assert invoice.invoice_number in str(exc.value)
    order.refresh_from_db()
    assert order.billing_mode == "PAUSCHAL"


@pytest.mark.django_db
def test_moduswechsel_bei_gebundenem_entwurf_ist_verboten(szenario):
    """Auch der ENTWURF bindet schon — und wäre nach dem Wechsel doppelt fakturiert."""
    order, _quote, _invoice = _pauschal_abgerechnet(szenario, publizieren=False)
    with pytest.raises(AbrechnungError) as exc:
        abrechnung_service.set_billing_mode(
            szenario["user"].id, work_order_id=order.id, billing_mode="REGIE"
        )
    assert "Entwurf" in str(exc.value)


@pytest.mark.django_db
def test_moduswechsel_nach_storno_ist_wieder_moeglich(szenario):
    """Der Storno löst die Bindungen und hebt die Rechnung auf — der Auftrag ist frei."""
    order, _quote, invoice = _pauschal_abgerechnet(szenario)
    beleg_service.create_cancellation(szenario["user"].id, invoice_id=invoice.id)
    assert BillingLink.objects.filter(
        invoice_id=invoice.id, released_at__isnull=True
    ).count() == 0

    order = abrechnung_service.set_billing_mode(
        szenario["user"].id, work_order_id=order.id, billing_mode="REGIE"
    )
    assert order.billing_mode == "REGIE"


@pytest.mark.django_db
def test_regie_nach_angebotsrechnung_scheitert_auch_bei_umgangenem_modus(
    szenario, fake_storage
):
    """Die zweite Sperre — dort, wo es wirklich zählt.

    Selbst wenn der `billing_mode` am Servicetor vorbei umgestellt wird (Altbestand,
    Datenkorrektur, künftiger Codepfad), erkennt `rechnung_aus_auftrag`, dass der
    Auftrag bereits über das **Angebot** abgerechnet ist. Der Doppelabrechnungspfad
    aus dem Repro endet hier — und nicht auf der zweiten Rechnung.
    """
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG")
    artikel = _artikel(szenario, "AB-9", vk="15.00")
    _bericht(szenario, order, [{
        "line_type": "MATERIAL", "description": "Kupferrohr 18",
        "quantity": "12", "unit": "m", "source_article_id": str(artikel.id),
    }])
    job = _job(szenario, order)
    monteur = _monteur(szenario, "Anton", stundensatz="65.00")
    _zeit(szenario, monteur, job, von=T0, bis=T0 + timedelta(hours=2))
    quote = _angebot(szenario, order, [_pos("Rohr DN20", "10", "12.50")])
    _kg(szenario, order)

    invoice = abrechnung_service.rechnung_aus_angebot(
        szenario["user"].id, quote_id=quote.id
    )
    _beteiligte(szenario, invoice)
    beleg_service.publish_invoice(szenario["user"].id, invoice_id=invoice.id)

    _modus_am_service_vorbei(szenario, order, "REGIE")
    with pytest.raises(AbrechnungError) as exc:
        abrechnung_service.rechnung_aus_auftrag(
            szenario["user"].id, work_order_id=order.id, tax_code="DE_19"
        )
    assert "bereits über das Angebot abgerechnet" in str(exc.value)
    assert Invoice.objects.filter(work_order_id=order.id).count() == 1


@pytest.mark.django_db
def test_angebotsrechnung_nach_regierechnung_scheitert_auch_bei_umgangenem_modus(
    szenario, fake_storage
):
    """Das Spiegelbild: erst Regie abgerechnet, dann PAUSCHAL — die Angebotskopie
    stellte dieselbe Leistung ein zweites Mal in Rechnung."""
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG", billing_mode="REGIE")
    job = _job(szenario, order)
    monteur = _monteur(szenario, "Anton", stundensatz="65.00")
    _zeit(szenario, monteur, job, von=T0, bis=T0 + timedelta(hours=2))
    quote = _angebot(szenario, order, [_pos("Rohr DN20", "10", "12.50")])
    _kg(szenario, order)

    invoice = abrechnung_service.rechnung_aus_auftrag(
        szenario["user"].id, work_order_id=order.id, tax_code="DE_19"
    )
    _beteiligte(szenario, invoice)
    beleg_service.publish_invoice(szenario["user"].id, invoice_id=invoice.id)

    _modus_am_service_vorbei(szenario, order, "PAUSCHAL")
    with pytest.raises(AbrechnungError) as exc:
        abrechnung_service.rechnung_aus_angebot(
            szenario["user"].id, quote_id=quote.id
        )
    assert "bereits über das Ist abgerechnet" in str(exc.value)
    assert Invoice.objects.filter(work_order_id=order.id).count() == 1
