"""Symmetrische Verschlüsselung von Werkzeug-/Geräte-Zugangsdaten (Fernet, at rest).

**Bewusst isoliert vom Mailversand:** eigener Schlüssel `MCN_CRED_KEY` (NICHT
`MCN_MAIL_KEY`). Damit sind die KI-Geräteflotte (ASR-Handy, S21-Vision, OCR) und der
Mailversand entkoppelt — eine Schlüsselrotation des einen bricht nicht das andere.
Das ist die Lehre aus dem IDS-Connect-Vorfall, wo Händler-Zugangsdaten und SMTP am
selben `MCN_MAIL_KEY` hingen und das Fehlen des Schlüssels beides lahmlegte.

Verschlüsselt die Bearer-Token, mit denen MCN die **passiven** Geräte
authentifiziert (MCN initiiert alle Verbindungen). Das Klartext-Token liegt NIE in
einer Fachtabelle; `ai.tool` trägt nur einen `credential_reference` (Verweis), nie
das Secret.

Schlüsselverwaltung (`MCN_CRED_KEY`):
- base64-kodierter 32-Byte-Fernet-Key (`cryptography.fernet.Fernet.generate_key()`),
  NUR in der Umgebung, NIE im Repo.
- **Fail-closed**: fehlt oder unbrauchbar → `CredKeyError`. Ohne Schlüssel ist weder
  Speichern (Verschlüsseln) noch Aufrufen (Entschlüsseln) möglich.
- **Rotation** = neuen Schlüssel erzeugen, alle Chiffren mit dem alten ent- und mit
  dem neuen wieder verschlüsseln (Re-Encrypt); ein Rotationswerkzeug ist nicht Teil
  dieses Slices.

Diese Datei loggt NIE Klartext und gibt in Fehlermeldungen weder das Zugangsdatum
noch den Schlüssel aus (dieselbe Doktrin wie mail_crypto/llm.py).
"""
from django.conf import settings
from cryptography.fernet import Fernet, InvalidToken


class CredKeyError(RuntimeError):
    """MCN_CRED_KEY fehlt, ist ungültig oder die Chiffre passt nicht zum Schlüssel.

    Eigene Klasse, damit die App sie gezielt in eine klare, secret-freie Meldung
    übersetzen kann (fail-closed), ohne einen 500-Leak.
    """


def _fernet() -> Fernet:
    key = getattr(settings, "MCN_CRED_KEY", "") or ""
    if not key:
        raise CredKeyError(
            "MCN_CRED_KEY ist nicht gesetzt. Ohne Schlüssel können Werkzeug-"
            "Zugangsdaten nicht ver-/entschlüsselt werden (fail-closed). "
            "Den Schlüssel in der Umgebung setzen."
        )
    try:
        return Fernet(key.encode("ascii") if isinstance(key, str) else key)
    except Exception:  # ValueError bei falscher Länge/kein base64
        # Der Schlüsselwert selbst wird NICHT in die Meldung aufgenommen.
        raise CredKeyError(
            "MCN_CRED_KEY ist kein gültiger Fernet-Schlüssel "
            "(base64-kodierter 32-Byte-Schlüssel erwartet)."
        ) from None


def encrypt(plaintext: str) -> bytes:
    """Verschlüsselt ein Geräte-Bearer für die Ablage (bytea)."""
    return _fernet().encrypt(plaintext.encode("utf-8"))


def decrypt(token) -> str:
    """Entschlüsselt eine Chiffre aus der DB (bytes/memoryview) zur Aufrufzeit."""
    try:
        return _fernet().decrypt(bytes(token)).decode("utf-8")
    except InvalidToken:
        raise CredKeyError(
            "Ein gespeichertes Werkzeug-Zugangsdatum konnte nicht entschlüsselt "
            "werden — der Schlüssel (MCN_CRED_KEY) passt nicht zur Chiffre. "
            "Zugangsdatum neu hinterlegen oder den korrekten Schlüssel bereitstellen."
        ) from None
