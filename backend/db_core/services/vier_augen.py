"""Vier-Augen-Service: Antrag/Freigabe über security.approval_request (0028).

Setzt das Vier-Augen-Prinzip in der App-Schicht durch — physisch abgesichert
durch den CHECK `decided_by <> requested_by` in der Datenbank. Der Service prüft
dieselbe Regel zusätzlich, damit ein Selbst-Freigabe-Versuch als klarer 422 statt
als IntegrityError beim Aufrufer landet.

Zwei Anwendungsmuster, beide über dieselbe Tabelle:

  * **Anwenden-beim-Genehmigen** (Applier): Die beantragte Änderung liegt
    vollständig im `payload` und wird erst durch die Genehmigung geschrieben —
    z. B. BANKDATEN (neue IBAN/BIC). `approve()` wendet sie im selben
    Transaktions-Zug an und markiert die Genehmigung als verbraucht (`applied_at`).

  * **Torfunktion** (`assert_approved`): Für Aktionen, deren Durchführung ein
    eigener, komplexer Ablauf ist (z. B. RECHNUNGSKORREKTUR über Positionen).
    Der ausführende Endpunkt fordert vor der Ausführung eine gültige, noch nicht
    verbrauchte Genehmigung an und ruft nach Erfolg `consume()`.

`applied_at` sichert die Einmaligkeit: eine Genehmigung wirkt genau einmal.
"""
import uuid

from django.utils import timezone

from db_core.db_context import business_transaction
from db_core.gate_errors import as_business_error
from db_core.models import ApprovalRequest, CompanyProfile, FourEyesAction

# Statusautomat (spiegelt den DB-Trigger security.enforce_approval_status).
_TERMINAL = {"GENEHMIGT", "ABGELEHNT", "ZURUECKGEZOGEN"}


class NotApproved(ValueError):
    """Es liegt keine gültige, unverbrauchte Genehmigung für die Aktion vor."""


# --- Applier-Registry ------------------------------------------------------

def _apply_bankdaten(req):
    """Schreibt die genehmigten Bankdaten ins Firmenprofil (läuft INNERHALB der
    bereits offenen business_transaction von approve())."""
    payload = req.payload or {}
    fields = {k: payload.get(k) for k in ("bank_name", "iban", "bic") if k in payload}
    if not fields:
        return
    profile = CompanyProfile.objects.filter(id=req.target_id).first()
    if profile is None:
        raise ValueError(
            "Firmenprofil nicht gefunden; die genehmigten Bankdaten können nicht "
            "angewandt werden."
        )
    for key, val in fields.items():
        setattr(profile, key, val)
    profile.save(update_fields=list(fields) + ["updated_at"])


# action_code -> Applier. Aktionen OHNE Eintrag sind Tor-Aktionen: ihre
# Durchführung erfolgt separat über assert_approved()/consume().
_APPLIERS = {
    "BANKDATEN": _apply_bankdaten,
}


# --- Antrag stellen --------------------------------------------------------

def request_approval(actor_app_user_id, *, action_code, payload=None,
                     target_table=None, target_id=None, reason=None):
    """Legt einen Freigabeantrag (Status ANGEFORDERT) an.

    Prüft, dass action_code eine bekannte, aktive Vier-Augen-Aktion ist. Die
    Zielreferenz ist entweder vollständig (Tabelle + id) oder gar nicht gesetzt.
    """
    action = FourEyesAction.objects.filter(action_code=action_code).first()
    if action is None:
        raise ValueError(f"Unbekannte Vier-Augen-Aktion: {action_code}")
    if not action.active:
        raise ValueError(f"Vier-Augen-Aktion {action_code} ist nicht aktiv.")
    if (target_table is None) != (target_id is None):
        raise ValueError("Zielreferenz muss Tabelle UND id enthalten oder ganz fehlen.")

    with business_transaction(actor_app_user_id):
        req = ApprovalRequest.objects.create(
            id=uuid.uuid4(),
            action_id=action_code,
            status="ANGEFORDERT",
            payload=payload or {},
            target_table=target_table,
            target_id=target_id,
            reason=reason,
            requested_by_id=actor_app_user_id,
        )
    req.refresh_from_db()
    return req


def find_pending(action_code, *, target_table=None, target_id=None):
    """Offener (ANGEFORDERT) Antrag zu Aktion + Ziel, sonst None (Dedupe-Helfer)."""
    return (
        ApprovalRequest.objects.filter(
            action_id=action_code, status="ANGEFORDERT",
            target_table=target_table, target_id=target_id,
        )
        .order_by("requested_at")
        .first()
    )


# --- Entscheiden -----------------------------------------------------------

def _load_open(request_id):
    req = ApprovalRequest.objects.filter(id=request_id).first()
    if req is None:
        raise ValueError("Freigabeantrag nicht gefunden.")
    if req.status != "ANGEFORDERT":
        raise ValueError(
            f"Freigabeantrag ist bereits entschieden (Status {req.status})."
        )
    return req


def approve(actor_app_user_id, *, request_id):
    """Genehmigt einen Antrag. Der Entscheider MUSS ein anderer sein als der
    Antragsteller (Vier-Augen-Prinzip; DB-CHECK + hier als klarer 422).

    Ist für die Aktion ein Applier registriert (z. B. BANKDATEN), wird die
    beantragte Änderung im selben Transaktions-Zug angewandt und der Antrag als
    verbraucht markiert.
    """
    req = _load_open(request_id)
    if str(actor_app_user_id) == str(req.requested_by_id):
        raise ValueError(
            "Vier-Augen-Prinzip: Wer einen Antrag stellt, darf ihn nicht selbst "
            "genehmigen."
        )
    applier = _APPLIERS.get(req.action_id)
    now = timezone.now()
    with as_business_error():
        with business_transaction(actor_app_user_id):
            # Auf ANGEFORDERT filtern: der DB-Trigger lässt GENEHMIGT→GENEHMIGT
            # als No-Op durch, sodass ein zweiter, nebenläufiger Genehmiger sonst
            # `decided_by` überschriebe und den Applier ein zweites Mal ausführte.
            updated = ApprovalRequest.objects.filter(
                id=request_id, status="ANGEFORDERT"
            ).update(
                status="GENEHMIGT",
                decided_by_id=actor_app_user_id,
                decided_at=now,
                applied_at=now if applier else None,
            )
            if not updated:
                raise ValueError(
                    "Freigabeantrag wurde zwischenzeitlich entschieden."
                )
            if applier:
                applier(req)
    req.refresh_from_db()
    return req


def reject(actor_app_user_id, *, request_id, note):
    """Lehnt einen Antrag ab — begründungspflichtig (DB-CHECK). Auch die Ablehnung
    darf nicht durch den Antragsteller selbst erfolgen (Vier-Augen-Prinzip)."""
    if not (note or "").strip():
        raise ValueError("Eine Ablehnung ist begründungspflichtig.")
    req = _load_open(request_id)
    if str(actor_app_user_id) == str(req.requested_by_id):
        raise ValueError(
            "Vier-Augen-Prinzip: Wer einen Antrag stellt, darf ihn nicht selbst "
            "entscheiden."
        )
    now = timezone.now()
    with as_business_error():
        with business_transaction(actor_app_user_id):
            updated = ApprovalRequest.objects.filter(
                id=request_id, status="ANGEFORDERT"
            ).update(
                status="ABGELEHNT",
                decided_by_id=actor_app_user_id,
                decided_at=now,
                decision_note=note.strip(),
            )
            if not updated:
                raise ValueError(
                    "Freigabeantrag wurde zwischenzeitlich entschieden."
                )
    req.refresh_from_db()
    return req


def withdraw(actor_app_user_id, *, request_id):
    """Zieht den EIGENEN offenen Antrag zurück (nur der Antragsteller)."""
    req = _load_open(request_id)
    if str(actor_app_user_id) != str(req.requested_by_id):
        raise ValueError("Nur der Antragsteller kann den Antrag zurückziehen.")
    with as_business_error():
        with business_transaction(actor_app_user_id):
            updated = ApprovalRequest.objects.filter(
                id=request_id, status="ANGEFORDERT"
            ).update(status="ZURUECKGEZOGEN")
            if not updated:
                raise ValueError(
                    "Freigabeantrag wurde zwischenzeitlich entschieden."
                )
    req.refresh_from_db()
    return req


# --- Torfunktion für Aufrufer ---------------------------------------------

def find_grant(action_code, *, target_table=None, target_id=None, payload=None):
    """Eine erteilte, noch nicht verbrauchte Genehmigung zu Aktion + Ziel, sonst
    None.

    `payload` bindet die Genehmigung an die **konkrete** beantragte Änderung:
    Genehmigt wurde nicht „irgendetwas an dieser Rechnung", sondern genau der
    Vorgang, den der Entscheider gesehen hat. Ohne diese Bindung ließe sich eine
    genehmigte Teilgutschrift als Vollstorno einlösen.
    """
    qs = ApprovalRequest.objects.filter(
        action_id=action_code, status="GENEHMIGT", applied_at__isnull=True,
        target_table=target_table, target_id=target_id,
    )
    if payload is not None:
        qs = qs.filter(payload=payload)
    return qs.order_by("decided_at").first()


def assert_approved(action_code, *, target_table=None, target_id=None, payload=None):
    """Torfunktion: gibt die gültige Genehmigung zurück oder wirft NotApproved
    (→ 422). Der Aufrufer ruft nach erfolgreicher Ausführung consume()."""
    grant = find_grant(
        action_code, target_table=target_table, target_id=target_id, payload=payload
    )
    if grant is None:
        raise NotApproved(
            "Für diese Aktion liegt keine gültige Genehmigung vor. Bitte einen "
            "Vier-Augen-Antrag stellen und genehmigen lassen."
        )
    return grant


def claim(actor_app_user_id, *, action_code, target_table=None, target_id=None,
          payload=None):
    """Sperrt eine passende Genehmigung und verbraucht sie **sofort** — gedacht
    für den Aufruf INNERHALB der Transaktion, die auch die Aktion ausführt.

    `SELECT … FOR UPDATE` serialisiert nebenläufige Einlöser: der zweite Request
    sieht `applied_at` gesetzt und findet keine Genehmigung mehr. Scheitert die
    Aktion danach fachlich, rollt die umgebende Transaktion auch das Verbrauchen
    zurück — die Genehmigung bleibt gültig.

    Gibt die Genehmigung zurück oder None, wenn keine (mehr) vorliegt.
    """
    qs = ApprovalRequest.objects.select_for_update().filter(
        action_id=action_code, status="GENEHMIGT", applied_at__isnull=True,
        target_table=target_table, target_id=target_id,
    )
    if payload is not None:
        qs = qs.filter(payload=payload)
    grant = qs.order_by("decided_at").first()
    if grant is None:
        return None
    updated = ApprovalRequest.objects.filter(
        id=grant.id, status="GENEHMIGT", applied_at__isnull=True
    ).update(applied_at=timezone.now())
    if not updated:          # von einem Nebenläufer zwischen SELECT und UPDATE geschnappt
        return None
    return grant


def consume(actor_app_user_id, *, request_id):
    """Markiert eine erteilte Genehmigung als verbraucht (Einmaligkeit)."""
    with business_transaction(actor_app_user_id):
        updated = ApprovalRequest.objects.filter(
            id=request_id, status="GENEHMIGT", applied_at__isnull=True
        ).update(applied_at=timezone.now())
    if not updated:
        raise ValueError("Genehmigung ist nicht (mehr) verfügbar oder bereits verbraucht.")


# --- Liste (für die API) ---------------------------------------------------

def list_requests(*, status=None):
    """Anträge, optional nach Status gefiltert (Standard: alle, neueste zuerst)."""
    qs = ApprovalRequest.objects.select_related(
        "action", "requested_by", "decided_by"
    )
    if status:
        qs = qs.filter(status=status)
    return qs.order_by("-requested_at", "id")
