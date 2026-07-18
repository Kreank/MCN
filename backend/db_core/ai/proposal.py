"""ai.ai_proposal-Service — annehmen (materialisieren), ablehnen, (DSGVO) löschen.

Der Kern der Vision: Die KI **schreibt nie selbst**. Sie legt einen Vorschlag ab;
ausgeführt wird er ausschließlich durch die App-Schicht über die jeweilige Fach-API —
durch **dieselben Statusautomaten, Freigaben und DB-Trigger wie beim Menschen**. Genau
das leistet `approve`: Es ruft die **bestehenden** Fach-Services (nicht die DB direkt)
und materialisiert daraus ein echtes Fachobjekt. Ein KI-Entwurf, der angenommen wird,
geht damit durch kein anderes Tor als der von Hand angelegte.

Drei Vorgänge:
* `approve`  — nimmt einen PENDING-Vorschlag an: materialisiert ihn über die Fach-API
  (v1: `SITE_REPORT_ENTWURF` → ein `workflow.site_report` im ENTWURF) und schaltet den
  Vorschlag in derselben Transaktion auf APPROVED. Idempotent/nebenläufigkeitssicher
  über `SELECT … FOR UPDATE`.
* `reject`   — lehnt einen offenen Vorschlag ab (PENDING → REJECTED).
* `delete_proposal` — löscht einen REJECTED/EXPIRED-Vorschlag (DSGVO Art. 17) gegen den
  personenbezogenen Berichtstext im `proposed_payload`. Der DB-Trigger
  `guard_ai_proposal_delete` (Migration 0110) lässt nur diese beiden Zustände zu.
"""
from django.utils import timezone

from db_core.betriebszeit import betriebs_datum
from db_core.db_context import business_transaction
from db_core.gate_errors import as_business_error
from db_core.models import AiProposal
from db_core.services import site_report as site_report_service


class ProposalError(ValueError):
    """Der Vorschlags-Vorgang ist fachlich unzulässig (→ 422)."""


# ---------------------------------------------------------------------------
# Annehmen (Materialisierung über die Fach-API)
# ---------------------------------------------------------------------------
#
# Ein KI-Berichtsentwurf trägt dieselben Positionsarten wie eine Berichtsposition
# (BERICHT_LINE_TYPES, Migration 0080). Der Entwurf entsteht per Constrained
# Decoding (BERICHT_SCHEMA in workflow_sprachmemo) — trotzdem wird beim
# Materialisieren NICHTS geglaubt: die Positionen laufen durch `set_report_lines`
# und damit durch alle Service- und DB-Tore.

# Ein neutraler Platzhalter, falls der Entwurf keine Tätigkeitsbeschreibung trägt.
# Der Bericht bleibt ein ENTWURF, den ein Mensch vor der Unterschrift korrigiert —
# der Platzhalter erfindet keine Tatsache, er benennt nur die Herkunft.
_LEERER_TEXT_PLATZHALTER = "Einsatzbericht (Entwurf aus Sprachmemo)"


def _payload_zu_berichtszeilen(payload_lines):
    """Bildet die Entwurfszeilen auf Eingaben für `set_report_lines` ab.

    Grundsatz: **nichts erfinden, nichts verlieren.** Eine mengenbehaftete Position
    (alles außer TEXT) braucht Menge UND Einheit; fehlt eines, wäre jede erfundene
    Zahl eine Fälschung. Statt die Zeile zu verwerfen ODER eine Menge zu erfinden,
    wird sie zur **TEXT-Zeile herabgestuft** — die Beschreibung bleibt erhalten, die
    unvollständige Mengenangabe wandert in die Notiz. Der Monteur stuft sie im
    ENTWURF wieder zu einer echten Position hoch und trägt die richtige Menge nach.
    """
    zeilen = []
    for line in payload_lines or []:
        if not isinstance(line, dict):
            continue
        beschreibung = (line.get("description") or "").strip()
        if not beschreibung:
            # Eine Zeile ohne Bezeichnung trägt nichts — die DB nähme sie ohnehin
            # nicht (CHECK). Still überspringen statt den ganzen Entwurf blockieren.
            continue
        line_type = line.get("line_type")
        menge = line.get("quantity")
        einheit = (line.get("unit") or "").strip() or None

        if line_type == "TEXT":
            zeilen.append({"line_type": "TEXT", "description": beschreibung})
            continue

        if line_type not in site_report_service.BERICHT_LINE_TYPES:
            # Unbekannte Positionsart → als Textnotiz erhalten (nicht erfinden).
            zeilen.append({
                "line_type": "TEXT",
                "description": beschreibung,
                "note": f"KI-Entwurf, Positionsart unklar ({line_type}).",
            })
            continue

        if menge is not None and einheit is not None:
            zeilen.append({
                "line_type": line_type,
                "description": beschreibung,
                "quantity": menge,
                "unit": einheit,
            })
            continue

        # Mengenbehaftete Position ohne vollständige Menge/Einheit: herabstufen,
        # das Teilwissen in die Notiz retten — keine Zahl erfinden.
        hinweis = [f"Art laut KI-Entwurf: {line_type}"]
        if menge is not None:
            hinweis.append(f"Menge laut Memo: {menge}")
        if einheit is not None:
            hinweis.append(f"Einheit: {einheit}")
        zeilen.append({
            "line_type": "TEXT",
            "description": beschreibung,
            "note": "; ".join(hinweis) + ". Bitte Menge/Einheit ergänzen.",
        })
    return zeilen


def _materialisiere_site_report(actor_app_user_id, prop):
    """Materialisiert einen `SITE_REPORT_ENTWURF` als echten `workflow.site_report`.

    Über die **Fach-Services** (`create_report` + `set_report_lines`), damit derselbe
    Statusautomat, dieselben Trigger und dieselbe Preisfreiheit greifen wie bei der
    manuellen Anlage. Der Bericht entsteht als **ENTWURF** — der Mensch korrigiert und
    unterschreibt ihn danach; die KI liefert nur den Rohentwurf.
    """
    if prop.target_type != "work_order":
        raise ProposalError(
            f"Ein Berichtsentwurf muss an einem Auftrag hängen, nicht an "
            f"'{prop.target_type}'."
        )
    payload = prop.proposed_payload if isinstance(prop.proposed_payload, dict) else {}
    activity = (payload.get("activity_text") or "").strip() or _LEERER_TEXT_PLATZHALTER

    report = site_report_service.create_report(
        actor_app_user_id,
        work_order_id=prop.target_id,
        report_date=betriebs_datum(),
        activity_text=activity,
    )
    zeilen = _payload_zu_berichtszeilen(payload.get("lines"))
    if zeilen:
        site_report_service.set_report_lines(
            actor_app_user_id, report_id=report.id, lines=zeilen
        )
    return {
        "result_type": "site_report",
        "result_id": report.id,
        "work_order_id": report.work_order_id,
    }


# proposal_type → Materialisierer. Ein unbekannter Typ ist kein stiller No-Op,
# sondern ein klarer Fachfehler (siehe `approve`).
MATERIALISIERER = {
    "SITE_REPORT_ENTWURF": _materialisiere_site_report,
}


def approve(actor_app_user_id, *, proposal_id):
    """Nimmt einen offenen Vorschlag an: materialisieren + auf APPROVED schalten.

    Atomar in **einer** Transaktion: erst die Sperre auf den Vorschlag
    (`SELECT … FOR UPDATE` — serialisiert zwei gleichzeitige Freigaben und macht das
    Annehmen idempotent), dann die Materialisierung über die Fach-Services (die ihre
    eigenen Tore/Trigger mitbringen), zuletzt der Statuswechsel. Scheitert die
    Materialisierung, rollt alles zurück und der Vorschlag bleibt PENDING — es
    entsteht kein halber Bericht.

    Rückgabe: `(prop, result)` — `result` beschreibt das erzeugte Fachobjekt
    (`result_type`/`result_id`), damit die API darauf verlinken kann.
    """
    with as_business_error():
        with business_transaction(actor_app_user_id):
            prop = (
                AiProposal.objects.select_for_update()
                .filter(id=proposal_id)
                .first()
            )
            if prop is None:
                raise ProposalError("Vorschlag nicht gefunden.")
            if prop.status != "PENDING":
                raise ProposalError(
                    f"Der Vorschlag ist bereits {prop.status.lower()} und kann nicht "
                    "mehr angenommen werden."
                )
            if prop.expires_at is not None and prop.expires_at <= timezone.now():
                # Der Trigger würde die Freigabe nach Ablauf ohnehin abweisen; hier
                # die klare Fachmeldung, bevor überhaupt materialisiert wird.
                raise ProposalError(
                    "Der Vorschlag ist abgelaufen und kann nicht mehr angenommen "
                    "werden."
                )

            materialisierer = MATERIALISIERER.get(prop.proposal_type)
            if materialisierer is None:
                raise ProposalError(
                    f"Für den Vorschlagstyp '{prop.proposal_type}' gibt es keine "
                    "Materialisierung."
                )
            result = materialisierer(actor_app_user_id, prop)

            prop.status = "APPROVED"
            prop.approved_by_user_id = actor_app_user_id
            # approved_at setzt der DB-Trigger `guard_ai_proposal` serverseitig
            # (nicht fälschbar) — nicht in update_fields, aber der BEFORE-Trigger
            # schreibt es mit.
            prop.save(update_fields=["status", "approved_by_user_id"])
    prop.refresh_from_db()
    return prop, result


# ---------------------------------------------------------------------------
# Ablehnen / Löschen
# ---------------------------------------------------------------------------


def reject(actor_app_user_id, *, proposal_id, reason):
    """Lehnt einen offenen Vorschlag ab (PENDING → REJECTED)."""
    if not reason or not str(reason).strip():
        raise ProposalError("Ein Ablehnungsgrund ist erforderlich.")
    with as_business_error():
        with business_transaction(actor_app_user_id):
            prop = (
                AiProposal.objects.select_for_update()
                .filter(id=proposal_id)
                .first()
            )
            if prop is None:
                raise ProposalError("Vorschlag nicht gefunden.")
            if prop.status != "PENDING":
                raise ProposalError(
                    f"Der Vorschlag ist bereits {prop.status.lower()} und kann nicht "
                    "mehr abgelehnt werden."
                )
            prop.status = "REJECTED"
            prop.rejection_reason = reason
            prop.save(update_fields=["status", "rejection_reason"])
    prop.refresh_from_db()
    return prop


def expire_stale_proposals(actor_app_user_id, *, limit=200):
    """Setzt abgelaufene offene Vorschläge auf EXPIRED (PENDING → EXPIRED).

    Ohne diesen Sweep bliebe ein nie beschiedener Vorschlag für immer PENDING —
    er verstopfte die Freigabe-Kachel UND wäre nicht DSGVO-löschbar (das Löschtor
    lässt nur REJECTED/EXPIRED). Der Statusautomat erlaubt PENDING → EXPIRED; die
    Materialisierung/Freigabe ist danach ausgeschlossen (nur PENDING wechselt).
    Wird vom KI-Queue-Tick getrieben, in Sinneseinheit mit dem workflow_run-Reaper.
    """
    expired = []
    with business_transaction(actor_app_user_id):
        faellig = list(
            AiProposal.objects.select_for_update(skip_locked=True)
            .filter(status="PENDING", expires_at__lt=timezone.now())
            .order_by("expires_at")[:limit]
        )
        for prop in faellig:
            prop.status = "EXPIRED"
            prop.save(update_fields=["status"])
            expired.append(prop.id)
    return expired


def delete_proposal(actor_app_user_id, *, proposal_id):
    """Löscht einen abgelehnten/abgelaufenen Vorschlag (DSGVO Art. 17).

    Der DB-Trigger `guard_ai_proposal_delete` weist PENDING/APPROVED physisch ab
    (P0001) — dieser Service reicht den rohen DB-Fehler durch; die **API-Schicht**
    übersetzt ihn über `as_business_error` in eine klare 422 (so erwartet es der
    Grundlagen-Test). Ein bereits verschwundener Vorschlag ist kein Fehler
    (idempotent)."""
    prop = AiProposal.objects.filter(id=proposal_id).first()
    if prop is None:
        return
    with business_transaction(actor_app_user_id):
        prop.delete()
