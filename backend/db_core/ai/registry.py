"""KI-Werkzeug-Registry — Werkzeuge anlegen und ihr Geräte-Bearer (at rest) pflegen.

Werkzeuge sind Konfiguration, nicht Code. Das Bearer, mit dem MCN das passive Gerät
authentifiziert, liegt Fernet-verschlüsselt in `ai.tool.bearer_encrypted`
(`cred_crypto`/`MCN_CRED_KEY`, isoliert vom Mailversand). Der Klartext verlässt diese
Schicht nur zum Dispatch-Zeitpunkt (`get_bearer`), wird nie geloggt und nie in einer
Statusantwort ausgegeben.
"""
import uuid

from django.db import IntegrityError

from db_core import cred_crypto
from db_core.db_context import business_transaction
from db_core.models import Tool


def register_tool(
    actor_app_user_id,
    *,
    tool_key,
    label,
    capability,
    invocation_mode,
    endpoint_url=None,
    data_boundary="LOCAL_ONLY",
    timeout_seconds=120,
    max_attempts=3,
):
    """Legt ein Werkzeug in der Registry an (eindeutiger tool_key)."""
    if Tool.objects.filter(tool_key=tool_key).exists():
        raise ValueError(f"Werkzeug '{tool_key}' existiert bereits.")
    try:
        with business_transaction(actor_app_user_id):
            tool = Tool.objects.create(
                id=uuid.uuid4(),
                tool_key=tool_key,
                label=label,
                capability=capability,
                invocation_mode=invocation_mode,
                endpoint_url=endpoint_url,
                data_boundary=data_boundary,
                timeout_seconds=timeout_seconds,
                max_attempts=max_attempts,
            )
    except IntegrityError:
        # Nebenläufige zweite Registrierung desselben tool_key: die DB-UNIQUE hält,
        # der Fehler wird zum sauberen 422 statt eines 500.
        raise ValueError(f"Werkzeug '{tool_key}' existiert bereits.") from None
    return tool


def set_bearer(actor_app_user_id, *, tool_id, bearer):
    """Setzt (Fernet-verschlüsselt) oder löscht ("" / None) das Geräte-Bearer.

    Gibt den Status OHNE das Secret zurück. Fehlt der Schlüssel, ist das fail-closed
    ein ValueError (aus `CredKeyError`, secret-frei).
    """
    tool = Tool.objects.filter(id=tool_id).first()
    if tool is None:
        raise ValueError("Werkzeug nicht gefunden.")
    cipher = None
    if bearer:
        try:
            cipher = cred_crypto.encrypt(bearer)
        except cred_crypto.CredKeyError as exc:
            raise ValueError(str(exc)) from exc
    with business_transaction(actor_app_user_id):
        tool.bearer_encrypted = cipher
        tool.save(update_fields=["bearer_encrypted"])
    return {"tool_id": str(tool.id), "has_bearer": cipher is not None}


def get_bearer(tool_id):
    """Entschlüsselt das Bearer für den Dispatch. None, wenn keins hinterlegt ist."""
    tool = Tool.objects.filter(id=tool_id).first()
    if tool is None or tool.bearer_encrypted is None:
        return None
    try:
        return cred_crypto.decrypt(tool.bearer_encrypted)
    except cred_crypto.CredKeyError as exc:
        raise ValueError(str(exc)) from exc
