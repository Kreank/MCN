"""Der **Steckbrief** eines Kontakts — Kontext für die Kontakt-Trefferliste.

Dasselbe Problem wie bei der Liegenschaft (`services/property_steckbrief.py`),
eine Entität weiter: Drei Zeilen „Meier" im Kontakt-Picker sind keine Auskunft.
**Telefon, E-Mail, Adresse und die Objekte, an denen der Kontakt hängt**
beantworten die Frage „ist das der, den ich suche?" — und verhindern damit, dass
derselbe Herr Meier zum vierten Mal angelegt wird.

Konstruktionsregel wie dort: **wenige Abfragen, egal wie viele Zeilen** (hier
drei). Reine Anzeige; die Zeilenmenge begrenzt der Aufrufer, bevor er hereinreicht.

## Die eine Grenze, die dieses Modul selbst ziehen MUSS: `objekte`

Anders als beim Liegenschafts-Steckbrief genügt es hier **nicht**, dass der
Aufrufer die Zeilenmenge begrenzt: Die begrenzte Menge sind die *Parties*, das
Feld `objekte` nennt aber **Liegenschaften**. Ein Kontakt darf für den Monteur
sichtbar sein, weil er an *seinem* Objekt hängt — und trüge dann, ungefiltert,
die Namen **aller** Objekte desselben Kontakts in die Antwort. Liegenschaftsnamen
enthalten in diesem Datenmodell regelmäßig die Adresse; das wäre genau das Leck,
das `services/objektsicht.py` ausschließt („Die Grenze bleibt das Objekt").

Deshalb nimmt `steckbriefe` `scope` und `actor` **verpflichtend** entgegen und
begrenzt die Rollenabfrage über `objektsicht.begrenzen` — dieselbe eine Regel,
kein zweiter Filter. Das ist auch der Grund, aus dem `MAX_OBJEKTE` erst **nach**
der Begrenzung greift: Sonst verdrängten fremde Objekte die eigenen aus der Liste.
"""
from dataclasses import dataclass, field
from datetime import date

from db_core.models import ContactPoint, PartyAddress, PropertyPartyRole
from db_core.services import objektsicht
from db_core.services.property_steckbrief import (
    ROLLE_LABEL,
    TELEFON_TYPEN,
    adresszeile,
    gilt,
)

#: Mehr als drei Objekte je Kontakt sind keine Auskunft mehr, sondern eine Liste —
#: dafür gibt es das Kontaktdetail.
MAX_OBJEKTE = 3


@dataclass
class KontaktSteckbrief:
    telefon: str | None = None
    email: str | None = None
    address_line: str | None = None
    objekte: list = field(default_factory=list)


def steckbriefe(party_ids, *, scope, actor, stichtag=None):
    """Steckbriefe für viele Kontakte — **drei** Abfragen, unabhängig von N.

    Gibt `dict[party_id, KontaktSteckbrief]` zurück.

    `scope`/`actor` sind **Pflicht** (kein Default): Sie begrenzen `objekte` auf
    die Liegenschaften, die der Akteur sehen darf. Ein Aufrufer, der sie vergisst,
    bekommt einen TypeError — nicht stillschweigend fremde Objektnamen.
    """
    ids = list(dict.fromkeys(party_ids))
    if not ids:
        return {}
    stichtag = stichtag or date.today()

    ergebnis = {pid: KontaktSteckbrief() for pid in ids}

    # (1) Kontaktwege: primäre zuerst, danach die jüngeren — die erste gültige
    #     Zeile je Party und Art gewinnt.
    for cp in (
        ContactPoint.objects.filter(party_id__in=ids)
        .order_by("-is_primary", "-valid_from", "id")
    ):
        if not gilt(cp, stichtag):
            continue
        eintrag = ergebnis[cp.party_id]
        if cp.contact_type in TELEFON_TYPEN and eintrag.telefon is None:
            eintrag.telefon = cp.value
        elif cp.contact_type == "EMAIL" and eintrag.email is None:
            eintrag.email = cp.value

    # (2) Primäre aktuelle Adresse.
    for pa in (
        PartyAddress.objects.filter(party_id__in=ids)
        .select_related("address")
        .order_by("-is_primary", "-valid_from", "id")
    ):
        if not gilt(pa, stichtag):
            continue
        eintrag = ergebnis[pa.party_id]
        if eintrag.address_line is None:
            eintrag.address_line = adresszeile(pa.address)

    # (3) Objekte mit Rolle — „WEG Albrechtstr. 30 (Eigentümer)".
    #     `begrenzen` VOR der Sortierung/Kappung: Fremde Objekte dürfen die
    #     eigenen nicht aus MAX_OBJEKTE verdrängen (und erst recht nicht in die
    #     Antwort geraten).
    rollen_qs = objektsicht.begrenzen(
        PropertyPartyRole.objects.filter(party_id__in=ids),
        scope, actor, "property_id",
    )
    for r in (
        rollen_qs.select_related("property").order_by("-valid_from", "id")
    ):
        if not gilt(r, stichtag):
            continue
        eintrag = ergebnis[r.party_id]
        if len(eintrag.objekte) >= MAX_OBJEKTE:
            continue
        rolle = ROLLE_LABEL.get(r.role, r.role)
        text = f"{r.property.name} ({rolle})"
        if text not in eintrag.objekte:
            eintrag.objekte.append(text)

    return ergebnis
