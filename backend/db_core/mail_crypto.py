"""Symmetrische Verschlüsselung des SMTP-Passworts (Fernet, at rest).

Der sicherheitskritische Kern des Mailversand-Fundaments. Das SMTP-Passwort darf
NIE im Klartext in der Datenbank liegen (siehe Migration 0046). Es wird VOR dem
Speichern mit Fernet verschlüsselt (`encrypt`) und ausschließlich zur Sendezeit
wieder entschlüsselt (`decrypt`, services/mail.py::send_mail).

Schlüsselverwaltung (`MCN_MAIL_KEY`):
- Der Schlüssel ist ein base64-kodierter 32-Byte-Fernet-Key
  (`cryptography.fernet.Fernet.generate_key()`), NUR in der Umgebung, NIE im Repo.
- **Fail-closed**: fehlt oder unbrauchbar → `MailKeyError`. Ohne Schlüssel ist
  weder Speichern (Verschlüsseln) noch Versenden (Entschlüsseln) möglich —
  analog zur MCN_DEBUG-Pflicht: die Sicherheitsentscheidung wird bewusst
  erzwungen statt stillschweigend umgangen.
- **Produktion**: den Schlüssel über einen Secret-Manager / eine
  Umgebungsvariable des Dienstes bereitstellen (wie MCN_DB_PASSWORD, MCN_MINIO_*).
- **Rotation** = neuen Schlüssel erzeugen, alle `password_encrypted` mit dem
  alten Schlüssel ent- und mit dem neuen wieder verschlüsseln (Re-Encrypt). Ein
  Rotations-Werkzeug ist NICHT Teil dieses Slices; die einzelne Chiffre ist
  self-describing genug, dass ein späteres MultiFernet-Verfahren nachrüstbar ist.

Wichtig: Diese Datei loggt NIE Klartext-Passwörter und gibt in Fehlermeldungen
weder das Passwort noch den Schlüssel aus.
"""
from django.conf import settings
from cryptography.fernet import Fernet, InvalidToken


class MailKeyError(RuntimeError):
    """MCN_MAIL_KEY fehlt, ist ungültig oder die Chiffre passt nicht zum Schlüssel.

    Bewusst eine eigene Klasse, damit die API sie gezielt in eine klare,
    passwortfreie Meldung übersetzen kann (fail-closed), ohne einen 500-Leak.
    """


def _fernet() -> Fernet:
    key = getattr(settings, "MCN_MAIL_KEY", "") or ""
    if not key:
        raise MailKeyError(
            "MCN_MAIL_KEY ist nicht gesetzt. Ohne Schlüssel kann das "
            "SMTP-Passwort nicht ver-/entschlüsselt werden (fail-closed). "
            "Den Schlüssel in der Umgebung setzen."
        )
    try:
        return Fernet(key.encode("ascii") if isinstance(key, str) else key)
    except Exception as exc:  # ValueError bei falscher Länge/kein base64
        # Der Schlüsselwert selbst wird NICHT in die Meldung aufgenommen.
        raise MailKeyError(
            "MCN_MAIL_KEY ist kein gültiger Fernet-Schlüssel "
            "(base64-kodierter 32-Byte-Schlüssel erwartet)."
        ) from None


def encrypt(plaintext: str) -> bytes:
    """Verschlüsselt ein Passwort für die Ablage in password_encrypted (bytea)."""
    return _fernet().encrypt(plaintext.encode("utf-8"))


def decrypt(token) -> str:
    """Entschlüsselt eine Chiffre aus der DB (bytes/memoryview) zur Sendezeit."""
    try:
        return _fernet().decrypt(bytes(token)).decode("utf-8")
    except InvalidToken:
        raise MailKeyError(
            "Das gespeicherte SMTP-Passwort konnte nicht entschlüsselt werden — "
            "der Schlüssel (MCN_MAIL_KEY) passt nicht zur Chiffre. Passwort neu "
            "hinterlegen oder den korrekten Schlüssel bereitstellen."
        ) from None
