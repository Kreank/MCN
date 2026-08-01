"""Rechte-Service: wertet die Rechtematrix aus security.* aus.

Migration 0026 legt die Stammdaten an und sagt im Kopf ausdrücklich: „Die
DURCHSETZUNG der Matrix erfolgt in der App-Schicht (die Anwendung verbindet sich
als technischer DB-Benutzer)." Genau das passiert hier — bis zu diesem Slice
wurden `role_permission`/`row_scope` nirgends ausgewertet.

Modell:
  security.user_role       zeitabhängige Zuordnung app_user → Rolle
  security.role_permission Rolle × Modul × Aktion → allowed + row_scope

Ein Benutzer darf eine Aktion, wenn **mindestens eine** seiner heute gültigen
Rollen sie erlaubt (Rollen addieren Rechte, sie beschneiden sie nicht). Beim
row_scope gilt umgekehrt die **weiteste** Sicht seiner Rollen: wer über
irgendeine Rolle 'ALLE' sieht, sieht alles — sonst nur 'EIGENE'.

Das Ergebnis wird pro Request gecacht (siehe api/permissions.py); die Matrix ist
Stammdaten und ändert sich selten.
"""
from datetime import date

from django.db.models import Q

from db_core.models import RolePermission, UserRole

MODULES = (
    "identity",
    "property",
    "management",
    "tenure",
    "billing",
    "workflow",
    "invoicing",
    "pricing",
    "content",
    "security",
    "ai",
    # Nachgezogene Module: der CHECK auf security.role_permission.module wurde
    # je Migration erweitert (hr=0021, company=0024, accounting=0032,
    # maintenance=0071). Fehlt ein Modul hier, führt die Matrix zwar Zellen dafür
    # und `effective_permissions` setzt sie durch, aber die Pflege lehnt sie mit
    # „Unbekanntes Modul" ab und `GET /security/permissions` liefert eine
    # unvollständige Spaltenliste.
    "hr",
    "company",
    "accounting",
    "maintenance",
)

ACTIONS = (
    "LESEN",
    "ANLEGEN",
    "AENDERN",
    "FREIGEBEN",
    "VERSENDEN",
    "STORNIEREN",
    "EXPORTIEREN",
    "LOESCHEN",
)


class PermissionDenied(Exception):
    """Der Benutzer hat für diese Modul/Aktion-Kombination kein Recht."""


def active_role_codes(app_user_id, on=None):
    """Rollen-Codes, die für diesen app_user am Stichtag gültig sind.

    `valid_until` ist exklusiv (DB-CHECK `valid_until > valid_from`), NULL heißt
    unbefristet.
    """
    on = on or date.today()
    rows = (
        UserRole.objects.filter(user_id=app_user_id, valid_from__lte=on)
        .filter(Q(valid_until__isnull=True) | Q(valid_until__gt=on))
        .values_list("role_id", flat=True)
    )
    return set(rows)


def effective_permissions(app_user_id, on=None):
    """Alle erlaubten (modul, aktion) → row_scope für diesen Benutzer.

    Rollen addieren Rechte. Beim row_scope gewinnt die weiteste Sicht ('ALLE'),
    weil eine zweite Rolle das Sichtfeld erweitert und nicht verengt.
    """
    codes = active_role_codes(app_user_id, on)
    if not codes:
        return {}

    result = {}
    rows = RolePermission.objects.filter(role_id__in=codes, allowed=True).values_list(
        "module", "action", "row_scope"
    )
    for module, action, row_scope in rows:
        key = (module, action)
        if result.get(key) == "ALLE":
            continue
        result[key] = row_scope
    return result


def has_permission(app_user_id, module, action, on=None):
    """Darf dieser Benutzer die Aktion im Modul ausführen?"""
    _validate(module, action)
    return (module, action) in effective_permissions(app_user_id, on)


def row_scope(app_user_id, module, action, on=None):
    """'ALLE' | 'EIGENE' | None (kein Recht)."""
    _validate(module, action)
    return effective_permissions(app_user_id, on).get((module, action))


def require(app_user_id, module, action, on=None):
    """Wirft PermissionDenied, wenn das Recht fehlt. Gibt den row_scope zurück."""
    _validate(module, action)
    scope = effective_permissions(app_user_id, on).get((module, action))
    if scope is None:
        raise PermissionDenied(
            f"Keine Berechtigung: {action} im Modul {module}. "
            "Wenden Sie sich an die Administration."
        )
    return scope


def empfaenger_mit_recht(module, action, on=None):
    """app_user-Ids, die diese Aktion heute mit Scope 'ALLE' ausführen dürfen.

    Gedacht als **Empfängerkreis für Benachrichtigungen**: „Eine Benachrichtigung
    darf nur enthalten, was ihr Empfänger am Ziel ohnehin sehen darf"
    (`docs/INVARIANTEN.md`, Abschnitt 5). Wer eine Meldung an „die zuständige
    Rolle" schickt, muss diesen Kreis aus derselben Matrix ableiten, aus der die
    API ihre 403 zieht — sonst trägt die Glocke Text an jemanden, den der
    Endpunkt abweist.

    **Nur 'ALLE'.** Ein Konto mit `row_scope='EIGENE'` bekäme auf dem
    zugehörigen Endpunkt ohnehin 403 (`api/permissions.require` weitet 'EIGENE'
    nie auf) — es darf das Ziel also nicht sehen und gehört nicht in den Kreis.

    Systemakteure (`is_system`) bleiben außen vor: Sie haben kein Postfach, das
    jemand liest.
    """
    _validate(module, action)
    codes = set(
        RolePermission.objects.filter(
            module=module, action=action, allowed=True, row_scope="ALLE"
        ).values_list("role_id", flat=True)
    )
    if not codes:
        return []
    on = on or date.today()
    ids = (
        UserRole.objects.filter(role_id__in=codes, valid_from__lte=on)
        .filter(Q(valid_until__isnull=True) | Q(valid_until__gt=on))
        .filter(user__status="ACTIVE", user__is_system=False)
        .values_list("user_id", flat=True)
        .distinct()
    )
    return list(ids)


def _validate(module, action):
    """Tippfehler im Aufrufer sollen laut scheitern, nicht still 403 liefern."""
    if module not in MODULES:
        raise ValueError(f"Unbekanntes Modul: {module}")
    if action not in ACTIONS:
        raise ValueError(f"Unbekannte Aktion: {action}")
