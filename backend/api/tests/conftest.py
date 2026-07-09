"""Fixtures für die API-Tests: eingeloggte Clients mit Rolle, anonymer Client.

Seit dem Auth-/Rechte-Slice ist die gesamte API anmeldepflichtig (globales
`auth=django_auth`), und jeder Endpunkt prüft zusätzlich ein Recht aus der
Rechtematrix (`api/permissions.require`). Ein Test, der einen Endpunkt aufruft,
braucht daher einen eingeloggten Client, dessen `security.app_user` eine Rolle
mit dem passenden Recht trägt. ADMINISTRATION darf alles, deshalb deckt
`admin_client` sämtliche positiven Fälle ab. Für gezielte Rechte-/Negativtests
gibt es `client_with_role(...)` und `anonymous_client`.

Die Helfer sind auch als Modulfunktionen importierbar (make_role_user,
grant_role, logged_in_client), damit Tests einzelne Bausteine wiederverwenden
können, ohne die Fixtures zu bemühen.
"""
import uuid
from datetime import date

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from db_core.db_context import business_transaction
from db_core.models import AppUser, UserRole

User = get_user_model()

# Wegwerf-Passwort für Test-Logins; erfüllt die 12-Zeichen-Policy.
TEST_PASSWORD = "test-passwort-2026"


def make_app_user(display_name="Test-Login"):
    """Ein fachlicher security.app_user (Akteur für Schreibvorgänge/Rollen)."""
    return AppUser.objects.create(
        id=uuid.uuid4(), display_name=display_name, status="ACTIVE", version=1,
    )


def grant_role(app_user_id, role_code, *, granted_by=None, valid_from=None,
               valid_until=None):
    """Rollenzuordnung über security.user_role.

    user_role ist eine Fachtabelle → Anlage über business_transaction
    (Benutzerkontext/Audit). granted_by ist NOT NULL und wird, wenn nicht
    angegeben, auf den Beschenkten selbst gesetzt (Selbstzuweisung im Test).
    """
    granted_by = granted_by or app_user_id
    with business_transaction(granted_by):
        return UserRole.objects.create(
            id=uuid.uuid4(), user_id=app_user_id, role_id=role_code,
            valid_from=valid_from or date.today(), valid_until=valid_until,
            granted_by_id=granted_by,
        )


def make_role_user(role_code, *, with_app_user=True, password=None, email=None,
                   is_staff=False, is_superuser=False):
    """Login-Konto (accounts.User) + optional app_user + Rolle.

    Gibt (user, app_user) zurück; app_user ist None, wenn with_app_user=False
    (für den Negativtest „Konto ohne app_user_id → 403"). Wird eine Rolle
    angegeben, aber with_app_user=False, kann keine Rolle vergeben werden.
    """
    suffix = uuid.uuid4().hex[:8]
    email = email or f"user-{suffix}@example.test"
    user = User(username=email, email=email, is_staff=is_staff,
                is_superuser=is_superuser)
    user.set_password(password or TEST_PASSWORD)
    app_user = None
    if with_app_user:
        app_user = make_app_user()
        user.app_user_id = app_user.id
    user.save()
    if with_app_user and role_code:
        grant_role(app_user.id, role_code)
    return user, app_user


def logged_in_client(role_code, **kwargs):
    """Ein eingeloggter Django-Test-Client mit der gegebenen Rolle."""
    user, _ = make_role_user(role_code, **kwargs)
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def client_with_role(db):
    """Factory: eingeloggter Client mit beliebiger Rolle.

        c = client_with_role("MONTEUR")
        c_ohne = client_with_role("MONTEUR", with_app_user=False)
    """
    def _make(role_code, *, with_app_user=True, **kwargs):
        return logged_in_client(role_code, with_app_user=with_app_user, **kwargs)
    return _make


@pytest.fixture
def admin_client(db):
    """Eingeloggter Client mit app_user + Rolle ADMINISTRATION (darf alles)."""
    return logged_in_client("ADMINISTRATION")


@pytest.fixture
def anonymous_client():
    """Frischer, nicht eingeloggter Client — für 401-Prüfungen."""
    return Client()
