"""Auth-API — Anmeldung mit E-Mail + Passwort, Django-Session.

Bewusst kein Fremdanbieter (kein SSO/OIDC): eigenes Login, keine externe
Abhängigkeit. Der Mechanismus ist Djangos Session-Cookie, den django-ninja über
`auth=django_auth` auf allen übrigen Endpunkten bereits erwartet — dieser Router
füllt die Lücke, mehr nicht.

Ablauf im Frontend:
  1. GET  /api/auth/csrf   setzt das csrftoken-Cookie (nur nötig vor dem ersten
                           unsicheren Request einer frischen Sitzung)
  2. POST /api/auth/login  {email, password} → Session-Cookie + Benutzerprofil
  3. GET  /api/auth/me     wer bin ich, was darf ich (für Guard und UI)
  4. POST /api/auth/logout beendet die Sitzung

Diese vier Endpunkte tragen `auth=None`, weil sie erreichbar sein müssen, bevor
eine Sitzung besteht. Alles andere in der API ist ab jetzt anmeldepflichtig
(globales `auth=django_auth` auf der NinjaAPI-Instanz).
"""
from uuid import UUID

from django.contrib.auth import authenticate, login, logout
from django.middleware.csrf import get_token
from ninja import Router, Schema
from ninja.errors import HttpError
from ninja.utils import check_csrf

from db_core.models import AppUser
from db_core.services import rechte as rechte_service

router = Router()


def _require_csrf(request):
    """CSRF-Prüfung für die `auth=None`-Endpunkte.

    django-ninja prüft CSRF nur bei Cookie-Auth — Endpunkte mit `auth=None`
    sind befreit. Für Login und Logout ist das zu wenig:

    * **Login-CSRF**: Ohne Prüfung könnte eine fremde Seite das Opfer
      unbemerkt in ein vom Angreifer kontrolliertes Konto einloggen; alles,
      was das Opfer danach anlegt, landet in dessen Konto.
    * **Logout-CSRF**: harmloser, aber ein unnötiges Ärgernis.

    `check_csrf` ist ninjas eigene Prüffunktion (ninja.utils) und gibt bei
    Verstoß ein HttpResponseForbidden zurück. Das Frontend holt sich den Token
    vorher über GET /api/auth/csrf.
    """
    if check_csrf(request) is not None:
        raise HttpError(403, "CSRF-Prüfung fehlgeschlagen.")


class LoginIn(Schema):
    email: str
    password: str


class PermissionOut(Schema):
    module: str
    action: str
    row_scope: str


class MeOut(Schema):
    id: int
    email: str
    display_name: str
    app_user_id: UUID | None = None
    is_staff: bool
    roles: list[str]
    permissions: list[PermissionOut]


def _me(user):
    """Profil + effektive Rechte. Ohne app_user gibt es weder Rollen noch Rechte."""
    roles = []
    permissions = []
    display_name = user.get_full_name() or user.email

    if user.app_user_id:
        app_user = AppUser.objects.filter(id=user.app_user_id).first()
        if app_user is not None:
            display_name = app_user.display_name
        roles = sorted(rechte_service.active_role_codes(user.app_user_id))
        permissions = [
            PermissionOut(module=module, action=action, row_scope=scope)
            for (module, action), scope in sorted(
                rechte_service.effective_permissions(user.app_user_id).items()
            )
        ]

    return MeOut(
        id=user.pk,
        email=user.email,
        display_name=display_name,
        app_user_id=user.app_user_id,
        is_staff=user.is_staff,
        roles=roles,
        permissions=permissions,
    )


@router.get("/csrf", auth=None)
def csrf(request):
    """Setzt das csrftoken-Cookie und liefert den Token auch im Body.

    `get_token(request)` erzeugt den Token und markiert das Cookie zur
    Aktualisierung; die `CsrfViewMiddleware` setzt es dann auf der fertigen
    Antwort. Kein `@ensure_csrf_cookie`: dessen process_response läuft hier auf
    dem noch nicht serialisierten dict der ninja-Operation (das kein set_cookie
    kennt) und würde abstürzen.

    Das Cookie ist absichtlich NICHT HttpOnly — das Frontend muss es lesen, um
    es als X-CSRFToken-Header zurückzuschicken (Djangos Double-Submit-Verfahren).
    """
    return {"csrftoken": get_token(request)}


@router.post("/login", response=MeOut, auth=None)
def login_view(request, payload: LoginIn):
    """Anmeldung mit E-Mail + Passwort."""
    _require_csrf(request)
    user = authenticate(request, email=payload.email, password=payload.password)
    if user is None:
        # Eine einzige, unspezifische Meldung für falsches Passwort, unbekannte
        # Adresse UND deaktiviertes Konto: `user_can_authenticate` filtert
        # inaktive Konten bereits hier heraus. Ob die Adresse existiert oder das
        # Konto gesperrt ist, geht den Anfragenden nichts an.
        raise HttpError(401, "E-Mail-Adresse oder Passwort ist falsch.")

    login(request, user)  # rotiert die Session-ID (Session Fixation)
    return _me(user)


@router.post("/logout", auth=None)
def logout_view(request):
    """Beendet die Sitzung. Idempotent — auch ohne Sitzung ein sauberes 200."""
    _require_csrf(request)
    logout(request)
    return {"detail": "abgemeldet"}


@router.get("/me", response=MeOut, auth=None)
def me(request):
    """Aktueller Benutzer samt Rollen und effektiven Rechten.

    `auth=None` und manuelle Prüfung: der Angular-Guard fragt diesen Endpunkt
    beim Start, und ein anonymer Aufruf soll ein sauberes 401 liefern statt
    einer Ninja-Auth-Exception.
    """
    if not request.user.is_authenticated:
        raise HttpError(401, "Nicht angemeldet.")
    return _me(request.user)
