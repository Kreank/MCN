"""Beauftragungsvollmacht — `management.party_authority` (Beschluss A-26).

## Die Frage, die am Telefon zählt

Sascha zu Arbeitspaket AP2: *„Das sind Daten, die der Dispo schnell wissen
will."* Ruft die Hausverwaltung an und sagt „machen Sie mal", ist die Frage
nicht, ob sie nett ist, sondern ob sie **darf** — und bis zu welchem Betrag.
Ohne diese Angabe nimmt der Disponent einen Auftrag entgegen, den am Ende
niemand bezahlen will.

Die Tabelle steht seit Migration 0006 und war bis AP2 von **null** Backend-Zeilen
benutzt.

## Drei Befugnisarten, die man nicht verwechseln darf

* **ORDER** — darf beauftragen. Der Regelfall.
* **APPROVAL** — darf freigeben, aber nicht selbst beauftragen. Wer nur
  freigeben darf, kann keinen Auftrag auslösen; das ist der Unterschied
  zwischen „ja, machen Sie" und „ja, ich genehmige das".
* **EMERGENCY_ORDER** — darf im Notfall beauftragen. Der Rohrbruch um drei Uhr
  nachts wartet nicht auf die Eigentümerversammlung.

  A-26 sieht dafür einen Nachweis in Textform vor; `evidence_document_id` ist
  das Feld dafür. **Erzwungen wird er nicht** — weder von der DDL noch hier.
  Das ist bewusst so gelassen, solange das Dokumentmodul nicht angebunden ist
  (die Spalte trägt bis heute keinen Fremdschlüssel).

## Die Wertgrenze hängt an der Befugnis, nicht an der Person

Dieselbe Verwaltung kann für eine WEG bis 5.000 € beauftragen und für eine
andere gar nicht. Deshalb steht `amount_limit` an der Vollmacht und nicht am
Kontakt — und deshalb gibt es je Liegenschaft eine eigene Antwort.

Betrag und Währung gehören zusammen (CHECK): eine Grenze ohne Währung ist keine
Aussage.

## Widerrufen heißt nicht löschen

`status = REVOKED` (widerrufen) oder `EXPIRED` (abgelaufen, verlangt ein
`valid_until` — F-10). Wer wann wie weit bevollmächtigt war, ist der Nachweis;
gelöscht wird nichts.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.db.models import Q

from db_core.db_context import business_transaction
from db_core.gate_errors import as_business_error
from db_core.models import ManagementMandate, PartyAuthority
from db_core.services._validation import ensure_party_usable

#: Codeliste `authority_type` (CHECK aus 0006).
AUTHORITY_TYPES = ("ORDER", "APPROVAL", "EMERGENCY_ORDER")

#: Codeliste `scope_type`. MANDATE verlangt ein `mandate_id` (CHECK).
SCOPE_TYPES = ("GENERAL", "MANDATE")

#: Codeliste `status`. EXPIRED verlangt ein `valid_until` (F-10).
AUTHORITY_STATUS = ("ACTIVE", "REVOKED", "EXPIRED")


class VollmachtError(ValueError):
    """Der Vollmachts-Vorgang ist fachlich unzulässig (→ 422)."""


def _aktiv_q(stichtag):
    """Gilt am Stichtag: Status ACTIVE und im Gültigkeitszeitraum."""
    return (
        Q(status="ACTIVE")
        & Q(valid_from__lte=stichtag)
        & (Q(valid_until__isnull=True) | Q(valid_until__gt=stichtag))
    )


def vollmachten_der_liegenschaft(property_id, *, nur_aktive=True, stichtag=None):
    """Die Vollmachten, die an dieser Liegenschaft gelten.

    Zwei Wege führen hierher, und beide zählen:

    * **mandatsgebunden** — die Vollmacht hängt an einem Verwaltungsmandat
      dieser Liegenschaft (`scope_type = MANDATE`).
    * **allgemein** — die Vollmacht gilt unabhängig vom Mandat
      (`scope_type = GENERAL`) und wird über die Beteiligten der Liegenschaft
      gefunden: Wer hier eine Rolle hat und jemanden bevollmächtigt hat, dessen
      Vollmacht gehört auf diese Seite.

    Ohne den zweiten Weg fehlte genau der Fall, den A-26 im Blick hat: Die
    Eigentümergemeinschaft bevollmächtigt die Verwaltung allgemein, nicht je
    Objekt.

    **Der zweite Weg braucht zwei Einschränkungen**, sonst leckt er:

    * Nur `scope_type = GENERAL`. Eine Vollmacht, die ausdrücklich an das
      Mandat einer **anderen** Liegenschaft gebunden ist, gehört nicht hierher —
      auch dann nicht, wenn ihr Geber hier zufällig eine Rolle hat. Genau das
      soll der MANDATE-Scope ja ausschließen.
    * Nur **am Stichtag gültige** Rollen. Eine 2016 beendete Eigentümerrolle
      zöge sonst bis heute jede Vollmacht dieser Partei an dieses Objekt.

    Ohne diese beiden Filter stand an einer Liegenschaft eine Vollmacht über
    99.000 €, die für ein ganz anderes Objekt erteilt worden war — die falsche
    Antwort auf die eine Frage, wegen der es diesen Bereich gibt.
    """
    tag = stichtag or date.today()
    # Die Rollengültigkeit gilt IMMER, auch bei `nur_aktive=False`: Sie sagt
    # nicht „ist die Vollmacht aktiv", sondern „gehört sie überhaupt hierher".
    rolle_gilt = (
        Q(principal_party__property_roles__property_id=property_id)
        & Q(principal_party__property_roles__valid_from__lte=tag)
        & (
            Q(principal_party__property_roles__valid_until__isnull=True)
            | Q(principal_party__property_roles__valid_until__gt=tag)
        )
    )
    qs = PartyAuthority.objects.filter(
        Q(mandate__property_id=property_id) | (Q(scope_type="GENERAL") & rolle_gilt)
    ).distinct()
    if nur_aktive:
        qs = qs.filter(_aktiv_q(tag))
    return qs.select_related("principal_party", "authorized_party", "mandate")


def get_vollmacht(authority_id):
    v = (
        PartyAuthority.objects.select_related(
            "principal_party", "authorized_party", "mandate"
        )
        .filter(id=authority_id)
        .first()
    )
    if v is None:
        raise VollmachtError("Vollmacht nicht gefunden.")
    return v


def property_id_der_vollmacht(authority_id):
    """Die Liegenschaft, an der die Vollmacht hängt — für die Objektgrenze.

    Bei einer allgemeinen Vollmacht gibt es keine; dann ist die Antwort None und
    der Aufrufer entscheidet (die API verlangt dort das Recht ohne Objektbezug).
    """
    v = (
        PartyAuthority.objects.filter(id=authority_id)
        .select_related("mandate")
        .first()
    )
    if v is None:
        return None
    return v.mandate.property_id if v.mandate_id else None


def _betrag(wert):
    if wert is None or (isinstance(wert, str) and not wert.strip()):
        return None
    try:
        betrag = Decimal(str(wert))
    except (InvalidOperation, ValueError):
        raise VollmachtError("Die Wertgrenze ist keine gültige Zahl.")
    if not betrag.is_finite() or betrag <= 0:
        raise VollmachtError(
            "Eine Wertgrenze von null oder weniger ist keine Grenze — lassen Sie "
            "das Feld leer, wenn es keine gibt."
        )
    return betrag.quantize(Decimal("0.01"))


def create_vollmacht(
    actor_app_user_id,
    *,
    principal_party_id,
    authorized_party_id,
    authority_type,
    valid_from,
    scope_type="GENERAL",
    mandate_id=None,
    amount_limit=None,
    currency=None,
    valid_until=None,
    evidence_document_id=None,
):
    """Legt eine Vollmacht an."""
    if authority_type not in AUTHORITY_TYPES:
        raise VollmachtError(f"Unbekannte Befugnisart: {authority_type}")
    if scope_type not in SCOPE_TYPES:
        raise VollmachtError(f"Unbekannter Geltungsbereich: {scope_type}")
    if principal_party_id == authorized_party_id:
        raise VollmachtError(
            "Vollmachtgeber und Bevollmächtigter müssen verschieden sein — "
            "sich selbst zu bevollmächtigen ergibt keine Aussage."
        )
    ensure_party_usable(principal_party_id, "Vollmachtgeber")
    ensure_party_usable(authorized_party_id, "Bevollmächtigter")

    if scope_type == "MANDATE":
        if mandate_id is None:
            raise VollmachtError(
                "Eine mandatsgebundene Vollmacht braucht das Mandat, für das "
                "sie gilt."
            )
        if not ManagementMandate.objects.filter(id=mandate_id).exists():
            raise VollmachtError("Mandat nicht gefunden.")
    elif mandate_id is not None:
        raise VollmachtError(
            "Eine allgemeine Vollmacht hängt an keinem Mandat. Wählen Sie den "
            "Geltungsbereich „für ein Mandat“, wenn sie nur dort gelten soll."
        )

    if valid_from is None:
        raise VollmachtError("Der Beginn der Vollmacht ist erforderlich.")
    if valid_until is not None and valid_until <= valid_from:
        raise VollmachtError("Das Ende muss nach dem Beginn liegen.")

    grenze = _betrag(amount_limit)
    waehrung = (currency or "").strip().upper() or None
    # Betrag und Währung sind ein Paar (CHECK aus 0006): Eine Grenze ohne
    # Währung ist keine Aussage, eine Währung ohne Grenze auch nicht.
    if (grenze is None) != (waehrung is None):
        if grenze is not None:
            waehrung = "EUR"
        else:
            raise VollmachtError(
                "Zur Währung gehört eine Wertgrenze. Ohne Grenze bitte beide "
                "Felder leer lassen."
            )
    if waehrung is not None and len(waehrung) != 3:
        raise VollmachtError("Die Währung ist ein dreistelliger Code, z. B. EUR.")

    with as_business_error():
        with business_transaction(actor_app_user_id):
            vollmacht = PartyAuthority.objects.create(
                id=uuid.uuid4(),
                principal_party_id=principal_party_id,
                authorized_party_id=authorized_party_id,
                mandate_id=mandate_id,
                authority_type=authority_type,
                scope_type=scope_type,
                amount_limit=grenze,
                currency=waehrung,
                valid_from=valid_from,
                valid_until=valid_until,
                evidence_document_id=evidence_document_id,
                status="ACTIVE",
            )
    return get_vollmacht(vollmacht.id)


def widerrufen(actor_app_user_id, authority_id, *, valid_until=None):
    """Widerruft eine Vollmacht — sie wird nicht gelöscht.

    Wer wann wie weit bevollmächtigt war, ist der Nachweis. Ein Widerruf ohne
    Datum wirkt ab heute.
    """
    vollmacht = get_vollmacht(authority_id)
    if vollmacht.status != "ACTIVE":
        raise VollmachtError("Diese Vollmacht ist bereits beendet.")

    ende = valid_until or date.today()
    if ende <= vollmacht.valid_from:
        # Eine noch nicht begonnene Vollmacht lässt sich sonst gar nicht mehr
        # zurücknehmen: Man müsste warten, bis sie gilt. Der DB-CHECK verlangt
        # `valid_until > valid_from`; das früheste zulässige Ende ist damit der
        # Tag nach dem Beginn. Der Status REVOKED macht sie trotzdem sofort
        # unwirksam — `_aktiv_q` filtert auf ACTIVE.
        ende = vollmacht.valid_from + timedelta(days=1)

    with as_business_error():
        with business_transaction(actor_app_user_id):
            vollmacht.status = "REVOKED"
            vollmacht.valid_until = ende
            vollmacht.save(update_fields=["status", "valid_until", "updated_at"])
    return get_vollmacht(authority_id)


def _hoechste_grenze(vollmachten):
    """(Grenze, Währung) der weitreichendsten Vollmacht — None = unbegrenzt.

    **Nur innerhalb derselben Währung.** 5.000 CHF und 4.000 EUR numerisch zu
    vergleichen wäre eine erfundene Aussage; bei gemischten Währungen gewinnt
    keine, und die Auskunft trägt beide Beträge (der Aufrufer zeigt sie an).
    """
    if not vollmachten:
        return None, None, []
    if any(v.amount_limit is None for v in vollmachten):
        return None, None, []  # unbegrenzt schlägt jede Zahl

    je_waehrung = {}
    for v in vollmachten:
        w = v.currency or "EUR"
        if w not in je_waehrung or v.amount_limit > je_waehrung[w]:
            je_waehrung[w] = v.amount_limit
    if len(je_waehrung) == 1:
        waehrung, grenze = next(iter(je_waehrung.items()))
        return grenze, waehrung, []
    # Mehrere Währungen: keine gewinnt, alle werden ausgewiesen.
    weitere = sorted((w, b) for w, b in je_waehrung.items())
    return None, None, weitere


def beauftragungslage(property_id, *, stichtag=None):
    """Die Beauftragungslage **aller** Parteien an dieser Liegenschaft.

    Eine Wahrheit für alle Aufrufer: Die Kopfzeile braucht sie für jede
    Verwaltung, `darf_beauftragen` für eine einzelne Partei. Zwei getrennte
    Auswertungen wären zwei Stellen, an denen dieselbe Regel driften kann —
    genau das ist beim ersten Anlauf passiert.

    Rückgabe: `{party_id: auskunft}` mit je einem Dict:

    * `darf` — darf im **Alltag** beauftragen
    * `grenze` / `waehrung` — Alltagsgrenze (None = unbegrenzt)
    * `notfall_grenze` / `notfall_waehrung` — Grenze der Notfallbefugnis
    * `nur_notfall` — darf **ausschließlich** im Notfall beauftragen
    * `nur_freigabe` — darf genehmigen, aber nicht beauftragen
    * `arten` — die gefundenen Befugnisarten
    * `gemischte_waehrungen` — Liste (Währung, Betrag), falls mehrere

    **Alltag und Notfall werden nie vermischt.** Wer ORDER bis 5.000 € und
    EMERGENCY_ORDER bis 50.000 € trägt, darf am Dienstagvormittag 5.000 € —
    nicht 50.000 €. Der erste Anlauf nahm das Maximum über beide und hätte dem
    Disponenten grünes Licht für einen Auftrag gegeben, der nicht gedeckt ist.
    """
    tag = stichtag or date.today()
    je_partei = {}
    for v in vollmachten_der_liegenschaft(property_id, stichtag=tag):
        je_partei.setdefault(v.authorized_party_id, []).append(v)

    lage = {}
    for party_id, vollmachten in je_partei.items():
        alltag = [v for v in vollmachten if v.authority_type == "ORDER"]
        notfall = [v for v in vollmachten if v.authority_type == "EMERGENCY_ORDER"]
        grenze, waehrung, gemischt = _hoechste_grenze(alltag)
        n_grenze, n_waehrung, n_gemischt = _hoechste_grenze(notfall)
        lage[party_id] = {
            "darf": bool(alltag),
            "grenze": grenze,
            "waehrung": waehrung,
            "notfall_grenze": n_grenze,
            "notfall_waehrung": n_waehrung,
            "nur_notfall": not alltag and bool(notfall),
            "nur_freigabe": not alltag and not notfall,
            "arten": sorted({v.authority_type for v in vollmachten}),
            "gemischte_waehrungen": gemischt or n_gemischt,
        }
    return lage


def darf_beauftragen(property_id, party_id, *, betrag=None, stichtag=None):
    """Darf diese Partei an dieser Liegenschaft beauftragen — und bis wie viel?

    Die Antwort, die der Disponent am Telefon braucht. Ein Einzelfall von
    `beauftragungslage`, damit es genau eine Auswertung gibt.

    `betrag` ist optional: Ist er angegeben, prüft die Funktion zusätzlich gegen
    die **Alltagsgrenze**. Ohne Betrag ist die Frage nur „darf überhaupt".

    **Bewusst keine Sperre**, sondern eine Auskunft: Ob ein Auftrag angenommen
    wird, entscheidet der Betrieb — hier steht nur, was vereinbart ist. Die
    Freigabetore des Auftrags sind ein eigener Mechanismus.
    """
    leer = {
        "darf": False,
        "grenze": None,
        "waehrung": None,
        "notfall_grenze": None,
        "notfall_waehrung": None,
        "nur_notfall": False,
        "nur_freigabe": False,
        "arten": [],
        "gemischte_waehrungen": [],
    }
    auskunft = beauftragungslage(property_id, stichtag=stichtag).get(party_id)
    if auskunft is None:
        return leer

    if betrag is not None and auskunft["darf"] and auskunft["grenze"] is not None:
        auskunft = {
            **auskunft,
            "darf": Decimal(str(betrag)) <= auskunft["grenze"],
        }
    return auskunft
