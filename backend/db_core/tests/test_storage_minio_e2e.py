"""End-to-End gegen ein echtes MinIO (db_core/storage.py).

Wird sauber übersprungen, wenn kein Objektspeicher erreichbar/authentifizierbar
ist (``storage.probe()``), damit die Suite auf einem Rechner ohne MinIO nicht rot
wird. Läuft der Container mit gültigen ``MCN_MINIO_*``-Zugangsdaten, wird ein
echter Round-Trip geprüft: put → get → sha256/Größe, Entfernen.
"""
import uuid

import pytest

from db_core import storage

_ok, _reason = storage.probe()
pytestmark = pytest.mark.skipif(
    not _ok, reason=f"MinIO nicht erreichbar/authentifizierbar: {_reason}"
)


def test_round_trip():
    st = storage.get_storage()
    key = f"tests/{uuid.uuid4()}.bin"
    payload = b"%PDF-1.7 mcn-e2e-" + uuid.uuid4().bytes
    try:
        info = st.put_object(key, payload, content_type="application/pdf")
        assert info.size_bytes == len(payload)
        assert st.get_object(key) == payload
        from hashlib import sha256
        assert info.sha256 == sha256(payload).hexdigest()
        # presigned URL ist eine gültige http(s)-URL auf den Key.
        url = st.presigned_get_url(key)
        assert url.startswith("http")
    finally:
        st.remove_object(key)
