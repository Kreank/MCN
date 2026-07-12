"""Service-Tests des DATEV-EXTF-Exports gegen die echte Test-DB.

Prüft die Buchungslogik (je Steuergruppe ein Satz, Debitor an Erlöskonto,
Brutto + Automatik, Soll/Haben-Umkehr bei Storno), die cent-genaue Reconciliation
mit `gross_total`, die EXTF-Struktur (Kopf-/Spaltenzeile) und die cp1252-Kodierung
sowie die Vorbedingungen (Konfiguration, Zeitraum).
"""
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from django.db import connection

from db_core.models import Invoice
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import datev as datev_service
from db_core.services import firma as firma_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service

_HEUTE = date.today()


def _force_deferred_checks():
    """Wertet die DEFERRED Constraint-Trigger sofort aus (Veröffentlichungstore
    scharf; Muster aus test_schlussrechnung_service.py)."""
    with connection.cursor() as cur:
        cur.execute("SET CONSTRAINTS ALL IMMEDIATE")


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


# --- Abschlagsrechnungen: Erlös vs. Anzahlungskonto (Migration 0063) ---------
# Die Falle ist die Summe: über Abschlag UND Schlussrechnung müssen beide Modi
# denselben Erlös und dieselbe Umsatzsteuer ergeben, und je Beleg muss die Summe
# der Buchungssätze cent-genau `gross_total` treffen.

def _auftrag_mit_kunde(app_user):
    obj = _property(app_user)
    kunde = identity_service.create_person(
        app_user.id, first_name="Sabine", last_name="Krüger"
    )
    return obj, kunde, _gepruefter_auftrag(app_user, obj, kunde)


def _beleg(app_user, obj, kunde, order, *, typ, lines, advances=None):
    """Legt einen Beleg an und veröffentlicht ihn."""
    inv = beleg_service.create_invoice(
        app_user.id, property_id=obj.id, invoice_type=typ, work_order_id=order.id,
        invoice_date=_HEUTE, lines=lines,
        advance_invoice_ids=[a.id for a in advances] if advances else None,
    )
    for role in ("INVOICE_DEBTOR", "INVOICE_RECIPIENT"):
        beleg_service.add_invoice_party(
            app_user.id, invoice_id=inv.id, party_id=kunde.id,
            role=role, is_primary=True,
        )
    beleg_service.publish_invoice(app_user.id, invoice_id=inv.id)
    inv.refresh_from_db()
    return inv


def _pauschale(betrag, tax="DE_19", text="Leistung"):
    return {"line_type": "PAUSCHALE", "description": text, "quantity": 1,
            "unit_price": betrag, "tax_code": tax}


def _abschlag_und_schlussrechnung(app_user, *, abschlag, leistung, tax="DE_19"):
    obj, kunde, order = _auftrag_mit_kunde(app_user)
    ar = _beleg(app_user, obj, kunde, order, typ="ABSCHLAGSRECHNUNG",
                lines=[_pauschale(abschlag, tax, "1. Abschlag")])
    sr = _beleg(app_user, obj, kunde, order, typ="SCHLUSSRECHNUNG",
                lines=[_pauschale(leistung, tax, "Gesamtleistung")], advances=[ar])
    return ar, sr


def _saetze(inhalt):
    """Buchungssätze als Dicts: {konto, gegenkonto, sh, umsatz(Decimal), beleg1}."""
    saetze = []
    for row in _booking_rows(inhalt):
        f = row.split(";")
        saetze.append({
            "umsatz": Decimal(f[0].replace(",", ".")),
            "sh": f[1].strip('"'),
            "konto": f[6],
            "gegenkonto": f[7],
            "beleg1": f[10].strip('"'),
            "beleg2": f[11],
            "text": f[13].strip('"'),
        })
    return saetze


def _saldo(saetze, konto):
    """Saldo eines Gegenkontos aus Sicht des Kontos (S = Soll, H = Haben).

    Rückgabe positiv = das Konto steht im HABEN (so entsteht Erlös bzw. eine
    Anzahlungs-Verbindlichkeit), negativ = im Soll.
    """
    summe = Decimal("0.00")
    for s in saetze:
        if s["gegenkonto"] != konto:
            continue
        # Konto (Debitor) im Soll → Gegenkonto im Haben.
        summe += s["umsatz"] if s["sh"] == "S" else -s["umsatz"]
    return summe


def _beleg_summe(saetze, nummer):
    """Vorzeichenbehaftete Summe der Buchungssätze eines Belegs (S = +, H = −).

    Das ist die Bewegung auf dem Debitor — sie muss `gross_total` treffen.
    """
    return sum(
        (s["umsatz"] if s["sh"] == "S" else -s["umsatz"])
        for s in saetze if s["beleg1"] == nummer
    )


@pytest.mark.django_db
def test_default_ist_erloes_abschlag_bucht_erlöskonto(app_user):
    """Bestandsverhalten: ohne Umstellung bucht der Abschlag auf Erlös."""
    _config(app_user)
    profil = firma_service.get_company_profile()
    assert profil.datev_advance_mode == "ERLOES"
    ar, _sr = _abschlag_und_schlussrechnung(
        app_user, abschlag="1000.00", leistung="5000.00"
    )
    saetze = _saetze(datev_service.build_datev_export(_HEUTE, _HEUTE)[1])
    ar_saetze = [s for s in saetze if s["beleg1"] == ar.invoice_number]
    assert len(ar_saetze) == 1
    assert ar_saetze[0]["gegenkonto"] == "8400"     # Erlöse 19 % (SKR03)
    assert ar_saetze[0]["umsatz"] == Decimal("1190.00")
    assert ar_saetze[0]["sh"] == "S"
    # Kein Anzahlungskonto im Spiel.
    assert all(s["gegenkonto"] != "1718" for s in saetze)


@pytest.mark.django_db
def test_anzahlung_abschlag_auf_anzahlungskonto(app_user):
    _config(app_user, datev_advance_mode="ANZAHLUNG")
    ar, _sr = _abschlag_und_schlussrechnung(
        app_user, abschlag="1000.00", leistung="5000.00"
    )
    saetze = _saetze(datev_service.build_datev_export(_HEUTE, _HEUTE)[1])
    ar_saetze = [s for s in saetze if s["beleg1"] == ar.invoice_number]
    assert len(ar_saetze) == 1
    # Debitor an „Erhaltene, versteuerte Anzahlungen 19 % USt" (SKR03 1718).
    assert ar_saetze[0]["konto"] == "1400"
    assert ar_saetze[0]["gegenkonto"] == "1718"
    assert ar_saetze[0]["sh"] == "S"
    assert ar_saetze[0]["umsatz"] == Decimal("1190.00")


@pytest.mark.django_db
def test_anzahlung_schlussrechnung_loest_auf(app_user):
    """Der Kern: die SR bucht Leistung auf Erlös und löst die Anzahlung auf."""
    _config(app_user, datev_advance_mode="ANZAHLUNG")
    ar, sr = _abschlag_und_schlussrechnung(
        app_user, abschlag="1000.00", leistung="5000.00"
    )
    saetze = _saetze(datev_service.build_datev_export(_HEUTE, _HEUTE)[1])
    sr_saetze = [s for s in saetze if s["beleg1"] == sr.invoice_number]
    assert len(sr_saetze) == 2

    anrechnung = next(s for s in sr_saetze if s["gegenkonto"] == "1718")
    assert anrechnung["sh"] == "H"                    # Anzahlungskonto im SOLL
    assert anrechnung["umsatz"] == Decimal("1190.00")
    # Rückverweis auf den Abschlag im Buchungstext (Belegfeld 2 ist zu kurz und
    # trägt konventionell die Fälligkeit — es bleibt leer).
    assert ar.invoice_number in anrechnung["text"]
    assert anrechnung["beleg2"] == ""

    erloes = next(s for s in sr_saetze if s["gegenkonto"] == "8400")
    assert erloes["sh"] == "S"
    assert erloes["umsatz"] == Decimal("5950.00")     # 5000 netto + 19 %

    # Anzahlungskonto ist nach Abschlag + Schlussrechnung wieder ausgeglichen.
    assert _saldo(saetze, "1718") == Decimal("0.00")
    # Erlös entsteht in voller Höhe genau einmal.
    assert _saldo(saetze, "8400") == Decimal("5950.00")
    # Reconciliation je Beleg: Debitorbewegung == gross_total.
    assert _beleg_summe(saetze, ar.invoice_number) == ar.gross_total
    assert _beleg_summe(saetze, sr.invoice_number) == sr.gross_total


@pytest.mark.django_db
def test_beide_modi_ergeben_dieselben_erloese(app_user):
    """Die eigentliche Falle: die Summe über alle Belege muss identisch sein.

    Zwei Steuersätze, krumme Beträge (Rundung je Steuergruppe), Abschlag +
    Schlussrechnung. Erlöse, Umsatzsteuerbasis und Debitorbewegung sind in beiden
    Modi gleich — nur der Weg dorthin unterscheidet sich.
    """
    _config(app_user)
    obj, kunde, order = _auftrag_mit_kunde(app_user)
    ar = _beleg(app_user, obj, kunde, order, typ="ABSCHLAGSRECHNUNG", lines=[
        _pauschale("333.33", "DE_19", "Abschlag 19 %"),
        _pauschale("111.11", "DE_7", "Abschlag 7 %"),
    ])
    sr = _beleg(app_user, obj, kunde, order, typ="SCHLUSSRECHNUNG", lines=[
        _pauschale("1234.57", "DE_19", "Leistung 19 %"),
        _pauschale("777.77", "DE_7", "Leistung 7 %"),
    ], advances=[ar])

    erloes = _saetze(datev_service.build_datev_export(_HEUTE, _HEUTE)[1])

    firma_service.update_company_profile(app_user.id, datev_advance_mode="ANZAHLUNG")
    anzahlung = _saetze(datev_service.build_datev_export(_HEUTE, _HEUTE)[1])

    for konto in ("8400", "8300"):
        assert _saldo(erloes, konto) == _saldo(anzahlung, konto)
    # Anzahlungskonten gleichen sich vollständig aus (Modus ANZAHLUNG) …
    for konto in ("1718", "1711"):
        assert _saldo(anzahlung, konto) == Decimal("0.00")
    # … und im Modus ERLOES kommen sie gar nicht vor.
    assert all(s["gegenkonto"] not in ("1718", "1711") for s in erloes)
    # Reconciliation cent-genau je Beleg, in BEIDEN Modi.
    for saetze in (erloes, anzahlung):
        assert _beleg_summe(saetze, ar.invoice_number) == ar.gross_total
        assert _beleg_summe(saetze, sr.invoice_number) == sr.gross_total


@pytest.mark.django_db
def test_anzahlung_skr04_konten(app_user):
    """SKR04: Anzahlungen 19 % = 3272 (NICHT 3270 — das ist der 16-%-Sondersatz)."""
    _config(app_user, datev_chart_of_accounts="SKR04",
            datev_advance_mode="ANZAHLUNG")
    obj, kunde, order = _auftrag_mit_kunde(app_user)
    ar = _beleg(app_user, obj, kunde, order, typ="ABSCHLAGSRECHNUNG", lines=[
        _pauschale("100.00", "DE_19"), _pauschale("100.00", "DE_7"),
    ])
    sr = _beleg(app_user, obj, kunde, order, typ="SCHLUSSRECHNUNG", lines=[
        _pauschale("500.00", "DE_19"), _pauschale("500.00", "DE_7"),
    ], advances=[ar])
    saetze = _saetze(datev_service.build_datev_export(_HEUTE, _HEUTE)[1])
    konten = {s["gegenkonto"] for s in saetze}
    assert konten == {"3272", "3260", "4400", "4300"}
    assert all(s["konto"] == "1200" for s in saetze)   # Sammeldebitor SKR04
    assert _saldo(saetze, "3272") == Decimal("0.00")
    assert _saldo(saetze, "3260") == Decimal("0.00")
    assert _saldo(saetze, "4400") == Decimal("595.00")  # 500 + 19 %
    assert _saldo(saetze, "4300") == Decimal("535.00")  # 500 + 7 %
    assert _beleg_summe(saetze, sr.invoice_number) == sr.gross_total
    assert _beleg_summe(saetze, ar.invoice_number) == ar.gross_total


@pytest.mark.django_db
def test_anzahlung_konto_override(app_user):
    _config(app_user, datev_advance_mode="ANZAHLUNG",
            datev_advance_account_full="1799")
    ar, _sr = _abschlag_und_schlussrechnung(
        app_user, abschlag="1000.00", leistung="5000.00"
    )
    saetze = _saetze(datev_service.build_datev_export(_HEUTE, _HEUTE)[1])
    assert all(s["gegenkonto"] != "1718" for s in saetze)
    assert _saldo(saetze, "1799") == Decimal("0.00")
    assert any(s["gegenkonto"] == "1799" for s in saetze)


@pytest.mark.django_db
def test_anzahlung_storno_des_abschlags(app_user):
    """Der Storno einer Abschlagsrechnung gibt die Anzahlung zurück — nicht den
    Erlös (sonst bliebe die Verbindlichkeit stehen)."""
    _config(app_user, datev_advance_mode="ANZAHLUNG")
    obj, kunde, order = _auftrag_mit_kunde(app_user)
    ar = _beleg(app_user, obj, kunde, order, typ="ABSCHLAGSRECHNUNG",
                lines=[_pauschale("1000.00")])
    storno = beleg_service.create_cancellation(app_user.id, invoice_id=ar.id)
    storno.refresh_from_db()

    saetze = _saetze(datev_service.build_datev_export(_HEUTE, _HEUTE)[1])
    st = [s for s in saetze if s["beleg1"] == storno.invoice_number]
    assert len(st) == 1
    assert st[0]["gegenkonto"] == "1718"
    assert st[0]["sh"] == "H"                      # Anzahlungskonto im Soll
    assert st[0]["umsatz"] == Decimal("1190.00")
    assert _saldo(saetze, "1718") == Decimal("0.00")
    assert _beleg_summe(saetze, storno.invoice_number) == storno.gross_total


@pytest.mark.django_db
def test_anzahlung_storno_der_schlussrechnung(app_user):
    """Das Storno der Schlussrechnung dreht Erlös UND Anrechnung um: die Anzahlung
    lebt wieder auf (der Abschlag ist danach erneut anrechenbar)."""
    _config(app_user, datev_advance_mode="ANZAHLUNG")
    ar, sr = _abschlag_und_schlussrechnung(
        app_user, abschlag="1000.00", leistung="5000.00"
    )
    storno = beleg_service.create_cancellation(app_user.id, invoice_id=sr.id)
    storno.refresh_from_db()

    saetze = _saetze(datev_service.build_datev_export(_HEUTE, _HEUTE)[1])
    st = [s for s in saetze if s["beleg1"] == storno.invoice_number]
    assert len(st) == 2
    anrechnung = next(s for s in st if s["gegenkonto"] == "1718")
    assert anrechnung["sh"] == "S"                  # Anzahlung lebt wieder auf
    assert anrechnung["umsatz"] == Decimal("1190.00")
    erloes = next(s for s in st if s["gegenkonto"] == "8400")
    assert erloes["sh"] == "H"                      # Erlös wieder heraus
    assert erloes["umsatz"] == Decimal("5950.00")

    # Nach dem Storno steht wieder genau die Anzahlung des Abschlags im Haben,
    # und es ist kein Erlös übrig.
    assert _saldo(saetze, "1718") == Decimal("1190.00")
    assert _saldo(saetze, "8400") == Decimal("0.00")
    assert _beleg_summe(saetze, storno.invoice_number) == storno.gross_total
    assert _beleg_summe(saetze, ar.invoice_number) == ar.gross_total


@pytest.mark.django_db
def test_anzahlung_laesst_normale_kreditbelege_unveraendert(app_user):
    """Storno einer normalen Rechnung bucht auch im Modus ANZAHLUNG gegen Erlös."""
    _config(app_user, datev_advance_mode="ANZAHLUNG")
    inv = _published(app_user, lines=[
        {"line_type": "MATERIAL", "description": "Ziegel", "quantity": 100,
         "unit": "Stk", "unit_price": "2.40", "tax_code": "DE_19"},
    ])
    storno = beleg_service.create_cancellation(app_user.id, invoice_id=inv.id)
    storno.refresh_from_db()
    saetze = _saetze(datev_service.build_datev_export(_HEUTE, _HEUTE)[1])
    assert {s["gegenkonto"] for s in saetze} == {"8400"}
    assert sorted(s["sh"] for s in saetze) == ["H", "S"]
    assert _saldo(saetze, "8400") == Decimal("0.00")


@pytest.mark.django_db
def test_anzahlung_mehrere_abschlaege_und_teilrechnung(app_user):
    """Zwei Abschläge + eine TEILRECHNUNG auf eine Schlussrechnung: je Abschlag ein
    eigener Auflösungssatz, das Anzahlungskonto steht am Ende auf null."""
    _config(app_user, datev_advance_mode="ANZAHLUNG")
    obj, kunde, order = _auftrag_mit_kunde(app_user)
    a1 = _beleg(app_user, obj, kunde, order, typ="ABSCHLAGSRECHNUNG",
                lines=[_pauschale("1000.00", text="1. Abschlag")])
    a2 = _beleg(app_user, obj, kunde, order, typ="ABSCHLAGSRECHNUNG",
                lines=[_pauschale("2000.00", text="2. Abschlag")])
    tr = _beleg(app_user, obj, kunde, order, typ="TEILRECHNUNG",
                lines=[_pauschale("500.00", text="Teilrechnung Rohbau")])
    sr = _beleg(app_user, obj, kunde, order, typ="SCHLUSSRECHNUNG",
                lines=[_pauschale("9000.00", text="Gesamtleistung")],
                advances=[a1, a2, tr])

    saetze = _saetze(datev_service.build_datev_export(_HEUTE, _HEUTE)[1])
    # Die Teilrechnung bucht wie ein Abschlag auf das Anzahlungskonto.
    tr_satz = next(s for s in saetze if s["beleg1"] == tr.invoice_number)
    assert tr_satz["gegenkonto"] == "1718" and tr_satz["sh"] == "S"

    sr_saetze = [s for s in saetze if s["beleg1"] == sr.invoice_number]
    aufloesungen = [s for s in sr_saetze if s["gegenkonto"] == "1718"]
    assert len(aufloesungen) == 3                       # je angerechnetem Beleg einer
    assert all(s["sh"] == "H" for s in aufloesungen)
    assert sorted(s["umsatz"] for s in aufloesungen) == [
        Decimal("595.00"), Decimal("1190.00"), Decimal("2380.00")
    ]
    for beleg in (a1, a2, tr):
        assert any(beleg.invoice_number in s["text"] for s in aufloesungen)

    erloes = next(s for s in sr_saetze if s["gegenkonto"] == "8400")
    assert erloes["sh"] == "S"
    assert erloes["umsatz"] == Decimal("10710.00")      # 9000 netto + 19 %
    assert _saldo(saetze, "1718") == Decimal("0.00")
    assert _saldo(saetze, "8400") == Decimal("10710.00")
    for beleg in (a1, a2, tr, sr):
        assert _beleg_summe(saetze, beleg.invoice_number) == beleg.gross_total


@pytest.mark.django_db
def test_anzahlung_storno_sr_danach_storno_des_abschlags(app_user):
    """Nach dem Storno der Schlussrechnung ist der Abschlag wieder frei und kann
    selbst storniert werden — das Anzahlungskonto endet trotzdem auf null."""
    _config(app_user, datev_advance_mode="ANZAHLUNG")
    ar, sr = _abschlag_und_schlussrechnung(
        app_user, abschlag="1000.00", leistung="5000.00"
    )
    # Die Veröffentlichungstore sind DEFERRED Constraint-Trigger. In der einen
    # Test-Transaktion würden sie am Ende ERNEUT laufen und dann den (später
    # entstandenen) Storno der Abschlagsrechnung sehen — in der Wirklichkeit ist
    # die Veröffentlichung längst committet. Hier also jetzt auswerten, wie in
    # test_schlussrechnung_service.
    _force_deferred_checks()
    beleg_service.create_cancellation(app_user.id, invoice_id=sr.id)
    # Erst jetzt zulässig: die Anrechnung ist mit dem SR-Storno erloschen.
    beleg_service.create_cancellation(app_user.id, invoice_id=ar.id)

    saetze = _saetze(datev_service.build_datev_export(_HEUTE, _HEUTE)[1])
    # AR (+1190 H auf 1718), SR-Auflösung (−1190), SR-Storno (+1190), AR-Storno (−1190)
    assert _saldo(saetze, "1718") == Decimal("0.00")
    assert _saldo(saetze, "8400") == Decimal("0.00")
    for inv in Invoice.objects.filter(status="VEROEFFENTLICHT"):
        assert _beleg_summe(saetze, inv.invoice_number) == inv.gross_total


@pytest.mark.django_db
def test_moduswechsel_bei_offenem_abschlag_abgelehnt(app_user):
    """Der Server verhindert den Wechsel am unsauberen Schnitt (sonst bliebe ein
    Saldo auf dem Anzahlungskonto stehen)."""
    _config(app_user)
    obj, kunde, order = _auftrag_mit_kunde(app_user)
    ar = _beleg(app_user, obj, kunde, order, typ="ABSCHLAGSRECHNUNG",
                lines=[_pauschale("1000.00")])
    with pytest.raises(ValueError) as exc:
        firma_service.update_company_profile(
            app_user.id, datev_advance_mode="ANZAHLUNG"
        )
    assert ar.invoice_number in str(exc.value)

    # Andere Profilfelder bleiben pflegbar (der Modus wird unverändert mitgesendet).
    firma_service.update_company_profile(
        app_user.id, city="Musterstadt", datev_advance_mode="ERLOES"
    )
    assert firma_service.get_company_profile().city == "Musterstadt"

    # Nach der Schlussrechnung ist der Schnitt sauber → Wechsel erlaubt.
    _beleg(app_user, obj, kunde, order, typ="SCHLUSSRECHNUNG",
           lines=[_pauschale("5000.00")], advances=[ar])
    firma_service.update_company_profile(app_user.id, datev_advance_mode="ANZAHLUNG")
    assert firma_service.get_company_profile().datev_advance_mode == "ANZAHLUNG"


@pytest.mark.django_db
def test_anzahlung_reine_rechnung_bleibt_erloes(app_user):
    """Eine normale RECHNUNG bucht auch im Modus ANZAHLUNG auf Erlös."""
    _config(app_user, datev_advance_mode="ANZAHLUNG")
    inv = _published(app_user, lines=[
        {"line_type": "MATERIAL", "description": "Ziegel", "quantity": 1,
         "unit": "Stk", "unit_price": "100.00", "tax_code": "DE_19"},
    ])
    saetze = _saetze(datev_service.build_datev_export(_HEUTE, _HEUTE)[1])
    assert len(saetze) == 1
    assert saetze[0]["gegenkonto"] == "8400"
    assert _beleg_summe(saetze, inv.invoice_number) == inv.gross_total
