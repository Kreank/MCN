"""cred_crypto — isolierte Fernet-Verschlüsselung der Werkzeug-Zugangsdaten.

DB-frei. Eigener Schlüssel MCN_CRED_KEY (nicht MCN_MAIL_KEY): entkoppelt
Geräteflotte und Mailversand. Fail-closed ohne Schlüssel; keine Secrets in
Fehlermeldungen.
"""
import pytest
from cryptography.fernet import Fernet
from django.test import override_settings

from db_core import cred_crypto

KEY = Fernet.generate_key().decode()


@override_settings(MCN_CRED_KEY=KEY)
def test_roundtrip():
    token = cred_crypto.encrypt("geheim-bearer-xyz")
    assert isinstance(token, bytes)
    assert cred_crypto.decrypt(token) == "geheim-bearer-xyz"


@override_settings(MCN_CRED_KEY="")
def test_ohne_schluessel_fail_closed():
    with pytest.raises(cred_crypto.CredKeyError):
        cred_crypto.encrypt("x")


@override_settings(MCN_CRED_KEY="kein-gueltiger-fernet-key")
def test_ungueltiger_schluessel():
    with pytest.raises(cred_crypto.CredKeyError):
        cred_crypto.encrypt("x")


def test_falscher_schluessel_entschluesselt_nicht():
    with override_settings(MCN_CRED_KEY=KEY):
        token = cred_crypto.encrypt("geheim")
    anderer = Fernet.generate_key().decode()
    with override_settings(MCN_CRED_KEY=anderer):
        with pytest.raises(cred_crypto.CredKeyError):
            cred_crypto.decrypt(token)


def test_kein_secret_in_fehlermeldung():
    with override_settings(MCN_CRED_KEY=KEY):
        token = cred_crypto.encrypt("supergeheim")
    anderer = Fernet.generate_key().decode()
    with override_settings(MCN_CRED_KEY=anderer):
        with pytest.raises(cred_crypto.CredKeyError) as ei:
            cred_crypto.decrypt(token)
    msg = str(ei.value)
    assert "supergeheim" not in msg
    assert anderer not in msg and KEY not in msg
