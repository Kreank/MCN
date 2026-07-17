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
import logging
import threading
from uuid import UUID

from django.conf import settings
from django.contrib.auth import (
    authenticate,
    get_user_model,
    login,
    logout,
    update_session_auth_hash,
)
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage, get_connection
from django.db import connection as db_connection
from django.middleware.csrf import get_token
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from ninja import Router, Schema
from ninja.errors import HttpError
from ninja.security import django_auth
from ninja.utils import check_csrf

from api.device_auth import DeviceTokenAuth
from db_core import mail_crypto
from db_core.models import AppUser
from db_core.services import geraetetoken
from db_core.services import mail as mail_service
from db_core.services import rechte as rechte_service

logger = logging.getLogger(__name__)

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


# ---------------------------------------------------------------------------
# Geräte-Login (Bearer) — Anmeldeweg der nativen App neben der Session-Cookie-Auth
# ---------------------------------------------------------------------------


class DeviceLoginIn(Schema):
    email: str
    password: str
    device_name: str


class DeviceLoginOut(Schema):
    # Das Klartext-Bearer-Token — NUR in dieser Antwort, danach nie wieder.
    token: str
    display_name: str
    app_user_id: UUID | None = None
    roles: list[str]


@router.post("/device/login", response=DeviceLoginOut, auth=None)
def device_login(request, payload: DeviceLoginIn):
    """Anmeldung der nativen App: E-Mail + Passwort → Bearer-Token.

    KEIN CSRF (anders als der Session-Login): Es wird kein Cookie gesetzt — das
    Token kommt im Antwort-Body und wird von der App gespeichert. Es gibt keine
    CSRF-Angriffsfläche (kein Cookie, das ein Browser automatisch mitschickt).

    Bei falschen Zugangsdaten dieselbe unspezifische 401-Meldung wie beim
    Session-Login (keine User-Enumeration; `authenticate` filtert inaktive Konten
    bereits heraus).

    Das Klartext-Token steht AUSSCHLIESSLICH in dieser Antwort; in der DB liegt
    nur sein SHA-256-Hash. `display_name`/`app_user_id`/`roles` werden identisch
    zu `_me` berechnet.

    OFFEN (nicht in diesem Slice): Rate-Limiting/Brute-Force-Schutz für diesen
    Login-Endpunkt.
    """
    user = authenticate(request, email=payload.email, password=payload.password)
    if user is None:
        raise HttpError(401, "E-Mail-Adresse oder Passwort ist falsch.")

    token = geraetetoken.token_ausstellen(user, payload.device_name)
    profil = _me(user)
    return DeviceLoginOut(
        token=token,
        display_name=profil.display_name,
        app_user_id=profil.app_user_id,
        roles=profil.roles,
    )


@router.post("/device/logout", auth=DeviceTokenAuth())
def device_logout(request):
    """Widerruft das aktuell präsentierte Geräte-Token. Idempotent (200).

    Nur Bearer-Auth: der Endpunkt wirkt ausschließlich auf das Token, mit dem er
    aufgerufen wurde (`request.device_token`, in `DeviceTokenAuth` hinterlegt) —
    kein Zielparameter, kein Modul-Recht (analog `POST /api/auth/password`, das
    ebenfalls nur auf das eigene Konto wirkt). Ein bereits widerrufenes Token zu
    widerrufen ist ein sauberes 200.
    """
    device_token = getattr(request, "device_token", None)
    if device_token is not None:
        geraetetoken.token_widerrufen(device_token)
    return {"detail": "abgemeldet"}


class PasswordChangeIn(Schema):
    old_password: str
    new_password: str


@router.post("/password", auth=django_auth)
def change_password(request, payload: PasswordChangeIn):
    """Eigenes Passwort ändern (nur das des angemeldeten Kontos).

    Bewusst OHNE Modul-Recht: jeder darf sein eigenes Passwort ändern. Dennoch
    `auth=django_auth` — ohne Anmeldung 401. Der Endpunkt kann kein fremdes Konto
    treffen: er wirkt ausschließlich auf `request.user`, es gibt keinen Parameter
    für ein Zielkonto.

    Ablauf:
      1. altes Passwort verifizieren (`check_password`) → bei Fehlschlag 400 mit
         unspezifischer Meldung (keine Aussage darüber, was genau falsch war);
      2. neues Passwort gegen die Policy prüfen (`validate_password`, u. a. min.
         12 Zeichen) → 422 mit den deutschen Validator-Meldungen;
      3. setzen und speichern, dann `update_session_auth_hash`, damit die
         laufende Sitzung nicht durch den geänderten Passwort-Hash ungültig wird
         (sonst fliegt der Benutzer beim nächsten Request raus).

    Passwörter werden niemals geloggt.
    """
    user = request.user
    if not user.check_password(payload.old_password):
        raise HttpError(400, "Das aktuelle Passwort ist falsch.")
    try:
        validate_password(payload.new_password, user=user)
    except ValidationError as exc:
        raise HttpError(422, " ".join(exc.messages))
    user.set_password(payload.new_password)
    user.save(update_fields=["password"])
    update_session_auth_hash(request, user)
    return {"detail": "Passwort geändert."}


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


# ---------------------------------------------------------------------------
# Passwort vergessen — Reset per Einmal-Link über den Mailversand
# ---------------------------------------------------------------------------
#
# Beide Endpunkte tragen `auth=None` (sie müssen ohne Sitzung erreichbar sein)
# und holen die CSRF-Prüfung selbst nach (`_require_csrf`, wie login/logout).
#
# Der Token ist Djangos zustandsloser `default_token_generator` — KEINE eigene
# Tabelle, KEINE Migration. Er wird single-use, sobald sich das Passwort ändert
# (`_make_hash_value` bindet den Passwort-Hash), und läuft nach
# PASSWORD_RESET_TIMEOUT (12 h) ab.
#
# Sicherheitsleitplanken (nicht verhandelbar):
#   * Anti-Enumeration: /request antwortet IMMER mit demselben 200, egal ob die
#     Adresse existiert. Der eigentliche Versand läuft in einem Hintergrund-Thread
#     — so hängt auch die AntwortZEIT nicht davon ab, ob ein Konto existiert oder
#     wie lange der SMTP-Server braucht (sonst wäre die Existenz über Timing
#     ablesbar).
#   * Kein Token/Passwort in Logs, Responses, Fehlern ODER in content.communication.
#     Deshalb wird die Reset-Mail bewusst NICHT über
#     `db_core.services.mail.send_mail` verschickt: send_mail protokolliert Betreff
#     und Body wortgetreu in content.communication — der Link (mit Token) würde
#     dort landen. Außerdem verlangt send_mail einen `actor` (app_user) für die
#     Audit-Transaktion, den es in diesem anonymen Fluss gar nicht gibt. Der Link
#     steht ausschließlich in der E-Mail an den Nutzer; es wird NICHTS
#     protokolliert. Wir nutzen aber dieselbe Absenderkonto-Infrastruktur
#     (mail_service.get_mail_account + mail_crypto) read-only weiter.

User = get_user_model()

# Eine einzige, unspezifische Meldung für unbekannte uid, kaputten/abgelaufenen
# Token und deaktiviertes Konto — nichts davon verrät, woran es lag (kein
# Enumerations-/Detail-Leak).
_INVALID_LINK = "Der Link ist ungültig oder abgelaufen."

# Immer dieselbe Antwort auf /request, unabhängig davon, ob ein Konto existiert.
_REQUEST_DETAIL = "Falls ein Konto existiert, wurde eine E-Mail gesendet."


def _reset_link(user) -> str:
    """Baut den Reset-Link auf die anmeldefreie Frontend-Route."""
    uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    base = settings.MCN_FRONTEND_BASE_URL.rstrip("/")
    return f"{base}/passwort-zuruecksetzen?uid={uidb64}&token={token}"


def _send_reset_email(to_address: str, reset_link: str) -> None:
    """Versendet die Reset-Mail über das aktive Absenderkonto — OHNE
    content.communication-Zeile (der Link/Token darf nirgends persistiert oder
    protokolliert werden).

    Läuft im Hintergrund-Thread (siehe `_run_in_background`). Jede Ausnahme wird
    geschluckt: der Client hat längst 200 erhalten, und ein Fehler darf weder als
    500 durchschlagen (Anti-Enumeration) noch den Token in ein Log tragen.

    Ist KEIN Absenderkonto konfiguriert, passiert bewusst nichts (der Admin muss
    unter Einstellungen → Mailversand ein SMTP-Konto hinterlegen). Auch das ist
    kein Fehler nach außen — /request hat trotzdem mit 200 geantwortet.
    """
    try:
        account = mail_service.get_mail_account()
        if account is None:
            return

        password = ""
        if account.password_encrypted is not None:
            password = mail_crypto.decrypt(account.password_encrypted)

        connection = get_connection(
            host=account.host,
            port=account.port,
            username=account.username or "",
            password=password,
            use_tls=(account.security == "STARTTLS"),
            use_ssl=(account.security == "SSL"),
            fail_silently=False,
        )
        from_email = (
            f"{account.from_name} <{account.from_address}>"
            if account.from_name else account.from_address
        )
        body = (
            "Sie haben das Zurücksetzen Ihres MCN-Passworts angefordert.\n\n"
            "Über den folgenden Link vergeben Sie ein neues Passwort:\n"
            f"{reset_link}\n\n"
            "Der Link ist 12 Stunden gültig. Falls Sie das nicht waren, "
            "ignorieren Sie diese E-Mail — Ihr Passwort bleibt unverändert.\n"
        )
        message = EmailMessage(
            subject="Passwort zurücksetzen — MCN",
            body=body,
            from_email=from_email,
            to=[to_address],
            connection=connection,
        )
        message.send(fail_silently=False)
    except Exception:
        # Bewusst still: KEINE Details, KEIN Token/Link, KEIN Passwort in ein Log.
        # Nur ein generischer Hinweis ohne Empfänger/Link.
        logger.warning("Versand der Passwort-Reset-Mail fehlgeschlagen.")


def _run_in_background(target, *args) -> None:
    """Feuert die Zustellung in einem Daemon-Thread ab, damit die Antwortzeit von
    /request NICHT davon abhängt, ob ein Konto existiert oder wie langsam der
    SMTP-Server ist (Anti-Enumeration, auch über Timing).

    Der Thread erhält seine eigene DB-Verbindung; die wird am Ende geschlossen,
    damit keine Verbindung leakt. (In Tests wird diese Funktion synchron
    gepatcht — dann übernimmt der Test-Framework-Kontext die Verbindung.)
    """
    def _wrapped():
        try:
            target(*args)
        finally:
            db_connection.close()

    threading.Thread(target=_wrapped, daemon=True).start()


class PasswordResetRequestIn(Schema):
    email: str


@router.post("/password-reset/request", auth=None)
def password_reset_request(request, payload: PasswordResetRequestIn):
    """Fordert einen Reset-Link an. Antwortet IMMER identisch mit 200 — egal ob
    die Adresse existiert (Anti-Enumeration). Existiert ein aktives Konto, geht
    im Hintergrund eine Mail mit einem Einmal-Link raus."""
    _require_csrf(request)

    email = (payload.email or "").strip()
    if email:
        user = User.objects.filter(email__iexact=email).first()
        # Nur an existierende, AKTIVE Konten senden. Alles andere: schweigend
        # überspringen — die Antwort bleibt in jedem Fall dieselbe.
        if user is not None and user.is_active and user.email:
            link = _reset_link(user)
            _run_in_background(_send_reset_email, user.email, link)

    return {"detail": _REQUEST_DETAIL}


class PasswordResetConfirmIn(Schema):
    uid: str
    token: str
    new_password: str


@router.post("/password-reset/confirm", auth=None)
def password_reset_confirm(request, payload: PasswordResetConfirmIn):
    """Setzt das Passwort anhand von uid + Einmal-Token neu.

    Ungültige/abgelaufene Token, unbekannte uid und deaktivierte Konten führen zu
    EINER einheitlichen 400-Meldung (kein Enumerations-/Detail-Leak). Ein zu
    schwaches Passwort → 422 mit den Validator-Meldungen (nie das Passwort selbst).
    Keine automatische Anmeldung — der Nutzer meldet sich anschließend neu an.
    Passwörter/Token werden nie geloggt.
    """
    _require_csrf(request)

    # 1) uid dekodieren + Nutzer laden. Jeder Fehlerpfad → einheitliches 400.
    try:
        uid = force_str(urlsafe_base64_decode(payload.uid))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        raise HttpError(400, _INVALID_LINK)

    # 2) Token prüfen (zustandslos, wird durch spätere Passwortänderung ungültig)
    #    und deaktivierte Konten ausschließen — beides mit derselben Meldung.
    #    check_token wird IMMER ausgewertet (nicht per and/or kurzgeschlossen),
    #    damit die Antwortzeit nicht verrät, ob das Konto aktiv ist.
    token_ok = default_token_generator.check_token(user, payload.token)
    if not user.is_active or not token_ok:
        raise HttpError(400, _INVALID_LINK)

    # 3) Passwortstärke erst NACH gültigem Token prüfen (422, ohne Passwort im Text).
    try:
        validate_password(payload.new_password, user=user)
    except ValidationError as exc:
        raise HttpError(422, " ".join(exc.messages))

    # 4) Setzen + speichern. Der geänderte Hash entwertet den Token (single-use).
    user.set_password(payload.new_password)
    user.save(update_fields=["password"])
    return {"detail": "Passwort gesetzt. Bitte melden Sie sich neu an."}
