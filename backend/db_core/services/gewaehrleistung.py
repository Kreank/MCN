"""Gewährleistung — Fristen aus abgeschlossenen Aufträgen.

Aus einem technisch abgeschlossenen bzw. abgerechneten Auftrag entsteht eine
Gewährleistungsfrist. Sie ist **je Auftrag einstellbar**; der Default kommt aus
dem Firmenprofil (`warranty_default_months`, ausgeliefert mit 60). Der Ablauf
wird über den Vorlauf (`lead_time_days`, Default 90 Tage) **rechtzeitig vorher**
als Fälligkeit sichtbar — nur so kann man noch reagieren (Nachbesserung
einfordern, Anlage prüfen, Wartungsvertrag anbieten).

## Das Produkt gibt KEINE Rechtsauskunft

Die üblichen Rahmen — BGB regelmäßig 5 Jahre bei Bauwerken, VOB/B regelmäßig
4 Jahre, bei wartungsbedürftigen maschinellen Anlagen OHNE Wartungsvertrag ggf.
nur 2 Jahre — sind hier **Erinnerungshilfe für den Menschen**, nicht Logik im
Code. Konkret:

* `basis` (BGB|VOB|INDIVIDUELL) ist ein **Label**. Der Code leitet daraus KEINE
  Frist ab. Maßgeblich ist allein `duration_months`, und die setzt der Betrieb.
* `is_machinery` ist ein **Hinweis-Schalter**, keine automatische Verkürzung.
  Trägt der Auftrag eine wartungsbedürftige maschinelle Anlage und hat die
  Liegenschaft **keinen aktiven Wartungsvertrag**, meldet `vertriebshinweis()`
  genau das — als Anlass, einen Wartungsvertrag anzubieten. Das ist ein
  Verkaufsargument, keine Rechtsbehauptung: die Frist bleibt, was eingetragen ist.
* `VORSCHLAEGE` liefert Anhaltspunkte fürs Formular. Sie sind Voreinstellungen,
  keine Zusicherung.

Wer eine verbindliche Frist braucht, fragt seinen Anwalt — nicht dieses CRM.
"""
import uuid
from datetime import date

from django.utils import timezone

from db_core.db_context import business_transaction
from db_core.gate_errors import as_business_error
from db_core.models import (
    CompanyProfile,
    DueItem,
    MaintenanceContract,
    Warranty,
    WorkOrder,
)
from db_core.services._validation import ensure_party_usable
from db_core.services.faelligkeit import add_months, datum_bereits_abgeschlossen

BASES = ("BGB", "VOB", "INDIVIDUELL")
STATUSES = ("AKTIV", "ARCHIVIERT")

# Anhaltspunkte fürs Formular — Voreinstellungen, KEINE Rechtsauskunft.
VORSCHLAEGE = {
    "BGB": 60,
    "VOB": 48,
    "INDIVIDUELL": 24,
}

# Aus diesen Auftragsstatus heraus entsteht eine Gewährleistung: die Leistung ist
# erbracht. Vorher (ENTWURF/FREIGEGEBEN/IN_*) gibt es noch nichts zu gewährleisten.
ABGESCHLOSSEN = ("TECHNISCH_ABGESCHLOSSEN", "KAUFMAENNISCH_GEPRUEFT", "ABGERECHNET")

_UNSET = object()


def default_monate():
    profil = CompanyProfile.objects.first()
    return profil.warranty_default_months if profil else 60


def default_vorlauf():
    profil = CompanyProfile.objects.first()
    return profil.warranty_default_lead_days if profil else 90


def set_defaults(actor_app_user_id, *, months=_UNSET, lead_days=_UNSET):
    """Pflegt die Voreinstellung am Firmenprofil (betriebliche Einstellung)."""
    profil = CompanyProfile.objects.first()
    if profil is None:
        raise ValueError("Es ist kein Firmenprofil angelegt.")
    felder = {}
    if months is not _UNSET:
        if months is None or not (1 <= months <= 240):
            raise ValueError("Die Gewährleistungsfrist muss 1–240 Monate betragen.")
        felder["warranty_default_months"] = months
    if lead_days is not _UNSET:
        if lead_days is None or not (0 <= lead_days <= 730):
            raise ValueError("Der Vorlauf muss zwischen 0 und 730 Tagen liegen.")
        felder["warranty_default_lead_days"] = lead_days
    if felder:
        with as_business_error():
            with business_transaction(actor_app_user_id):
                CompanyProfile.objects.filter(id=profil.id).update(**felder)
    profil.refresh_from_db()
    return profil


def hat_wartungsvertrag(property_id):
    """Gibt es an dieser Liegenschaft einen AKTIVEN Wartungsvertrag?"""
    return MaintenanceContract.objects.filter(
        property_id=property_id, status="AKTIV"
    ).exists()


def objekte_mit_wartungsvertrag(property_ids):
    """{property_id} mit aktivem Wartungsvertrag — EINE Abfrage für viele Zeilen.

    Für Listen: `vertriebshinweis` je Zeile einzeln zu fragen wäre eine Query pro
    Zeile.
    """
    ids = [p for p in property_ids if p is not None]
    if not ids:
        return set()
    return set(
        MaintenanceContract.objects.filter(
            property_id__in=ids, status="AKTIV"
        ).values_list("property_id", flat=True)
    )


def vertriebshinweis(warranty, mit_vertrag=None):
    """Der Hinweis „Anlage ohne Wartungsvertrag" — oder None.

    Ausdrücklich ein **Hinweis**, keine Rechtsbehauptung: das Produkt verkürzt
    keine Frist, es macht nur auf einen bekannten Zusammenhang aufmerksam.

    `mit_vertrag` ist die vorab geladene Menge der Liegenschaften mit aktivem
    Wartungsvertrag (siehe `objekte_mit_wartungsvertrag`) — ohne sie fragt jede
    Zeile die DB selbst.
    """
    if not warranty.is_machinery:
        return None
    hat_vertrag = (
        warranty.property_id in mit_vertrag
        if mit_vertrag is not None
        else hat_wartungsvertrag(warranty.property_id)
    )
    if hat_vertrag:
        return None
    return (
        "Wartungsbedürftige Anlage ohne aktiven Wartungsvertrag an dieser "
        "Liegenschaft. Ohne Wartungsvertrag kann die Gewährleistung für "
        "maschinelle Anlagen kürzer ausfallen — ein guter Anlass, dem Kunden "
        "einen Wartungsvertrag anzubieten. (Hinweis, keine Rechtsauskunft: die "
        "eingetragene Frist bleibt unverändert.)"
    )


def _end_date(start_date, duration_months):
    return add_months(start_date, duration_months)


def create_warranty(
    actor_app_user_id,
    *,
    work_order_id,
    start_date=None,
    duration_months=None,
    lead_time_days=None,
    basis="BGB",
    is_machinery=False,
    party_id=None,
    notes=None,
):
    """Legt die Gewährleistung eines Auftrags an (genau eine je Auftrag)."""
    order = WorkOrder.objects.filter(id=work_order_id).first()
    if order is None:
        raise ValueError("Auftrag nicht gefunden.")
    if order.status not in ABGESCHLOSSEN:
        raise ValueError(
            "Eine Gewährleistung entsteht erst mit erbrachter Leistung "
            f"(Auftragsstatus {', '.join(ABGESCHLOSSEN)}); "
            f"dieser Auftrag steht auf {order.status}."
        )
    if Warranty.objects.filter(work_order_id=work_order_id).exists():
        raise ValueError("Für diesen Auftrag gibt es bereits eine Gewährleistung.")
    if basis not in BASES:
        raise ValueError(f"Ungültige basis '{basis}'. Erlaubt: {', '.join(BASES)}.")

    monate = duration_months if duration_months is not None else default_monate()
    if not (1 <= monate <= 240):
        raise ValueError("Die Gewährleistungsfrist muss 1–240 Monate betragen.")
    vorlauf = lead_time_days if lead_time_days is not None else default_vorlauf()
    if not (0 <= vorlauf <= 730):
        raise ValueError("Der Vorlauf muss zwischen 0 und 730 Tagen liegen.")
    start = start_date or date.today()
    ensure_party_usable(party_id, "Kunde")

    with as_business_error():
        with business_transaction(actor_app_user_id):
            w = Warranty.objects.create(
                id=uuid.uuid4(),
                work_order_id=order.id,
                property_id=order.property_id,
                party_id=party_id,
                basis=basis,
                start_date=start,
                duration_months=monate,
                end_date=_end_date(start, monate),
                lead_time_days=vorlauf,
                is_machinery=bool(is_machinery),
                status="AKTIV",
                notes=notes,
                created_by_id=actor_app_user_id,
                version=1,
            )
            w.refresh_from_db()
    return w


def update_warranty(
    actor_app_user_id,
    *,
    warranty_id,
    start_date=_UNSET,
    duration_months=_UNSET,
    lead_time_days=_UNSET,
    basis=_UNSET,
    is_machinery=_UNSET,
    party_id=_UNSET,
    notes=_UNSET,
    status=_UNSET,
):
    """Ändert die Gewährleistung — die Frist ist je Auftrag einstellbar.

    Wichtig: `end_date` wird IMMER neu aus (start_date, duration_months)
    gerechnet, nie separat gesetzt. Zwei Wahrheiten über dasselbe Datum wären
    ein Fehler, der erst beim Fristablauf auffiele.
    """
    w = Warranty.objects.filter(id=warranty_id).first()
    if w is None:
        raise ValueError("Gewährleistung nicht gefunden.")

    felder = {}
    start = start_date if start_date is not _UNSET else w.start_date
    monate = duration_months if duration_months is not _UNSET else w.duration_months
    if start_date is not _UNSET or duration_months is not _UNSET:
        if start is None:
            raise ValueError("start_date darf nicht leer sein.")
        if monate is None or not (1 <= monate <= 240):
            raise ValueError("Die Gewährleistungsfrist muss 1–240 Monate betragen.")
        felder["start_date"] = start
        felder["duration_months"] = monate
        felder["end_date"] = _end_date(start, monate)
    if lead_time_days is not _UNSET:
        if lead_time_days is None or not (0 <= lead_time_days <= 730):
            raise ValueError("Der Vorlauf muss zwischen 0 und 730 Tagen liegen.")
        felder["lead_time_days"] = lead_time_days
    if basis is not _UNSET:
        if basis not in BASES:
            raise ValueError(f"Ungültige basis '{basis}'. Erlaubt: {', '.join(BASES)}.")
        felder["basis"] = basis
    if is_machinery is not _UNSET:
        felder["is_machinery"] = bool(is_machinery)
    if party_id is not _UNSET:
        ensure_party_usable(party_id, "Kunde")
        felder["party_id"] = party_id
    if notes is not _UNSET:
        felder["notes"] = notes
    if status is not _UNSET:
        if status not in STATUSES:
            raise ValueError(f"Ungültiger Status '{status}'.")
        felder["status"] = status

    neues_ende = felder.get("end_date")
    # Ein „verbranntes" Datum: zu (Gewährleistung, Datum) gibt es schon eine
    # ABGESCHLOSSENE Fälligkeit. Der Idempotenz-Index ist statusunabhängig — der
    # Scheduler könnte dort nie wieder etwas anlegen, der Fristablauf käme nie
    # mehr auf den Tisch. Klare Ansage statt stillem Verstummen.
    if (
        neues_ende is not None
        and neues_ende != w.end_date
        and datum_bereits_abgeschlossen(warranty_id=warranty_id, datum=neues_ende)
    ):
        raise ValueError(
            f"Zum {neues_ende:%d.%m.%Y} gibt es für diese Gewährleistung bereits "
            "eine abgeschlossene Fälligkeit (erledigt oder verworfen). Der "
            "Fristablauf kann zu diesem Datum nicht erneut fällig werden — bitte "
            "Frist oder Beginn anders wählen."
        )
    with as_business_error():
        with business_transaction(actor_app_user_id):
            if felder:
                Warranty.objects.filter(id=warranty_id).update(**felder)
            # Verschiebt sich das Fristende, ist eine bereits erzeugte, noch
            # offene Fälligkeit auf das ALTE Datum gegenstandslos. Sie wird nicht
            # gelöscht (GoBD) und nicht umdatiert (due_date ist unveränderlich),
            # sondern begründet verworfen — der Scheduler erzeugt die neue.
            if neues_ende is not None and neues_ende != w.end_date:
                DueItem.objects.filter(
                    warranty_id=warranty_id, status="OFFEN"
                ).update(
                    status="VERWORFEN",
                    resolution_note=(
                        f"Gewährleistungsfrist geändert "
                        f"({w.end_date:%d.%m.%Y} → {neues_ende:%d.%m.%Y}); "
                        "die Fälligkeit zum alten Datum ist gegenstandslos."
                    ),
                    resolved_at=timezone.now(),
                    resolved_by_id=actor_app_user_id,
                )
    w.refresh_from_db()
    return w
