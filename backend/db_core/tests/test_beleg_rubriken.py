"""Abschnitte (Rubriken), Alternativ-/Bedarfspositionen und die interne
Kalkulationsübersicht je Abschnitt.

Zwei Ebenen müssen exakt dieselbe Summe bilden:
  * der Service (`_prepare_lines`) beim Anlegen, und
  * die DB (`assert_quote_totals`/`assert_invoice_totals`, Migration 0036) beim
    Versand bzw. der Veröffentlichung.
Zählte nur eine der beiden die Alternativpositionen mit, würde jeder Beleg mit
einer Alternative als „Summen inkonsistent" abgewiesen. Deshalb prüft
`test_versand_akzeptiert_beleg_mit_alternativposition` den scharfen DB-Pfad und
nicht nur die berechneten Felder.

Die Kalkulationsübersicht wird bei jedem Aufruf aus den eingefrorenen
Positionswerten gerechnet, nie gespeichert. Fehlt ein EK-Snapshot, wird die Marge
nicht geschätzt (siehe test_kalkulation_ohne_ek_raet_keine_marge).
"""
from decimal import Decimal

import pytest
from django.db import Error, connection, transaction

from db_core.models import QuoteLine
from db_core.services import beleg as beleg_service
from db_core.services import property as property_service


def _force_deferred_checks():
    """Erzwingt die Prüfung DEFERRED Constraint-Trigger sofort.

    Das Versand-Tor des Angebots (`trg_quote_send_gate`) ist DEFERRABLE INITIALLY
    DEFERRED und feuert erst beim echten COMMIT — den pytest je Test zurückrollt.
    Ohne dieses SET CONSTRAINTS liefe ein Test, der die Summenprüfung belegen
    soll, am Tor vorbei und wäre grün, ohne irgendetwas zu beweisen.
    """
    with connection.cursor() as cur:
        cur.execute("SET CONSTRAINTS ALL IMMEDIATE")


def _property(app_user):
    return property_service.create_property(
        app_user.id, name="Rubrik-Objekt", property_type="WEG",
        street="Weg", postal_code="10115", city="Berlin",
    )


def _mat(desc, qty, price, **extra):
    return {
        "line_type": "MATERIAL", "description": desc, "quantity": qty,
        "unit": "Stk", "unit_price": price, "tax_code": "DE_19", **extra,
    }


# --- Alternativ- und Bedarfspositionen -------------------------------------

@pytest.mark.django_db
def test_alternativposition_zaehlt_nicht_zur_summe(app_user):
    obj = _property(app_user)
    q = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="Mit Alternative",
        lines=[
            _mat("Standardrinne", 2, 10),                        # 20,00 zaehlt
            _mat("Kupferrinne", 2, 50, line_kind="ALTERNATIV"),  # 100,00 zaehlt nicht
            _mat("Zusatzhalter", 1, 5, line_kind="BEDARF"),      # 5,00 zaehlt nicht
        ],
    )
    assert q.net_total == Decimal("20.00")
    assert q.tax_total == Decimal("3.80")
    assert q.gross_total == Decimal("23.80")

    alt = QuoteLine.objects.get(quote_id=q.id, position_number=2)
    assert alt.line_kind == "ALTERNATIV"
    assert alt.net_amount == Decimal("100.00")
    bedarf = QuoteLine.objects.get(quote_id=q.id, position_number=3)
    assert bedarf.line_kind == "BEDARF"
    assert bedarf.net_amount == Decimal("5.00")


@pytest.mark.django_db
def test_versand_akzeptiert_beleg_mit_alternativposition(app_user):
    """Scharfe Gegenprobe gegen den DB-Trigger: Service und DB müssen dieselbe
    Summe erwarten, sonst scheitert der Versand mit 'Summen inkonsistent'."""
    obj = _property(app_user)
    q = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="Versand mit Alternative",
        quote_date="2026-07-01",
        lines=[
            _mat("Standard", 1, 100),
            _mat("Luxus", 1, 500, line_kind="ALTERNATIV"),
        ],
    )
    versendet = beleg_service.send_quote(app_user.id, quote_id=q.id)
    _force_deferred_checks()          # das Tor jetzt wirklich auswerten
    assert versendet.status == "VERSENDET"
    assert versendet.quote_number.startswith("AN-")
    # Der Snapshot deckt die Positionsart ab: sonst waere nicht vom Hash gedeckt,
    # ob eine Position summenwirksam war.
    arten = [line["line_kind"] for line in versendet.billing_snapshot["lines"]]
    assert arten == ["NORMAL", "ALTERNATIV"]


@pytest.mark.django_db
def test_nur_alternativen_ist_kein_gueltiger_beleg(app_user):
    """Ein Beleg ohne summenwirksame Position hat einen Gesamtbetrag von 0 — das
    Versand-Tor der DB weist ihn ab."""
    obj = _property(app_user)
    q = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="Nur Alternativen",
        quote_date="2026-07-01",
        lines=[_mat("Variante A", 1, 100, line_kind="ALTERNATIV")],
    )
    assert q.net_total == Decimal("0.00")
    with pytest.raises((ValueError, Error), match="summenwirksame"):
        with transaction.atomic():
            beleg_service.send_quote(app_user.id, quote_id=q.id)
            _force_deferred_checks()


@pytest.mark.django_db
def test_textzeile_kann_keine_alternative_sein(app_user):
    obj = _property(app_user)
    with pytest.raises(ValueError, match="Alternativ"):
        beleg_service.create_quote(
            app_user.id, property_id=obj.id, title="X",
            lines=[{"line_type": "TEXT", "description": "Hinweis",
                    "line_kind": "ALTERNATIV"}],
        )


@pytest.mark.django_db
def test_ungueltige_line_kind_wird_abgewiesen(app_user):
    obj = _property(app_user)
    with pytest.raises(ValueError, match="line_kind"):
        beleg_service.create_quote(
            app_user.id, property_id=obj.id, title="X",
            lines=[_mat("A", 1, 10, line_kind="QUATSCH")],
        )


# --- Abschnitte (Rubriken) --------------------------------------------------

@pytest.mark.django_db
def test_abschnitte_gliedern_das_angebot(app_user):
    obj = _property(app_user)
    q = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="Zwei Abschnitte",
        rubriken=[
            {"title": "Dacharbeiten", "description": "Rinne und Ablauf"},
            {"title": "Fassade"},
        ],
        lines=[
            _mat("Rinne", 2, 10, rubrik=1),
            _mat("Ablauf", 1, 30, rubrik=1),
            _mat("Putz", 5, 8, rubrik=2),
        ],
    )
    rubriken = sorted(q.rubriken.all(), key=lambda r: r.position_number)
    assert [r.title for r in rubriken] == ["Dacharbeiten", "Fassade"]
    assert rubriken[0].description == "Rinne und Ablauf"
    assert rubriken[1].description is None

    zeilen = sorted(q.lines.all(), key=lambda line: line.position_number)
    assert zeilen[0].rubrik_id == rubriken[0].id
    assert zeilen[2].rubrik_id == rubriken[1].id
    assert q.net_total == Decimal("90.00")   # 20 + 30 + 40


@pytest.mark.django_db
def test_position_verweist_auf_unbekannten_abschnitt(app_user):
    """Ohne Vorabpruefung liefe das in einen IntegrityError (500) statt 422."""
    obj = _property(app_user)
    with pytest.raises(ValueError, match="Abschnitt 3 existiert nicht"):
        beleg_service.create_quote(
            app_user.id, property_id=obj.id, title="X",
            rubriken=[{"title": "Nur einer"}],
            lines=[_mat("A", 1, 10, rubrik=3)],
        )


@pytest.mark.django_db
def test_abschnitt_ohne_titel_wird_abgewiesen(app_user):
    obj = _property(app_user)
    with pytest.raises(ValueError, match="title"):
        beleg_service.create_quote(
            app_user.id, property_id=obj.id, title="X",
            rubriken=[{"title": "  "}], lines=[_mat("A", 1, 10)],
        )


@pytest.mark.django_db
def test_abschnitte_stehen_im_snapshot(app_user):
    obj = _property(app_user)
    q = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="Snapshot", quote_date="2026-07-01",
        rubriken=[{"title": "Dach", "description": "Oben"}],
        lines=[_mat("Rinne", 1, 100, rubrik=1)],
    )
    versendet = beleg_service.send_quote(app_user.id, quote_id=q.id)
    snap = versendet.billing_snapshot
    assert snap["rubriken"] == [
        {"position_number": 1, "title": "Dach", "description": "Oben"}
    ]
    assert snap["lines"][0]["rubrik"] == 1


# --- Kalkulationsuebersicht -------------------------------------------------

@pytest.mark.django_db
def test_kalkulation_je_abschnitt(app_user):
    obj = _property(app_user)
    q = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="Kalkulation",
        rubriken=[{"title": "Dach"}, {"title": "Fassade"}],
        lines=[
            # Dach: VK 100, EK 60 -> DB 40, Marge 40 %
            _mat("Rinne", 2, 50, rubrik=1, unit_cost=30),
            # Fassade: VK 40, EK 30 -> DB 10, Marge 25 %
            _mat("Putz", 4, 10, rubrik=2, unit_cost="7.50"),
            # Alternative: separat ausgewiesen, nicht in netto
            _mat("Kupfer", 1, 500, rubrik=1, unit_cost=400, line_kind="ALTERNATIV"),
        ],
    )
    k = beleg_service.quote_kalkulation(q.id)
    dach, fassade = k["abschnitte"]

    assert dach["title"] == "Dach"
    assert dach["netto"] == Decimal("100.00")
    assert dach["ek"] == Decimal("60.00")
    assert dach["deckungsbeitrag"] == Decimal("40.00")
    assert dach["marge_prozent"] == Decimal("40.00")
    assert dach["alternativ_netto"] == Decimal("500.00")
    assert dach["ek_vollstaendig"] is True

    assert fassade["netto"] == Decimal("40.00")
    assert fassade["ek"] == Decimal("30.00")
    assert fassade["marge_prozent"] == Decimal("25.00")

    assert k["gesamt"]["netto"] == Decimal("140.00")
    assert k["gesamt"]["deckungsbeitrag"] == Decimal("50.00")


@pytest.mark.django_db
def test_kalkulation_ohne_ek_raet_keine_marge(app_user):
    """Fehlt der EK auch nur einer Position, wird die Marge NICHT geschaetzt.

    Eine 0 saehe aus wie 'kein Gewinn'; ek_vollstaendig=False sagt dem UI, dass
    die Zahl schlicht nicht bekannt ist.
    """
    obj = _property(app_user)
    q = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="Ohne EK",
        lines=[_mat("Mit EK", 1, 100, unit_cost=60), _mat("Ohne EK", 1, 50)],
    )
    k = beleg_service.quote_kalkulation(q.id)
    ohne = k["abschnitte"][0]
    assert ohne["rubrik"] is None
    assert ohne["title"] == "Ohne Abschnitt"
    assert ohne["positionen"] == 2
    assert ohne["positionen_ohne_ek"] == 1
    assert ohne["ek_vollstaendig"] is False
    assert ohne["deckungsbeitrag"] is None
    assert ohne["marge_prozent"] is None


@pytest.mark.django_db
def test_markup_wird_aus_ek_und_vk_abgeleitet(app_user):
    obj = _property(app_user)
    q = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="Aufschlag",
        lines=[_mat("Ware", 1, 150, unit_cost=100)],
    )
    line = QuoteLine.objects.get(quote_id=q.id, position_number=1)
    assert line.unit_cost == Decimal("100.00")
    assert line.markup_percent == Decimal("50.000")   # (150-100)/100


@pytest.mark.django_db
def test_negativer_ek_wird_abgewiesen(app_user):
    obj = _property(app_user)
    with pytest.raises(ValueError, match="unit_cost"):
        beleg_service.create_quote(
            app_user.id, property_id=obj.id, title="X",
            lines=[_mat("A", 1, 10, unit_cost=-5)],
        )


@pytest.mark.django_db
def test_arbeitszeit_wird_je_abschnitt_summiert(app_user):
    obj = _property(app_user)
    q = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="Zeiten",
        rubriken=[{"title": "Montage"}],
        lines=[
            {"line_type": "ARBEITSZEIT", "description": "Monteur", "quantity": "3.5",
             "unit": "h", "unit_price": 60, "tax_code": "DE_19", "rubrik": 1},
            _mat("Material", 1, 20, rubrik=1),
        ],
    )
    k = beleg_service.quote_kalkulation(q.id)
    assert k["abschnitte"][0]["arbeitszeit"] == Decimal("3.500")
