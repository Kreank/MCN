"""ai.ai_proposal-Service — ablehnen und (DSGVO) löschen.

Die **Ausführung** eines genehmigten Vorschlags läuft NICHT hier, sondern durch die
App-Schicht über die jeweilige Fach-API (dieselben Tore wie beim Menschen). Hier nur:
ablehnen (PENDING → REJECTED) und einen REJECTED/EXPIRED-Vorschlag löschen — der
Betroffenen-Löschanspruch (DSGVO Art. 17) gegen den personenbezogenen Berichtstext im
`proposed_payload`. Der DB-Trigger `guard_ai_proposal_delete` (Migration 0110) lässt
nur diese beiden Zustände zum Löschen zu.
"""
from db_core.db_context import business_transaction
from db_core.models import AiProposal


def reject(actor_app_user_id, *, proposal_id, reason):
    """Lehnt einen offenen Vorschlag ab (PENDING → REJECTED)."""
    if not reason or not str(reason).strip():
        raise ValueError("Ein Ablehnungsgrund ist erforderlich.")
    prop = AiProposal.objects.filter(id=proposal_id).first()
    if prop is None:
        raise ValueError("Vorschlag nicht gefunden.")
    with business_transaction(actor_app_user_id):
        prop.status = "REJECTED"
        prop.rejection_reason = reason
        prop.save(update_fields=["status", "rejection_reason"])
    return prop


def delete_proposal(actor_app_user_id, *, proposal_id):
    """Löscht einen abgelehnten/abgelaufenen Vorschlag (DSGVO Art. 17).

    Der DB-Trigger weist PENDING/APPROVED physisch ab — die App-Schicht übersetzt das
    (P0001) über `gate_errors` in eine klare 422. Ein bereits verschwundener Vorschlag
    ist kein Fehler (idempotent)."""
    prop = AiProposal.objects.filter(id=proposal_id).first()
    if prop is None:
        return
    with business_transaction(actor_app_user_id):
        prop.delete()
