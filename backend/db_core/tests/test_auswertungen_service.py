"""Service-Tests der Auswertungen (lesende Aggregationen) gegen die Test-DB.

Veröffentlichte Rechnungen werden über den echten publish-Pfad erzeugt (die
DEFERRED-Freigabe-Tore feuern unter der pytest-Transaktion nicht — ihre
Korrektheit prüft der Beleg-Slice; hier zählt nur die Aggregation).
"""
from datetime import date

import pytest

from db_core.services import auftrag as auftrag_service
from db_core.services import auswertungen as auswertungen_service
from db_core.services import beleg as beleg_service
from db_core.services import identity as identity_service
from db_core.services import projekt as projekt_service
from db_core.services import property as property_service


def _published_invoice_lines(app_user, obj, party, *, lines, invoice_date=None,
                             project_id=None):
    """Wie _published_invoice, aber mit frei gesetzten Positionen (inkl. unit_cost)."""
    order = _gepruefter_auftrag(app_user, obj, party)
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="RECHNUNG",
        work_order_id=order.id, invoice_date=invoice_date, project_id=project_id,
        lines=lines,
    )
    for role in ("INVOICE_DEBTOR", "INVOICE_RECIPIENT"):
        beleg_service.add_invoice_party(
            app_user.id, invoice_id=inv.id, party_id=party.id, role=role,
            is_primary=True,
        )
    return beleg_service.publish_invoice(app_user.id, invoice_id=inv.id)


def _line(desc, qty, price, *, cost=None, line_type="MATERIAL"):
    row = {"line_type": line_type, "description": desc, "quantity": qty,
           "unit": "Stk", "unit_price": str(price), "tax_code": "DE_19"}
    if cost is not None:
        row["unit_cost"] = str(cost)
    return row


def _property(app_user, name="Objekt"):
    return property_service.create_property(
        app_user.id, name=name, property_type="WEG",
        street="Weg", postal_code="10115", city="Berlin",
    )


def _party(app_user):
    return identity_service.create_person(app_user.id, first_name="W", last_name="EG")


def _gepruefter_auftrag(app_user, obj, debtor):
    order = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Auftrag"
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


def _published_invoice(app_user, obj, party, *, unit_price, quantity=1,
                       invoice_date=None, invoice_type="RECHNUNG",
                       reference_invoice_id=None):
    """Erzeugt eine veröffentlichte Rechnung (net = quantity*unit_price) über den
    vollständigen, gültigen Publish-Pfad (geprüfter Auftrag + Beteiligte)."""
    work_order_id = None
    if invoice_type not in ("GUTSCHRIFT", "STORNO"):
        work_order_id = _gepruefter_auftrag(app_user, obj, party).id
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type=invoice_type,
        work_order_id=work_order_id, invoice_date=invoice_date,
        reference_invoice_id=reference_invoice_id,
        lines=[
            {"line_type": "MATERIAL", "description": "X", "quantity": quantity,
             "unit": "Stk", "unit_price": str(unit_price), "tax_code": "DE_19"},
        ],
    )
    beleg_service.add_invoice_party(
        app_user.id, invoice_id=inv.id, party_id=party.id,
        role="INVOICE_DEBTOR", is_primary=True,
    )
    beleg_service.add_invoice_party(
        app_user.id, invoice_id=inv.id, party_id=party.id,
        role="INVOICE_RECIPIENT", is_primary=True,
    )
    return beleg_service.publish_invoice(app_user.id, invoice_id=inv.id)


@pytest.mark.django_db
def test_list_dashboards_enthaelt_umsatz(app_user):
    keys = {d["key"] for d in auswertungen_service.list_dashboards()}
    assert "umsatz-projektuebersicht" in keys


@pytest.mark.django_db
def test_umsatz_zaehlt_nur_veroeffentlichte(app_user):
    obj = _property(app_user)
    weg = _party(app_user)
    _published_invoice(app_user, obj, weg, unit_price="100.00")  # net 100
    # Entwurf (nicht veröffentlicht) — darf nicht zählen.
    beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="RECHNUNG",
        lines=[{"line_type": "MATERIAL", "description": "Y", "quantity": 1,
                "unit_price": "500.00", "tax_code": "DE_19"}],
    )
    s = auswertungen_service.umsatz_projektuebersicht_summary()
    assert s["revenue"]["net_total"] == "100.00"
    assert s["revenue"]["invoice_count"] == 1


@pytest.mark.django_db
def test_gutschrift_mindert_umsatz(app_user):
    obj = _property(app_user)
    weg = _party(app_user)
    _published_invoice(app_user, obj, weg, unit_price="100.00")  # +100
    r2 = _published_invoice(app_user, obj, weg, unit_price="30.00")  # +30 → 130
    # Vollstorno von r2 erzeugt einen Beleg mit net −30 (echte Folgebeleg-Kette).
    beleg_service.create_cancellation(app_user.id, invoice_id=r2.id)
    s = auswertungen_service.umsatz_projektuebersicht_summary()
    assert s["revenue"]["net_total"] == "100.00"  # 130 − 30
    assert s["revenue"]["invoice_count"] == 2  # Korrekturbelege zählen nicht
    assert s["revenue"]["credit_count"] == 1


@pytest.mark.django_db
def test_kunden_umsatz_je_kunde_sortiert(app_user):
    obj = _property(app_user)
    anna = identity_service.create_person(app_user.id, first_name="Anna", last_name="A")
    bodo = identity_service.create_person(app_user.id, first_name="Bodo", last_name="B")
    _published_invoice(app_user, obj, anna, unit_price="100.00")
    _published_invoice(app_user, obj, bodo, unit_price="300.00")
    s = auswertungen_service.kunden_summary()
    assert s["customer_count"] == 2
    assert s["net_total"] == "400.00"
    # Nach Netto-Umsatz absteigend → Bodo zuerst.
    assert s["customers"][0]["display_name"] == "Bodo B"
    assert s["customers"][0]["net_total"] == "300.00"
    assert s["customers"][1]["net_total"] == "100.00"


@pytest.mark.django_db
def test_kunden_storno_mindert_kundenumsatz(app_user):
    obj = _property(app_user)
    anna = identity_service.create_person(app_user.id, first_name="Anna", last_name="A")
    r = _published_invoice(app_user, obj, anna, unit_price="100.00")
    beleg_service.create_cancellation(app_user.id, invoice_id=r.id)
    s = auswertungen_service.kunden_summary()
    row = next(c for c in s["customers"] if c["display_name"] == "Anna A")
    assert row["net_total"] == "0.00"
    assert row["invoice_count"] == 1
    assert row["credit_count"] == 1


@pytest.mark.django_db
def test_projekte_nach_gewerk_und_status(app_user):
    projekt_service.create_project(app_user.id, name="P1")
    projekt_service.create_project(app_user.id, name="P2")
    s = auswertungen_service.umsatz_projektuebersicht_summary()
    assert s["projects"]["total"] == 2
    assert s["projects"]["open"] == 2
    assert s["projects"]["by_gewerk"] == [{"name": "Ohne Kategorie", "count": 2}]


@pytest.mark.django_db
def test_datumsfilter_grenzt_umsatz_ein(app_user):
    obj = _property(app_user)
    weg = _party(app_user)
    _published_invoice(app_user, obj, weg, unit_price="100.00", invoice_date=date(2026, 1, 15))
    _published_invoice(app_user, obj, weg, unit_price="200.00", invoice_date=date(2026, 6, 15))
    s = auswertungen_service.umsatz_projektuebersicht_summary(
        date_from=date(2026, 6, 1), date_to=date(2026, 6, 30)
    )
    assert s["revenue"]["net_total"] == "200.00"
    assert s["revenue"]["invoice_count"] == 1
    # Zeitstrahl enthält genau den Junimonat.
    assert s["timeline"] == [{"month": "2026-06", "net": "200.00"}]


@pytest.mark.django_db
def test_umsatzverlauf_gruppiert_nach_monat(app_user):
    obj = _property(app_user)
    weg = _party(app_user)
    _published_invoice(app_user, obj, weg, unit_price="100.00", invoice_date=date(2026, 1, 10))
    _published_invoice(app_user, obj, weg, unit_price="50.00", invoice_date=date(2026, 1, 20))
    _published_invoice(app_user, obj, weg, unit_price="80.00", invoice_date=date(2026, 2, 5))
    s = auswertungen_service.umsatz_projektuebersicht_summary()
    monate = {t["month"]: t["net"] for t in s["timeline"]}
    assert monate["2026-01"] == "150.00"
    assert monate["2026-02"] == "80.00"


# ===========================================================================
# Deckungsbeitrag / Marge
# ===========================================================================

@pytest.mark.django_db
def test_marge_grundberechnung(app_user):
    """DB = Netto - EK; Marge% = DB/Netto. Vollstaendige EK-Deckung."""
    obj = _property(app_user)
    weg = _party(app_user)
    _published_invoice_lines(
        app_user, obj, weg,
        lines=[_line("Rohr", qty=2, price="100.00", cost="60.00")],  # net 200, ek 120
    )
    s = auswertungen_service.umsatz_projektuebersicht_summary(ek_allowed=True)
    m = s["marge"]
    assert s["marge_sichtbar"] is True
    assert m["net_total"] == "200.00"
    assert m["net_mit_ek"] == "200.00"
    assert m["net_ohne_ek"] == "0.00"
    assert m["ek_total"] == "120.00"
    assert m["deckungsbeitrag"] == "80.00"
    assert m["marge_prozent"] == "40.00"
    assert m["positionen"] == 1
    assert m["positionen_ohne_ek"] == 0
    assert m["ek_vollstaendig"] is True


@pytest.mark.django_db
def test_marge_bei_fehlendem_ek_unbekannt_nicht_null(app_user):
    """Fehlt an einer Zeile der EK, ist deren Marge UNBEKANNT (nicht 0/100).

    Die Marge bezieht sich nur auf den gedeckten Netto-Anteil: 100 mit EK 40
    -> DB 60, Marge 60 % (auf 100), NICHT 30 % (200 Basis) und NICHT 100 %.
    Der ungedeckte Anteil (100, 1 Position) wird getrennt ausgewiesen.
    """
    obj = _property(app_user)
    weg = _party(app_user)
    _published_invoice_lines(
        app_user, obj, weg,
        lines=[
            _line("Mit EK", qty=1, price="100.00", cost="40.00"),   # net 100, ek 40
            _line("Ohne EK", qty=1, price="100.00"),                # net 100, kein EK
        ],
    )
    m = auswertungen_service.umsatz_projektuebersicht_summary(ek_allowed=True)["marge"]
    assert m["net_total"] == "200.00"
    assert m["net_mit_ek"] == "100.00"
    assert m["net_ohne_ek"] == "100.00"
    assert m["ek_total"] == "40.00"
    assert m["deckungsbeitrag"] == "60.00"       # auf dem gedeckten Anteil
    assert m["marge_prozent"] == "60.00"         # NICHT 30.00, NICHT 100.00
    assert m["positionen"] == 2
    assert m["positionen_ohne_ek"] == 1
    assert m["ek_vollstaendig"] is False


@pytest.mark.django_db
def test_marge_komplett_ohne_ek_ist_none(app_user):
    """Traegt KEINE Position einen EK, sind DB und Marge None (unbekannt), nie 0."""
    obj = _property(app_user)
    weg = _party(app_user)
    _published_invoice_lines(
        app_user, obj, weg,
        lines=[_line("Ohne EK", qty=1, price="100.00")],
    )
    m = auswertungen_service.umsatz_projektuebersicht_summary(ek_allowed=True)["marge"]
    assert m["net_total"] == "100.00"
    assert m["net_mit_ek"] == "0.00"
    assert m["net_ohne_ek"] == "100.00"
    assert m["deckungsbeitrag"] is None
    assert m["marge_prozent"] is None
    assert m["positionen_ohne_ek"] == 1
    assert m["ek_vollstaendig"] is False


@pytest.mark.django_db
def test_marge_ek_rundung_half_up(app_user):
    """EK je Zeile = round2(unit_cost x Menge), kaufmaennisch (ROUND_HALF_UP).

    unit_cost 0,01 x Menge 2,5 = 0,025 -> 0,03 (HALF_UP), NICHT 0,02 (HALF_EVEN).
    """
    obj = _property(app_user)
    weg = _party(app_user)
    _published_invoice_lines(
        app_user, obj, weg,
        lines=[_line("Kleinteil", qty="2.5", price="1.00", cost="0.01")],  # net 2.50
    )
    m = auswertungen_service.umsatz_projektuebersicht_summary(ek_allowed=True)["marge"]
    assert m["net_mit_ek"] == "2.50"
    assert m["ek_total"] == "0.03"          # HALF_UP von 0.025
    assert m["deckungsbeitrag"] == "2.47"


@pytest.mark.django_db
def test_marge_nur_mit_ek_recht(app_user):
    """Ohne ek_allowed bleibt der Umsatz sichtbar, die Marge aber ausgeblendet."""
    obj = _property(app_user)
    weg = _party(app_user)
    _published_invoice_lines(
        app_user, obj, weg,
        lines=[_line("Rohr", qty=1, price="100.00", cost="60.00")],
    )
    s = auswertungen_service.umsatz_projektuebersicht_summary(ek_allowed=False)
    assert s["revenue"]["net_total"] == "100.00"   # Umsatz weiter da
    assert s["marge_sichtbar"] is False
    assert s["marge"] is None
    assert s["marge_by_gewerk"] == []


@pytest.mark.django_db
def test_marge_gutschrift_bleibt_aussen_vor(app_user):
    """Korrekturbelege tragen keinen EK-Snapshot -> nicht in der Marge-Basis.

    Der Umsatz wird durch den Storno gemindert, die Marge misst aber nur die
    ausgestellte (nicht-stornierende) Rechnung.
    """
    obj = _property(app_user)
    weg = _party(app_user)
    r = _published_invoice_lines(
        app_user, obj, weg,
        lines=[_line("Rohr", qty=1, price="100.00", cost="60.00")],
    )
    beleg_service.create_cancellation(app_user.id, invoice_id=r.id)
    s = auswertungen_service.umsatz_projektuebersicht_summary(ek_allowed=True)
    assert s["revenue"]["net_total"] == "0.00"     # Umsatz durch Storno gemindert
    m = s["marge"]
    assert m["net_total"] == "100.00"              # nur die Original-Rechnung
    assert m["deckungsbeitrag"] == "40.00"
    assert m["positionen"] == 1


@pytest.mark.django_db
def test_marge_je_gewerk(app_user):
    """Marge je Gewerk (Projektkategorie); Rechnung ohne Projekt -> 'Ohne Gewerk'."""
    obj = _property(app_user)
    weg = _party(app_user)
    proj = projekt_service.create_project(app_user.id, name="P")
    _published_invoice_lines(
        app_user, obj, weg, project_id=proj.id,
        lines=[_line("A", qty=1, price="100.00", cost="70.00")],
    )
    rows = auswertungen_service.umsatz_projektuebersicht_summary(
        ek_allowed=True
    )["marge_by_gewerk"]
    ohne = next(r for r in rows if r["name"] == "Ohne Gewerk")
    assert ohne["net_total"] == "100.00"
    assert ohne["deckungsbeitrag"] == "30.00"
    assert ohne["marge_prozent"] == "30.00"


@pytest.mark.django_db
def test_artikel_marge_je_position(app_user):
    """Das Artikel-Dashboard traegt Marge je Positionstext (mit ek_allowed)."""
    obj = _property(app_user)
    weg = _party(app_user)
    _published_invoice_lines(
        app_user, obj, weg,
        lines=[_line("Dichtung DN20", qty=2, price="50.00", cost="20.00")],  # net 100, ek 40
    )
    s = auswertungen_service.artikel_summary(ek_allowed=True)
    assert s["marge_sichtbar"] is True
    top = next(a for a in s["articles"] if a["description"] == "Dichtung DN20")
    assert top["ek_total"] == "40.00"
    assert top["deckungsbeitrag"] == "60.00"
    assert top["marge_prozent"] == "60.00"
    assert top["positionen_ohne_ek"] == 0
    # Ohne Recht keine Marge-Felder.
    s2 = auswertungen_service.artikel_summary(ek_allowed=False)
    assert s2["marge_sichtbar"] is False
    assert s2["marge"] is None
    top2 = next(a for a in s2["articles"] if a["description"] == "Dichtung DN20")
    assert top2["marge_prozent"] is None


@pytest.mark.django_db
def test_projekt_realisierte_und_geplante_marge(app_user):
    """Projekte-Dashboard: realisierte Marge (Rechnung) + geplante (Angebot)."""
    obj = _property(app_user)
    weg = _party(app_user)
    proj = projekt_service.create_project(app_user.id, name="Sanierung")
    _published_invoice_lines(
        app_user, obj, weg, project_id=proj.id,
        lines=[_line("Ausfuehrung", qty=1, price="100.00", cost="70.00")],  # DB 30
    )
    # Geplante Marge aus einem versendeten Angebot (Snapshot der EK-Basis).
    quote = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="Angebot", project_id=proj.id,
        lines=[_line("Ausfuehrung", qty=1, price="120.00", cost="70.00")],  # DB 50
    )
    beleg_service.send_quote(app_user.id, quote_id=quote.id)

    s = auswertungen_service.projekte_summary(ek_allowed=True)
    assert s["marge_sichtbar"] is True
    assert s["marge"]["deckungsbeitrag"] == "30.00"          # realisiert
    assert s["geplante_marge"]["deckungsbeitrag"] == "50.00"  # geplant
    top = next(p for p in s["top_projects"] if p["name"] == "Sanierung")
    assert top["deckungsbeitrag"] == "30.00"
    assert top["marge_prozent"] == "30.00"


@pytest.mark.django_db
def test_geplante_marge_ignoriert_entwuerfe(app_user):
    """Ein Angebot im ENTWURF zaehlt nicht zur geplanten Marge (nicht verbindlich)."""
    obj = _property(app_user)
    beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="Entwurf",
        lines=[_line("X", qty=1, price="100.00", cost="60.00")],
    )
    s = auswertungen_service.projekte_summary(ek_allowed=True)
    assert s["geplante_marge"]["net_total"] == "0.00"
    assert s["geplante_marge"]["deckungsbeitrag"] is None
