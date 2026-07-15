"""Runtime — führt EINEN geclaimten tool_call aus (Dispatch + Ergebnis).

Die Queue-Mechanik (Claiming per SELECT … FOR UPDATE SKIP LOCKED, Lease, Reaper,
Scheduler-Tick) liegt in der nächsten Schicht; hier ist die reine Ausführung eines
Aufrufs: Envelope bauen → ans Gerät dispatchen → Ergebnis verbuchen. Diese Schicht
bedient nur **externe Wahrnehmungs-Werkzeuge** (ASR/VISION/OCR); LLM/DOMAIN_QUERY
sind INTERNAL und laufen über den Adapter bzw. die Lese-Services, nicht hier.

PII-Grenze (Vertrag Rev 2/3):
- Die Eingabe (Audio/Bild/Dokument) liegt im **löschbaren** `content.file` und wird
  NUR zur Dispatch-Zeit geladen und ans passive Gerät **gepusht** (das Gerät kann
  nicht ziehen) — NIE in `tool_call` persistiert.
- Der erzeugte Text (Transkript = personenbezogen) landet im **löschbaren**
  `content_item`; `tool_call` hält nur einen Verweis (`content_item_id`) + Hash.
- `is_untrusted` leitet der SERVER aus der Capability ab, nie vom Gerät.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import random
import uuid
from datetime import timedelta

from django.utils import timezone

from db_core import storage as storage_module
from db_core.ai import registry
from db_core.ai.tool_client import PERMANENT, ToolClient, ToolError
from db_core.db_context import business_transaction
from db_core.models import ContentItem, File, ToolCall
from db_core.storage import StorageError

logger = logging.getLogger(__name__)

# Wahrnehmung: Ausgabe ist DATEN (untrusted). LLM/DOMAIN_QUERY wären trusted.
_UNTRUSTED = frozenset({"ASR", "VISION", "OCR"})
# Capability → ai.content_item.source_type
_SOURCE_TYPE = {"ASR": "PROTOKOLL", "VISION": "FOTO_BESCHREIBUNG", "OCR": "PDF"}


def is_untrusted_for(capability: str) -> bool:
    return capability in _UNTRUSTED


def run_tool_call(tool_call_id, *, actor_id, client=None, storage=None) -> str:
    """Führt einen bereits geclaimten (RUNNING) tool_call aus. Gibt den End-/
    Zwischenstatus zurück. Der Dispatch läuft OHNE gehaltene DB-Transaktion (das
    Gerät kann langsam sein); nur das Verbuchen ist transaktional."""
    call = ToolCall.objects.select_related("tool").get(id=tool_call_id)
    if call.status != "RUNNING":
        return call.status
    tool = call.tool

    if call.deadline_at and timezone.now() > call.deadline_at:
        _to_terminal(actor_id, call, "EXPIRED", "TIMEOUT", "Deadline überschritten.")
        return "EXPIRED"

    # Aufbau-Fehler differenziert klassifizieren: eine fehlende Datei-ZEILE ist
    # permanent, ein wegge­brochener Objektspeicher oder ein Schlüssel-Rotations­
    # fenster (MCN_CRED_KEY) ist TRANSIENT — sonst dead-lettert ein kurzer Infra-
    # Aussetzer alle laufenden Calls.
    try:
        envelope = _build_envelope(call, storage or storage_module.get_storage())
        bearer = registry.get_bearer(tool.id)
    except File.DoesNotExist:
        return _handle_failure(actor_id, call, tool, ToolError("BAD_INPUT", "Quell-Datei fehlt."))
    except StorageError:
        return _handle_failure(actor_id, call, tool, ToolError("UNREACHABLE", "Objektspeicher nicht erreichbar."))
    except ValueError:
        # get_bearer wirft ValueError bei fehlendem/rotiertem MCN_CRED_KEY — transient.
        return _handle_failure(actor_id, call, tool, ToolError("UNREACHABLE", "Bearer/Schlüssel nicht verfügbar."))
    except Exception:
        # Unerwartet (Code-Defekt): server-seitig loggen (secret-frei), Call permanent
        # scheitern lassen — nicht endlos retryen, aber die Ursache nicht verschlucken.
        logger.exception("runtime: unerwarteter Fehler beim Dispatch-Aufbau (tool_call %s)", call.id)
        return _handle_failure(actor_id, call, tool, ToolError("BAD_INPUT", "Dispatch-Aufbau fehlgeschlagen."))

    try:
        result = (client or ToolClient()).dispatch(tool, envelope, bearer=bearer)
    except ToolError as exc:
        return _handle_failure(actor_id, call, tool, exc)

    if result.status == "pending":
        if not result.job_id:
            # pending ohne Job-Kennung würde den Call sonst nur über die Deadline
            # terminieren (die NULL sein darf) — als transient behandeln.
            return _handle_failure(actor_id, call, tool, ToolError("TOOL_ERROR", "Gerät meldete pending ohne Job-Kennung."))
        return _mark_pending(actor_id, call, tool, result.job_id)
    if result.status == "error":
        # Der (untrusted) Geräte-Code darf nur ESKALIEREN (permanent → schneller
        # scheitern), nie eine eigentlich permanente Lage als transient tarnen.
        code = result.error_code if result.error_code in PERMANENT else "TOOL_ERROR"
        return _handle_failure(actor_id, call, tool, ToolError(code, "Gerät meldete einen Fehler."))
    return _finish_success(actor_id, call, tool, result)


def _build_envelope(call, storage) -> dict:
    """Baut das Envelope. Lädt die Eingabe-Datei (falls referenziert) und pusht sie
    base64-kodiert mit — das Ergebnis ist EPHEMER (nie in tool_call gespeichert)."""
    ref = dict(call.input_ref or {})
    file_id = ref.pop("file_id", None)
    payload = {
        "contract_version": call.contract_version,
        "tool_key": call.tool.tool_key,
        "capability": call.capability,
        "correlation_id": str(call.id),
        "idempotency_key": f"{call.workflow_run_id}:{call.step_key}",
        "input": ref,
    }
    if file_id:
        f = File.objects.get(id=file_id)
        raw = storage.get_object(f.storage_key)
        payload["input"]["file_b64"] = base64.b64encode(raw).decode("ascii")
        payload["input"]["mime_type"] = f.mime_type
    if call.deadline_at:
        payload["deadline_ts"] = call.deadline_at.isoformat()
    return payload


def _finish_success(actor_id, call, tool, result) -> str:
    """Erfolg: den erzeugten Text ins löschbare content_item schreiben (mit der
    Eingabe-Datei als Quelle), tool_call auf SUCCEEDED mit NUR einem Verweis."""
    text = (result.output or {}).get("text")
    if not text or call.capability not in _SOURCE_TYPE:
        # Diese Runtime bedient nur textliefernde Wahrnehmung; alles andere ist ein
        # Aufruf-Fehler (permanentes Vertrags-/Konfigproblem).
        return _handle_failure(actor_id, call, tool, ToolError("BAD_INPUT", "Kein verwertbarer Text im Ergebnis."))
    file_id = (call.input_ref or {}).get("file_id")
    if not file_id:
        return _handle_failure(actor_id, call, tool, ToolError("BAD_INPUT", "Quell-Datei fehlt für den erzeugten Text."))
    try:
        file_uuid = uuid.UUID(str(file_id))
    except ValueError:
        return _handle_failure(actor_id, call, tool, ToolError("BAD_INPUT", "Ungültige Quell-Datei-Referenz."))
    output_hash = result.content_hash or hashlib.sha256(text.encode("utf-8")).hexdigest()
    with business_transaction(actor_id):
        item = ContentItem.objects.create(
            id=uuid.uuid4(),
            source_type=_SOURCE_TYPE[call.capability],
            file_id=file_uuid,
            extracted_text=text,
            content_hash=output_hash,
            is_untrusted=is_untrusted_for(call.capability),
            source_tool_call_id=call.id,
        )
        call.status = "SUCCEEDED"
        call.output_ref = {"content_item_id": str(item.id)}
        call.output_hash = output_hash
        call.metrics = result.metrics
        call.save(update_fields=["status", "output_ref", "output_hash", "metrics"])
    return "SUCCEEDED"


def _mark_pending(actor_id, call, tool, job_id) -> str:
    """Gerät arbeitet noch: job_id merken, Lease erneuern; bleibt RUNNING (der Tick
    pollt später)."""
    with business_transaction(actor_id):
        call.output_ref = {"job_id": job_id}
        call.leased_until = timezone.now() + timedelta(seconds=tool.timeout_seconds)
        call.save(update_fields=["output_ref", "leased_until"])
    return "RUNNING"


def _handle_failure(actor_id, call, tool, err: ToolError) -> str:
    """Transienter Fehler → erneut einreihen mit Backoff (Fence via leased_until),
    solange Versuche übrig sind; sonst FAILED. Permanenter Fehler → sofort FAILED.
    Nur MCN-eigene, secret-freie Fehlermeldungen werden gespeichert."""
    will_retry = err.transient and (call.attempt + 1) < tool.max_attempts
    with business_transaction(actor_id):
        if will_retry:
            call.status = "QUEUED"
            call.attempt = call.attempt + 1
            backoff = float(tool.backoff_seconds) * (2 ** call.attempt)
            backoff += random.uniform(0, float(tool.backoff_seconds))   # Jitter gegen Thundering Herd
            call.leased_until = timezone.now() + timedelta(seconds=backoff)
            call.error_code = err.code
            call.error_message = str(err)[:500]
            call.save(update_fields=["status", "attempt", "leased_until", "error_code", "error_message"])
            return "QUEUED"
        call.status = "FAILED"
        call.error_code = err.code
        call.error_message = str(err)[:500]
        call.save(update_fields=["status", "error_code", "error_message"])
    return "FAILED"


def _to_terminal(actor_id, call, status, error_code, error_message) -> None:
    with business_transaction(actor_id):
        call.status = status
        call.error_code = error_code
        call.error_message = error_message
        call.save(update_fields=["status", "error_code", "error_message"])
