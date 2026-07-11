"""Service-Tests des DATEV-EXTF-Exports gegen die echte Test-DB.

Prüft die Buchungslogik (je Steuergruppe ein Satz, Debitor an Erlöskonto,
Brutto + Automatik, Soll/Haben-Umkehr bei Storno), die cent-genaue Reconciliation
mit `gross_total`, die EXTF-Struktur (Kopf-/Spaltenzeile) und die cp1252-Kodierung
sowie die Vorbedingungen (Konfiguration, Zeitraum).
"""
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import datev as datev_service
from db_core.services import firma as firma_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service

_HEUTE = date.today()


def _config(app_user, **overrides):
    fields = dict(
        company_name="Mitra Sanitär GmbH",
        datev_consultant_number="12345",
        datev_client_number="1001",
        datev_chart_of_accounts="SKR03",
        datev_account_length=4,
        datev_fiscal_year_start_month=1,
    )
    fields.update(overrides)
    firma_service.update_company_profile(app_user.id, **fields)


def _property(app_user):
    return property_service.create_property(
        app_user.id, name="DATEV-Objekt", property_type="WEG",
        street="Weg", postal_code="10115", city="Berlin",
    )


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


def _published(app_user, *, lines, debtor_last="Krüger", invoice_date=None):
    obj = _property(app_user)
    debtor = identity_service.create_person(
        app_user.id, first_name="Sabine", last_name=debtor_last
    )
    order = _gepruefter_auftrag(app_user, obj, debtor)
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="RECHNUNG",
        work_order_id=order.id, invoice_date=invoice_date or _HEUTE,
        lines=lines,
    )
    for role in ("INVOICE_DEBTOR", "INVOICE_RECIPIENT"):
        beleg_service.add_invoice_party(
            app_user.id, invoice_id=inv.id, party_id=debtor.id,
            role=role, is_primary=True,
        )
    beleg_service.publish_invoice(app_user.id, invoice_id=inv.id)
    inv.refresh_from_db()
    return inv


def _rows(inhalt):
    """EXTF-Bytes → Liste der (nicht-leeren) Zeilen als cp1252-Strings."""
    return inhalt.decode("cp1252").split("\r\n")


def _booking_rows(inhalt):
    """Nur die Buchungssatz-Zeilen (ohne Kopf- und Spaltenzeile, ohne Leerzeile)."""
    return [r for r in _rows(inhalt)[2:] if r]


# --- Vorbedingungen ---------------------------------------------------------

@pytest.mark.django_db
def test_export_ohne_profil_scheitert(app_user):
    with pytest.raises(datev_service.DatevExportError):
        datev_service.build_datev_export(_HEUTE, _HEUTE)


@pytest.mark.django_db
def test_export_ohne_beraternummer_scheitert(app_user):
    _config(app_user, datev_consultant_number=None)
    with pytest.raises(datev_service.DatevExportError) as exc:
        datev_service.build_datev_export(_HEUTE, _HEUTE)
    assert "Beraternummer" in str(exc.value)


@pytest.mark.django_db
def test_zeitraum_ueber_jahresgrenze_scheitert(app_user):
    _config(app_user)
    with pytest.raises(datev_service.DatevExportError):
        datev_service.build_datev_export(date(2025, 12, 1), date(2026, 1, 31))


@pytest.mark.django_db
def test_von_nach_bis_scheitert(app_user):
    _config(app_user)
    with pytest.raises(datev_service.DatevExportError):
        datev_service.build_datev_export(_HEUTE, _HEUTE - timedelta(days=1))


@pytest.mark.django_db
def test_abweichendes_wirtschaftsjahr_kopf(app_user):
    """April-WJ: WJ-Beginn im Kopf gehört zum Wirtschaftsjahr des Von-Datums."""
    _config(app_user, datev_fiscal_year_start_month=4)
    # Mai–August 2026 liegt im WJ, das am 1. April 2026 begann.
    _, inhalt = datev_service.build_datev_export(date(2026, 5, 1), date(2026, 8, 31))
    assert _rows(inhalt)[0].split(";")[12] == "20260401"
    # Februar–März 2026 gehört noch zum WJ, das am 1. April 2025 begann.
    _, inhalt2 = datev_service.build_datev_export(date(2026, 2, 1), date(2026, 3, 31))
    assert _rows(inhalt2)[0].split(";")[12] == "20250401"


@pytest.mark.django_db
def test_zeitraum_ueber_wirtschaftsjahr_scheitert(app_user):
    """April-WJ: Jan–Dez 2026 überspannt die WJ-Grenze (1. April) → 422."""
    _config(app_user, datev_fiscal_year_start_month=4)
    with pytest.raises(datev_service.DatevExportError):
        datev_service.build_datev_export(date(2026, 1, 1), date(2026, 12, 31))


# --- Struktur & Kodierung ---------------------------------------------------

@pytest.mark.django_db
def test_kopf_und_spaltenzeile(app_user):
    _config(app_user)
    _, inhalt = datev_service.build_datev_export(
        _HEUTE, _HEUTE, erzeugt_am=datetime(2026, 7, 11, 9, 15, 0)
    )
    zeilen = _rows(inhalt)
    kopf = zeilen[0].split(";")
    assert kopf[0] == '"EXTF"'
    assert kopf[2] == "21"                     # Datenkategorie Buchungsstapel
    assert kopf[3] == '"Buchungsstapel"'
    assert kopf[10] == "12345"                 # Beraternummer
    assert kopf[11] == "1001"                  # Mandantennummer
    assert kopf[13] == "4"                     # Sachkontenlänge
    assert kopf[12] == f"{_HEUTE.year}0101"    # WJ-Beginn (Januar)
    assert kopf[5] == "20260711091500000"      # erzeugt am, 17 Stellen
    assert len(kopf) == 31
    # Spaltenzeile: die 14 führenden Standardspalten, exakt benannt.
    spalten = zeilen[1].split(";")
    assert spalten[0] == '"Umsatz (ohne Soll/Haben-Kz)"'
    assert spalten[1] == '"Soll/Haben-Kennzeichen"'
    assert spalten[7] == '"Gegenkonto (ohne BU-Schlüssel)"'
    assert len(spalten) == 14


@pytest.mark.django_db
def test_cp1252_kodierung_umlaut(app_user):
    _config(app_user)
    _published(app_user, debtor_last="Krüger", lines=[
        {"line_type": "MATERIAL", "description": "Ziegel", "quantity": 1,
         "unit": "Stk", "unit_price": "100.00", "tax_code": "DE_19"},
    ])
    _, inhalt = datev_service.build_datev_export(_HEUTE, _HEUTE)
    # ü ist in cp1252 das Byte 0xFC (nicht der UTF-8-Doppelbyte 0xC3 0xBC).
    assert b"\xfc" in inhalt
    assert "Krüger" in inhalt.decode("cp1252")


# --- Buchungslogik ----------------------------------------------------------

@pytest.mark.django_db
def test_grundfall_ein_steuersatz(app_user):
    _config(app_user)
    inv = _published(app_user, lines=[
        {"line_type": "MATERIAL", "description": "Ziegel", "quantity": 100,
         "unit": "Stk", "unit_price": "2.40", "tax_code": "DE_19"},
    ])
    _, inhalt = datev_service.build_datev_export(_HEUTE, _HEUTE)
    rows = _booking_rows(inhalt)
    assert len(rows) == 1
    f = rows[0].split(";")
    # netto 240,00 → brutto 285,60 (19 %)
    assert f[0] == "285,60"                     # Umsatz brutto
    assert f[1] == '"S"'                        # normaler Verkauf: Forderung Soll
    assert f[6] == "1400"                       # Konto: Sammeldebitor (SKR03)
    assert f[7] == "8400"                       # Gegenkonto: Erlöse 19 % (SKR03)
    assert f[8] == ""                           # kein BU-Schlüssel (Automatik)
    assert f[9] == _HEUTE.strftime("%d%m")      # Belegdatum TTMM
    assert f[10] == f'"{inv.invoice_number}"'   # Belegfeld 1
    assert f[13] == '"Sabine Krüger"'           # Buchungstext: Kundenname
    assert Decimal("285.60") == inv.gross_total


@pytest.mark.django_db
def test_zwei_steuersaetze_reconciliation(app_user):
    _config(app_user)
    inv = _published(app_user, lines=[
        {"line_type": "MATERIAL", "description": "Ware 19", "quantity": 1,
         "unit": "Stk", "unit_price": "100.00", "tax_code": "DE_19"},
        {"line_type": "MATERIAL", "description": "Ware 7", "quantity": 1,
         "unit": "Stk", "unit_price": "50.00", "tax_code": "DE_7"},
    ])
    _, inhalt = datev_service.build_datev_export(_HEUTE, _HEUTE)
    rows = [r.split(";") for r in _booking_rows(inhalt)]
    assert len(rows) == 2
    konten = {r[7]: Decimal(r[0].replace(",", ".")) for r in rows}
    assert konten["8400"] == Decimal("119.00")  # 100 + 19 %
    assert konten["8300"] == Decimal("53.50")   # 50 + 7 %
    # Summe der Buchungssätze == Bruttosumme der Rechnung (cent-genau).
    assert sum(konten.values()) == inv.gross_total


@pytest.mark.django_db
def test_storno_kehrt_soll_haben_um(app_user):
    _config(app_user)
    inv = _published(app_user, lines=[
        {"line_type": "MATERIAL", "description": "Ziegel", "quantity": 100,
         "unit": "Stk", "unit_price": "2.40", "tax_code": "DE_19"},
    ])
    storno = beleg_service.create_cancellation(app_user.id, invoice_id=inv.id)
    _, inhalt = datev_service.build_datev_export(_HEUTE, _HEUTE)
    rows = [r.split(";") for r in _booking_rows(inhalt)]
    # Original (S) + Storno (H), beide 285,60 brutto.
    kennzeichen = sorted(r[1] for r in rows)
    assert kennzeichen == ['"H"', '"S"']
    for r in rows:
        assert r[0] == "285,60"
    assert storno.invoice_type == "STORNO"


@pytest.mark.django_db
def test_entwurf_wird_nicht_exportiert(app_user):
    _config(app_user)
    obj = _property(app_user)
    beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type="RECHNUNG",
        invoice_date=_HEUTE,
        lines=[{"line_type": "MATERIAL", "description": "X", "quantity": 1,
                "unit": "Stk", "unit_price": "100.00", "tax_code": "DE_19"}],
    )
    _, inhalt = datev_service.build_datev_export(_HEUTE, _HEUTE)
    assert _booking_rows(inhalt) == []


@pytest.mark.django_db
def test_konto_override_wird_verwendet(app_user):
    _config(app_user, datev_revenue_account_full="8401",
            datev_debtor_account="10001")
    _published(app_user, lines=[
        {"line_type": "MATERIAL", "description": "Ziegel", "quantity": 1,
         "unit": "Stk", "unit_price": "100.00", "tax_code": "DE_19"},
    ])
    _, inhalt = datev_service.build_datev_export(_HEUTE, _HEUTE)
    f = _booking_rows(inhalt)[0].split(";")
    assert f[6] == "10001"    # Debitor-Override
    assert f[7] == "8401"     # Erlöskonto-Override
