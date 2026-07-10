"""Mailversand-Service: Absenderkonto pflegen, Mail senden, Versand protokollieren.

Fundament (dieser Slice), NOCH KEINE Beleg-Anbindung. Das Passwort wird
ausschließlich verschlüsselt in company.mail_account.password_encrypted abgelegt
(mail_crypto, Fernet) und nur zur Sendezeit entschlüsselt. Es wird NIE über die
API zurückgegeben, NIE geloggt, NIE in einer Fehlermeldung ausgegeben.

Writes über business_transaction (Benutzerkontext/Audit). Validierungsfehler →
ValueError (die API übersetzt in 422). SMTP-/Verbindungsfehler → MailSendError
mit einer klaren, passwortfreien Meldung (kein 500-Leak). Fehlender
MCN_MAIL_KEY → MailKeyError (fail-closed) aus mail_crypto.

Protokollierung: Ein erfolgreicher Versand schreibt eine content.communication-
Zeile (channel EMAIL, direction AUSGEHEND — die DB-CHECK-Werte sind deutsch —,
recorded_by=Akteur). Sie landet im Klärungskorb (KLAERUNGSKORB, ohne
Verknüpfung); die Zuordnung an Beleg/Vorgang ist ein späterer Slice.
"""
import re
import uuid

from django.core.mail import EmailMessage, get_connection

from db_core import mail_crypto
from db_core.db_context import business_transaction
from db_core.models import Communication, MailAccount

_SECURITY_CHOICES = ("NONE", "STARTTLS", "SSL")
# Grober E-Mail-Check, spiegelt den DB-CHECK aus 0046 (from_address). Feiner als
# der DB-CHECK wäre Overengineering — SMTP validiert beim Versand endgültig.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class MailSendError(RuntimeError):
    """SMTP-/Verbindungsfehler beim Versand. Trägt NUR eine sichere Meldung
    (nie Passwort/Zugangsdaten); die Ursache wird als __cause__ angehängt, aber
    von der API nicht an den Client ausgegeben."""


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _validate(*, label, host, port, security, from_address):
    label = _clean(label)
    host = _clean(host)
    from_address = _clean(from_address)
    if not label:
        raise ValueError("Bezeichnung ist erforderlich.")
    if not host:
        raise ValueError("SMTP-Host ist erforderlich.")
    try:
        port = int(port)
    except (TypeError, ValueError):
        raise ValueError("Port muss eine Zahl sein.")
    if not 1 <= port <= 65535:
        raise ValueError("Port muss zwischen 1 und 65535 liegen.")
    security = (security or "").strip().upper()
    if security not in _SECURITY_CHOICES:
        raise ValueError("Sicherheit muss NONE, STARTTLS oder SSL sein.")
    if not from_address:
        raise ValueError("Absender-Adresse ist erforderlich.")
    if not _EMAIL_RE.match(from_address):
        raise ValueError("Absender-Adresse ist keine gültige E-Mail-Adresse.")
    return label, host, port, security, from_address


def get_mail_account():
    """Das aktive Absenderkonto oder None. Enthält die Chiffre — die API gibt das
    Passwort NIE aus, nur `has_password`."""
    return MailAccount.objects.filter(active=True).first()


def set_mail_account(actor, *, label, host, port, security, username=None,
                     password=None, from_address, from_name=None):
    """Legt das aktive Absenderkonto an oder aktualisiert es (Singleton-Upsert).

    `password`:
      - None  → beim Update unverändert lassen; beim Anlegen kein Passwort setzen.
      - ""    → als „kein Passwort" behandelt (offenes Relay); Chiffre = NULL.
      - sonst → mit Fernet verschlüsseln (mail_crypto). Fehlt MCN_MAIL_KEY,
        wirft mail_crypto.MailKeyError (fail-closed) BEVOR geschrieben wird.

    Erzwingt genau ein aktives Konto (partieller Unique-Index in 0046): es gibt
    immer nur die eine aktive Zeile, die hier gepflegt wird.
    """
    label, host, port, security, from_address = _validate(
        label=label, host=host, port=port, security=security,
        from_address=from_address,
    )
    username = _clean(username)
    from_name = _clean(from_name)

    # Verschlüsselung VOR der Transaktion (CPU, kein DB-Zugriff). Fail-closed:
    # ohne Schlüssel wird gar nicht erst geschrieben.
    set_password = password is not None
    encrypted = None
    if set_password and password != "":
        encrypted = mail_crypto.encrypt(password)

    account = get_mail_account()
    with business_transaction(actor):
        if account is None:
            account = MailAccount.objects.create(
                id=uuid.uuid4(), label=label, host=host, port=port,
                security=security, username=username,
                password_encrypted=encrypted, from_address=from_address,
                from_name=from_name, active=True,
            )
        else:
            account.label = label
            account.host = host
            account.port = port
            account.security = security
            account.username = username
            account.from_address = from_address
            account.from_name = from_name
            fields = ["label", "host", "port", "security", "username",
                      "from_address", "from_name"]
            if set_password:
                account.password_encrypted = encrypted
                fields.append("password_encrypted")
            account.save(update_fields=fields + ["updated_at"])
    account.refresh_from_db()
    return account


def send_mail(actor, *, to_address, subject, body, attachments=None,
              party_id=None, is_commercial=False):
    """Sendet eine Mail über das aktive Konto und protokolliert sie.

    Lädt das aktive Konto, entschlüsselt das Passwort (nur hier), baut die
    EmailMessage und sendet über eine dedizierte SMTP-Verbindung. Bei Erfolg wird
    eine content.communication-Zeile geschrieben (channel EMAIL, direction
    AUSGEHEND, recorded_by=actor).

    Fehler:
      - kein aktives Konto → ValueError (klar).
      - MCN_MAIL_KEY fehlt/passt nicht → mail_crypto.MailKeyError (fail-closed).
      - SMTP-/Verbindungsfehler → MailSendError (passwortfreie Meldung).

    `attachments`: optionale Liste von (dateiname, inhalt_bytes, mimetype) —
    Plumbing für spätere Beleg-Anbindung; hier ungenutzt.
    """
    to_address = _clean(to_address)
    if not to_address:
        raise ValueError("Empfänger-Adresse ist erforderlich.")
    if not _EMAIL_RE.match(to_address):
        raise ValueError("Empfänger-Adresse ist keine gültige E-Mail-Adresse.")

    account = get_mail_account()
    if account is None:
        raise ValueError(
            "Kein aktives Mailkonto konfiguriert. Bitte zuerst unter "
            "Einstellungen → Mailversand ein Absenderkonto hinterlegen."
        )

    password = ""
    if account.password_encrypted is not None:
        # Kann MailKeyError werfen (fail-closed) — bewusst nicht abgefangen.
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
    message = EmailMessage(
        subject=subject, body=body, from_email=from_email,
        to=[to_address], connection=connection,
    )
    for att in attachments or []:
        filename, content, mimetype = att
        message.attach(filename, content, mimetype)

    try:
        message.send(fail_silently=False)
    except Exception as exc:
        # Bewusst KEINE Details/Zugangsdaten in die Client-Meldung. Die Ursache
        # bleibt als __cause__ für Server-Logs erhalten (enthält kein Passwort).
        raise MailSendError(
            "Die E-Mail konnte nicht versendet werden. Bitte SMTP-Host, Port, "
            "Sicherheit und Zugangsdaten prüfen und erneut versuchen."
        ) from exc

    with business_transaction(actor):
        communication = Communication.objects.create(
            id=uuid.uuid4(), channel="EMAIL", direction="AUSGEHEND",
            subject=subject, body=body, counterpart_party_id=party_id,
            counterpart_raw=to_address, recorded_by=actor,
            is_commercial=is_commercial,
        )
    return communication


def send_test_mail(actor, to_address):
    """Testmail (Betreff/Body „MCN Testmail") über send_mail."""
    return send_mail(
        actor,
        to_address=to_address,
        subject="MCN Testmail",
        body=(
            "Dies ist eine Testmail aus MCN. Sie bestätigt, dass der "
            "SMTP-Versand mit dem hinterlegten Absenderkonto funktioniert."
        ),
    )
