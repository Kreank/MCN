"""Der **Steckbrief** einer Liegenschaft — Kontext für die Trefferliste.

## Warum es das gibt

Ein Mieter ruft an. Der Mitarbeiter tippt die Adresse in den Liegenschafts-Picker
und bekommt — im besten Fall — drei Zeilen „WEG Albrechtstraße". Welche davon ist
die richtige? Ohne **Eigentümer, Verwaltung und Telefonnummer** ist das eine
Ratefrage, und die falsche Antwort heißt: er legt eine **Dublette** an.

Der Steckbrief beantwortet genau diese Frage, ohne dass der Datensatz geöffnet
werden muss. Er ist reine Anzeige — er zieht keine neue Grenze und hebt keine auf;
die Zeilenmenge begrenzt der Aufrufer (`objektsicht.begrenzen`), bevor er hier
hereinreicht.

## Die eine Konstruktionsregel: **wenige Abfragen, egal wie viele Zeilen**

Der Picker zeigt 25 Liegenschaften. Würde jede ihre Rollen, ihr Mandat, ihre
Kontaktwege, ihre Einheiten und ihre Gebäude einzeln nachladen, wären das 125
zusätzliche Abfragen je Tastendruck. Deshalb sammelt dieses Modul **in Bündeln**:
sechs Abfragen für eine Liegenschaft, sechs für tausend.
`db_core/tests/test_property_steckbrief.py` hält das mit `assertNumQueries` fest.

## Verwaltung ≠ Eigentümer (die Unterscheidung, die hier zählt)

`property.property_party_role` kennt **keine** Verwaltung — die hängt
ausschließlich am **Mandat** (`management.management_mandate`, siehe
`services/verwaltung.py`). Im Demo-Szenario gehört die Badensche Straße 53 der
*WEG Badensche Straße 53* (Rolle COMMUNITY_OF_OWNERS) und wird von der *Stegos
Immobilien GmbH* verwaltet (Mandat). Beides in ein Feld zu werfen wäre bequem und
falsch: Der Anrufer will die **Verwaltung** erreichen, die Rechnung geht an die
**WEG**.

## Die Telefonnummer: drei Quellen, feste Reihenfolge

1. der **Standardkontakt des Mandats** (`default_contact_party`) — die Person, die
   der Vertrag als Ansprechpartner benennt,
2. sonst die **Verwaltung** selbst (`management_party`),
3. sonst der erste aktuelle **Eigentümer**.

`telefon_quelle` sagt immer dazu, **wessen** Nummer das ist („Verwaltung Stegos
Immobilien GmbH"). Eine Nummer ohne Herkunft wäre im Zweifel die falsche.
"""
from dataclasses import dataclass, field
from datetime import date

from django.db.models import Count

from db_core.models import (
    Building,
    ContactPoint,
    ManagementMandate,
    Property,
    PropertyPartyRole,
    Unit,
)

#: Rollen aus `property.property_party_role`, die „Eigentümer" bedeuten.
#: OPERATOR (Betreiber) und CARETAKER (Hausmeister) gehören ausdrücklich **nicht**
#: dazu — ein Hausmeister im Eigentümerfeld wäre eine falsche Auskunft.
EIGENTUEMER_ROLLEN = ("PROPERTY_OWNER", "COMMUNITY_OF_OWNERS")

#: Deutsche Beschriftung der Rollen (für `telefon_quelle` und Kontakt-Steckbriefe).
ROLLE_LABEL = {
    "COMMUNITY_OF_OWNERS": "Eigentümergemeinschaft",
    "PROPERTY_OWNER": "Eigentümer",
    "OPERATOR": "Betreiber",
    "CARETAKER": "Hausmeister",
}

#: Kontaktarten, die als „Telefon" gelten (`identity.contact_point.contact_type`).
TELEFON_TYPEN = ("PHONE", "MOBILE")


@dataclass
class Steckbrief:
    """Anzeigekontext einer Liegenschaft — alle Felder optional/leer belegbar."""

    address_line: str | None = None
    eigentuemer: list = field(default_factory=list)
    verwaltung: str | None = None
    telefon: str | None = None
    telefon_quelle: str | None = None
    einheiten_anzahl: int = 0
    gebaeude_adressen: list = field(default_factory=list)


def adresszeile(adresse):
    """„Albrechtstraße 30, 12167 Berlin" — leere Teile fallen weg."""
    if adresse is None:
        return None
    strasse = " ".join(x for x in (adresse.street, adresse.house_number) if x)
    ort = " ".join(x for x in (adresse.postal_code, adresse.city) if x)
    return ", ".join(x for x in (strasse, ort) if x) or None


def gilt(zeile, stichtag):
    """Zeitraumsemantik `[valid_from, valid_until)` — die obere Grenze ist exklusiv."""
    if zeile.valid_from and zeile.valid_from > stichtag:
        return False
    return zeile.valid_until is None or zeile.valid_until > stichtag


def steckbriefe(property_ids, *, stichtag=None):
    """Steckbriefe für viele Liegenschaften — **sechs** Abfragen, unabhängig von N.

    Gibt `dict[property_id, Steckbrief]` zurück; für eine unbekannte ID fehlt der
    Eintrag (der Aufrufer nimmt dann einen leeren `Steckbrief()`).
    """
    ids = list(dict.fromkeys(property_ids))
    if not ids:
        return {}
    stichtag = stichtag or date.today()

    # (1) Die Liegenschaften selbst — für Adresszeile und Gebäudevergleich.
    props = {
        p.id: p
        for p in Property.objects.filter(id__in=ids).select_related("address")
    }

    # (2) Aktuelle Beteiligtenrollen (Eigentümer/WEG).
    rollen = {pid: [] for pid in ids}
    for r in (
        PropertyPartyRole.objects
        .filter(property_id__in=ids, role__in=EIGENTUEMER_ROLLEN)
        .select_related("party")
        .order_by("-valid_from", "id")
    ):
        if gilt(r, stichtag):
            rollen[r.property_id].append(r)

    # (3) Das geltende Mandat je Liegenschaft. „Geltend" ist mehr als
    #     status='ACTIVE': Ein Mandat mit abgelaufenem valid_until gilt nicht mehr,
    #     auch wenn niemand den Status nachgezogen hat (Muster aus
    #     services/verwaltung.py::mandate_der_liegenschaft).
    mandate = {}
    for m in (
        ManagementMandate.objects
        .filter(property_id__in=ids)
        .exclude(status="ENDED")
        .select_related("management_party", "default_contact_party")
        .order_by("-valid_from", "id")
    ):
        if gilt(m, stichtag) and m.property_id not in mandate:
            mandate[m.property_id] = m

    # (4) Einheitenzahl in EINER Aggregatabfrage.
    einheiten = {pid: 0 for pid in ids}
    for zeile in (
        Unit.objects.filter(property_id__in=ids)
        .values("property_id")
        .annotate(anzahl=Count("id"))
    ):
        einheiten[zeile["property_id"]] = zeile["anzahl"]

    # (5) Gebäudeadressen (nur Gebäude, die überhaupt eine eigene Adresse tragen).
    gebaeude = {pid: [] for pid in ids}
    for b in (
        Building.objects.filter(property_id__in=ids, address__isnull=False)
        .select_related("address")
        .order_by("building_number", "id")
    ):
        gebaeude[b.property_id].append(b)

    # (6) Telefonnummern aller in Frage kommenden Parties — ein Bündel für alle
    #     Liegenschaften zusammen (deshalb erst jetzt, nachdem 2 und 3 die
    #     Kandidaten kennen).
    kandidaten = set()
    for m in mandate.values():
        kandidaten.add(m.default_contact_party_id)
        kandidaten.add(m.management_party_id)
    for liste in rollen.values():
        for r in liste:
            kandidaten.add(r.party_id)
    telefone = _telefone(kandidaten, stichtag)

    ergebnis = {}
    for pid in ids:
        prop = props.get(pid)
        if prop is None:
            continue
        ergebnis[pid] = _bauen(
            prop, rollen[pid], mandate.get(pid), einheiten[pid], gebaeude[pid],
            telefone,
        )
    return ergebnis


def _telefone(party_ids, stichtag):
    """`dict[party_id, nummer]` — primärer aktueller PHONE/MOBILE, sonst irgendeiner.

    Eine Abfrage für alle Parties. Die Sortierung entscheidet: `is_primary`
    absteigend zuerst, danach der jüngere Eintrag — die erste Zeile je Party ist
    damit die beste, und der Rest wird verworfen.
    """
    if not party_ids:
        return {}
    treffer = {}
    for cp in (
        ContactPoint.objects
        .filter(party_id__in=party_ids, contact_type__in=TELEFON_TYPEN)
        .order_by("-is_primary", "-valid_from", "id")
    ):
        if cp.party_id in treffer or not gilt(cp, stichtag):
            continue
        treffer[cp.party_id] = cp.value
    return treffer


def _bauen(prop, rollen, mandat, einheiten_anzahl, gebaeude, telefone):
    eigen_adresse = adresszeile(prop.address)

    eigentuemer = []
    for r in rollen:
        name = r.party.display_name
        if name not in eigentuemer:
            eigentuemer.append(name)

    # Telefon: Standardkontakt des Mandats → Verwaltung → erster Eigentümer.
    telefon = telefon_quelle = None
    if mandat is not None:
        for partei, label in (
            (mandat.default_contact_party, "Verwaltung"),
            (mandat.management_party, "Verwaltung"),
        ):
            nummer = telefone.get(partei.id)
            if nummer:
                telefon = nummer
                telefon_quelle = f"{label} {partei.display_name}"
                break
    if telefon is None:
        for r in rollen:
            nummer = telefone.get(r.party_id)
            if nummer:
                telefon = nummer
                rolle = ROLLE_LABEL.get(r.role, r.role)
                telefon_quelle = f"{rolle} {r.party.display_name}"
                break

    # Gebäudeadressen NUR, soweit sie von der Liegenschaftsadresse abweichen —
    # sonst stünde an jeder Zeile zweimal dasselbe und die eine echte Abweichung
    # (der WEG-Fall: Gebäude in der Nachbarhausnummer) ginge darin unter.
    gebaeude_adressen = []
    for b in gebaeude:
        zeile = adresszeile(b.address)
        if zeile and zeile != eigen_adresse and zeile not in gebaeude_adressen:
            gebaeude_adressen.append(zeile)

    return Steckbrief(
        address_line=eigen_adresse,
        eigentuemer=eigentuemer,
        verwaltung=mandat.management_party.display_name if mandat else None,
        telefon=telefon,
        telefon_quelle=telefon_quelle,
        einheiten_anzahl=einheiten_anzahl,
        gebaeude_adressen=gebaeude_adressen,
    )
