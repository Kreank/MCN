"""Lohn-/Maschinengruppen-Service (pricing.wage_group, Migration 0033/0034).

Eine Lohngruppe bündelt einen Verrechnungssatz (`hourly_rate`, VK je Stunde) und
optional den internen Kostensatz (`cost_rate`, für die Marge). `kind` trennt
Personal- (LOHN) von Maschinen-/Gerätestunden (MASCHINE). Verwendet wird sie in
Baugruppen (assembly_component) und als Kalkulationsgrundlage.

Kein Löschen (GoBD-Schutzstandard wie alle Fachtabellen): eine nicht mehr
benötigte Gruppe wird auf status INAKTIV gesetzt. Bestehende Verweise (Baugruppen)
bleiben gültig. Alle Writes laufen über business_transaction (Audit/Benutzer-
kontext). Fachfehler werden als ValueError geworfen und von der API in 422
übersetzt; die Validierung spiegelt die DB-CHECKs, damit kein DataError als 500
durchschlägt.
"""
import uuid
from decimal import Decimal, InvalidOperation

from db_core.db_context import business_transaction
from db_core.models import WageGroup

_KINDS = ("LOHN", "MASCHINE")
_STATUS = ("AKTIV", "INAKTIV")


def _clean(value):
    """Leerstrings zu None normalisieren, Strings trimmen."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _parse_rate(value, *, feld, erlaube_none=False):
    """Prüft einen Geldbetrag >= 0 (spiegelt den DB-CHECK). `erlaube_none` für
    den optionalen Kostensatz."""
    if value is None or (isinstance(value, str) and not value.strip()):
        if erlaube_none:
            return None
        raise ValueError(f"{feld} ist erforderlich.")
    try:
        betrag = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{feld} ist keine gültige Zahl.")
    # NaN/Infinity abweisen: Postgres speichert NaN in numeric, und der DB-CHECK
    # `>= 0` greift nicht (NaN >= 0 ist dort TRUE) — sonst stille Datenkorruption.
    if not betrag.is_finite():
        raise ValueError(f"{feld} ist keine gültige Zahl.")
    if betrag < 0:
        raise ValueError(f"{feld} darf nicht negativ sein.")
    # Obergrenze der Spalte numeric(12,2) spiegeln (max 9.999.999.999,99); sonst
    # schlägt der INSERT als DataError (numeric overflow) durch → 500 statt 422.
    if betrag >= Decimal("10000000000"):
        raise ValueError(f"{feld} ist zu groß (höchstens 9.999.999.999,99).")
    return betrag


def list_wage_groups(*, include_inactive=True):
    """Alle Lohn-/Maschinengruppen, nach Art und Name sortiert."""
    qs = WageGroup.objects.all()
    if not include_inactive:
        qs = qs.filter(status="AKTIV")
    return qs.order_by("kind", "name", "id")


def create_wage_group(actor_app_user_id, *, name, kind="LOHN", hourly_rate,
                      cost_rate=None):
    name = _clean(name)
    if not name:
        raise ValueError("Name der Lohngruppe ist erforderlich.")
    kind = (_clean(kind) or "LOHN").upper()
    if kind not in _KINDS:
        raise ValueError("Art muss LOHN oder MASCHINE sein.")
    hourly = _parse_rate(hourly_rate, feld="Verrechnungssatz")
    cost = _parse_rate(cost_rate, feld="Kostensatz", erlaube_none=True)
    # Unique-Constraint auf name vorab spiegeln (sonst IntegrityError → 500).
    if WageGroup.objects.filter(name=name).exists():
        raise ValueError(f"Eine Lohngruppe mit dem Namen '{name}' existiert bereits.")
    with business_transaction(actor_app_user_id):
        group = WageGroup.objects.create(
            id=uuid.uuid4(), name=name, kind=kind,
            hourly_rate=hourly, cost_rate=cost, status="AKTIV", version=1,
        )
    return group


def update_wage_group(actor_app_user_id, *, wage_group_id, **fields):
    group = WageGroup.objects.filter(id=wage_group_id).first()
    if group is None:
        raise ValueError("Lohngruppe nicht gefunden.")
    allowed = ("name", "kind", "hourly_rate", "cost_rate", "status")
    unknown = set(fields) - set(allowed)
    if unknown:
        raise ValueError(f"Unbekannte Felder: {', '.join(sorted(unknown))}")

    changed = []
    if "name" in fields:
        name = _clean(fields["name"])
        if not name:
            raise ValueError("Name der Lohngruppe darf nicht leer sein.")
        if WageGroup.objects.filter(name=name).exclude(id=group.id).exists():
            raise ValueError(
                f"Eine Lohngruppe mit dem Namen '{name}' existiert bereits."
            )
        group.name = name
        changed.append("name")
    if "kind" in fields:
        kind = (_clean(fields["kind"]) or "").upper()
        if kind not in _KINDS:
            raise ValueError("Art muss LOHN oder MASCHINE sein.")
        group.kind = kind
        changed.append("kind")
    if "hourly_rate" in fields:
        group.hourly_rate = _parse_rate(
            fields["hourly_rate"], feld="Verrechnungssatz"
        )
        changed.append("hourly_rate")
    if "cost_rate" in fields:
        group.cost_rate = _parse_rate(
            fields["cost_rate"], feld="Kostensatz", erlaube_none=True
        )
        changed.append("cost_rate")
    if "status" in fields:
        status = (_clean(fields["status"]) or "").upper()
        if status not in _STATUS:
            raise ValueError("Status muss AKTIV oder INAKTIV sein.")
        group.status = status
        changed.append("status")

    if changed:
        with business_transaction(actor_app_user_id):
            group.save(update_fields=changed + ["updated_at"])
        group.refresh_from_db()
    return group
