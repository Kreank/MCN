"""Rechtematrix-Pflege: Rollen lesen, Matrix-Zellen ändern, Rollen zuweisen/beenden.

Ergänzt den reinen Lese-/Durchsetzungs-Service `rechte.py` um die administrative
PFLEGE der Rechtematrix (security.role_permission) und der Rollenzuordnungen
(security.user_role). Alle Writes laufen über business_transaction; das Modul-
Recht (`security`/`AENDERN`) prüft die API-Schicht.

Zwei Härtungen sind hier physisch/logisch erzwungen (klarer ValueError → 422),
weil sie sonst zur Rechteausweitung bzw. Aussperrung führen könnten:

  * **Keine Selbst-Erweiterung.** Niemand darf eine Zelle einer Rolle, die er
    selbst innehat, so ändern, dass sie MEHR erlaubt (allowed false→true oder
    Sichtfeld EIGENE→ALLE). Und niemand darf sich selbst eine Rolle zuweisen.
  * **Kein Verlust der letzten ADMINISTRATION.** Die letzte aktive
    ADMINISTRATION-Zuordnung lässt sich nicht beenden — sonst wäre das System
    ohne Vollzugriff nicht mehr administrierbar.
"""
import uuid
from datetime import date, timedelta

from django.db.models import Q

from db_core.db_context import business_transaction
from db_core.models import Role, RolePermission, UserRole
from db_core.services import rechte as rechte_service
from db_core.services.rechte import ACTIONS, MODULES

ROW_SCOPES = ("ALLE", "EIGENE")
ADMIN_ROLE = "ADMINISTRATION"


# --- Rollen & Matrix lesen -------------------------------------------------

def list_roles():
    """Alle Rollen (Codeliste, Spalten der Matrix)."""
    return Role.objects.order_by("code")


def permission_rows():
    """Alle Matrix-Zellen (Rolle × Modul × Aktion) mit Rolle."""
    return RolePermission.objects.select_related("role").order_by(
        "role_id", "module", "action"
    )


# --- Matrix-Zelle ändern ---------------------------------------------------

def _is_expansion(current, new_allowed, new_scope):
    """True, wenn (new_allowed, new_scope) MEHR erlaubt als die aktuelle Zelle."""
    if current is None:
        return new_allowed
    if new_allowed and not current.allowed:
        return True
    if new_allowed and current.allowed:
        # Sichtfeld verbreitern (EIGENE -> ALLE) ist ebenfalls eine Erweiterung.
        if current.row_scope == "EIGENE" and new_scope == "ALLE":
            return True
    return False


def set_permission(actor_app_user_id, *, role_code, module, action, allowed,
                   row_scope="ALLE"):
    """Setzt eine Matrix-Zelle (allowed + row_scope).

    Härtung: Wer die Rolle `role_code` selbst innehat, darf ihre Rechte nicht
    ERWEITERN (Selbst-Erweiterung) — Reduzieren bleibt erlaubt.
    """
    if module not in MODULES:
        raise ValueError(f"Unbekanntes Modul: {module}")
    if action not in ACTIONS:
        raise ValueError(f"Unbekannte Aktion: {action}")
    if row_scope not in ROW_SCOPES:
        raise ValueError(f"Unbekannter row_scope: {row_scope}")
    if not Role.objects.filter(code=role_code).exists():
        raise ValueError(f"Unbekannte Rolle: {role_code}")

    row = RolePermission.objects.filter(
        role_id=role_code, module=module, action=action
    ).first()

    allowed = bool(allowed)
    own_roles = rechte_service.active_role_codes(actor_app_user_id)
    if role_code in own_roles and _is_expansion(row, allowed, row_scope):
        raise ValueError(
            "Sie können Ihre eigenen Rechte nicht erweitern (Selbst-Erweiterung "
            "ist ausgeschlossen)."
        )

    if row is None:
        # Die Matrix ist vollständig vorbefüllt; eine fehlende Zelle wäre ein
        # Datenfehler. Defensiv anlegen statt still zu scheitern.
        with business_transaction(actor_app_user_id):
            row = RolePermission.objects.create(
                id=uuid.uuid4(), role_id=role_code, module=module, action=action,
                allowed=allowed, row_scope=row_scope,
            )
        return row

    row.allowed = allowed
    row.row_scope = row_scope
    with business_transaction(actor_app_user_id):
        row.save(update_fields=["allowed", "row_scope", "updated_at"])
    row.refresh_from_db()
    return row


# --- Rollenzuordnungen -----------------------------------------------------

def list_user_roles(*, active_only=False, on=None):
    """Alle Rollenzuordnungen (mit Benutzer + Rolle). `active_only` filtert auf
    zum Stichtag gültige."""
    on = on or date.today()
    qs = UserRole.objects.select_related("user", "role").order_by(
        "user__display_name", "role_id", "valid_from"
    )
    if active_only:
        qs = qs.filter(valid_from__lte=on).filter(
            Q(valid_until__isnull=True) | Q(valid_until__gt=on)
        )
    return qs


def _active_admin_assignments(on=None, *, exclude_id=None):
    on = on or date.today()
    qs = UserRole.objects.filter(role_id=ADMIN_ROLE, valid_from__lte=on).filter(
        Q(valid_until__isnull=True) | Q(valid_until__gt=on)
    )
    if exclude_id is not None:
        qs = qs.exclude(id=exclude_id)
    return qs


def assign_role(actor_app_user_id, *, user_id, role_code, valid_from=None):
    """Weist einem Benutzer eine Rolle zu (security.user_role).

    Härtung: Niemand kann sich selbst eine Rolle zuweisen (Selbst-Erweiterung).
    """
    if str(user_id) == str(actor_app_user_id):
        raise ValueError(
            "Sie können sich selbst keine Rollen zuweisen (Selbst-Erweiterung "
            "ist ausgeschlossen)."
        )
    if not Role.objects.filter(code=role_code).exists():
        raise ValueError(f"Unbekannte Rolle: {role_code}")
    from db_core.models import AppUser

    if not AppUser.objects.filter(id=user_id).exists():
        raise ValueError("Benutzer (app_user) existiert nicht.")

    valid_from = valid_from or date.today()
    # zeitgleiche Doppelzuordnung derselben Rolle vermeiden (die DB hätte einen
    # EXCLUDE, aber ein klarer 422 ist besser als ein IntegrityError).
    clash = UserRole.objects.filter(user_id=user_id, role_id=role_code).filter(
        Q(valid_until__isnull=True) | Q(valid_until__gt=valid_from)
    ).filter(valid_from__lte=valid_from).exists()
    if clash:
        raise ValueError("Diese Rolle ist dem Benutzer bereits zugewiesen.")

    with business_transaction(actor_app_user_id):
        row = UserRole.objects.create(
            id=uuid.uuid4(), user_id=user_id, role_id=role_code,
            valid_from=valid_from, granted_by_id=actor_app_user_id,
        )
    return row


def end_user_role(actor_app_user_id, *, user_role_id, valid_until=None):
    """Beendet eine Rollenzuordnung (setzt valid_until; kein Löschen — No-Delete).

    Härtung: Die letzte aktive ADMINISTRATION-Zuordnung lässt sich nicht beenden.
    """
    row = UserRole.objects.filter(id=user_role_id).first()
    if row is None:
        raise ValueError("Rollenzuordnung nicht gefunden.")

    today = date.today()
    if row.valid_until is not None and row.valid_until <= today:
        raise ValueError("Diese Rollenzuordnung ist bereits beendet.")

    end = valid_until or today
    # DB-CHECK: valid_until > valid_from. Bei einer am selben Tag angelegten
    # Zuordnung ist der früheste zulässige Endtag der Folgetag.
    if end <= row.valid_from:
        end = row.valid_from + timedelta(days=1)

    # Der Schutz muss zum ENDZEITPUNKT greifen, nicht heute: sonst ließen sich
    # zwei Administratoren nacheinander auf denselben künftigen Tag beenden —
    # jeder Aufruf ginge durch, weil der jeweils andere HEUTE noch aktiv ist, und
    # ab diesem Tag hätte das System keinen Administrator mehr.
    if row.role_id == ADMIN_ROLE and not _active_admin_assignments(
        on=end, exclude_id=row.id
    ).exists():
        raise ValueError(
            "Die letzte ADMINISTRATION-Zuordnung kann nicht beendet werden — "
            "sonst wäre das System nicht mehr administrierbar."
        )

    row.valid_until = end
    with business_transaction(actor_app_user_id):
        row.save(update_fields=["valid_until"])
    row.refresh_from_db()
    return row
