"""Mailversand-API — SMTP-Absenderkonto (Konfig ohne Passwort) und Testmail.

Modul `company` in der Rechtematrix (0024 db_core): LESEN für alle Rollen,
AENDERN nur ADMINISTRATION/GESCHAEFTSFUEHRUNG. Das SMTP-Passwort ist
**write-only**: es wird nie zurückgegeben (nur `has_password: bool`), nie
geloggt, nie in einer Fehlermeldung ausgegeben.

Fehlerabbildung:
  - Validierung (Port/Adresse/…) → 422 (ValueError).
  - fehlender/ungültiger MCN_MAIL_KEY → 422 mit klarer, passwortfreier Meldung
    (fail-closed; MailKeyError).
  - SMTP-/Verbindungsfehler bei der Testmail → 422 (MailSendError, passwortfrei).
"""
from ninja import Router, Schema
from ninja.errors import HttpError
from ninja.security import django_auth

from api.permissions import require
from db_core.mail_crypto import MailKeyError
from db_core.services import mail as mail_service
from db_core.services.mail import MailSendError

router = Router()


# --- Schemas ---------------------------------------------------------------

class MailAccountOut(Schema):
    exists: bool
    label: str | None = None
    host: str | None = None
    port: int | None = None
    security: str | None = None
    username: str | None = None
    from_address: str | None = None
    from_name: str | None = None
    active: bool | None = None
    # Ob ein Passwort hinterlegt ist — das Passwort selbst wird NIE ausgeliefert.
    has_password: bool = False


class MailAccountIn(Schema):
    label: str
    host: str
    port: int
    security: str
    username: str | None = None
    # Write-only. Nicht gesetzt / weggelassen = Passwort unverändert lassen.
    password: str | None = None
    from_address: str
    from_name: str | None = None


class TestMailIn(Schema):
    to_address: str


class TestMailOut(Schema):
    sent: bool
    to_address: str


# --- Mapper ----------------------------------------------------------------

def _account_out(a):
    if a is None:
        return MailAccountOut(exists=False)
    return MailAccountOut(
        exists=True, label=a.label, host=a.host, port=a.port,
        security=a.security, username=a.username, from_address=a.from_address,
        from_name=a.from_name, active=a.active,
        has_password=a.password_encrypted is not None,
    )


# --- Endpunkte -------------------------------------------------------------

@router.get("/mail-account", response=MailAccountOut)
def get_mail_account(request):
    """Absenderkonto lesen — OHNE Passwort (nur `has_password`). LESEN."""
    require(request, "company", "LESEN")
    return _account_out(mail_service.get_mail_account())


@router.put("/mail-account", response=MailAccountOut, auth=django_auth)
def put_mail_account(request, payload: MailAccountIn):
    """Absenderkonto setzen/ändern (nur company/AENDERN). Passwort write-only,
    optional beim Update (unset = unverändert)."""
    actor, _ = require(request, "company", "AENDERN")
    fields = payload.model_dump(exclude_unset=True)
    try:
        account = mail_service.set_mail_account(actor, **fields)
    except ValueError as exc:
        raise HttpError(422, str(exc))
    except MailKeyError as exc:
        # Server-Fehlkonfiguration, aber als klare, passwortfreie 422-Meldung an
        # das UI (Konfig nicht speicherbar) statt eines 500-Leaks.
        raise HttpError(422, str(exc))
    return _account_out(account)


@router.post("/mail-account/test", response=TestMailOut, auth=django_auth)
def test_mail_account(request, payload: TestMailIn):
    """Testmail an `to_address` senden (nur company/AENDERN). Fehler (SMTP nicht
    erreichbar, Schlüssel fehlt, kein Konto) → 422 mit passwortfreier Meldung."""
    actor, _ = require(request, "company", "AENDERN")
    try:
        mail_service.send_test_mail(actor, payload.to_address)
    except ValueError as exc:
        raise HttpError(422, str(exc))
    except (MailKeyError, MailSendError) as exc:
        raise HttpError(422, str(exc))
    return TestMailOut(sent=True, to_address=payload.to_address)
