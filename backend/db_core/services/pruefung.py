"""Prüffristen — Prüfarten (Stammdaten) und Prüfungen (an Objekt/Anlage).

Eine **Prüfart** (`maintenance.inspection_type`) ist ein vom Betrieb gepflegter
Baustein: Name, Intervall, Vorlauf, Zuständigkeit. Eine **Prüfung**
(`maintenance.inspection`) ist die konkrete wiederkehrende Prüfung an einer
Liegenschaft (optional an einer technischen Anlage) — sie braucht KEINEN
Wartungsvertrag.

## Bewusst keine Rechtsauskunft

Das Produkt liefert ein paar gängige SHK-Prüfarten als **Vorschlag** aus
(`is_suggestion=True`, siehe Migration 0071) — Trinkwasser/Legionellen,
Schornsteinfeger, Rückflussverhinderer, Sicherheitsventil, Rauchwarnmelder,
Druckbehälter. Diese Vorschläge sind **kein Normkatalog**: welche Prüfung fällig
ist, in welchem Intervall und wer sie durchführen darf, hängt am Einzelfall
(Anlage, Nutzung, Landesrecht) und ändert sich. Der Betrieb pflegt seine Prüfarten
selbst; er kann die Vorschläge ändern oder deaktivieren.

## Kopie statt Verweis

Beim Anlegen einer Prüfung werden Intervall und Vorlauf aus der Prüfart
**kopiert**. Eine spätere Änderung der Prüfart verschiebt den Plan einer
laufenden Prüfung nicht rückwirkend — gleiche Haltung wie bei der Belegposition.
"""
import uuid

from django.utils import timezone

from db_core.db_context import business_transaction
from db_core.gate_errors import as_business_error
from db_core.models import (
    DueItem,
    Inspection,
    InspectionType,
    Property,
    TechnicalAsset,
)
from db_core.services import faelligkeit as faelligkeit_service
from db_core.services._validation import ensure_exists, ensure_party_usable

INTERVAL_KINDS = ("JAEHRLICH", "MONATLICH", "WOECHENTLICH", "TAGE")

# Spiegelt maintenance.enforce_inspection_status (identisch zum Wartungsvertrag).
INSPECTION_TRANSITIONS = {
    "AKTIV": {"INAKTIV"},
    "INAKTIV": {"AKTIV", "ARCHIVIERT"},
    "ARCHIVIERT": set(),
}

_UNSET = object()


def _check_intervall(interval_kind, interval_days):
    if interval_kind not in INTERVAL_KINDS:
        raise ValueError(
            f"Ungültige interval_kind '{interval_kind}'. "
            f"Erlaubt: {', '.join(INTERVAL_KINDS)}."
        )
    if interval_kind == "TAGE" and not interval_days:
        raise ValueError("interval_kind 'TAGE' erfordert interval_days > 0.")


def _check_vorlauf(lead_time_days):
    if lead_time_days is not None and lead_time_days < 0:
        raise ValueError("lead_time_days darf nicht negativ sein.")


# ---------------------------------------------------------------------------
# Prüfarten
# ---------------------------------------------------------------------------

def create_inspection_type(
    actor_app_user_id,
    *,
    name,
    interval_kind,
    interval_days=None,
    lead_time_days=30,
    responsibility=None,
    notes=None,
):
    """Legt eine Prüfart an (immer is_suggestion=False — selbst gepflegt)."""
    if not name or not name.strip():
        raise ValueError("name darf nicht leer sein.")
    _check_intervall(interval_kind, interval_days)
    _check_vorlauf(lead_time_days)
    if InspectionType.objects.filter(name=name.strip()).exists():
        raise ValueError(f"Eine Prüfart „{name.strip()}“ gibt es bereits.")
    with as_business_error():
        with business_transaction(actor_app_user_id):
            art = InspectionType.objects.create(
                id=uuid.uuid4(),
                name=name.strip(),
                interval_kind=interval_kind,
                interval_days=interval_days,
                lead_time_days=lead_time_days if lead_time_days is not None else 30,
                responsibility=responsibility,
                notes=notes,
                is_suggestion=False,
                is_active=True,
                created_by_id=actor_app_user_id,
                version=1,
            )
            art.refresh_from_db()
    return art


def update_inspection_type(
    actor_app_user_id,
    *,
    inspection_type_id,
    name=_UNSET,
    interval_kind=_UNSET,
    interval_days=_UNSET,
    lead_time_days=_UNSET,
    responsibility=_UNSET,
    notes=_UNSET,
    is_active=_UNSET,
):
    """Ändert eine Prüfart. Auch die ausgelieferten Vorschläge sind änderbar —
    der Betrieb soll sie an seine Wirklichkeit anpassen (deaktivieren statt
    löschen; Löschen ist per Trigger ohnehin gesperrt)."""
    art = InspectionType.objects.filter(id=inspection_type_id).first()
    if art is None:
        raise ValueError("Prüfart nicht gefunden.")

    felder = {}
    if name is not _UNSET:
        if not name or not name.strip():
            raise ValueError("name darf nicht leer sein.")
        if (
            InspectionType.objects.filter(name=name.strip())
            .exclude(id=inspection_type_id)
            .exists()
        ):
            raise ValueError(f"Eine Prüfart „{name.strip()}“ gibt es bereits.")
        felder["name"] = name.strip()
    kind = interval_kind if interval_kind is not _UNSET else art.interval_kind
    days = interval_days if interval_days is not _UNSET else art.interval_days
    if interval_kind is not _UNSET or interval_days is not _UNSET:
        _check_intervall(kind, days)
        felder["interval_kind"] = kind
        felder["interval_days"] = days if kind == "TAGE" else None
    if lead_time_days is not _UNSET:
        _check_vorlauf(lead_time_days)
        felder["lead_time_days"] = lead_time_days
    if responsibility is not _UNSET:
        felder["responsibility"] = responsibility
    if notes is not _UNSET:
        felder["notes"] = notes
    if is_active is not _UNSET:
        felder["is_active"] = bool(is_active)

    if felder:
        with as_business_error():
            with business_transaction(actor_app_user_id):
                InspectionType.objects.filter(id=inspection_type_id).update(**felder)
    art.refresh_from_db()
    return art


# ---------------------------------------------------------------------------
# Prüfungen
# ---------------------------------------------------------------------------

def _check_asset(asset_id, property_id):
    """Die Anlage muss zur Liegenschaft gehören (die DB erzwingt es zusätzlich
    über den zusammengesetzten FK — hier für ein 422 statt 500)."""
    if asset_id is None:
        return
    passend = TechnicalAsset.objects.filter(
        id=asset_id, property_id=property_id
    ).exists()
    if not passend:
        raise ValueError(
            "Die Anlage gehört nicht zu dieser Liegenschaft (oder existiert nicht)."
        )


def create_inspection(
    actor_app_user_id,
    *,
    inspection_type_id,
    property_id,
    start_date,
    name=None,
    asset_id=None,
    interval_kind=None,
    interval_days=None,
    lead_time_days=None,
    responsibility=None,
    party_id=None,
    notes=None,
):
    """Legt eine Prüfung im Status AKTIV an; erste Fälligkeit = Startdatum.

    Intervall/Vorlauf/Zuständigkeit werden aus der Prüfart übernommen, sofern
    nicht ausdrücklich überschrieben — und dann KOPIERT (siehe Modulkopf).
    """
    art = InspectionType.objects.filter(id=inspection_type_id).first()
    if art is None:
        raise ValueError("Prüfart nicht gefunden.")
    if not art.is_active:
        raise ValueError("Die Prüfart ist deaktiviert und nicht mehr zuweisbar.")
    ensure_exists(Property, property_id, "Liegenschaft")
    ensure_party_usable(party_id, "Dienstleister/Kunde")
    _check_asset(asset_id, property_id)

    kind = interval_kind or art.interval_kind
    days = interval_days if interval_days is not None else art.interval_days
    _check_intervall(kind, days)
    vorlauf = lead_time_days if lead_time_days is not None else art.lead_time_days
    _check_vorlauf(vorlauf)

    with as_business_error():
        with business_transaction(actor_app_user_id):
            pruefung = Inspection.objects.create(
                id=uuid.uuid4(),
                inspection_type_id=art.id,
                property_id=property_id,
                asset_id=asset_id,
                name=(name or art.name).strip(),
                status="AKTIV",
                start_date=start_date,
                interval_kind=kind,
                interval_days=days if kind == "TAGE" else None,
                lead_time_days=vorlauf,
                next_due_date=start_date,
                responsibility=(
                    responsibility
                    if responsibility is not None
                    else art.responsibility
                ),
                party_id=party_id,
                notes=notes,
                created_by_id=actor_app_user_id,
                version=1,
            )
            pruefung.refresh_from_db()
    return pruefung


def update_inspection(
    actor_app_user_id,
    *,
    inspection_id,
    name=_UNSET,
    interval_kind=_UNSET,
    interval_days=_UNSET,
    lead_time_days=_UNSET,
    next_due_date=_UNSET,
    responsibility=_UNSET,
    party_id=_UNSET,
    notes=_UNSET,
):
    """Ändert eine Prüfung. Prüfart, Liegenschaft und Anlage bleiben fest — eine
    Prüfung, die das Objekt wechselt, ist eine andere Prüfung."""
    pruefung = Inspection.objects.filter(id=inspection_id).first()
    if pruefung is None:
        raise ValueError("Prüfung nicht gefunden.")

    felder = {}
    if name is not _UNSET:
        if not name or not name.strip():
            raise ValueError("name darf nicht leer sein.")
        felder["name"] = name.strip()
    kind = interval_kind if interval_kind is not _UNSET else pruefung.interval_kind
    days = interval_days if interval_days is not _UNSET else pruefung.interval_days
    if interval_kind is not _UNSET or interval_days is not _UNSET:
        _check_intervall(kind, days)
        felder["interval_kind"] = kind
        felder["interval_days"] = days if kind == "TAGE" else None
    if lead_time_days is not _UNSET:
        _check_vorlauf(lead_time_days)
        felder["lead_time_days"] = lead_time_days
    if next_due_date is not _UNSET:
        if next_due_date is None:
            raise ValueError("next_due_date darf nicht leer sein.")
        # Ein „verbranntes" Datum: zu (Prüfung, Datum) gibt es bereits eine
        # ABGESCHLOSSENE Fälligkeit. Der Idempotenz-Index ist statusunabhängig —
        # der Scheduler könnte dort nie wieder etwas anlegen, die Prüfung wäre
        # still tot. Also lieber jetzt eine klare Ansage als später ein
        # Schweigen.
        if next_due_date != pruefung.next_due_date and (
            faelligkeit_service.datum_bereits_abgeschlossen(
                inspection_id=inspection_id, datum=next_due_date
            )
        ):
            raise ValueError(
                f"Zum {next_due_date:%d.%m.%Y} gibt es für diese Prüfung bereits "
                "eine abgeschlossene Fälligkeit (erledigt oder verworfen). Sie "
                "kann nicht erneut fällig werden — bitte ein anderes Datum wählen."
            )
        felder["next_due_date"] = next_due_date
    if responsibility is not _UNSET:
        felder["responsibility"] = responsibility
    if party_id is not _UNSET:
        ensure_party_usable(party_id, "Dienstleister/Kunde")
        felder["party_id"] = party_id
    if notes is not _UNSET:
        felder["notes"] = notes

    neues_datum = felder.get("next_due_date")
    if felder:
        with as_business_error():
            with business_transaction(actor_app_user_id):
                Inspection.objects.filter(id=inspection_id).update(**felder)
                # Wird umdatiert, ist eine bereits erzeugte, noch OFFENE
                # Fälligkeit zum ALTEN Datum gegenstandslos. Sie wird nicht
                # gelöscht (GoBD) und nicht umdatiert (due_date ist
                # unveränderlich), sondern begründet verworfen — sonst stünden
                # zwei offene Fälligkeiten für dieselbe Prüfung. Gleiche Haltung
                # wie bei der Gewährleistung (gewaehrleistung.update_warranty).
                if (
                    neues_datum is not None
                    and pruefung.next_due_date is not None
                    and neues_datum != pruefung.next_due_date
                ):
                    DueItem.objects.filter(
                        inspection_id=inspection_id,
                        due_date=pruefung.next_due_date,
                        status="OFFEN",
                    ).update(
                        status="VERWORFEN",
                        resolution_note=(
                            f"Prüftermin geändert "
                            f"({pruefung.next_due_date:%d.%m.%Y} → "
                            f"{neues_datum:%d.%m.%Y}); die Fälligkeit zum alten "
                            "Datum ist gegenstandslos."
                        ),
                        resolved_at=timezone.now(),
                        resolved_by_id=actor_app_user_id,
                    )
    pruefung.refresh_from_db()
    return pruefung


def set_inspection_status(actor_app_user_id, *, inspection_id, to_status):
    """AKTIV ↔ INAKTIV, INAKTIV → ARCHIVIERT (final). Vorab geprüft (→422), vom
    DB-Trigger physisch erzwungen."""
    pruefung = Inspection.objects.filter(id=inspection_id).first()
    if pruefung is None:
        raise ValueError("Prüfung nicht gefunden.")
    if to_status not in INSPECTION_TRANSITIONS.get(pruefung.status, set()):
        raise ValueError(
            f"Statuswechsel {pruefung.status} → {to_status} ist nicht zulässig."
        )
    with as_business_error():
        with business_transaction(actor_app_user_id):
            Inspection.objects.filter(id=inspection_id).update(status=to_status)
    pruefung.refresh_from_db()
    return pruefung
