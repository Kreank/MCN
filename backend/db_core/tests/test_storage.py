"""Unit-Tests für den Objektspeicher-Adapter (db_core/storage.py) ohne Netz.

Prüft Endpoint-Parsing und das Fehlerverhalten bei fehlerhafter Konfiguration
(klare StorageError, kein stiller Fehlschlag). Der echte MinIO-Round-Trip steht
in test_storage_minio_e2e.py.
"""
import pytest
from django.test import override_settings

from db_core import storage


def test_endpoint_parsing_http():
    host, secure = storage._endpoint_to_host("http://127.0.0.1:9100")
    assert host == "127.0.0.1:9100"
    assert secure is False


def test_endpoint_parsing_https():
    host, secure = storage._endpoint_to_host("https://minio.example:9000")
    assert host == "minio.example:9000"
    assert secure is True


def test_endpoint_parsing_ohne_schema():
    # Fehlt das Schema, wird http angenommen (lokale Entwicklung).
    host, secure = storage._endpoint_to_host("localhost:9100")
    assert host == "localhost:9100"
    assert secure is False


def test_endpoint_leer_wirft():
    with pytest.raises(storage.StorageError):
        storage._endpoint_to_host("")


def test_fehlende_zugangsdaten_wirft():
    storage.reset_storage()
    with override_settings(MCN_MINIO_ACCESS_KEY="", MCN_MINIO_SECRET_KEY=""):
        with pytest.raises(storage.StorageError):
            storage._build_storage()
    storage.reset_storage()


def test_probe_meldet_fehler_statt_werfen():
    """Ein nicht erreichbarer/falsch konfigurierter Speicher liefert (False, grund),
    nicht eine Ausnahme — für Test-Skips und Health-Checks."""
    storage.reset_storage()
    with override_settings(MCN_MINIO_ENDPOINT="http://127.0.0.1:9",
                           MCN_MINIO_ACCESS_KEY="x", MCN_MINIO_SECRET_KEY="y"):
        ok, reason = storage.probe()
    storage.reset_storage()
    assert ok is False
    assert isinstance(reason, str) and reason
