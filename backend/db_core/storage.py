"""Objektspeicher-Adapter (MinIO/S3) — schlanke Kapselung für Beleg-PDFs.

Binärinhalte (Beleg-PDFs, später Fotos/Scans) liegen im Object Storage; die
Datenbank hält nur den unveränderlichen Steckbrief (`content.file`:
storage_key, sha256, size_bytes). Diese Datei kapselt genau die Operationen,
die die GoBD-Archivierung braucht:

    put_object(key, data, content_type) -> ObjectInfo(sha256, size)
    get_object(key) -> bytes
    presigned_get_url(key, expires) -> str   (optional, read-only)

Gewählte Abhängigkeit: **minio** (offizielles MinIO-Python-SDK von PyPI).
Begründung: Ziel ist ausdrücklich MinIO; das SDK ist zweckgebunden, deutlich
schlanker als boto3/botocore (keine AWS-Service-Modelle), bringt presigned
URLs und Multipart out of the box mit und hält den Adapter klein. boto3 wäre
nur nötig, wenn wir gegen echtes AWS S3 mit dessen Feature-Breite müssten.

Fehlerverhalten — **kein stiller Fehlschlag**: Ist der Speicher nicht
erreichbar oder die Authentifizierung falsch, wirft jede Operation eine klare
``StorageError``. Der Aufrufer entscheidet über Degradation (der PDF-Endpunkt
liefert dann on-the-fly weiter, siehe services/beleg_pdf.py); der Adapter selbst
schluckt nichts.

Secrets werden niemals geloggt. Konfiguration ausschließlich über die
``MCN_MINIO_*``-Settings (aus der Umgebung, Muster wie MCN_DB_*).
"""
import io
import threading
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256 as _sha256
from urllib.parse import urlparse

from django.conf import settings

try:  # Das SDK ist eine harte Dependency (pyproject); der Guard dient nur einer
    from minio import Minio  # klaren Fehlermeldung, falls die Umgebung unsauber ist.
    from minio.error import S3Error
except ImportError as exc:  # pragma: no cover - Umgebungsfehler
    raise ImportError(
        "Das Paket 'minio' fehlt (uv sync). Es kapselt den Objektspeicher-Zugriff."
    ) from exc

# urllib3-Fehler (DNS/Connection/Timeout) leben unter dem SDK; wir fangen sie
# breit, um sie in StorageError zu übersetzen (kein Leck von Transport-Details).
try:  # pragma: no cover - Import-Robustheit
    from urllib3.exceptions import HTTPError as _URLLib3Error
except ImportError:  # pragma: no cover
    _URLLib3Error = ()


class StorageError(RuntimeError):
    """Objektspeicher nicht erreichbar/authentifizierbar oder Operation gescheitert.

    Bewusst eine eigene Klasse: der Aufrufer kann Degradation von echten
    Programmierfehlern unterscheiden, ohne SDK-interne Ausnahmen zu kennen.
    """


@dataclass(frozen=True)
class ObjectInfo:
    """Ergebnis eines put_object: Prüfsumme und Größe der abgelegten Bytes."""

    storage_key: str
    sha256: str
    size_bytes: int


class ObjectStorage:
    """Dünne Hülle um einen ``minio.Minio``-Client + festen Bucket.

    Der Client selbst öffnet keine Verbindung; erst eine Operation spricht das
    Netz an. ``ensure_bucket`` wird lazy einmal je Instanz ausgeführt.
    """

    def __init__(self, client, bucket):
        self._client = client
        self._bucket = bucket
        self._bucket_checked = False
        self._lock = threading.Lock()

    @property
    def bucket(self):
        return self._bucket

    # -- interne Helfer ------------------------------------------------------
    def _wrap(self, action, exc):
        # Transport-/S3-Details nicht nach außen lecken, aber die Ursache nennen.
        detail = getattr(exc, "code", None) or type(exc).__name__
        return StorageError(f"Objektspeicher: {action} fehlgeschlagen ({detail}).")

    def ensure_bucket(self):
        """Legt den Bucket an, falls er fehlt (idempotent). Netzoperation."""
        if self._bucket_checked:
            return
        with self._lock:
            if self._bucket_checked:
                return
            try:
                if not self._client.bucket_exists(self._bucket):
                    self._client.make_bucket(self._bucket)
                self._bucket_checked = True
            except (S3Error, _URLLib3Error, OSError, ValueError) as exc:
                raise self._wrap("Bucket-Prüfung/-Anlage", exc) from exc

    # -- öffentliche Operationen --------------------------------------------
    def put_object(self, key, data, content_type="application/octet-stream"):
        """Legt ``data`` (bytes) unter ``key`` ab und gibt ObjectInfo zurück.

        Berechnet sha256 und Größe aus genau den gesendeten Bytes — dieselben
        Werte, die als content.file.sha256/size_bytes registriert werden.
        """
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("put_object erwartet bytes.")
        payload = bytes(data)
        digest = _sha256(payload).hexdigest()
        size = len(payload)
        self.ensure_bucket()
        try:
            self._client.put_object(
                self._bucket,
                key,
                io.BytesIO(payload),
                length=size,
                content_type=content_type,
            )
        except (S3Error, _URLLib3Error, OSError, ValueError) as exc:
            raise self._wrap("put_object", exc) from exc
        return ObjectInfo(storage_key=key, sha256=digest, size_bytes=size)

    def get_object(self, key):
        """Lädt das Objekt ``key`` und gibt seine Bytes zurück."""
        response = None
        try:
            response = self._client.get_object(self._bucket, key)
            return response.read()
        except (S3Error, _URLLib3Error, OSError, ValueError) as exc:
            raise self._wrap("get_object", exc) from exc
        finally:
            if response is not None:
                response.close()
                response.release_conn()

    def remove_object(self, key):
        """Löscht ein Objekt (Best-Effort-Cleanup für verwaiste Wettlauf-Uploads).

        Objekt-Löschung ist erlaubt (der DB-Steckbrief ist unveränderlich, das
        Objekt selbst nicht) — hier nur für Uploads OHNE registrierten
        content.file-Eintrag."""
        try:
            self._client.remove_object(self._bucket, key)
        except (S3Error, _URLLib3Error, OSError, ValueError) as exc:
            raise self._wrap("remove_object", exc) from exc

    def presigned_get_url(self, key, expires=timedelta(minutes=15)):
        """Zeitlich begrenzte Lese-URL (optional; z. B. für direktes Ausliefern)."""
        try:
            return self._client.presigned_get_object(
                self._bucket, key, expires=expires
            )
        except (S3Error, _URLLib3Error, OSError, ValueError) as exc:
            raise self._wrap("presigned_get_url", exc) from exc


def _endpoint_to_host(endpoint):
    """Zerlegt ``http(s)://host:port`` in (host:port, secure). Fehlt das Schema,
    wird http angenommen (lokale Entwicklung)."""
    parsed = urlparse(endpoint if "//" in endpoint else f"http://{endpoint}")
    host = parsed.netloc or parsed.path
    if not host:
        raise StorageError(f"MCN_MINIO_ENDPOINT ungültig: {endpoint!r}.")
    return host, (parsed.scheme == "https")


# Der Client wird je Prozess einmal gebaut (Konfiguration ist Prozess-stabil).
# Kein Verbindungsaufbau beim Bauen — erst Operationen sprechen das Netz an.
_singleton = None
_singleton_lock = threading.Lock()


def _build_storage():
    host, secure = _endpoint_to_host(settings.MCN_MINIO_ENDPOINT)
    access = settings.MCN_MINIO_ACCESS_KEY
    secret = settings.MCN_MINIO_SECRET_KEY
    if not access or not secret:
        raise StorageError(
            "MinIO-Zugangsdaten fehlen (MCN_MINIO_ACCESS_KEY/-SECRET_KEY)."
        )
    client = Minio(
        host,
        access_key=access,
        secret_key=secret,
        secure=secure,
        region=getattr(settings, "MCN_MINIO_REGION", None) or None,
    )
    return ObjectStorage(client, settings.MCN_MINIO_BUCKET)


def get_storage():
    """Gibt den prozessweiten ObjectStorage zurück (baut ihn beim ersten Aufruf).

    Wirft StorageError nur bei fehlerhafter Konfiguration; ein nicht erreichbarer
    Server fällt erst bei der ersten Operation auf.
    """
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = _build_storage()
    return _singleton


def reset_storage():
    """Verwirft den zwischengespeicherten Client (Tests / geänderte Settings)."""
    global _singleton
    with _singleton_lock:
        _singleton = None


def probe():
    """Prüft Erreichbarkeit + Authentifizierung. Gibt (ok, reason) zurück.

    Für Test-Skips und Health-Checks: fängt jeden Fehler ab und meldet ihn als
    Text (ohne Secrets). ``ok=True`` heißt: Bucket ist erreichbar/anlegbar.
    """
    try:
        storage = get_storage()
        storage.ensure_bucket()
        return True, "ok"
    except StorageError as exc:
        return False, str(exc)
    except Exception as exc:  # pragma: no cover - Defensive
        return False, f"{type(exc).__name__}: {exc}"
