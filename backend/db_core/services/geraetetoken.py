"""Geräte-Token (Bearer) — Ausstellung, Auflösung, Widerruf.

Neben der Session-Cookie-Auth des Web-Cockpits meldet sich die native
Android-App (Projekt MCN-APP) mit einem Bearer-Token an. Sicherheitsleitplanken:

* Das Klartext-Token verlässt den Server AUSSCHLIESSLICH einmalig in der
  Login-Antwort. In der DB liegt nur der SHA-256-Hex-Hash; aus ihm lässt sich das
  Token nicht zurückrechnen. Das Token wird nirgends geloggt.
* Widerruf über `revoked_at` (stilllegen statt löschen — die Tabelle trägt den
  No-Delete-Schutz). `token_aufloesen` liefert widerrufene Token nicht mehr.
* `last_used_at` wird bewusst NICHT bei jedem Request fortgeschrieben: Jede
  fachliche Schreiboperation läuft durch `business_transaction` und erzeugt einen
  Audit-Eintrag — eine Schreibtransaktion je Lese-Request wäre eine Audit-Flut
  und ein Perf-Problem. Die Spalte existiert für eine spätere, grob gedrosselte
  Nutzungsanzeige; in diesem Slice bleibt sie NULL (die einfache, korrekte
  Variante).

OFFEN (bewusst nicht in diesem Slice): Rate-Limiting/Brute-Force-Schutz für den
Login (`api.auth.device_login`). Bis dahin schützt allein die Passwort-Policy.
"""
import hashlib
import secrets
import uuid

from django.db import transaction
from django.utils import timezone

from db_core.db_context import business_transaction
from db_core.models import DeviceToken


def _hash(klartext: str) -> str:
    """SHA-256-Hex des Klartext-Tokens. Deterministisch (für den Lookup) und
    nicht umkehrbar. Kein Salt nötig: das Token ist selbst hochentropisch
    (`secrets.token_urlsafe(32)` → 256 Bit)."""
    return hashlib.sha256(klartext.encode("utf-8")).hexdigest()


def token_ausstellen(user, device_name: str | None) -> str:
    """Stellt ein neues Geräte-Token für `user` aus und gibt das KLARTEXT-Token
    zurück (nur hier, nie wieder). Gespeichert wird ausschließlich der Hash.

    Der Insert läuft über `business_transaction` mit der eigenen `app_user_id` des
    Nutzers (Akteur = der sich anmeldende Nutzer). Konten OHNE `app_user_id`
    können keine `business_transaction` fahren (sie verlangt die UUID); für sie
    greift ein einfacher atomarer Insert — der dokumentierte Bootstrapping-Pfad
    wie in seed_demo. Das ist unbedenklich: Der INSERT ist nicht auditiert (der
    Audit-Trigger feuert nur AFTER UPDATE), und ein Konto ohne `app_user_id` hat
    ohnehin keine Fachrechte.
    """
    klartext = secrets.token_urlsafe(32)
    name = (device_name or "").strip() or None

    def _insert():
        DeviceToken.objects.create(
            id=uuid.uuid4(),
            user_id=user.pk,
            app_user_id=user.app_user_id,
            token_hash=_hash(klartext),
            device_name=name,
        )

    if user.app_user_id:
        with business_transaction(user.app_user_id):
            _insert()
    else:
        with transaction.atomic():
            _insert()
    return klartext


def token_aufloesen(klartext: str) -> DeviceToken | None:
    """Löst ein präsentiertes Klartext-Token zu seinem nicht-widerrufenen
    `DeviceToken` auf (oder None). Hasht und sucht — das Klartext-Token wird nie
    gespeichert oder geloggt."""
    if not klartext:
        return None
    return (
        DeviceToken.objects
        .filter(token_hash=_hash(klartext), revoked_at__isnull=True)
        .first()
    )


def token_widerrufen(device_token: DeviceToken) -> None:
    """Widerruft ein Token (setzt `revoked_at`). Idempotent: ein bereits
    widerrufenes Token bleibt unverändert.

    Der UPDATE feuert den Audit-Trigger; deshalb läuft er über
    `business_transaction` mit der `app_user_id` des Token-Kontos (Akteur). Konten
    ohne `app_user_id` widerrufen über einen einfachen atomaren UPDATE (Audit
    protokolliert dann SYSTEM) — derselbe Bootstrapping-Pfad wie bei der
    Ausstellung.
    """
    if device_token.revoked_at is not None:
        return
    device_token.revoked_at = timezone.now()

    def _save():
        device_token.save(update_fields=["revoked_at"])

    if device_token.app_user_id:
        with business_transaction(device_token.app_user_id):
            _save()
    else:
        with transaction.atomic():
            _save()
