"""Der Briefkopf für die Dokumentansicht (`beleg.dokumentkopf`, Befund G1).

Das Blatt zeigt Absender und Empfänger — und die Frage, wo die herkommen, ist
keine Formsache: Bei einer **gestellten** Rechnung muss auf dem Schirm dasselbe
stehen wie auf dem Beleg, den der Kunde in Händen hält. Zieht der Kunde um,
darf sich die gestellte Rechnung nicht ändern (B-30, GoBD).

Diese Tests decken die drei Stellen ab, an denen das schiefgehen kann.
"""
import uuid

import pytest

from db_core.models import AppUser
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service


def _gepruefter_auftrag(actor, obj, debtor):
    order = auftrag_service.create_work_order(actor, property_id=obj.id, title="Auftrag")
    auftrag_service.set_order_evidence(actor, work_order_id=order.id, reference="N")
    auftrag_service.confirm_responsibility(
        actor, work_order_id=order.id, scope="COMMON_PROPERTY"
    )
    for role in ("PRINCIPAL", "INVOICE_DEBTOR"):
        auftrag_service.add_work_order_party(
            actor, work_order_id=order.id, party_id=debtor.id, role=role, is_primary=True
        )
    for to in ("FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG",
               "TECHNISCH_ABGESCHLOSSEN", "KAUFMAENNISCH_GEPRUEFT"):
        auftrag_service.advance_status(actor, work_order_id=order.id, to_status=to)
    return order


def _rechnung(actor, *, mit_adresse=True):
    obj = property_service.create_property(
        actor, name="Objekt", property_type="WEG",
        street="Objektweg", house_number="3", postal_code="10115", city="Berlin",
    )
    kunde = identity_service.create_organization(
        actor, legal_name="Kundschaft GmbH", organization_type="COMPANY"
    )
    if mit_adresse:
        identity_service.add_address(
            actor, kunde.id, address_type="BUSINESS",
            street="Altstraße", house_number="1", postal_code="10115", city="Berlin",
        )
    order = _gepruefter_auftrag(actor, obj, kunde)
    inv = beleg_service.create_invoice(
        actor, property_id=obj.id, invoice_type="RECHNUNG", work_order_id=order.id,
        lines=[{"line_type": "MATERIAL", "description": "Ziegel", "quantity": 10,
                "unit": "Stk", "unit_price": "2.40", "tax_code": "DE_19"}],
    )
    for role in ("INVOICE_DEBTOR", "INVOICE_RECIPIENT"):
        beleg_service.add_invoice_party(
            actor, invoice_id=inv.id, party_id=kunde.id, role=role, is_primary=True
        )
    return inv, kunde


@pytest.mark.django_db
def test_gestellte_rechnung_zeigt_die_anschrift_von_damals(app_user):
    """Der Kern: Nach einem Umzug bleibt die gestellte Rechnung, wie sie war."""
    a = app_user.id
    inv, kunde = _rechnung(a)
    beleg_service.publish_invoice(a, invoice_id=inv.id)
    inv.refresh_from_db()

    kopf = beleg_service.dokumentkopf(inv)
    assert "Altstraße 1" in kopf["empfaenger"]
    assert kopf["aus_snapshot"] is True

    # Der Kunde zieht um.
    from db_core.models import PartyAddress

    zuordnung = PartyAddress.objects.filter(party_id=kunde.id).first()
    identity_service.ersetze_party_address(
        a, zuordnung.id,
        street="Neustraße", house_number="9", postal_code="20095", city="Hamburg",
    )

    from db_core.models import Invoice

    frisch = Invoice.objects.get(id=inv.id)
    kopf = beleg_service.dokumentkopf(frisch)
    assert "Altstraße 1" in kopf["empfaenger"], (
        "Eine gestellte Rechnung zeigt die Anschrift, unter der sie gestellt wurde"
    )
    assert not any("Neustraße" in z for z in kopf["empfaenger"])


@pytest.mark.django_db
def test_entwurf_zeigt_die_aktuelle_anschrift(app_user):
    """Beim Entwurf ist das Gegenteil richtig: Er soll Änderungen zeigen."""
    a = app_user.id
    inv, kunde = _rechnung(a)

    kopf = beleg_service.dokumentkopf(inv)
    assert "Altstraße 1" in kopf["empfaenger"]
    assert kopf["aus_snapshot"] is False

    from db_core.models import PartyAddress

    zuordnung = PartyAddress.objects.filter(party_id=kunde.id).first()
    identity_service.ersetze_party_address(
        a, zuordnung.id,
        street="Neustraße", house_number="9", postal_code="20095", city="Hamburg",
    )
    from db_core.models import Invoice

    kopf = beleg_service.dokumentkopf(Invoice.objects.get(id=inv.id))
    assert "Neustraße 9" in kopf["empfaenger"]


@pytest.mark.django_db
def test_veroeffentlicht_ohne_firmenprofil_bleibt_im_snapshot_zweig(app_user):
    """Der Fall, der die erste Fassung der Weiche aushebelte.

    Wird ein Beleg gestellt, BEVOR das Firmenprofil gepflegt ist, steht im
    Snapshot `header.issuer = null` — die **Beteiligten** sind aber sehr wohl
    eingefroren. Die erste Fassung fragte nach dem Aussteller und schickte
    genau diesen Beleg in den Live-Zweig; nach einem Umzug hätte der Schirm
    eine andere Anschrift gezeigt als das PDF.

    Jetzt entscheidet der Status. Ohne Firmenprofil bleibt der Absender leer —
    das ist ehrlich, denn es gibt keinen.
    """
    a = app_user.id
    from db_core.models import CompanyProfile, Invoice

    CompanyProfile.objects.all().delete()

    inv, kunde = _rechnung(a)
    beleg_service.publish_invoice(a, invoice_id=inv.id)
    inv.refresh_from_db()
    assert (inv.billing_snapshot or {}).get("header", {}).get("issuer") is None

    from db_core.models import PartyAddress

    zuordnung = PartyAddress.objects.filter(party_id=kunde.id).first()
    identity_service.ersetze_party_address(
        a, zuordnung.id,
        street="Neustraße", house_number="9", postal_code="20095", city="Hamburg",
    )
    kopf = beleg_service.dokumentkopf(Invoice.objects.get(id=inv.id))
    assert "Altstraße 1" in kopf["empfaenger"], (
        "Auch ohne Firmenprofil ist der Beleg gestellt — der Snapshot gewinnt"
    )
    assert kopf["aus_snapshot"] is True


@pytest.mark.django_db
def test_entwurf_waehlt_denselben_empfaenger_wie_beleg_stammdaten(app_user):
    """Beide Codepfade müssen denselben Empfänger wählen.

    Bei zwei **nicht-primären** Empfängern (Erbengemeinschaft, WEG-Beirat)
    entscheidet allein die Sortierung, wer adressiert wird. Der Entwurfszweig
    von `dokumentkopf` liest das Queryset — und `InvoiceParty` trägt kein
    `Meta.ordering`, die Reihenfolge wäre also die von PostgreSQL und damit
    undefiniert. `beleg_stammdaten` sortiert dagegen nach `(role, party_id)`.

    Verglichen wird deshalb direkt gegen `beteiligter(beleg_stammdaten(...))`,
    die Quelle, aus der auch PDF und XML ihren Empfänger nehmen. Ein Vergleich
    „vor und nach dem Veröffentlichen" ginge hier nicht: Ohne genau einen
    primären Empfänger lässt der Trigger A-28 gar keine Veröffentlichung zu —
    der Fall lebt allein im Entwurf.
    """
    a = app_user.id
    obj = property_service.create_property(
        a, name="Objekt", property_type="WEG",
        street="Objektweg", house_number="3", postal_code="10115", city="Berlin",
    )
    schuldner = identity_service.create_organization(
        a, legal_name="Schuldner GmbH", organization_type="COMPANY"
    )
    order = _gepruefter_auftrag(a, obj, schuldner)
    inv = beleg_service.create_invoice(
        a, property_id=obj.id, invoice_type="RECHNUNG", work_order_id=order.id,
        lines=[{"line_type": "MATERIAL", "description": "Ziegel", "quantity": 10,
                "unit": "Stk", "unit_price": "2.40", "tax_code": "DE_19"}],
    )
    beleg_service.add_invoice_party(
        a, invoice_id=inv.id, party_id=schuldner.id, role="INVOICE_DEBTOR",
        is_primary=True,
    )
    # ZWEI nicht-primäre Empfänger — erst dann entscheidet die Sortierung.
    for name, primaer in (("Erbe Anton", False), ("Erbe Zacharias", False)):
        vor, nach = name.split(" ")
        erbe = identity_service.create_person(a, first_name=vor, last_name=nach)
        beleg_service.add_invoice_party(
            a, invoice_id=inv.id, party_id=erbe.id, role="INVOICE_RECIPIENT",
            is_primary=primaer,
        )

    from db_core.models import Invoice
    from db_core.services.beleg_pdf import empfaenger_zeilen

    frisch = Invoice.objects.get(id=inv.id)
    aus_dokumentkopf = beleg_service.dokumentkopf(frisch)["empfaenger"]

    # Die Quelle, aus der PDF und ZUGFeRD-XML ihren Empfänger nehmen.
    stamm = beleg_service.beleg_stammdaten(frisch)
    erwartet = empfaenger_zeilen(
        beleg_service.beteiligter(stamm, "INVOICE_RECIPIENT")["snapshot"]
    )

    assert aus_dokumentkopf == erwartet, (
        "Bildschirm und Beleg adressieren verschiedene Empfänger: "
        f"{aus_dokumentkopf} statt {erwartet}"
    )
