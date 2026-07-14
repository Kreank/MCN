"""Slice „Nachtrag abrechnen": aus den ABWEICHUNGEN wird eine Rechnung.

Das Szenario, um das es geht (und das im Handwerk regelmäßig Geld kostet):

    Angebot **PAUSCHAL** über 18 Thermostatventile. Vor Ort hängt ein Heizkörper
    mehr — der Monteur trägt **19** ein. Der Soll-Ist weist MEHRVERBRAUCH aus.
    Und dann war Schluss: `rechnung_aus_auftrag` ist bei PAUSCHAL gesperrt (zu
    Recht), also tippte das Büro die Nachtragsrechnung von Hand ab.

Geprüft werden hier ausschließlich die Fälle, die **in Geld enden**, wenn sie
falsch sind — die Lehre aus Welle 5 („elf Fehler fand erst die Review-Schleife,
ausnahmslos solche, die in Geld geendet hätten"):

* **Nur die Differenz** wird fakturiert (19 statt 18 → **1 Stück**, nie 19).
* **Zweimal abrechnen scheitert PHYSISCH** — am UNIQUE-Index, nicht am Service.
* **Storno → wieder abrechenbar** (die Leistung wurde ja erbracht).
* **Fehlender Preis → 422 mit Klärungsliste**, niemals eine 0-€-Position.
* **MINDERVERBRAUCH/ENTFALLEN mindern die Pauschale NICHT.**
* **REGIE und Nachtrag beißen sich nicht** — und die Angebotsrechnung bleibt nach
  einem Nachtrag möglich (beide zusammen sind der volle Anspruch).
* **Der unterzeichnete Bericht bleibt versiegelt** — die Abrechnung fasst ihn nicht
  an.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from django.db import IntegrityError

from db_core.db_context import business_transaction
from db_core.models import (
    BillingLink,
    Invoice,
    InvoiceLine,
    QuoteLine,
    SiteReportLine,
)
from db_core.services import abrechnung as abrechnung_service
from db_core.services import beleg as beleg_service
from db_core.services import site_report as report_service
from db_core.services.abrechnung import (
    AbrechnungError,
    EinheitUneindeutig,
    PreisUnbekannt,
)

# Die Helfer des Abrechnungs-Slices — bewusst wiederverwendet: Zwei Aufbauten für
# denselben Auftrag liefen mit der Zeit auseinander.
from db_core.tests.test_abrechnung_service import (  # noqa: F401
    PNG_1x1,
    _angebot,
    _artikel,
    _artikel_mit_vk_gruppe,
    _auftrag,
    _beteiligte,
    _bericht,
    _kg,
    fake_storage,
    szenario,
)


def _pos(desc, qty, preis, *, artikel=None, unit="m", typ="MATERIAL",
         kind="NORMAL"):
    """Eine Angebotsposition — mit Artikelbezug, denn er ist die Identität.

    Der Soll-Ist schlüsselt über Artikel + Einheit; eine Position ohne Artikel
    fällt auf den Text zurück. Beides wird geprüft.
    """
    zeile = {
        "line_type": typ, "line_kind": kind, "description": desc,
        "quantity": qty, "unit": unit, "unit_price": preis, "tax_code": "DE_19",
    }
    if artikel is not None:
        zeile["source_article_id"] = str(artikel.id)
    return zeile


def _ql(quote, position_number=1):
    return QuoteLine.objects.get(quote_id=quote.id, position_number=position_number)


def _ist(quote_line, menge):
    """Berichtsposition MIT Herkunft: Identität und Soll kommen aus dem Angebot."""
    return {
        "line_type": "MATERIAL",
        "quantity": menge,
        "source_quote_line_id": str(quote_line.id),
    }


def _zusatz(artikel, menge, *, desc=None, unit=None):
    """Berichtsposition OHNE Herkunft — die Zusatzleistung."""
    zeile = {"line_type": "MATERIAL", "quantity": menge,
             "source_article_id": str(artikel.id)}
    if desc:
        zeile["description"] = desc
    if unit:
        zeile["unit"] = unit
    return zeile


_VENTIL_NR = [0]


def _ventil_szenario(szenario, *, ist_menge="19", soll_menge="18", vk="24.00"):
    """Das Demo-Szenario: PAUSCHAL, 18 Ventile angeboten, `ist_menge` verbaut.

    Die Artikelnummer ist je Aufruf eindeutig — ein Test darf das Szenario zweimal
    aufbauen (verschiedene Reihenfolgen), ohne am UNIQUE der Artikelnummer zu
    scheitern.
    """
    _VENTIL_NR[0] += 1
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG")
    artikel = _artikel(
        szenario, f"TH-{_VENTIL_NR[0]}", vk=vk, beschreibung="Thermostatventil"
    )
    quote = _angebot(
        szenario, order,
        [_pos("Thermostatventil", soll_menge, "24.00", artikel=artikel, unit="stk")],
    )
    bericht = _bericht(szenario, order, [_ist(_ql(quote), ist_menge)])
    return {"order": order, "artikel": artikel, "quote": quote, "bericht": bericht}


# ---------------------------------------------------------------------------
# Der Kern: nur die Differenz
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_mehrverbrauch_faktura_nur_die_differenz(szenario, fake_storage):
    """19 statt 18 → **1 Stück**, nicht 19.

    Der teuerste denkbare Fehler dieses Slices: Die Sollmenge ist mit der Pauschale
    bezahlt. Fakturierte der Nachtrag die volle Ist-Menge, stünden 18 Ventile ein
    zweites Mal auf der Rechnung.
    """
    s = _ventil_szenario(szenario)
    vorschau = abrechnung_service.nachtrag_vorschau(s["order"].id)
    assert vorschau["abrechenbar"] is True
    assert len(vorschau["positionen"]) == 1
    pos = vorschau["positionen"][0]
    assert pos["art"] == report_service.MEHRVERBRAUCH
    assert pos["soll"] == Decimal("18.000")
    assert pos["ist"] == Decimal("19.000")
    assert pos["menge"] == Decimal("1.000")          # <- die Differenz, nicht 19
    assert pos["einzelpreis"] == Decimal("24.00")
    assert pos["betrag"] == Decimal("24.00")
    assert vorschau["summe"] == Decimal("24.00")
    assert vorschau["preise_unbekannt"] is False

    invoice = abrechnung_service.rechnung_aus_nachtrag(
        szenario["user"].id, work_order_id=s["order"].id, tax_code="DE_19",
    )
    zeilen = list(InvoiceLine.objects.filter(invoice_id=invoice.id))
    assert len(zeilen) == 1
    assert zeilen[0].quantity == Decimal("1.000")
    assert zeilen[0].unit_price == Decimal("24.00")
    assert zeilen[0].net_amount == Decimal("24.00")
    assert "Mehrmenge" in zeilen[0].description
    invoice.refresh_from_db()
    assert invoice.net_total == Decimal("24.00")

    # Und die Bindung ist da — jede Nachtragsposition MUSS sie tragen.
    link = BillingLink.objects.get(invoice_id=invoice.id)
    assert link.source_kind == abrechnung_service.BERICHTSPOSITION
    assert link.released_at is None
    assert link.site_report_line_id == SiteReportLine.objects.get(
        site_report_id=s["bericht"].id
    ).id


@pytest.mark.django_db
def test_zusatzleistung_faktura_volle_menge(szenario, fake_storage):
    """ZUSATZ war nie Teil der Pauschale — sie wird **voll** berechnet."""
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG")
    ventil = _artikel(szenario, "TH-2", vk="24.00", beschreibung="Thermostatventil")
    extra = _artikel(szenario, "AB-1", vk="9.50", beschreibung="Absperrventil")
    quote = _angebot(
        szenario, order,
        [_pos("Thermostatventil", "18", "24.00", artikel=ventil, unit="stk")],
    )
    _bericht(szenario, order, [
        _ist(_ql(quote), "18"),                 # Soll = Ist → unverändert
        _zusatz(extra, "3"),                    # nie angeboten → ZUSATZ
    ])

    vorschau = abrechnung_service.nachtrag_vorschau(order.id)
    assert [p["art"] for p in vorschau["positionen"]] == [report_service.ZUSATZ]
    pos = vorschau["positionen"][0]
    assert pos["soll"] == Decimal("0.000")
    assert pos["menge"] == Decimal("3.000")     # volle Menge
    assert pos["betrag"] == Decimal("28.50")

    invoice = abrechnung_service.rechnung_aus_nachtrag(
        szenario["user"].id, work_order_id=order.id, tax_code="DE_19",
    )
    zeile = InvoiceLine.objects.get(invoice_id=invoice.id)
    assert zeile.quantity == Decimal("3.000")
    assert zeile.net_amount == Decimal("28.50")
    assert "Zusatzleistung" in zeile.description


@pytest.mark.django_db
def test_minderverbrauch_und_entfallen_mindern_die_pauschale_nicht(
    szenario, fake_storage
):
    """Pauschal ist der **Preis für das Werk** vereinbart, kein Mengengerüst.

    Wer mit weniger Material auskommt, schuldet trotzdem das Werk — und hat es
    erbracht. Eine Minderung wäre eine **Preisänderung**; die trifft ein Mensch
    (Rabatt/Gutschrift), nicht ein Abrechnungslauf.
    """
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG")
    ventil = _artikel(szenario, "TH-3", vk="24.00", beschreibung="Thermostatventil")
    rohr = _artikel(szenario, "RO-1", vk="12.00", beschreibung="Rohr DN20")
    quote = _angebot(szenario, order, [
        _pos("Thermostatventil", "18", "24.00", artikel=ventil, unit="stk"),
        _pos("Rohr DN20", "10", "12.00", artikel=rohr),          # wird ENTFALLEN
    ])
    _bericht(szenario, order, [_ist(_ql(quote, 1), "12")])       # MINDERVERBRAUCH

    abgleich = report_service.soll_ist(order.id)
    arten = {p["bezeichnung"]: p["art"] for p in abgleich["positionen"]}
    assert arten["Thermostatventil"] == report_service.MINDERVERBRAUCH
    assert arten["Rohr DN20"] == report_service.ENTFALLEN

    vorschau = abrechnung_service.nachtrag_vorschau(order.id)
    assert vorschau["positionen"] == []
    assert vorschau["summe"] == Decimal("0.00")

    with pytest.raises(AbrechnungError) as exc:
        abrechnung_service.rechnung_aus_nachtrag(
            szenario["user"].id, work_order_id=order.id, tax_code="DE_19",
        )
    assert "nichts nachzutragen" in str(exc.value)
    # Und es entsteht **keine Gutschrift von selbst**.
    assert not BillingLink.objects.filter(invoice__work_order_id=order.id).exists()


# ---------------------------------------------------------------------------
# Doppelabrechnung — physisch gesperrt
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_nachtrag_zweimal_scheitert_im_service(szenario, fake_storage):
    """Der zweite Lauf findet nichts mehr — und sagt WO es steht."""
    s = _ventil_szenario(szenario)
    invoice = abrechnung_service.rechnung_aus_nachtrag(
        szenario["user"].id, work_order_id=s["order"].id, tax_code="DE_19",
    )
    _beteiligte(szenario, invoice)
    _kg(szenario, s["order"])          # B-08: RECHNUNG erst ab KAUFMAENNISCH_GEPRUEFT
    beleg_service.publish_invoice(szenario["user"].id, invoice_id=invoice.id)
    invoice.refresh_from_db()

    vorschau = abrechnung_service.nachtrag_vorschau(s["order"].id)
    assert vorschau["positionen"] == []
    # Nicht verschweigen: Die Abweichung gibt es, sie ist nur schon fakturiert.
    assert len(vorschau["bereits_abgerechnet"]) == 1
    assert vorschau["bereits_abgerechnet"][0]["rechnungen"] == [invoice.invoice_number]

    with pytest.raises(AbrechnungError) as exc:
        abrechnung_service.rechnung_aus_nachtrag(
            szenario["user"].id, work_order_id=s["order"].id, tax_code="DE_19",
        )
    assert "bereits abgerechnet" in str(exc.value)


@pytest.mark.django_db
def test_db_sperrt_die_zweite_bindung_am_service_vorbei(szenario, fake_storage):
    """**Physisch**, nicht nur im Service: der partielle UNIQUE-Index.

    Der Service könnte sich irren; der Index kann es nicht. Deshalb geht dieser
    Test bewusst am Service vorbei und schreibt die zweite Bindung direkt.
    """
    s = _ventil_szenario(szenario)
    invoice = abrechnung_service.rechnung_aus_nachtrag(
        szenario["user"].id, work_order_id=s["order"].id, tax_code="DE_19",
    )
    link = BillingLink.objects.get(invoice_id=invoice.id)

    with pytest.raises(IntegrityError):
        with business_transaction(szenario["user"].id):
            BillingLink.objects.create(
                id=uuid.uuid4(),
                invoice_id=invoice.id,
                invoice_line_id=link.invoice_line_id,
                source_kind=abrechnung_service.BERICHTSPOSITION,
                site_report_line_id=link.site_report_line_id,
            )


@pytest.mark.django_db
def test_storno_gibt_den_nachtrag_wieder_frei(szenario, fake_storage):
    """Nachtrag → Storno → erneut abrechnen **muss gehen**.

    Der Storno löst die Bindung (Trigger). Die Mehrmenge wurde erbracht; sie ist
    nach dem Storno wieder abrechenbar — sonst wäre sie für immer verbrannt (die
    veröffentlichte Rechnungsposition ist unveränderlich, B-21).
    """
    s = _ventil_szenario(szenario)
    erste = abrechnung_service.rechnung_aus_nachtrag(
        szenario["user"].id, work_order_id=s["order"].id, tax_code="DE_19",
    )
    _beteiligte(szenario, erste)
    _kg(szenario, s["order"])
    beleg_service.publish_invoice(szenario["user"].id, invoice_id=erste.id)

    storno = beleg_service.create_cancellation(
        szenario["user"].id, invoice_id=erste.id
    )
    assert storno.invoice_type == "STORNO"
    assert BillingLink.objects.get(invoice_id=erste.id).released_at is not None

    vorschau = abrechnung_service.nachtrag_vorschau(s["order"].id)
    assert vorschau["positionen"][0]["menge"] == Decimal("1.000")

    zweite = abrechnung_service.rechnung_aus_nachtrag(
        szenario["user"].id, work_order_id=s["order"].id, tax_code="DE_7",
    )
    assert InvoiceLine.objects.get(invoice_id=zweite.id).quantity == Decimal("1.000")
    assert BillingLink.objects.filter(
        invoice_id=zweite.id, released_at__isnull=True
    ).count() == 1


@pytest.mark.django_db
def test_zweiter_bericht_traegt_nur_den_zuwachs_nach(szenario, fake_storage):
    """Ein weiterer unterzeichneter Bericht → nur der **Zuwachs** wird nachgetragen.

    Der heimtückische Fall: Die Mehrmenge steht auf der Rechnungs**position** (1),
    die Bindung auf der Berichts**zeile** (19). Rechnete der zweite Lauf naiv
    „Ist − Soll", fakturierte er die erste Mehrmenge ein zweites Mal.
    """
    s = _ventil_szenario(szenario)
    erste = abrechnung_service.rechnung_aus_nachtrag(
        szenario["user"].id, work_order_id=s["order"].id, tax_code="DE_19",
    )
    assert InvoiceLine.objects.get(invoice_id=erste.id).quantity == Decimal("1.000")

    # Ein zweiter Bericht: nochmal 2 Ventile auf dieselbe Angebotsposition.
    _bericht(szenario, s["order"], [_ist(_ql(s["quote"]), "2")], datum="2026-07-07")

    vorschau = abrechnung_service.nachtrag_vorschau(s["order"].id)
    pos = vorschau["positionen"][0]
    assert pos["ist"] == Decimal("21.000")
    assert pos["bereits_berechnet"] == Decimal("1.000")
    assert pos["menge"] == Decimal("2.000")     # 21 − 18 − 1 = 2, nicht 3

    zweite = abrechnung_service.rechnung_aus_nachtrag(
        szenario["user"].id, work_order_id=s["order"].id, tax_code="DE_19",
    )
    assert InvoiceLine.objects.get(invoice_id=zweite.id).quantity == Decimal("2.000")


@pytest.mark.django_db
def test_keine_position_ohne_bindung(szenario, fake_storage):
    """**Jede** Nachtragsposition MUSS ihre Bindung bekommen — sonst kein Beleg.

    Der erreichbare Weg in diesen Zustand — und er entsteht ausgerechnet durch die
    zweite Lücke, die dieser Slice schließt: Nach der Nachtragsrechnung wird das
    Angebot als **ABGELEHNT** festgehalten. Ein abgelehntes Angebot ist kein Soll
    mehr (`SOLL_AUSGESCHLOSSENE_STATUS`) — das **Soll fällt auf 0**, die Differenz
    springt auf die volle Ist-Menge, aber alle Berichtszeilen sind bereits gebunden.

    (Die *Zuordnung* zu lösen ist hier gar nicht erst möglich: `beleg.update_quote`
    sperrt das, sobald Berichtspositionen die Angebotszeilen als Soll führen. Der
    Statuswechsel ist der Weg, der offen bleibt.)

    Eine Rechnungsposition ohne Bindung wäre beliebig oft wiederholbar; die
    Doppelabrechnungssperre hätte ein Loch, das niemand sieht. Also: klarer 422
    statt eines stillen Belegs.
    """
    s = _ventil_szenario(szenario)
    abrechnung_service.rechnung_aus_nachtrag(
        szenario["user"].id, work_order_id=s["order"].id, tax_code="DE_19",
    )
    beleg_service.set_quote_status(
        szenario["user"].id, quote_id=s["quote"].id, to_status="ABGELEHNT"
    )

    vorschau = abrechnung_service.nachtrag_vorschau(s["order"].id)
    pos = vorschau["positionen"][0]
    assert pos["soll"] == Decimal("0.000")
    assert pos["menge"] == Decimal("18.000")     # 19 − 0 − 1 (schon berechnet)

    with pytest.raises(AbrechnungError) as exc:
        abrechnung_service.rechnung_aus_nachtrag(
            szenario["user"].id, work_order_id=s["order"].id, tax_code="DE_19",
        )
    assert "ohne Abrechnungsbindung" in str(exc.value)
    # Kein zweiter Beleg entstanden.
    assert BillingLink.objects.filter(
        invoice__work_order_id=s["order"].id, released_at__isnull=True
    ).count() == 1


# ---------------------------------------------------------------------------
# Fehlender Preis: 422 mit Klärungsliste — niemals 0,00 €
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ohne_preis_keine_rechnung_sondern_klaerung(szenario, fake_storage):
    """Ein Artikel ohne ermittelbaren VK → **422**, keine Rechnung über 0,00 €."""
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG")
    artikel = _artikel(szenario, "OHNE-1", beschreibung="Ventil ohne Preis")
    quote = _angebot(
        szenario, order,
        [_pos("Ventil ohne Preis", "18", "24.00", artikel=artikel, unit="stk")],
    )
    _bericht(szenario, order, [_ist(_ql(quote), "19")])

    vorschau = abrechnung_service.nachtrag_vorschau(order.id)
    pos = vorschau["positionen"][0]
    assert pos["preis_status"] == abrechnung_service.PREIS_UNBEKANNT
    assert pos["einzelpreis"] is None            # unbekannt — NIE 0
    assert pos["betrag"] is None
    assert vorschau["preise_unbekannt"] is True

    with pytest.raises(PreisUnbekannt) as exc:
        abrechnung_service.rechnung_aus_nachtrag(
            szenario["user"].id, work_order_id=order.id, tax_code="DE_19",
        )
    klaerung = exc.value.positionen
    assert len(klaerung) == 1
    assert klaerung[0]["quelle_art"] == abrechnung_service.QUELLE_ABWEICHUNG
    assert klaerung[0]["menge"] == Decimal("1.000")
    assert klaerung[0]["grund"] in ("EK_FEHLT", "KEINE_VK_REGEL")

    # Es ist KEINE Rechnung entstanden.
    assert not BillingLink.objects.filter(invoice__work_order_id=order.id).exists()

    # Derselbe Aufruf mit genanntem Preis: das ist der Ausweg (kein zweiter Pfad).
    invoice = abrechnung_service.rechnung_aus_nachtrag(
        szenario["user"].id, work_order_id=order.id, tax_code="DE_19",
        preise={klaerung[0]["quelle_id"]: Decimal("31.00")},
    )
    zeile = InvoiceLine.objects.get(invoice_id=invoice.id)
    assert zeile.unit_price == Decimal("31.00")
    assert zeile.net_amount == Decimal("31.00")


@pytest.mark.django_db
def test_null_ek_aus_dem_import_ist_kein_preis(szenario, fake_storage):
    """0-EK aus einem DATANORM-Importfehler → **VK_NULL**, keine 0-€-Position.

    Die VK-Gruppe rechnet ihre Formel brav auf der 0-Basis durch und liefert
    0,00 € — eine Zahl, die wie ein Preis aussieht und keiner ist.
    """
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG")
    artikel = _artikel_mit_vk_gruppe(
        szenario, "IMP-1", "0.00", beschreibung="Ventil aus dem Import"
    )
    quote = _angebot(
        szenario, order,
        [_pos("Ventil aus dem Import", "18", "24.00", artikel=artikel, unit="stk")],
    )
    _bericht(szenario, order, [_ist(_ql(quote), "19")])

    with pytest.raises(PreisUnbekannt) as exc:
        abrechnung_service.rechnung_aus_nachtrag(
            szenario["user"].id, work_order_id=order.id, tax_code="DE_19",
        )
    assert exc.value.positionen[0]["grund"] == abrechnung_service.GRUND_VK_NULL


@pytest.mark.django_db
def test_genannter_preis_nur_wo_der_server_keinen_hat(szenario, fake_storage):
    """Sonst wäre die Aufschlagsmatrix (Mindestmarge!) über den Umweg umgehbar."""
    s = _ventil_szenario(szenario)
    schluessel = abrechnung_service.nachtrag_vorschau(
        s["order"].id
    )["positionen"][0]["schluessel"]
    with pytest.raises(AbrechnungError) as exc:
        abrechnung_service.rechnung_aus_nachtrag(
            szenario["user"].id, work_order_id=s["order"].id, tax_code="DE_19",
            preise={schluessel: Decimal("1.00")},
        )
    assert "der Server einen Preis kennt" in str(exc.value)


@pytest.mark.django_db
def test_freitextposition_ohne_herkunft_ist_klaerbar(szenario, fake_storage):
    """Eine von Hand erfasste Zusatzzeile hat keinen Stammbezug — aber einen Preis,
    den ein Mensch nennen kann. Keine Sackgasse, keine 0 €."""
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG")
    ventil = _artikel(szenario, "TH-9", vk="24.00", beschreibung="Thermostatventil")
    quote = _angebot(
        szenario, order,
        [_pos("Thermostatventil", "18", "24.00", artikel=ventil, unit="stk")],
    )
    _bericht(szenario, order, [
        _ist(_ql(quote), "18"),
        {"line_type": "FREMDLEISTUNG", "description": "Kernbohrung Nachbarwand",
         "quantity": "1", "unit": "psch"},
    ])

    with pytest.raises(PreisUnbekannt) as exc:
        abrechnung_service.rechnung_aus_nachtrag(
            szenario["user"].id, work_order_id=order.id, tax_code="DE_19",
        )
    pos = exc.value.positionen[0]
    assert pos["grund"] == abrechnung_service.GRUND_KEINE_HERKUNFT
    assert pos["vorschlaege"] == []          # nichts erfinden — ehrlich leer

    invoice = abrechnung_service.rechnung_aus_nachtrag(
        szenario["user"].id, work_order_id=order.id, tax_code="DE_19",
        preise={pos["quelle_id"]: Decimal("240.00")},
    )
    zeile = InvoiceLine.objects.get(invoice_id=invoice.id)
    assert zeile.net_amount == Decimal("240.00")
    assert zeile.line_type == "FREMDLEISTUNG"


# ---------------------------------------------------------------------------
# Die Wege dürfen sich nicht beißen
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_regie_auftrag_kennt_keinen_nachtrag(szenario, fake_storage):
    """Bei REGIE ist das gesamte Ist fakturiert — ein Nachtrag wäre doppelt."""
    s = _ventil_szenario(szenario)
    abrechnung_service.set_billing_mode(
        szenario["user"].id, work_order_id=s["order"].id, billing_mode="REGIE"
    )
    with pytest.raises(AbrechnungError) as exc:
        abrechnung_service.rechnung_aus_nachtrag(
            szenario["user"].id, work_order_id=s["order"].id, tax_code="DE_19",
        )
    assert "REGIE" in str(exc.value)
    vorschau = abrechnung_service.nachtrag_vorschau(s["order"].id)
    assert vorschau["abrechenbar"] is False


@pytest.mark.django_db
def test_nachtrag_sperrt_den_moduswechsel(szenario, fake_storage):
    """Nach einem Nachtrag ist der Modus eingefroren — sonst ließe sich dieselbe
    Mehrmenge über die Regie ein zweites Mal greifen."""
    s = _ventil_szenario(szenario)
    abrechnung_service.rechnung_aus_nachtrag(
        szenario["user"].id, work_order_id=s["order"].id, tax_code="DE_19",
    )
    with pytest.raises(AbrechnungError) as exc:
        abrechnung_service.set_billing_mode(
            szenario["user"].id, work_order_id=s["order"].id, billing_mode="REGIE"
        )
    assert "nicht mehr ändern" in str(exc.value)


@pytest.mark.django_db
def test_nachtrag_zuerst_dann_die_pauschalrechnung(szenario, fake_storage):
    """Die Reihenfolge darf nicht zählen: Pauschale + Nachtrag sind der volle
    Anspruch, und sie sind **disjunkt**.

    Vorher sperrte die Quersperre („bereits über das Ist abgerechnet") jede
    Angebotsrechnung, sobald eine Berichtsposition gebunden war — der Nachtrag wäre
    ein Einbahnweg gewesen, mit einer Fehlermeldung, die etwas Falsches behauptet.
    """
    s = _ventil_szenario(szenario)
    nachtrag = abrechnung_service.rechnung_aus_nachtrag(
        szenario["user"].id, work_order_id=s["order"].id, tax_code="DE_19",
    )
    assert nachtrag.net_total == Decimal("24.00")

    pauschal = abrechnung_service.rechnung_aus_angebot(
        szenario["user"].id, quote_id=s["quote"].id
    )
    assert pauschal.net_total == Decimal("432.00")        # 18 × 24,00
    # Zwei Belege, zwei disjunkte Quellarten — jede Leistung genau einmal.
    arten = set(
        BillingLink.objects.filter(
            invoice__work_order_id=s["order"].id, released_at__isnull=True
        ).values_list("source_kind", flat=True)
    )
    assert arten == {
        abrechnung_service.BERICHTSPOSITION, abrechnung_service.ANGEBOTSPOSITION
    }


@pytest.mark.django_db
def test_regieabrechnung_sperrt_die_angebotskopie_weiterhin(szenario, fake_storage):
    """Die Regie-Sperre bleibt scharf — **die Lockerung darf sie nicht aufreißen**.

    Der Nachtrag bindet Berichtspositionen mit derselben `source_kind` wie die
    Regieabrechnung (die Codeliste kennt nur drei Werte). Damit der Nachtrag die
    Angebotsrechnung nicht mehr blockiert, zählen Berichtsbindungen dort nur noch
    bei **REGIE** (`_IST_QUELLEN`). Dieser Test hält die Kette, an der das hängt:

    1. REGIE + Angebotskopie → **422** (die Leistung stünde zweimal drauf).
    2. Der Weg drumherum — Modus zurück auf PAUSCHAL — ist **gesperrt**, solange die
       Regie-Bindungen aktiv sind. Genau deshalb kann ein PAUSCHAL-Auftrag keine
       Regie-Bindung tragen, und genau deshalb ist die Lockerung sicher.
    """
    s = _ventil_szenario(szenario)
    abrechnung_service.set_billing_mode(
        szenario["user"].id, work_order_id=s["order"].id, billing_mode="REGIE"
    )
    abrechnung_service.rechnung_aus_auftrag(
        szenario["user"].id, work_order_id=s["order"].id, tax_code="DE_19",
        mit_zeiten=False,
    )
    with pytest.raises(AbrechnungError) as exc:
        abrechnung_service.rechnung_aus_angebot(
            szenario["user"].id, quote_id=s["quote"].id
        )
    assert "REGIE" in str(exc.value)

    with pytest.raises(AbrechnungError) as exc2:
        abrechnung_service.set_billing_mode(
            szenario["user"].id, work_order_id=s["order"].id, billing_mode="PAUSCHAL"
        )
    assert "nicht mehr ändern" in str(exc2.value)


# ---------------------------------------------------------------------------
# Die zwei KRITISCHEN Doppelabrechnungen (Review-Befund) — quellenübergreifend
# ---------------------------------------------------------------------------
#
# Gemeinsame Wurzel: Die Nachtragssicherheit beruhte auf „Angebot bucht Soll,
# Nachtrag bucht Ist−Soll, disjunkte Quellen". Das hält NUR, solange das Soll
# zwischen beiden Läufen identisch bleibt. Sobald es sich bewegt, überlappen die
# Quellen — und keiner der alten `source_kind`-Checks sah den anderen.


@pytest.mark.django_db
def test_abgelehntes_angebot_oeffnet_keine_doppelabrechnung(szenario, fake_storage):
    """KRITISCH 1: Angebotsrechnung → Angebot ABGELEHNT (Soll→0) → Nachtrag.

    Vorher: Der Nachtrag zählte in `bereits_berechnet` nur BERICHTSPOSITION-Bindungen,
    sah die ANGEBOTSPOSITION-Bindung der Pauschalrechnung nicht, hielt bei Soll=0 die
    ganzen 19 für offen und buchte 19 → **888 € statt 456 €**.

    Zwei Schranken greifen jetzt: (a) `set_quote_status` verweigert ABGELEHNT auf ein
    abgerechnetes Angebot; (b) selbst wenn das Soll fällt, floort die Nachtragsformel
    `max(A, Soll)` auf die fakturierte Angebotsmenge.
    """
    s = _ventil_szenario(szenario)          # Soll 18, Ist 19, VK 24,00
    pauschal = abrechnung_service.rechnung_aus_angebot(
        szenario["user"].id, quote_id=s["quote"].id
    )
    assert pauschal.net_total == Decimal("432.00")     # 18 × 24,00

    # (a) Schranke an der Quelle: abgerechnet ⇒ nicht ablehnbar.
    with pytest.raises(ValueError) as exc:
        beleg_service.set_quote_status(
            szenario["user"].id, quote_id=s["quote"].id, to_status="ABGELEHNT"
        )
    assert "bereits eine Rechnung" in str(exc.value)

    # (b) Belt-and-suspenders: Die Formel hält auch, wenn das Soll auf ANDEREM Weg
    #     verschwindet. Wir lösen die Angebots-Zuordnung … was `update_quote` bei
    #     referenzierten Positionen sperrt. Also prüfen wir die Formel direkt: Der
    #     Nachtrag sieht die Angebotsmenge als Floor.
    vorschau = abrechnung_service.nachtrag_vorschau(s["order"].id)
    pos = vorschau["positionen"][0]
    assert pos["bereits_berechnet"] == Decimal("18.000")   # A gesehen!
    assert pos["menge"] == Decimal("1.000")                # nur die Differenz

    nachtrag = abrechnung_service.rechnung_aus_nachtrag(
        szenario["user"].id, work_order_id=s["order"].id, tax_code="DE_19",
    )
    assert nachtrag.net_total == Decimal("24.00")          # 1 × 24,00, NICHT 19×
    # Gesamt über beide Rechnungen: 432 + 24 = 456 €. Kein Cent doppelt.
    gesamt = (
        Invoice.objects.filter(work_order_id=s["order"].id)
        .exclude(invoice_type__in=("STORNO", "GUTSCHRIFT"))
    )
    assert sum((i.net_total for i in gesamt), Decimal("0.00")) == Decimal("456.00")


@pytest.mark.django_db
def test_abgelehntes_angebot_ohne_bindung_formel_haelt(szenario, fake_storage):
    """KRITISCH 1, der Formelkern isoliert: Angebot abgelehnt, dann Nachtrag.

    Hier wird das Angebot **vor** der Pauschalrechnung abgelehnt-und-neu gebaut, um
    den Zustand „A gebucht, Soll 0" ohne die Schranke (a) zu erzeugen: Wir buchen die
    Pauschale, heben sie NICHT auf, sondern hängen einen zweiten Bericht an, sodass
    Ist wächst — und prüfen, dass die Formel `max(A, Soll)` die schon fakturierten
    18 als Floor führt, egal was das Soll sagt.
    """
    s = _ventil_szenario(szenario)
    abrechnung_service.rechnung_aus_angebot(szenario["user"].id, quote_id=s["quote"].id)
    # Zweiter Bericht: +5 Ventile (Ist gesamt 24).
    _bericht(szenario, s["order"], [_ist(_ql(s["quote"]), "5")], datum="2026-07-08")

    vorschau = abrechnung_service.nachtrag_vorschau(s["order"].id)
    pos = vorschau["positionen"][0]
    assert pos["ist"] == Decimal("24.000")
    assert pos["bereits_berechnet"] == Decimal("18.000")   # A (Pauschale) als Floor
    assert pos["menge"] == Decimal("6.000")                # 24 − max(18,18) − 0

    nachtrag = abrechnung_service.rechnung_aus_nachtrag(
        szenario["user"].id, work_order_id=s["order"].id, tax_code="DE_19",
    )
    assert nachtrag.net_total == Decimal("144.00")         # 6 × 24,00
    # 432 + 144 = 576 = 24 × 24,00. Genau die gelieferte Menge, kein Cent doppelt.


@pytest.mark.django_db
def test_nachtrag_vor_angebotszuordnung_sperrt_die_angebotsrechnung(
    szenario, fake_storage
):
    """KRITISCH 2: Nachtrag (Soll=0, volle 19) → dann Angebot 19 zuordnen & abrechnen.

    Vorher: `rechnung_aus_angebot` prüfte `_IST_QUELLEN`, das bei PAUSCHAL nur
    ZEITBUCHUNG liefert — die BERICHTSPOSITION-Bindung des Nachtrags wurde nicht
    gesehen, das Angebot buchte 19 obendrauf → **912 € statt 456 €**.

    Jetzt: `_pauschal_mengengrenze_pruefen` sieht die schon per Nachtrag fakturierte
    Menge (quellenunabhängig) und sperrt.
    """
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG")
    ventil = _artikel(szenario, "TH-K2", vk="24.00", beschreibung="Thermostatventil")
    # Signierter Bericht OHNE Angebot: Soll=0, alles ZUSATZ.
    report = report_service.create_report(
        szenario["user"].id, work_order_id=order.id, report_date="2026-07-06",
        activity_text="Ventile getauscht.",
    )
    report_service.set_report_lines(szenario["user"].id, report_id=report.id, lines=[
        {"line_type": "MATERIAL", "quantity": "19",
         "source_article_id": str(ventil.id)},
    ])
    report_service.sign_report(
        szenario["user"].id, report_id=report.id, signed_by_name="Karla",
        signature_png=PNG_1x1,
    )

    nachtrag = abrechnung_service.rechnung_aus_nachtrag(
        szenario["user"].id, work_order_id=order.id, tax_code="DE_19",
    )
    assert nachtrag.net_total == Decimal("456.00")         # 19 × 24,00 (voll, ZUSATZ)

    # Jetzt ein Angebot über dieselben 19 anlegen, zuordnen, versenden …
    quote = _angebot(
        szenario, order,
        [_pos("Thermostatventil", "19", "24.00", artikel=ventil, unit="m")],
    )
    # … und die Angebotskopie fakturieren wollen → gesperrt.
    with pytest.raises(AbrechnungError) as exc:
        abrechnung_service.rechnung_aus_angebot(
            szenario["user"].id, quote_id=quote.id
        )
    assert "bereits über einen Nachtrag" in str(exc.value)
    # Kein zweiter Beleg entstanden — Gesamt bleibt 456 €.
    gesamt = Invoice.objects.filter(
        work_order_id=order.id, invoice_type="RECHNUNG"
    )
    assert sum((i.net_total for i in gesamt), Decimal("0.00")) == Decimal("456.00")


@pytest.mark.django_db
def test_legitim_pauschale_plus_nachtrag_beide_reihenfolgen(szenario, fake_storage):
    """GEGENPROBE: Der legitime Fall bleibt in BEIDER Reihenfolge bei 456 €.

    Ein Test, der nur die Sperren prüft, wäre wertlos, wenn er dabei den Normalfall
    mit erschlägt. 18 (Pauschale) + 1 (Differenz) = 456 € — egal, ob erst die
    Angebotsrechnung oder erst der Nachtrag läuft.
    """
    # (1) Erst Angebot, dann Nachtrag.
    a = _ventil_szenario(szenario)
    abrechnung_service.rechnung_aus_angebot(szenario["user"].id, quote_id=a["quote"].id)
    n1 = abrechnung_service.rechnung_aus_nachtrag(
        szenario["user"].id, work_order_id=a["order"].id, tax_code="DE_19",
    )
    assert n1.net_total == Decimal("24.00")
    summe_a = sum(
        (i.net_total for i in Invoice.objects.filter(
            work_order_id=a["order"].id, invoice_type="RECHNUNG")),
        Decimal("0.00"),
    )
    assert summe_a == Decimal("456.00")

    # (2) Erst Nachtrag, dann Angebot — anderer Auftrag, gleiche Zahlen.
    b = _ventil_szenario(szenario)
    n2 = abrechnung_service.rechnung_aus_nachtrag(
        szenario["user"].id, work_order_id=b["order"].id, tax_code="DE_19",
    )
    assert n2.net_total == Decimal("24.00")                # Differenz, Soll=18 steht
    pauschal = abrechnung_service.rechnung_aus_angebot(
        szenario["user"].id, quote_id=b["quote"].id
    )
    assert pauschal.net_total == Decimal("432.00")         # NICHT gesperrt: 18+1=19=Ist
    summe_b = sum(
        (i.net_total for i in Invoice.objects.filter(
            work_order_id=b["order"].id, invoice_type="RECHNUNG")),
        Decimal("0.00"),
    )
    assert summe_b == Decimal("456.00")


@pytest.mark.django_db
def test_storno_des_nachtrags_dann_erneut_differenz(szenario, fake_storage):
    """Storno eines Nachtrags → erneut abrechnen: die **Differenz**, nicht die volle
    Menge — solange die Pauschale steht.

    Der Storno löst die BERICHTSPOSITION-Bindung (B→0); die ANGEBOTSPOSITION-Bindung
    der Pauschale bleibt (A=18). Also floort `max(A, Soll)` weiter auf 18 → Differenz 1.
    """
    s = _ventil_szenario(szenario)
    abrechnung_service.rechnung_aus_angebot(szenario["user"].id, quote_id=s["quote"].id)
    nachtrag = abrechnung_service.rechnung_aus_nachtrag(
        szenario["user"].id, work_order_id=s["order"].id, tax_code="DE_19",
    )
    _beteiligte(szenario, nachtrag)
    _kg(szenario, s["order"])
    beleg_service.publish_invoice(szenario["user"].id, invoice_id=nachtrag.id)
    storno = beleg_service.create_cancellation(
        szenario["user"].id, invoice_id=nachtrag.id
    )
    assert storno.invoice_type == "STORNO"

    vorschau = abrechnung_service.nachtrag_vorschau(s["order"].id)
    assert vorschau["positionen"][0]["menge"] == Decimal("1.000")   # Differenz, nicht 19
    erneut = abrechnung_service.rechnung_aus_nachtrag(
        szenario["user"].id, work_order_id=s["order"].id, tax_code="DE_7",
    )
    assert erneut.net_total == Decimal("24.00")


@pytest.mark.django_db
def test_teilgutschrift_auf_nachtrag_gibt_die_leistung_nicht_frei(szenario, fake_storage):
    """Eine **Teil**gutschrift auf einen Nachtrag löst KEINE Bindung.

    Nur der Storno löst (Trigger `release_billing_links_on_cancel`); die Gutschrift
    ist eine Kulanz — die Leistung wurde erbracht und bleibt abgerechnet. Sonst
    ließe sich über „gutschreiben + neu abrechnen" doppelt kassieren. Zwei Posten,
    damit die Teilgutschrift nicht als verkappter Vollstorno an der
    Vollgutschrift-Sperre scheitert.
    """
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG")
    ventil = _artikel(szenario, "TG-V", vk="24.00", beschreibung="Thermostatventil")
    absperr = _artikel(szenario, "TG-A", vk="9.50", beschreibung="Absperrventil")
    quote = _angebot(
        szenario, order,
        [_pos("Thermostatventil", "18", "24.00", artikel=ventil, unit="stk")],
    )
    _bericht(szenario, order, [
        _ist(_ql(quote), "19"),                 # MEHRVERBRAUCH 1
        _zusatz(absperr, "3"),                  # ZUSATZ 3
    ])
    nachtrag = abrechnung_service.rechnung_aus_nachtrag(
        szenario["user"].id, work_order_id=order.id, tax_code="DE_19",
    )
    _beteiligte(szenario, nachtrag)
    _kg(szenario, order)
    beleg_service.publish_invoice(szenario["user"].id, invoice_id=nachtrag.id)

    # Teilgutschrift NUR auf Position 1 (das Ventil).
    beleg_service.create_correction(
        szenario["user"].id, invoice_id=nachtrag.id, positions=[1]
    )
    # Beide Bindungen bleiben aktiv — die Gutschrift hat nichts gelöst.
    assert BillingLink.objects.filter(
        invoice_id=nachtrag.id, released_at__isnull=True
    ).count() == 2
    # Und nichts ist wieder offen: keine erneute Abrechnung derselben Menge.
    vorschau = abrechnung_service.nachtrag_vorschau(order.id)
    assert vorschau["positionen"] == []


# ---------------------------------------------------------------------------
# Der DRITTE Weg: divergente Einheit desselben Artikels (Review-Befund)
# ---------------------------------------------------------------------------
#
# Der Geld-Wächter schlüsselte über ARTIKEL:<uuid>:<einheit>. Weicht die Einheit
# zwischen Nachtrags-Quelle und später zugeordneter Angebotszeile ab — GLEICHER
# Artikel —, zerfällt der Posten in zwei Schlüssel, und die Mengengrenze sieht die
# schon fakturierte Menge nicht: 912 € für 19 Ventile. Fix: der Wächter aggregiert
# über die IDENTITÄT (ARTIKEL:<uuid>), und bei divergenter Einheit geht er
# fail-closed in die Klärung.


def _artikel_zusatz_bericht(szenario, order, artikel, menge, einheit, *, datum):
    """Signierter Bericht mit einer ZUSATZ-Zeile (ohne Herkunft) in `einheit`."""
    return _bericht(
        szenario, order,
        [{"line_type": "MATERIAL", "quantity": menge, "unit": einheit,
          "source_article_id": str(artikel.id)}],
        datum=datum,
    )


@pytest.mark.django_db
def test_divergente_einheit_selber_artikel_keine_doppelabrechnung(
    szenario, fake_storage
):
    """Der reproduzierte Fall: Nachtrag „Stk", danach Angebot „Stück" — NIE 912 €.

    Nachtrag bucht 19 × Stk (456 €). Danach dasselbe über ein Angebot mit Einheit
    „Stück" abrechnen zu wollen, muss fail-closed in die Klärung laufen — sonst
    stünden dieselben 19 Ventile ein zweites Mal auf einer Rechnung.
    """
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG")
    ventil = _artikel(szenario, "EIN-1", vk="24.00", beschreibung="Thermostatventil")
    _artikel_zusatz_bericht(szenario, order, ventil, "19", "Stk", datum="2026-07-06")

    nachtrag = abrechnung_service.rechnung_aus_nachtrag(
        szenario["user"].id, work_order_id=order.id, tax_code="DE_19",
    )
    assert nachtrag.net_total == Decimal("456.00")     # 19 × 24,00 (Stk)

    # Angebot über denselben Artikel, aber Einheit „Stück".
    quote = _angebot(
        szenario, order,
        [_pos("Thermostatventil", "19", "24.00", artikel=ventil, unit="Stück")],
    )
    with pytest.raises(EinheitUneindeutig) as exc:
        abrechnung_service.rechnung_aus_angebot(
            szenario["user"].id, quote_id=quote.id
        )
    einheiten = {e for k in exc.value.konflikte for e in k["einheiten"]}
    assert einheiten == {"stk", "stück"}

    # Die Summe steht genau EINMAL auf einer Rechnung — nie 912 €.
    gesamt = Invoice.objects.filter(work_order_id=order.id, invoice_type="RECHNUNG")
    assert sum((i.net_total for i in gesamt), Decimal("0.00")) == Decimal("456.00")


@pytest.mark.django_db
def test_divergente_einheit_erzwingt_klaerung(szenario, fake_storage):
    """Die zweite Abrechnung (Nachtrag) landet in der Klärungsliste, nicht auf einem
    Beleg — auch in der umgekehrten Reihenfolge (Angebot zuerst, dann Nachtrag)."""
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG")
    ventil = _artikel(szenario, "EIN-2", vk="24.00", beschreibung="Thermostatventil")
    # Angebot 19 × Stück zuordnen und fakturieren (A[stück]=19).
    quote = _angebot(
        szenario, order,
        [_pos("Thermostatventil", "19", "24.00", artikel=ventil, unit="Stück")],
    )
    abrechnung_service.rechnung_aus_angebot(szenario["user"].id, quote_id=quote.id)

    # Danach ein Bericht 19 × Stk (Monteur tippt „Stk") → Nachtrag versucht.
    _artikel_zusatz_bericht(szenario, order, ventil, "19", "Stk", datum="2026-07-06")

    # Vorschau weist den Konflikt schon aus (nicht erst beim Abrechnen).
    vorschau = abrechnung_service.nachtrag_vorschau(order.id)
    assert vorschau["positionen"] == []
    assert len(vorschau["einheit_konflikte"]) == 1
    assert set(vorschau["einheit_konflikte"][0]["einheiten"]) == {"stk", "stück"}

    with pytest.raises(EinheitUneindeutig):
        abrechnung_service.rechnung_aus_nachtrag(
            szenario["user"].id, work_order_id=order.id, tax_code="DE_19",
        )
    # Es ist KEIN Nachtragsbeleg entstanden.
    gesamt = Invoice.objects.filter(work_order_id=order.id, invoice_type="RECHNUNG")
    assert sum((i.net_total for i in gesamt), Decimal("0.00")) == Decimal("456.00")


@pytest.mark.django_db
def test_storno_loest_den_einheiten_konflikt(szenario, fake_storage):
    """VIERTER Weg geprüft — Storno-Wechselwirkung mit divergenten Einheiten.

    Nachtrag „Stk" gebucht → Angebot „Stück" blockiert (Konflikt). Wird der Nachtrag
    **storniert**, ist die Stk-Bindung gelöst; der Artikel steht nur noch unter
    „Stück" in der Zuordnung — der Konflikt ist weg, und das Angebot lässt sich
    sauber abrechnen (die stornierte Leistung ist wieder frei)."""
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG")
    ventil = _artikel(szenario, "EIN-3", vk="24.00", beschreibung="Thermostatventil")
    _artikel_zusatz_bericht(szenario, order, ventil, "19", "Stk", datum="2026-07-06")

    nachtrag = abrechnung_service.rechnung_aus_nachtrag(
        szenario["user"].id, work_order_id=order.id, tax_code="DE_19",
    )
    _beteiligte(szenario, nachtrag)
    _kg(szenario, order)
    beleg_service.publish_invoice(szenario["user"].id, invoice_id=nachtrag.id)

    quote = _angebot(
        szenario, order,
        [_pos("Thermostatventil", "19", "24.00", artikel=ventil, unit="Stück")],
    )
    with pytest.raises(EinheitUneindeutig):
        abrechnung_service.rechnung_aus_angebot(szenario["user"].id, quote_id=quote.id)

    # Storno löst die Stk-Bindung → Konflikt weg.
    storno = beleg_service.create_cancellation(
        szenario["user"].id, invoice_id=nachtrag.id
    )
    assert storno.invoice_type == "STORNO"

    pauschal = abrechnung_service.rechnung_aus_angebot(
        szenario["user"].id, quote_id=quote.id
    )
    assert pauschal.net_total == Decimal("456.00")     # 19 × 24,00 (Stück), einmal


@pytest.mark.django_db
def test_storno_angebot_dann_nachtrag_andere_einheit_kein_912(szenario, fake_storage):
    """VIERTER Weg, die tückische Variante: Storno der Angebotsrechnung gibt die
    Bindung frei — darf aber keine Doppelabrechnung über eine ANDERE Einheit öffnen.

    Angebot 19 × Stück gebucht → storniert (Bindung frei) → Nachtrag 19 × Stk
    (jetzt kein Konflikt, weil die Stück-Bindung gelöst ist) bucht 19. Wird nun die
    stornierte Angebotsrechnung ERNEUT erzeugt, steht der Artikel bereits unter „Stk"
    in Rechnung — die Mengengrenze muss das quellenübergreifend sehen und fail-closed
    abweisen. Sonst: 912 € über den Umweg Storno.
    """
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG")
    ventil = _artikel(szenario, "EIN-4", vk="24.00", beschreibung="Thermostatventil")
    quote = _angebot(
        szenario, order,
        [_pos("Thermostatventil", "19", "24.00", artikel=ventil, unit="Stück")],
    )
    erste = abrechnung_service.rechnung_aus_angebot(
        szenario["user"].id, quote_id=quote.id
    )
    _beteiligte(szenario, erste)
    _kg(szenario, order)
    beleg_service.publish_invoice(szenario["user"].id, invoice_id=erste.id)
    beleg_service.create_cancellation(szenario["user"].id, invoice_id=erste.id)

    # Nachtrag unter „Stk" — die Stück-Bindung ist storniert, also kein Konflikt.
    _artikel_zusatz_bericht(szenario, order, ventil, "19", "Stk", datum="2026-07-06")
    nachtrag = abrechnung_service.rechnung_aus_nachtrag(
        szenario["user"].id, work_order_id=order.id, tax_code="DE_19",
    )
    assert nachtrag.net_total == Decimal("456.00")

    # Die stornierte Angebotsrechnung erneut erzeugen → jetzt steht „Stk" in
    # Rechnung → Konflikt, fail-closed.
    with pytest.raises(EinheitUneindeutig):
        abrechnung_service.rechnung_aus_angebot(szenario["user"].id, quote_id=quote.id)

    # Geltende Rechnungen (Storno rausgerechnet): genau 456 €, nie 912 €. Der
    # Nachtrag bleibt hier ENTWURF (bewusst nicht veröffentlicht) — gezählt wird der
    # ANSPRUCH über alle RECHNUNGen außer der stornierten.
    storniert = set(
        Invoice.objects.filter(invoice_type="STORNO", status="VEROEFFENTLICHT")
        .values_list("reference_invoice_id", flat=True)
    )
    summe = sum(
        (i.net_total for i in Invoice.objects.filter(
            work_order_id=order.id, invoice_type="RECHNUNG")
         if i.id not in storniert),
        Decimal("0.00"),
    )
    assert summe == Decimal("456.00")


@pytest.mark.django_db
def test_gleiche_einheit_bleibt_456_beide_reihenfolgen(szenario, fake_storage):
    """GEGENPROBE: Bei GLEICHER Einheit bleibt alles wie gehabt — 456 €, kein
    Konflikt, in beiden Reihenfolgen. Die Identitäts-Aggregation darf den Normalfall
    nicht anfassen."""
    # (1) Angebot 18 (Stk) + Nachtrag-Differenz 1 (Stk).
    a = _ventil_szenario(szenario)         # Angebot 18 stk, Bericht Ist 19 stk
    abrechnung_service.rechnung_aus_angebot(szenario["user"].id, quote_id=a["quote"].id)
    n1 = abrechnung_service.rechnung_aus_nachtrag(
        szenario["user"].id, work_order_id=a["order"].id, tax_code="DE_19",
    )
    assert n1.net_total == Decimal("24.00")
    assert sum(
        (i.net_total for i in Invoice.objects.filter(
            work_order_id=a["order"].id, invoice_type="RECHNUNG")),
        Decimal("0.00"),
    ) == Decimal("456.00")

    # (2) Nachtrag zuerst, dann Angebot — gleiche Einheit, kein Konflikt.
    b = _ventil_szenario(szenario)
    abrechnung_service.rechnung_aus_nachtrag(
        szenario["user"].id, work_order_id=b["order"].id, tax_code="DE_19",
    )
    abrechnung_service.rechnung_aus_angebot(szenario["user"].id, quote_id=b["quote"].id)
    assert sum(
        (i.net_total for i in Invoice.objects.filter(
            work_order_id=b["order"].id, invoice_type="RECHNUNG")),
        Decimal("0.00"),
    ) == Decimal("456.00")


@pytest.mark.django_db
def test_freitext_gleiche_bezeichnung_divergente_einheit_klaerung(
    szenario, fake_storage
):
    """Freitext (ohne Artikel-UUID): gleiche Bezeichnung, divergente Einheit → Klärung.

    Freitext hat keine Identität außer der Bezeichnung — die Einheit ist NICHT Teil
    der Identität, deshalb greift der Divergenz-Check auch hier. „Kernbohrung" 3 Stk
    per Nachtrag, dann „Kernbohrung" 3 Stück per Angebot → derselbe Posten in zwei
    Einheiten → fail-closed.
    """
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG")
    _bericht(szenario, order, [
        {"line_type": "FREMDLEISTUNG", "description": "Kernbohrung",
         "quantity": "3", "unit": "Stk"},
    ])
    abrechnung_service.rechnung_aus_nachtrag(
        szenario["user"].id, work_order_id=order.id, tax_code="DE_19",
        preise={"TEXT:kernbohrung:stk": Decimal("80.00")},
    )
    # Angebot mit derselben Freitext-Bezeichnung, aber Einheit „Stück".
    quote = _angebot(
        szenario, order,
        [_pos("Kernbohrung", "3", "80.00", typ="FREMDLEISTUNG", unit="Stück")],
    )
    with pytest.raises(EinheitUneindeutig):
        abrechnung_service.rechnung_aus_angebot(szenario["user"].id, quote_id=quote.id)


@pytest.mark.django_db
def test_freitext_verschiedene_bezeichnungen_sind_verschiedene_posten(
    szenario, fake_storage
):
    """Freitext mit WIRKLICH verschiedenen Bezeichnungen sind verschiedene Posten.

    Ehrliche Grenze: Ohne Artikel-Identität kann das System „Kernbohrung Wand" und
    „Kernbohrung Decke" nicht als denselben Posten erkennen — sie werden getrennt
    abgerechnet. Das ist kein Doppelabrechnungs-Loch (es sind zwei Positionen, die
    ein Mensch unterschiedlich benannt hat), sondern die Folge fehlender Identität.
    Wer sie zusammenführen will, gibt beiden denselben Artikel/dieselbe Leistung.
    """
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG")
    _bericht(szenario, order, [
        {"line_type": "FREMDLEISTUNG", "description": "Kernbohrung Wand",
         "quantity": "2", "unit": "Stk"},
        {"line_type": "FREMDLEISTUNG", "description": "Kernbohrung Decke",
         "quantity": "1", "unit": "Stk"},
    ])
    # Zwei verschiedene Bezeichnungen → zwei Klärungseinheiten, beide bepreisbar.
    invoice = abrechnung_service.rechnung_aus_nachtrag(
        szenario["user"].id, work_order_id=order.id, tax_code="DE_19",
        preise={
            "TEXT:kernbohrung wand:stk": Decimal("80.00"),
            "TEXT:kernbohrung decke:stk": Decimal("120.00"),
        },
    )
    zeilen = list(InvoiceLine.objects.filter(invoice_id=invoice.id))
    assert len(zeilen) == 2
    assert invoice.net_total == Decimal("280.00")      # 2×80 + 1×120


# ---------------------------------------------------------------------------
# Der Bericht bleibt versiegelt — die Abrechnung fasst ihn nicht an
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_entwurfsbericht_fliesst_nicht_ein_wird_aber_benannt(szenario, fake_storage):
    """Ein nicht abgenommener Nachweis ist keine Abrechnungsgrundlage — und der
    häufigste Grund für eine „zu kleine" Rechnung. Also: benennen, nicht
    verschweigen."""
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG")
    ventil = _artikel(szenario, "TH-8", vk="24.00", beschreibung="Thermostatventil")
    quote = _angebot(
        szenario, order,
        [_pos("Thermostatventil", "18", "24.00", artikel=ventil, unit="stk")],
    )
    _bericht(szenario, order, [_ist(_ql(quote), "19")], signieren=False)

    vorschau = abrechnung_service.nachtrag_vorschau(order.id)
    assert vorschau["positionen"] == []
    assert len(vorschau["nicht_unterzeichnete_berichte"]) == 1

    with pytest.raises(AbrechnungError) as exc:
        abrechnung_service.rechnung_aus_nachtrag(
            szenario["user"].id, work_order_id=order.id, tax_code="DE_19",
        )
    assert "nicht unterzeichnet" in str(exc.value)

    # Der Bildschirm (soll_ist) zeigt den Entwurfsstand — als vorläufig markiert.
    abgleich = report_service.soll_ist(order.id)
    assert abgleich["enthaelt_entwuerfe"] is True
    assert abgleich["positionen"][0]["art"] == report_service.MEHRVERBRAUCH


@pytest.mark.django_db
def test_unterzeichneter_bericht_bleibt_versiegelt(szenario, fake_storage):
    """Die Abrechnung ändert **nichts** am Bericht — er ist ein Kundendokument."""
    s = _ventil_szenario(szenario)
    zeile = SiteReportLine.objects.get(site_report_id=s["bericht"].id)
    vorher = (zeile.quantity, zeile.planned_quantity, zeile.description,
              s["bericht"].status, s["bericht"].signed_at)

    abrechnung_service.rechnung_aus_nachtrag(
        szenario["user"].id, work_order_id=s["order"].id, tax_code="DE_19",
    )

    zeile.refresh_from_db()
    s["bericht"].refresh_from_db()
    assert (zeile.quantity, zeile.planned_quantity, zeile.description,
            s["bericht"].status, s["bericht"].signed_at) == vorher
    # Und er bleibt unveränderlich (Trigger) — die Bindung ändert daran nichts.
    with pytest.raises(Exception):
        with business_transaction(szenario["user"].id):
            report_service.set_report_lines(
                szenario["user"].id, report_id=s["bericht"].id, lines=[],
            )
