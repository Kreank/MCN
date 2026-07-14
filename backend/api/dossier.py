"""Dossier-API: alles zu EINER Entität in EINEM Aufruf — deterministisch, getort.

Vier Endpunkte (Konzeptpapier `docs/ki-first-konzept.html`, Slice 3):

    GET /api/dossier/kontakt/{party_id}
    GET /api/dossier/liegenschaft/{property_id}
    GET /api/dossier/projekt/{project_id}
    GET /api/dossier/auftrag/{work_order_id}

Rein lesend. Die Rechenarbeit macht `db_core/services/dossier.py`; hier stehen nur
Schemata und die Rechteprüfung.

## Das Rechtemodell dieses Routers (zwei Stufen, bewusst getrennt)

**1. Der KERN ist getort**: Kontakt→`identity/LESEN`, Liegenschaft→`property/LESEN`,
Projekt und Auftrag→`workflow/LESEN`. Fehlt das Recht: **403**, keine Antwort.

**Neu seit der Objektsicht (Migration 0099):** Ein Konto mit row_scope **EIGENE**
bekommt das Dossier **seiner Objekte** — der Liegenschaften, an denen es je einen
Einsatz hatte (`db_core/services/objektsicht.py`). Vorher galt hier „ein MONTEUR
bekommt gar kein Dossier"; das war der Grund, aus dem er zur „Heizkörper
kalt"-Meldung fuhr, ohne den Bericht von vorgestern und die Zentralanlage zu kennen.
Die Entität selbst wird deshalb mit `require_scoped` + Objekt-Guard geprüft:
**fremdes Objekt → 404** (nicht 403 — die Existenz wird nicht verraten).

**2. Jeder weitere Baustein prüft SEIN eigenes Modul** — weich, über
`_modul(request, ...)`: Fehlt das Recht, ist der Baustein `null` und ein Flag
`<baustein>_sichtbar: false` sagt, **warum** er fehlt. Es gibt hier bewusst **keinen**
dritten Weg:

    * Kein 403 auf die ganze Antwort, nur weil ein Teil fehlt (die Disposition
      soll das Projekt sehen, auch ohne Umsatzrecht).
    * Kein ungetorter Teil (der offene Posten kommt nie ohne `invoicing/LESEN`).
    * Kein stilles Weglassen (ein fehlendes Feld wäre von „es gibt nichts"
      nicht zu unterscheiden — deshalb das Flag).

| Baustein | Modul | Objektsicht ('EIGENE') |
|---|---|---|
| Kontaktdaten, Adressen, Kontaktwege, Ansprechpartner | `identity` | ja (nur Kontakte an meinen Objekten) |
| Liegenschaft, Gebäude/Einheiten/Anlagen, Beteiligte | `property` | ja (nur meine Objekte) |
| Vorgänge, Aufträge, Einsätze, Zeiten, Berichte, Soll-Ist | `workflow` | ja (nur an meinen Objekten) |
| Aufgaben | `workflow` | **nein** — `workflow.task` hängt an keinem Objekt |
| offene Posten, Zahlungsverhalten, Angebote/Rechnungen, Abrechnungsstand | `invoicing` | **nein** |
| Marge / Deckungsbeitrag (Umsatz **und** EK) | `invoicing` **+** `pricing` | **nein** |
| Wartung, Prüffristen, Gewährleistung | `maintenance` | **ja** (Migration 0100 — kein Geld im Schema) |
| Dokumente am Objekt/Auftrag | `content` | ja |
| Dokumente am Projekt/Kontakt, Kommunikation | `content` | **nein** (nicht objektgebunden) |

**Die Geld-Bausteine fallen für die Objektsicht von SELBST weg** — nicht durch eine
Filterzeile, die jemand vergessen könnte, sondern weil die Rolle MONTEUR auf
`invoicing`/`pricing` **kein einziges Recht** hat (Migration 0026, unverändert).
„Angebot ohne Preise, nur Mengen" ist ein eigener, späterer Slice.

**Fremde/unbekannte Entität → 404, nicht 403** — sonst verriete die Antwort deren
Existenz (Hausregel).
"""
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from ninja import Router, Schema
from ninja.errors import HttpError

from api.auftrag import OffeneAbrechnungOut, guard_auftrag
from api.objektgrenze import guard_objekt, guard_party, guard_projekt
from api.permissions import actor_id, check, require_scoped
from api.site_report import SollIstOut
from db_core.models import WorkOrder
from db_core.services import dossier as dossier_service

router = Router()


# ---------------------------------------------------------------------------
# Rechte → Sicht
# ---------------------------------------------------------------------------

def _modul(request, modul):
    """(darf_alles, darf_nur_eigene) für ein Modul — weich, ohne Ausnahme.

    `check` ist fail-closed und liefert bei row_scope EIGENE None; für die
    Objektsicht brauchen wir den Scope aber **weich** — deshalb zusätzlich
    `require_scoped` in einem try. Genau dasselbe Muster wie in `api/suche.py`.
    """
    if check(request, modul, "LESEN") is not None:
        return True, False
    try:
        _, scope = require_scoped(request, modul, "LESEN")
    except HttpError:
        return False, False
    return False, scope == "EIGENE"


def _sicht(request, *, kern_modul):
    """Kern-Scope zurückgeben und die Sicht auf alle Module bauen.

    Gibt `(actor, scope, sicht)` zurück. Der Aufrufer MUSS den Scope mit einem
    Objekt-Guard umsetzen — `require_scoped` ohne Guard wäre hier besonders
    verheerend, weil ein Dossier per Konstruktion alles bündelt.
    """
    actor, kern_scope = require_scoped(request, kern_modul, "LESEN")

    identity_alle, identity_eigene = _modul(request, "identity")
    property_alle, property_eigene = _modul(request, "property")
    workflow_alle, workflow_eigene = _modul(request, "workflow")
    content_alle, content_eigene = _modul(request, "content")
    maintenance_alle, maintenance_eigene = _modul(request, "maintenance")

    sicht = dossier_service.Sicht(
        identity=identity_alle,
        property=property_alle,
        workflow=workflow_alle,
        content=content_alle,
        maintenance=maintenance_alle,
        identity_eigene=identity_eigene,
        property_eigene=property_eigene,
        workflow_eigene=workflow_eigene,
        content_eigene=content_eigene,
        maintenance_eigene=maintenance_eigene,
        # GELD: die EINZIGE Ausnahme von „er darf alles sehen" — keine
        # EIGENE-Variante, hartes `check` (row_scope EIGENE → None).
        invoicing=check(request, "invoicing", "LESEN") is not None,
        pricing=check(request, "pricing", "LESEN") is not None,
        actor_id=actor_id(request),
    )
    return actor, kern_scope, sicht


def _liefern(bauen):
    try:
        return bauen()
    except dossier_service.DossierNichtGefunden as exc:
        raise HttpError(404, str(exc))


# ---------------------------------------------------------------------------
# Gemeinsame Schemata
# ---------------------------------------------------------------------------

class OffenerPostenOut(Schema):
    invoice_id: UUID
    invoice_number: str | None = None
    invoice_type: str
    invoice_date: date | None = None
    due_date: date | None = None
    gross_total: Decimal
    paid_total: Decimal
    # Storno/Gutschrift zu dieser Rechnung (≤ 0). Der Storno nimmt die Rechnung ganz
    # aus der Liste; eine Gutschrift MINDERT hier den geforderten Betrag.
    credit_total: Decimal
    open_amount: Decimal
    payment_status: str
    is_overdue: bool
    days_overdue: int | None = None
    dunning_level: int | None = None


class OffenePostenOut(Schema):
    posten: list[OffenerPostenOut]
    anzahl: int
    summe_offen: Decimal
    anzahl_ueberfaellig: int
    summe_ueberfaellig: Decimal


class ZahlungsverhaltenOut(Schema):
    """Wie zahlt dieser Kunde? Grundlage: veröffentlichte, nicht stornierte
    Forderungen (Korrekturbelege sind keine Forderung).

    `durchschnittliche_verzoegerung_tage` ist **None**, solange keine Rechnung
    bezahlt wurde — nicht 0. Eine 0 hieße „zahlt pünktlich" und wäre eine
    Behauptung über einen Kunden, über den wir nichts wissen. `bewertete_rechnungen`
    sagt, auf wie vielen Rechnungen der Durchschnitt beruht.
    """

    rechnungen_gesamt: int
    bezahlt_anzahl: int
    offen_anzahl: int
    ueberfaellig_anzahl: int
    summe_offen: Decimal
    summe_ueberfaellig: Decimal
    durchschnittliche_verzoegerung_tage: float | None = None
    groesste_verzoegerung_tage: int | None = None
    bewertete_rechnungen: int


class DokumentOut(Schema):
    file_id: UUID
    link_id: UUID
    original_filename: str
    mime_type: str
    size_bytes: int
    link_category: str | None = None
    uploaded_at: datetime
    uploaded_by: str | None = None


class VorgangOut(Schema):
    id: UUID
    case_number: str
    subject: str
    status: str
    priority: str
    received_at: datetime
    is_offen: bool
    property_id: UUID | None = None
    project_id: UUID | None = None


class AuftragZeileOut(Schema):
    id: UUID
    order_number: str
    title: str
    status: str
    priority: str
    billing_mode: str
    is_offen: bool
    desired_date: date | None = None
    property_id: UUID | None = None
    project_id: UUID | None = None


class EinsatzOut(Schema):
    id: UUID
    job_number: str
    title: str | None = None
    status: str
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    work_order_id: UUID | None = None
    zugewiesen: list[str]


class AufgabeOut(Schema):
    id: UUID
    title: str
    status: str
    due_date: date | None = None
    assigned_to: str | None = None


class BelegZeileOut(Schema):
    id: UUID
    invoice_number: str | None = None
    invoice_type: str
    status: str
    invoice_date: date | None = None
    net_total: Decimal | None = None
    gross_total: Decimal | None = None
    work_order_id: UUID | None = None


class AngebotZeileOut(Schema):
    id: UUID
    quote_number: str | None = None
    title: str
    status: str
    quote_date: date | None = None
    net_total: Decimal | None = None
    gross_total: Decimal | None = None
    work_order_id: UUID | None = None


class MargeOut(Schema):
    """Deckungsbeitrag/Marge — `None` heißt UNBEKANNT, nie 0 und nie 100 %.

    Bezugsgröße ist ausschließlich der Netto-Anteil MIT hinterlegtem EK
    (`net_mit_ek`); `positionen_ohne_ek`/`ek_vollstaendig` weisen die Lücke aus.
    Identisch mit dem Marge-Block der Auswertungen (dieselbe Rechenstelle).
    """

    net_total: str
    net_mit_ek: str
    net_ohne_ek: str
    ek_total: str
    deckungsbeitrag: str | None = None
    marge_prozent: str | None = None
    positionen: int
    positionen_ohne_ek: int
    ek_vollstaendig: bool


# ---------------------------------------------------------------------------
# Kontakt-Dossier
# ---------------------------------------------------------------------------

class KontaktKernOut(Schema):
    id: UUID
    party_type: str
    display_name: str
    status: str
    first_name: str | None = None
    last_name: str | None = None
    salutation: str | None = None
    legal_name: str | None = None
    organization_type: str | None = None
    vat_id: str | None = None
    acquisition_source: str | None = None


class DossierAdresseOut(Schema):
    address_type: str
    is_primary: bool
    street: str
    house_number: str | None = None
    postal_code: str
    city: str
    country_code: str


class KontaktwegOut(Schema):
    contact_type: str
    value: str
    label: str | None = None
    is_primary: bool


class AnsprechpartnerOut(Schema):
    person_party_id: UUID
    display_name: str
    valid_from: date


class PartyLiegenschaftOut(Schema):
    property_id: UUID
    property_number: str
    name: str
    city: str
    role: str
    valid_from: date
    valid_until: date | None = None
    is_current: bool


class KommunikationOut(Schema):
    id: UUID
    channel: str
    direction: str
    subject: str | None = None
    occurred_at: datetime
    counterpart: str | None = None


class KontaktDossierOut(Schema):
    kontakt: KontaktKernOut
    adressen: list[DossierAdresseOut]
    kontaktwege: list[KontaktwegOut]
    ansprechpartner: list[AnsprechpartnerOut]
    liegenschaften_sichtbar: bool
    liegenschaften: list[PartyLiegenschaftOut] | None = None
    vorgaenge_sichtbar: bool
    vorgaenge: list[VorgangOut] | None = None
    auftraege: list[AuftragZeileOut] | None = None
    aufgaben_sichtbar: bool
    aufgaben: list[AufgabeOut] | None = None
    offene_posten_sichtbar: bool
    offene_posten: OffenePostenOut | None = None
    zahlungsverhalten_sichtbar: bool
    zahlungsverhalten: ZahlungsverhaltenOut | None = None
    kommunikation_sichtbar: bool
    kommunikation: list[KommunikationOut] | None = None
    dokumente_sichtbar: bool
    dokumente: list[DokumentOut] | None = None


@router.get("/kontakt/{party_id}", response=KontaktDossierOut)
def kontakt_dossier(request, party_id: UUID):
    """Dossier eines Kontakts (Kern: `identity/LESEN`).

    Stammdaten, Adressen, Kontaktwege und Ansprechpartner sind der Kern. Rollen an
    Liegenschaften (`property`), offene Vorgänge/Aufträge/Aufgaben (`workflow`),
    offene Posten + **Zahlungsverhalten** (`invoicing`) sowie Kommunikation und
    Dokumente (`content`) kommen nur mit dem jeweiligen Recht — sonst `null` +
    `_sichtbar: false`.

    Objektsicht ('EIGENE'): nur Kontakte, die an einem meiner Objekte hängen (der
    Mieter, den ich anrufen muss) — jeder andere Kontakt ist **404**.
    """
    actor, scope, sicht = _sicht(request, kern_modul="identity")
    guard_party(scope, actor, party_id)
    return _liefern(lambda: dossier_service.kontakt_dossier(party_id, sicht))


# ---------------------------------------------------------------------------
# Liegenschafts-Dossier
# ---------------------------------------------------------------------------

class LiegenschaftKernOut(Schema):
    id: UUID
    property_number: str
    name: str
    property_type: str
    status: str
    street: str
    house_number: str | None = None
    postal_code: str
    city: str


class EinheitOut(Schema):
    unit_id: UUID
    unit_type: str
    unit_number: str


class GebaeudeOut(Schema):
    building_id: UUID
    building_number: str
    name: str | None = None
    units: list[EinheitOut]


class AnlageOut(Schema):
    id: UUID
    name: str
    asset_type: str | None = None
    building_id: UUID | None = None
    unit_id: UUID | None = None


class BeteiligterOut(Schema):
    party_id: UUID
    display_name: str
    role: str
    valid_from: date
    valid_until: date | None = None
    is_current: bool


class ZutrittshinweisOut(Schema):
    """Zutrittshinweis MIT HERKUNFT.

    Das Schema führt **kein** Zutrittsfeld an der Liegenschaft — nur am Einsatz
    (`service_job.access_instructions`). Deshalb wird hier nicht ein „Hinweis der
    Liegenschaft" behauptet, sondern jeder Hinweis mit seinem Einsatz und dessen
    Termin ausgewiesen: Der Nutzer sieht, woher die Angabe stammt und wie alt sie
    ist. Ein erfundenes Objektfeld wäre eine Behauptung, ein „aktuellster Hinweis"
    eine Vermutung.
    """

    service_job_id: UUID
    job_number: str
    scheduled_start: datetime | None = None
    work_order_id: UUID | None = None
    work_order_number: str | None = None
    hinweis: str


class FaelligkeitOut(Schema):
    id: UUID
    kind: str  # WARTUNG | PRUEFUNG | GEWAEHRLEISTUNG
    title: str
    due_date: date
    status: str
    is_ueberfaellig: bool


class WartungsvertragOut(Schema):
    id: UUID
    contract_number: str
    name: str
    status: str
    interval_kind: str
    next_due_date: date | None = None
    due_action: str


class LiegenschaftDossierOut(Schema):
    liegenschaft: LiegenschaftKernOut
    gebaeude: list[GebaeudeOut]
    anlagen: list[AnlageOut]
    beteiligte: list[BeteiligterOut]
    vorgaenge_sichtbar: bool
    vorgaenge: list[VorgangOut] | None = None
    auftraege: list[AuftragZeileOut] | None = None
    einsaetze: list[EinsatzOut] | None = None
    zutrittshinweise: list[ZutrittshinweisOut] | None = None
    wartung_sichtbar: bool
    faelligkeiten: list[FaelligkeitOut] | None = None
    wartungsvertraege: list[WartungsvertragOut] | None = None
    offene_posten_sichtbar: bool
    offene_posten: OffenePostenOut | None = None
    dokumente_sichtbar: bool
    dokumente: list[DokumentOut] | None = None


@router.get("/liegenschaft/{property_id}", response=LiegenschaftDossierOut)
def liegenschaft_dossier(request, property_id: UUID):
    """Dossier einer Liegenschaft (Kern: `property/LESEN`).

    Struktur (Gebäude/Einheiten/Anlagen) und Beteiligte sind der Kern — genau wie
    in der bestehenden Liegenschaftsmappe. Vorgänge/Aufträge/Einsätze inkl.
    **Zutrittshinweisen mit Herkunft** (`workflow`), Wartung/Prüffristen/
    Gewährleistung (`maintenance`), offene Posten (`invoicing`) und Dokumente
    (`content`) kommen nur mit dem jeweiligen Recht.

    **Das ist die Gesamtsicht, für die dieser Slice gebaut wurde.** Objektsicht
    ('EIGENE'): meine Objekte — mit Vorgängen, Aufträgen, Einsätzen und Berichten
    **der Kollegen** (die Heizkörper-Historie), aber **ohne** Geld-Bausteine (kein
    `invoicing`-Recht → offene Posten `null`). Fremdes Objekt → 404.
    """
    actor, scope, sicht = _sicht(request, kern_modul="property")
    guard_objekt(scope, actor, property_id)
    return _liefern(lambda: dossier_service.liegenschaft_dossier(property_id, sicht))


# ---------------------------------------------------------------------------
# Projekt-Dossier
# ---------------------------------------------------------------------------

class ProjektKernOut(Schema):
    id: UUID
    project_number: str
    name: str
    status: str
    start_date: date | None = None
    target_end_date: date | None = None
    category: str | None = None


class ProjektLiegenschaftOut(Schema):
    property_id: UUID
    property_number: str
    name: str
    city: str


class ChecklistItemOut(Schema):
    id: UUID
    position: int
    label: str
    is_done: bool
    done_at: datetime | None = None


class ChecklistOut(Schema):
    id: UUID
    name: str
    items: list[ChecklistItemOut]


class LogbuchOut(Schema):
    id: UUID
    category: str
    entry: str
    created_at: datetime
    author: str | None = None


class AbschlagOut(Schema):
    """Anrechenbarer Abschlag — aus `beleg.anrechenbare_abschlaege`, nicht neu gerechnet."""

    work_order_id: UUID
    invoice_id: UUID
    invoice_number: str | None = None
    invoice_type: str
    invoice_date: date | None = None
    net_total: Decimal | None = None
    gross_total: Decimal | None = None
    vorgemerkt: bool


class ProjektDossierOut(Schema):
    projekt: ProjektKernOut
    liegenschaften: list[ProjektLiegenschaftOut]
    vorgaenge: list[VorgangOut]
    auftraege: list[AuftragZeileOut]
    # Checklisten und Logbuch sind FREITEXT OHNE OBJEKTBEZUG — sie lassen sich als
    # einzige Bausteine nicht auf „meine Objekte" begrenzen (ein Eintrag kann ein
    # fremdes Objekt beim Namen nennen). Für die Objektsicht deshalb `null` +
    # `projektsteuerung_sichtbar: false`, nicht eine stille leere Liste.
    projektsteuerung_sichtbar: bool
    checklisten: list[ChecklistOut] | None = None
    logbuch: list[LogbuchOut] | None = None
    aufgaben: list[AufgabeOut]
    belege_sichtbar: bool
    angebote: list[AngebotZeileOut] | None = None
    rechnungen: list[BelegZeileOut] | None = None
    anrechenbare_abschlaege: list[AbschlagOut] | None = None
    offene_posten_sichtbar: bool
    offene_posten: OffenePostenOut | None = None
    marge_sichtbar: bool
    marge: MargeOut | None = None
    geplante_marge: MargeOut | None = None
    dokumente_sichtbar: bool
    dokumente: list[DokumentOut] | None = None


@router.get("/projekt/{project_id}", response=ProjektDossierOut)
def projekt_dossier(request, project_id: UUID):
    """Dossier eines Projekts (Kern: `workflow/LESEN`).

    Vorgänge, Aufträge, Liegenschaften, Checklisten, Logbuch und Aufgaben sind der
    Kern. Angebote/Rechnungen inkl. **Abschlagslage** und offene Posten brauchen
    `invoicing/LESEN`; die **Marge** zusätzlich `pricing/LESEN` (sie ist Umsatz
    minus Einkauf — ohne EK-Recht gibt es keine Marge, und ohne EK-Daten ist sie
    `null`, nie 0 %).

    Objektsicht ('EIGENE'): Projekte, die **mindestens eine** meiner Liegenschaften
    tragen — und darin **nur** meine Objekte samt deren Vorgängen und Aufträgen (der
    Service filtert nach; siehe `dossier_service.projekt_dossier`). Sonst wäre die
    Projektakte der Nebeneingang zum fremden Objekt.
    """
    actor, scope, sicht = _sicht(request, kern_modul="workflow")
    guard_projekt(scope, actor, project_id)
    return _liefern(lambda: dossier_service.projekt_dossier(project_id, sicht))


# ---------------------------------------------------------------------------
# Auftrags-Dossier
# ---------------------------------------------------------------------------

class AuftragKernOut(Schema):
    id: UUID
    order_number: str
    title: str
    description: str | None = None
    status: str
    priority: str
    billing_mode: str
    is_emergency: bool
    desired_date: date | None = None
    responsibility_scope: str
    responsibility_confirmed_at: datetime | None = None
    order_evidence_reference: str | None = None
    property_id: UUID
    property_name: str
    property_city: str
    project_id: UUID | None = None
    project_name: str | None = None
    service_case_number: str | None = None


class UebergangOut(Schema):
    """Ein möglicher Statusübergang — aus `auftrag.WORK_ORDER_TRANSITIONS`.

    **Möglich heißt nicht erlaubt und nicht zulässig.** Ob der Akteur ihn ausführen
    darf, entscheidet die Rechtematrix; ob er fachlich durchgeht, entscheiden die
    DB-Tore (Beauftragungsnachweis, Verantwortungsbereich, Beteiligte). Diese Liste
    sagt nur, welche Ziele der Statusautomat vom aktuellen Status aus überhaupt
    kennt — und ob der Übergang begründungspflichtig ist.
    """

    to_status: str
    begruendung_pflicht: bool


class AuftragBeteiligterOut(Schema):
    party_id: UUID
    display_name: str
    role: str
    is_primary: bool


class ZeiteintragOut(Schema):
    id: UUID
    started_at: datetime
    ended_at: datetime | None = None
    # Laufende Buchung → Dauer unbekannt (null), nicht 0.
    stunden: Decimal | None = None
    kategorie: str
    is_work_time: bool
    mitarbeiter: str
    note: str | None = None


class ZeitenOut(Schema):
    eintraege: list[ZeiteintragOut]
    laufende: int
    # Keine abgeschlossene Arbeitszeitbuchung → null (unbekannt), nie 0,0 h.
    summe_arbeitsstunden: Decimal | None = None


class MaterialOut(Schema):
    id: UUID
    description: str
    quantity: Decimal
    unit: str
    note: str | None = None
    service_job_id: UUID


class BerichtOut(Schema):
    id: UUID
    report_date: date
    status: str
    activity_text: str
    hours_worked: Decimal | None = None
    author: str | None = None
    signed_at: datetime | None = None
    signed_by_name: str | None = None


class AuftragDossierOut(Schema):
    auftrag: AuftragKernOut
    moegliche_uebergaenge: list[UebergangOut]
    beteiligte: list[AuftragBeteiligterOut]
    einsaetze: list[EinsatzOut]
    zeiten: ZeitenOut
    material: list[MaterialOut]
    berichte: list[BerichtOut]
    # Dieselbe Rechenstelle wie GET /workflow/work_orders/{id}/soll-ist.
    soll_ist: SollIstOut
    abrechnung_sichtbar: bool
    # Dieselbe Rechenstelle wie GET /workflow/work_orders/{id}/offene-abrechnung.
    abrechnung: OffeneAbrechnungOut | None = None
    belege_sichtbar: bool
    angebote: list[AngebotZeileOut] | None = None
    rechnungen: list[BelegZeileOut] | None = None
    offene_posten_sichtbar: bool
    offene_posten: OffenePostenOut | None = None
    dokumente_sichtbar: bool
    dokumente: list[DokumentOut] | None = None


@router.get("/auftrag/{work_order_id}", response=AuftragDossierOut)
def auftrag_dossier(request, work_order_id: UUID):
    """Dossier eines Auftrags (Kern: `workflow/LESEN`).

    Status **und mögliche Übergänge**, Beteiligte, Einsätze/Termine, erfasste
    Zeiten und Material, Baustellenberichte und der **Soll-Ist-Abgleich** sind der
    Kern (alles ohne Geldbetrag). Der **Abrechnungsstand** (`offene_abrechnung` —
    er führt Einzelpreise), Angebote/Rechnungen und die offenen Posten brauchen
    `invoicing/LESEN`; Dokumente `content/LESEN`.

    Objektsicht ('EIGENE'): Aufträge an meinen Objekten — **auch die der Kollegen**
    (Zeiten, Material, Berichte, Soll-Ist: alles ohne Geldbetrag). Fremdes Objekt →
    404. Preise bleiben weg, weil `invoicing` fehlt.
    """
    actor, scope, sicht = _sicht(request, kern_modul="workflow")
    if not WorkOrder.objects.filter(id=work_order_id).exists():
        raise HttpError(404, "Auftrag nicht gefunden.")
    guard_auftrag(work_order_id, actor, scope)
    return _liefern(lambda: dossier_service.auftrag_dossier(work_order_id, sicht))
