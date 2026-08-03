"""Sammelrechnung — mehrere Entwürfe desselben Eigentümers auf einem Beleg.

Sascha, 2026-08-02: *„Drei Bäder, alle drei Wohnungen gehören Herrn Meier."* Der
Kunde bekommt **einen** Beleg mit einer Rubrik je Wohnung, statt drei Rechnungen
über dieselbe Baustelle.

Der Weg ist bewusst eine Verkettung vorhandener Bausteine (siehe
`docs/ENTSCHEIDUNGEN.md`): Quellentwürfe verwerfen (0147), Bindungen lösen, eine
neue Rechnung an **einem** Auftrag anlegen, die freigewordenen Quellen neu
binden. Kein Freigabetor wird angefasst, keine Migration.

**Die Regel, die hier zum ersten Mal ein Dienst durchsetzen muss** (INVARIANTEN.md
§2): Bis hierher war „nie zwei Eigentümer auf einer Rechnung" physisch
unverletzbar, weil eine Rechnung an genau einem Auftrag hängt und ein Auftrag
höchstens eine Einheit trägt. Die Sammelrechnung hebt diese Kopplung auf — die
Prüfung unten ist die einzige verbliebene Sicherung.
"""
from datetime import date

import pytest

from db_core.models import BillingLink, Invoice
from db_core.services import abrechnung as abrechnung_service
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import eigentum as eigentum_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service


def _lines(bezeichnung, preis="30.00"):
    return [
        {
            "line_type": "MATERIAL",
            "description": bezeichnung,
            "quantity": 5,
            "unit": "m2",
            "unit_price": preis,
            "tax_code": "DE_19",
        }
    ]


@pytest.fixture
def haus(app_user):
    """Ein Haus, drei Wohnungen — zwei davon Herrn Meier, eine Frau Yilmaz."""
    a = app_user.id
    prop = property_service.create_property(
        a, name="WEG Sammelweg", property_type="WEG",
        street="Sammelweg", house_number="7", postal_code="10365", city="Berlin",
    )
    gebaeude = property_service.add_building(
        a, property_id=prop.id, building_number="1", name="Vorderhaus"
    )
    einheiten = {
        nr: property_service.add_unit(
            a, building_id=gebaeude.id, property_id=prop.id,
            unit_type="APARTMENT", unit_number=nr, storey=etage,
        )
        for nr, etage in (("1", "EG"), ("2", "1. OG"), ("3", "2. OG"))
    }
    meier = identity_service.create_person(a, first_name="Klaus", last_name="Meier")
    yilmaz = identity_service.create_person(a, first_name="Aylin", last_name="Yilmaz")
    for nr, partei in (("1", meier), ("2", meier), ("3", yilmaz)):
        eigentum_service.create_stand(
            a,
            unit_id=einheiten[nr].id,
            valid_from=date(2020, 1, 1),
            source_type="OWNER_LIST",
            source_reference="Eigentümerliste der Verwaltung",
            distribution_status="COMPLETE",
            eigentuemer=[{
                "party_id": partei.id,
                "share_numerator": 1,
                "share_denominator": 1,
                "ownership_type": "SOLE",
                "confirmation_status": "CONFIRMED",
            }],
        )
    return {"actor": a, "prop": prop, "einheiten": einheiten}


def _entwurf(haus, unit_nummer, bezeichnung, **kwargs):
    """Ein Rechnungsentwurf zur Wohnung — je Wohnung ein eigener Auftrag."""
    a = haus["actor"]
    auftrag = auftrag_service.create_work_order(
        a,
        property_id=haus["prop"].id,
        title=f"Bad WE {unit_nummer}",
        unit_id=haus["einheiten"][unit_nummer].id,
    )
    return beleg_service.create_invoice(
        a,
        property_id=haus["prop"].id,
        invoice_type="RECHNUNG",
        work_order_id=auftrag.id,
        lines=_lines(bezeichnung),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Der Normalfall
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_zwei_entwuerfe_werden_eine_rechnung(haus):
    e1 = _entwurf(haus, "1", "Fliesen Bad EG")
    e2 = _entwurf(haus, "2", "Fliesen Bad 1. OG")

    sammel = abrechnung_service.sammelrechnung(
        haus["actor"], invoice_ids=[e1.id, e2.id]
    )

    assert sammel.status == "ENTWURF"
    assert sammel.invoice_type == "RECHNUNG"
    # Die Summen addieren sich — nichts geht verloren, nichts kommt hinzu.
    assert sammel.net_total == e1.net_total + e2.net_total
    assert sammel.gross_total == e1.gross_total + e2.gross_total
    bezeichnungen = [l.description for l in sammel.lines.all()]
    assert "Fliesen Bad EG" in bezeichnungen
    assert "Fliesen Bad 1. OG" in bezeichnungen


@pytest.mark.django_db
def test_je_quellentwurf_eine_rubrik_mit_wohnungsbezug(haus):
    """Der Empfänger muss sehen, welche Position zu welcher Wohnung gehört."""
    e1 = _entwurf(haus, "1", "Fliesen Bad EG")
    e2 = _entwurf(haus, "2", "Fliesen Bad 1. OG")

    sammel = abrechnung_service.sammelrechnung(
        haus["actor"], invoice_ids=[e1.id, e2.id]
    )

    rubriken = sorted(sammel.rubriken.all(), key=lambda r: r.position_number)
    assert [r.title for r in rubriken] == [
        "Vorderhaus · EG · WE 1",
        "Vorderhaus · 1. OG · WE 2",
    ]
    # Und jede Position hängt in ihrem Abschnitt.
    je_rubrik = {
        r.id: [l.description for l in sammel.lines.filter(rubrik_id=r.id)]
        for r in rubriken
    }
    assert je_rubrik[rubriken[0].id] == ["Fliesen Bad EG"]
    assert je_rubrik[rubriken[1].id] == ["Fliesen Bad 1. OG"]


@pytest.mark.django_db
def test_die_reihenfolge_der_auswahl_ist_die_reihenfolge_der_rubriken(haus):
    e1 = _entwurf(haus, "1", "Bad EG")
    e2 = _entwurf(haus, "2", "Bad 1. OG")

    sammel = abrechnung_service.sammelrechnung(
        haus["actor"], invoice_ids=[e2.id, e1.id]
    )

    rubriken = sorted(sammel.rubriken.all(), key=lambda r: r.position_number)
    assert rubriken[0].title == "Vorderhaus · 1. OG · WE 2"


@pytest.mark.django_db
def test_die_quellentwuerfe_sind_danach_verworfen(haus):
    e1 = _entwurf(haus, "1", "Bad EG")
    e2 = _entwurf(haus, "2", "Bad 1. OG")

    abrechnung_service.sammelrechnung(haus["actor"], invoice_ids=[e1.id, e2.id])

    e1.refresh_from_db()
    e2.refresh_from_db()
    assert e1.status == "VERWORFEN"
    assert e2.status == "VERWORFEN"
    # Verwerfen ist kein Löschen: die Entwürfe bleiben vollständig lesbar.
    assert e1.lines.count() == 1


@pytest.mark.django_db
def test_die_sammelrechnung_haengt_an_einem_auftrag(haus):
    """B-08 bleibt unangetastet — genau ein Auftrag je Rechnung."""
    e1 = _entwurf(haus, "1", "Bad EG")
    e2 = _entwurf(haus, "2", "Bad 1. OG")

    sammel = abrechnung_service.sammelrechnung(
        haus["actor"], invoice_ids=[e1.id, e2.id]
    )

    assert sammel.work_order_id == e1.work_order_id


@pytest.mark.django_db
def test_zielauftrag_laesst_sich_waehlen(haus):
    e1 = _entwurf(haus, "1", "Bad EG")
    e2 = _entwurf(haus, "2", "Bad 1. OG")

    sammel = abrechnung_service.sammelrechnung(
        haus["actor"], invoice_ids=[e1.id, e2.id], work_order_id=e2.work_order_id
    )

    assert sammel.work_order_id == e2.work_order_id


@pytest.mark.django_db
def test_fremder_zielauftrag_wird_abgelehnt(haus):
    e1 = _entwurf(haus, "1", "Bad EG")
    e2 = _entwurf(haus, "2", "Bad 1. OG")
    fremd = _entwurf(haus, "3", "Bad 2. OG")

    with pytest.raises(ValueError, match="gehört zu keinem der zusammengefassten"):
        abrechnung_service.sammelrechnung(
            haus["actor"],
            invoice_ids=[e1.id, e2.id],
            work_order_id=fremd.work_order_id,
        )


# ---------------------------------------------------------------------------
# Die Invariante: nur EIN Eigentümer
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_verschiedene_eigentuemer_werden_abgelehnt(haus):
    """INVARIANTEN.md §2 — ausnahmslos.

    Stünden zwei Eigentümer auf einem Beleg, schuldete niemand die volle Summe:
    Die Forderung wäre nicht durchsetzbar, der Beleg müsste storniert und
    geteilt werden.
    """
    meier = _entwurf(haus, "1", "Bad EG")
    yilmaz = _entwurf(haus, "3", "Bad 2. OG")

    with pytest.raises(ValueError, match="verschiedenen Eigentümern"):
        abrechnung_service.sammelrechnung(
            haus["actor"], invoice_ids=[meier.id, yilmaz.id]
        )

    # Und der Abbruch hinterlässt nichts Halbes: Beide Entwürfe leben weiter.
    meier.refresh_from_db()
    yilmaz.refresh_from_db()
    assert meier.status == "ENTWURF"
    assert yilmaz.status == "ENTWURF"


@pytest.mark.django_db
def test_ohne_eigentuemer_bricht_der_vorgang_ab(app_user):
    """„Nicht prüfbar" darf nicht „durchgelassen" heißen."""
    a = app_user.id
    prop = property_service.create_property(
        a, name="Objekt ohne Eigentuemer", property_type="MIXED",
        street="Leerweg", house_number="1", postal_code="10115", city="Berlin",
    )
    entwuerfe = []
    for titel in ("Bad A", "Bad B"):
        auftrag = auftrag_service.create_work_order(
            a, property_id=prop.id, title=titel
        )
        entwuerfe.append(
            beleg_service.create_invoice(
                a, property_id=prop.id, invoice_type="RECHNUNG",
                work_order_id=auftrag.id, lines=_lines(titel),
            )
        )

    with pytest.raises(ValueError, match="kein Eigentümer hinterlegt"):
        abrechnung_service.sammelrechnung(
            a, invoice_ids=[e.id for e in entwuerfe]
        )


# ---------------------------------------------------------------------------
# Die Abrechnungsbindungen wandern mit
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_bindungen_wandern_auf_die_sammelrechnung(haus):
    """Der Kern: die Quelle bleibt genau einmal gebunden — jetzt am neuen Beleg.

    Bliebe die alte Bindung aktiv, wäre die Stunde für immer als abgerechnet
    markiert. Entstünde keine neue, ließe sie sich ein zweites Mal fakturieren.
    """
    a = haus["actor"]
    quelle = _entwurf(haus, "1", "Bad EG")
    zweiter = _entwurf(haus, "2", "Bad 1. OG")
    # Eine Bindung von Hand setzen: der Abrechnungsweg selbst (Angebot/Bericht)
    # ist hier nicht der Prüfgegenstand — das Umhängen ist es.
    angebot = beleg_service.create_quote(
        a, property_id=haus["prop"].id, work_order_id=quelle.work_order_id,
        title="Bad EG", lines=_lines("Bad EG"),
    )
    angebotszeile = angebot.lines.first()
    quellzeile = quelle.lines.first()
    from db_core.db_context import business_transaction
    import uuid as _uuid

    with business_transaction(a):
        BillingLink.objects.create(
            id=_uuid.uuid4(),
            invoice_id=quelle.id,
            invoice_line_id=quellzeile.id,
            source_kind="ANGEBOTSPOSITION",
            quote_line_id=angebotszeile.id,
        )

    sammel = abrechnung_service.sammelrechnung(
        a, invoice_ids=[quelle.id, zweiter.id]
    )

    aktiv = list(
        BillingLink.objects.filter(
            quote_line_id=angebotszeile.id, released_at__isnull=True
        )
    )
    assert len(aktiv) == 1, "die Quelle darf genau einmal aktiv gebunden sein"
    assert aktiv[0].invoice_id == sammel.id
    # Die alte Bindung bleibt als Nachweis stehen — gelöst, mit Grund.
    geloest = BillingLink.objects.get(
        quote_line_id=angebotszeile.id, invoice_id=quelle.id
    )
    assert geloest.released_at is not None
    assert "Sammelrechnung" in geloest.released_reason
    # Und sie hängt an der richtigen Position des neuen Belegs.
    neue_zeile = sammel.lines.get(id=aktiv[0].invoice_line_id)
    assert neue_zeile.description == "Bad EG"


@pytest.mark.django_db
def test_der_auftrag_bleibt_gesperrt_obwohl_der_beleg_woanders_haengt(haus):
    """Das Loch, das die Sammelrechnung sonst aufrisse.

    Bis hierher fragte die Doppelabrechnungssperre über den **Beleg**
    (`invoice.work_order_id`). Die Sammelrechnung hängt aber an **einem** Auftrag
    und bindet die Quellen mehrerer — für jeden anderen beteiligten Auftrag wäre
    die Klammer damit weg: Seine Abrechnungsart ließe sich wieder umstellen und
    dieselbe Leistung über die andere Quelle ein zweites Mal fakturieren.

    Seit dem Umbau fragt die Sperre über die **Herkunft der Quelle**.
    """
    a = haus["actor"]
    e1 = _entwurf(haus, "1", "Bad EG")
    zweiter = _entwurf(haus, "2", "Bad 1. OG")
    zweiter_auftrag = zweiter.work_order_id
    angebot = beleg_service.create_quote(
        a, property_id=haus["prop"].id, work_order_id=zweiter_auftrag,
        title="Bad 1. OG", lines=_lines("Bad 1. OG"),
    )
    from db_core.db_context import business_transaction
    import uuid as _uuid

    with business_transaction(a):
        BillingLink.objects.create(
            id=_uuid.uuid4(),
            invoice_id=zweiter.id,
            invoice_line_id=zweiter.lines.first().id,
            source_kind="ANGEBOTSPOSITION",
            quote_line_id=angebot.lines.first().id,
        )

    # Die Sammelrechnung hängt an Auftrag 1 — die Bindung stammt aus Auftrag 2.
    sammel = abrechnung_service.sammelrechnung(a, invoice_ids=[e1.id, zweiter.id])
    assert sammel.work_order_id == e1.work_order_id

    # Auftrag 2 ist trotzdem weiterhin als „abgerechnet" erkennbar …
    assert abrechnung_service._bindungen_am_auftrag(
        zweiter_auftrag, source_kinds=("ANGEBOTSPOSITION",)
    )
    # … und seine Abrechnungsart bleibt eingefroren.
    with pytest.raises(ValueError, match="lässt sich nicht mehr ändern"):
        abrechnung_service.set_billing_mode(
            a, work_order_id=zweiter_auftrag, billing_mode="REGIE"
        )


# ---------------------------------------------------------------------------
# Was nicht zusammengefasst wird
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ein_einzelner_entwurf_ist_keine_sammelrechnung(haus):
    e1 = _entwurf(haus, "1", "Bad EG")

    with pytest.raises(ValueError, match="mindestens zwei"):
        abrechnung_service.sammelrechnung(haus["actor"], invoice_ids=[e1.id])


@pytest.mark.django_db
def test_verworfener_entwurf_wird_abgelehnt(haus):
    e1 = _entwurf(haus, "1", "Bad EG")
    e2 = _entwurf(haus, "2", "Bad 1. OG")
    beleg_service.verwirf_rechnung(haus["actor"], invoice_id=e2.id)

    with pytest.raises(ValueError, match="verworfen"):
        abrechnung_service.sammelrechnung(
            haus["actor"], invoice_ids=[e1.id, e2.id]
        )


@pytest.mark.django_db
def test_abschlagsrechnung_wird_abgelehnt(haus):
    """Die Anrechnungskette lässt sich nicht mit umhängen."""
    e1 = _entwurf(haus, "1", "Bad EG")
    abschlag = _entwurf(haus, "2", "Anzahlung Bad 1. OG")
    Invoice.objects.filter(id=abschlag.id).update(invoice_type="ABSCHLAGSRECHNUNG")

    with pytest.raises(ValueError, match="Zusammengefasst werden nur"):
        abrechnung_service.sammelrechnung(
            haus["actor"], invoice_ids=[e1.id, abschlag.id]
        )


@pytest.mark.django_db
def test_verschiedene_liegenschaften_werden_abgelehnt(haus, app_user):
    e1 = _entwurf(haus, "1", "Bad EG")
    anderes = property_service.create_property(
        haus["actor"], name="Anderes Objekt", property_type="WEG",
        street="Woanders", house_number="2", postal_code="10115", city="Berlin",
    )
    auftrag = auftrag_service.create_work_order(
        haus["actor"], property_id=anderes.id, title="Bad woanders"
    )
    e2 = beleg_service.create_invoice(
        haus["actor"], property_id=anderes.id, invoice_type="RECHNUNG",
        work_order_id=auftrag.id, lines=_lines("Bad woanders"),
    )

    with pytest.raises(ValueError, match="verschiedenen Liegenschaften"):
        abrechnung_service.sammelrechnung(
            haus["actor"], invoice_ids=[e1.id, e2.id]
        )


# ---------------------------------------------------------------------------
# Kopffelder
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_einheitliche_zahlungsbedingungen_werden_uebernommen(haus):
    e1 = _entwurf(haus, "1", "Bad EG", payment_term_days=14)
    e2 = _entwurf(haus, "2", "Bad 1. OG", payment_term_days=14)

    sammel = abrechnung_service.sammelrechnung(
        haus["actor"], invoice_ids=[e1.id, e2.id]
    )

    assert sammel.payment_term_days == 14


@pytest.mark.django_db
def test_abweichende_zahlungsbedingungen_verlangen_eine_ansage(haus):
    """Ein stillschweigend gewähltes Zahlungsziel stünde anders auf dem Beleg
    als im Entwurf, den der Disponent gelesen hat."""
    e1 = _entwurf(haus, "1", "Bad EG", payment_term_days=14)
    e2 = _entwurf(haus, "2", "Bad 1. OG", payment_term_days=30)

    with pytest.raises(ValueError, match="Zahlungsziel"):
        abrechnung_service.sammelrechnung(
            haus["actor"], invoice_ids=[e1.id, e2.id]
        )

    # Mit ausdrücklicher Ansage geht es.
    sammel = abrechnung_service.sammelrechnung(
        haus["actor"], invoice_ids=[e1.id, e2.id], payment_term_days=21
    )
    assert sammel.payment_term_days == 21


@pytest.mark.django_db
def test_untergliederung_eines_entwurfs_bleibt_lesbar(haus):
    """Hatte ein Entwurf eigene Abschnitte, wandern deren Titel als Textzeile mit.

    Eine zweite Rubrikebene gibt es im Schema nicht — verschluckt man die
    Gliederung, wird aus zwei getrennten Abschnitten eine ununterscheidbare
    Positionsliste.
    """
    a = haus["actor"]
    auftrag = auftrag_service.create_work_order(
        a, property_id=haus["prop"].id, title="Bad EG",
        unit_id=haus["einheiten"]["1"].id,
    )
    e1 = beleg_service.create_invoice(
        a, property_id=haus["prop"].id, invoice_type="RECHNUNG",
        work_order_id=auftrag.id,
        rubriken=[{"title": "Demontage"}, {"title": "Montage"}],
        lines=[
            {"line_type": "ARBEITSZEIT", "description": "Altbad raus",
             "quantity": 4, "unit": "h", "unit_price": "55.00",
             "tax_code": "DE_19", "rubrik": 1},
            {"line_type": "MATERIAL", "description": "Neue Wanne",
             "quantity": 1, "unit": "Stk", "unit_price": "480.00",
             "tax_code": "DE_19", "rubrik": 2},
        ],
    )
    e2 = _entwurf(haus, "2", "Bad 1. OG")

    sammel = abrechnung_service.sammelrechnung(a, invoice_ids=[e1.id, e2.id])

    zeilen = [
        (l.line_type, l.description)
        for l in sorted(sammel.lines.all(), key=lambda l: l.position_number)
    ]
    assert ("TEXT", "Demontage") in zeilen
    assert ("TEXT", "Montage") in zeilen
    # Die Textzeilen tragen keinen Betrag — die Summe bleibt die der Entwürfe.
    assert sammel.net_total == e1.net_total + e2.net_total
