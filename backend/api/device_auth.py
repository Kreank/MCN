"""Bearer-Token-Auth für die native App (neben der Session-Cookie-Auth).

django-ninja probiert bei `auth=[django_auth, DeviceTokenAuth()]` die Verfahren
der Reihe nach: Fehlt eine gültige Session (App-Request ohne Cookie), greift der
Bearer. Erfolgreich, wenn der Authorization-Header ein gültiges, nicht
widerrufenes Geräte-Token trägt und das zugehörige Login-Konto aktiv ist.

Anders als der Session-Login setzt django-ninja bei `HttpBearer` `request.user`
NICHT selbst — die Endpunkte und die Rechteprüfung lesen aber
`request.user.app_user_id` (api/permissions.py). Deshalb setzt `authenticate`
`request.user` explizit auf das aufgelöste Login-Konto (sonst bliebe es
AnonymousUser und jeder rechtegeschützte Endpunkt bräche). Das aufgelöste Token
wird zusätzlich am Request hinterlegt (`request.device_token`), damit
`device_logout` genau das präsentierte Token widerrufen kann.

Sicherheit: Das Token wird hier nie geloggt; die Auflösung geht ausschließlich
über den Hash (db_core.services.geraetetoken).
"""
from ninja.security import HttpBearer

from db_core.services import geraetetoken


class DeviceTokenAuth(HttpBearer):
    def authenticate(self, request, token):
        device_token = geraetetoken.token_aufloesen(token)
        if device_token is None:
            return None
        user = device_token.user
        if user is None or not user.is_active:
            return None
        # ninja setzt request.user bei Bearer nicht — die Rechteprüfung braucht es.
        request.user = user
        # Für device_logout: genau dieses präsentierte Token widerrufen.
        request.device_token = device_token
        return user
