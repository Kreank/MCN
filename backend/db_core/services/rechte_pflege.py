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


# --- Benutzeranlage --------------------------------------------------------

def list_login_accounts():
    """Login-Konten (accounts.User) nach app_user_id — für die Benutzerliste.

    Die fachliche Identität (security.app_user) trägt nur den Anzeigenamen; die
    Anmeldedaten liegen bewusst in Djangos Welt. Für die Verwaltungsoberfläche
    braucht es beides, deshalb hier die Zuordnung als Dict.
    """
    from accounts.models import User

    return {
        u.app_user_id: u
        for u in User.objects.filter(app_user_id__isnull=False)
    }


def list_logins_ohne_identitaet():
    """Login-Konten, denen die fachliche Identität fehlt (`app_user_id IS NULL`).

    Diese Konten sind der gefährliche Zwischenzustand: **anmelden geht,
    speichern nicht** — `business_transaction` verlangt die UUID und bricht
    sonst hart ab. Im Bestand entstehen sie, wenn jemand Konten direkt im
    Django-Admin angelegt hat, bevor es die Benutzeranlage im Produkt gab.

    Sie tauchen in `list_users()` nicht auf (die Liste kommt aus app_user),
    wären also unsichtbar und unerreichbar — deshalb diese eigene Liste.
    """
    from accounts.models import User

    return User.objects.filter(app_user_id__isnull=True).order_by("email")


def identitaet_ergaenzen(actor_app_user_id, *, login_id, display_name):
    """Gibt einem bestehenden Login-Konto die fehlende fachliche Identität.

    Der Weg für Altbestand: `create_user` scheitert hier zu Recht mit „Adresse
    bereits verwendet" — das Konto SOLL nicht doppelt entstehen, ihm fehlt nur
    die andere Hälfte. Passwort und Anmeldung bleiben unangetastet.

    Wie beim Anlegen gibt es **keine Rolle** dazu; die vergibt man bewusst.
    """
    from accounts.models import User
    from db_core.models import AppUser

    display_name = (display_name or "").strip()
    if not display_name:
        raise ValueError("Der Anzeigename darf nicht leer sein.")

    login = User.objects.filter(id=login_id).first()
    if login is None:
        raise ValueError("Login-Konto nicht gefunden.")
    if login.app_user_id:
        raise ValueError(
            "Dieses Konto hat bereits eine fachliche Identität — es steht in der "
            "Benutzerliste."
        )

    with business_transaction(actor_app_user_id):
        konto = AppUser.objects.create(
            id=uuid.uuid4(), display_name=display_name, status="ACTIVE", version=1
        )
        login.app_user_id = konto.id
        login.save(update_fields=["app_user_id"])

    return konto, login


def create_user(actor_app_user_id, *, display_name, email, password, is_active=True):
    """Legt einen neuen Benutzer an: fachliche Identität UND Login-Konto.

    Vorher gab es dafür keinen Weg im Produkt: `security.app_user` hatte weder
    Endpunkt noch Oberfläche, und `hr.employee.app_user_id` ist NOT NULL — wer
    einen Mitarbeiter anlegen wollte, brauchte ein Benutzerkonto, das er nirgends
    anlegen konnte. Übrig blieb der Django-Admin, der aber Notfallwerkzeug ist
    und per MCN_ADMIN_ALLOW_IPS ohnehin nicht offen steht.

    Beides entsteht in EINER Transaktion. Bricht das Login-Konto ab (doppelte
    Adresse, zu schwaches Passwort), bleibt keine app_user-Waise zurück — die
    ließe sich wegen des No-Delete-Schutzes nicht mehr entfernen und würde als
    geisterhaftes „Benutzerkonto" in jedem Auswahlfeld auftauchen.

    Bewusst NICHT hier: die Rollenvergabe. Ein frisch angelegter Benutzer hat
    keine Rolle und darf damit nichts — die Rolle vergibt man anschließend
    bewusst über `assign_role`. Das hält die Selbst-Erweiterungs-Härtung intakt.
    """
    from django.contrib.auth.password_validation import validate_password
    from django.core.exceptions import ValidationError as DjangoValidationError
    from django.core.validators import validate_email

    from accounts.models import User
    from db_core.models import AppUser

    display_name = (display_name or "").strip()
    if not display_name:
        raise ValueError("Der Anzeigename darf nicht leer sein.")

    email = User.objects.normalize_email((email or "").strip())
    if not email:
        raise ValueError("Eine E-Mail-Adresse ist Pflicht — sie ist der Login.")
    try:
        validate_email(email)
    except DjangoValidationError:
        raise ValueError(f"Keine gültige E-Mail-Adresse: {email}")

    # Case-insensitiv, wie die DB-Constraint uniq_user_email_ci. Ein klarer 422
    # ist besser als ein IntegrityError aus der Tiefe.
    vorhanden = User.objects.filter(email__iexact=email).first()
    if vorhanden is not None:
        if vorhanden.app_user_id is None:
            # Der häufige Fall bei Altbestand: das Login gibt es schon (etwa aus
            # dem Django-Admin), nur die fachliche Identität fehlt. Nicht in eine
            # Sackgasse schicken, sondern den richtigen Weg nennen.
            raise ValueError(
                f"Für {email} gibt es bereits ein Login-Konto, dem nur die "
                f"fachliche Identität fehlt. Ergänzen Sie sie über „Konten ohne "
                f"Identität“ — ein zweites Konto wäre falsch."
            )
        raise ValueError(f"Diese E-Mail-Adresse wird bereits verwendet: {email}")

    try:
        validate_password(password or "")
    except DjangoValidationError as exc:
        raise ValueError(" ".join(exc.messages))

    with business_transaction(actor_app_user_id):
        # version ist NOT NULL ohne DB-Default (optimistisches Sperren), die
        # ORM muss den Startwert also selbst setzen.
        konto = AppUser.objects.create(
            id=uuid.uuid4(), display_name=display_name, status="ACTIVE", version=1
        )
        # username ist Pflichtfeld aus AbstractUser und eindeutig; angemeldet
        # wird sich über die E-Mail (accounts.backends.EmailBackend).
        login = User.objects.create_user(
            username=email, email=email, password=password, is_active=is_active
        )
        login.app_user_id = konto.id
        login.save(update_fields=["app_user_id"])

    return konto, login


def set_user_status(actor_app_user_id, *, user_id, status):
    """Aktiviert oder sperrt einen Benutzer — fachlich UND beim Login.

    Kein Löschen: `security.app_user` ist append-only. Ein gesperrtes Konto
    bleibt als Urheber vergangener Vorgänge lesbar, kann sich aber nicht mehr
    anmelden (`is_active=False`) und taucht in Auswahlfeldern nicht mehr auf.

    Härtung: Der letzte aktive ADMINISTRATION-Zugang lässt sich nicht sperren —
    dieselbe Begründung wie bei `end_user_role`. Und niemand sperrt sich selbst
    aus.
    """
    from accounts.models import User
    from db_core.models import AppUser

    if status not in ("ACTIVE", "DISABLED"):
        raise ValueError(f"Unbekannter Status: {status}")

    konto = AppUser.objects.filter(id=user_id).first()
    if konto is None:
        raise ValueError("Benutzer (app_user) existiert nicht.")

    if status == "DISABLED":
        if str(user_id) == str(actor_app_user_id):
            raise ValueError("Sie können sich nicht selbst sperren.")
        gesperrte_admin_rollen = _active_admin_assignments().filter(user_id=user_id)
        if gesperrte_admin_rollen.exists() and not _active_admin_assignments().exclude(
            user_id=user_id
        ).exists():
            raise ValueError(
                "Der letzte Zugang mit ADMINISTRATION kann nicht gesperrt werden — "
                "sonst wäre das System nicht mehr administrierbar."
            )

    konto.status = status
    with business_transaction(actor_app_user_id):
        konto.save(update_fields=["status", "updated_at"])
        User.objects.filter(app_user_id=user_id).update(is_active=(status == "ACTIVE"))
    konto.refresh_from_db()
    return konto
