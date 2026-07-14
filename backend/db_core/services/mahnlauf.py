"""Mahnlauf (semi-automatisch): bündelt die Einzel-Mahnung zu einem Stapel.

Statt jede überfällige Rechnung einzeln zu mahnen, ermittelt `list_candidates`
alle Rechnungen, die zum Stichtag für ihre NÄCHSTE Mahnstufe fällig sind, und
`run` führt einen vom Nutzer bestätigten Stapel aus (Mahnstufe ausstellen,
optional per E-Mail versenden). Jede Rechnung ist unabhängig: Teil­erfolge sind
gewollt, ein Fehler bei einer Rechnung bricht den Lauf nicht ab.

Eskalationsregel (aus der Mahnstufen-Konfiguration abgeleitet, keine neue Logik):
Eine Rechnung ist Kandidat für Stufe `k = aktuelle_stufe + 1`, wenn
  - sie eine **offene Forderung** ist (`buchhaltung.offene_forderungen` — die eine
    Grenze: veröffentlicht, kein Kreditbeleg, NICHT storniert, offener Betrag nach
    Abzug von Gutschriften und Zahlungen > 0) und überfällig,
  - Stufe `k` existiert und aktiv ist,
  - die Frist der Stufe erreicht ist: Überfälligkeitstage >= days_after_due(k).

Der Mahnlauf definiert „offen" NICHT selbst: Er mahnte sonst stornierte Rechnungen
— Geld, das der Kunde nicht mehr schuldet.
Die lückenlose Eskalation (max+1) erzwingt zusätzlich der DB-Trigger; `run`
prüft die Stufe vor dem Ausstellen erneut, damit ein zwischenzeitlich anderweitig
gemahnter Beleg nicht doppelt eskaliert (stale → übersprungen statt 500).

Der eigentliche Schreibvorgang läuft über `buchhaltung.issue_dunning_notice`
(business_transaction/Audit); der Versand über `beleg_versand.send_dunning_email`.
"""
from datetime import date

from django.db.models import F
from django.db import IntegrityError

from db_core.mail_crypto import MailKeyError
from db_core.models import DunningLevel
from db_core.services import beleg_versand, buchhaltung
from db_core.services.mail import MailSendError

# Zahlungsstand UND Forderungsgrenze: EINE Rechenstelle
# (db_core.services.buchhaltung). Diese Datei führte beides früher als eigene
# Kopie — und mahnte deshalb stornierte Rechnungen.


def list_candidates(*, stichtag=None):
    """Alle Rechnungen, die zum Stichtag für ihre nächste Mahnstufe fällig sind.

    Gibt eine Liste von Dicts zurück (nach Fälligkeit sortiert), je mit offenem
    Betrag, aktueller und vorgeschlagener Stufe, Überfälligkeitstagen und der
    ermittelten Schuldner-E-Mail (None, wenn keine hinterlegt ist — dann kann der
    Lauf die Stufe ausstellen, aber nicht mailen).
    """
    stichtag = stichtag or date.today()
    levels = {lv.level: lv for lv in DunningLevel.objects.all()}
    # Grundmenge = offene, überfällige FORDERUNGEN (die eine Grenze). Damit sind
    # stornierte Rechnungen, Kreditbelege und durch Gutschriften aufgezehrte
    # Beträge hier gar nicht erst im Rennen.
    qs = (
        buchhaltung.offene_forderungen(stichtag=stichtag)
        .prefetch_related("parties__party")
        .order_by(F("due_date").asc(nulls_last=True), "id")
    )

    candidates = []
    for inv in qs:
        spiegel = buchhaltung.zahlungsspiegel(inv, heute=stichtag)
        cur = spiegel["dunning_level"] or 0
        nxt = cur + 1
        lvl = levels.get(nxt)
        if lvl is None or not lvl.active:
            continue
        days_overdue = (stichtag - inv.due_date).days if inv.due_date else 0
        if days_overdue < lvl.days_after_due:
            continue
        party = beleg_versand._debtor_party(inv)
        candidates.append(
            {
                "invoice_id": inv.id,
                "invoice_number": inv.invoice_number,
                "debtor": party.display_name if party else None,
                "due_date": inv.due_date,
                "open_amount": spiegel["open_amount"],
                "current_level": cur,
                "next_level": nxt,
                "next_level_label": lvl.label,
                "days_overdue": days_overdue,
                "recipient_email": (
                    beleg_versand.primary_email(party.id) if party else None
                ),
            }
        )
    return candidates


def run(actor_app_user_id, *, items, stichtag=None, send_email=True):
    """Führt einen bestätigten Mahnlauf aus.

    `items` ist eine Liste von (invoice_id, level): die vom Nutzer bestätigten
    Rechnungen mit der erwarteten nächsten Stufe. Vor dem Ausstellen wird jede
    Rechnung ERNEUT vollständig gegen den aktuellen Stand geprüft (offener Betrag,
    Überfälligkeit, Frist, Stufe) — mit exakt derselben Logik wie die Vorschau.
    So wird eine im Fenster Vorschau→Lauf bezahlte oder anderweitig gemahnte
    Rechnung nicht fälschlich (weiter) gemahnt, sondern übersprungen.

    Passende Rechnungen werden ausgestellt und — wenn `send_email` und eine Adresse
    vorhanden — versendet. Der Versand ist Best-Effort: schlägt er fehl, bleibt die
    ausgestellte Stufe bestehen und die Zeile wird als `issued`, aber nicht `sent`,
    mit Fehlertext gemeldet. Ein DB-Konflikt beim Ausstellen (z. B. Nebenläufigkeit)
    bricht den Lauf nicht ab, sondern wird als `failed`-Zeile gemeldet.

    Gibt ein Dict {results: [...], issued: n, sent: n, skipped: n, failed: n}
    zurück.
    """
    stichtag = stichtag or date.today()
    # Aktueller Stand zum Stichtag: {invoice_id: candidate}. Nur was JETZT noch für
    # genau die erwartete Stufe fällig ist, wird ausgestellt (Re-Prüfung gegen
    # Zahlung/andere Mahnung im Fenster Vorschau→Lauf).
    eligible = {c["invoice_id"]: c for c in list_candidates(stichtag=stichtag)}
    results = []
    issued = sent = skipped = failed = 0
    verarbeitet = set()

    for item in items:
        invoice_id = item["invoice_id"]
        expected = item["level"]
        cand = eligible.get(invoice_id)
        if invoice_id in verarbeitet or cand is None or cand["next_level"] != expected:
            skipped += 1
            results.append(
                {
                    "invoice_id": invoice_id,
                    "status": "skipped",
                    "detail": (
                        f"Übersprungen: zum Stichtag nicht (mehr) für Stufe "
                        f"{expected} fällig — Zahlung, andere Mahnung oder "
                        "Doppelauswahl dazwischen."
                    ),
                }
            )
            continue
        verarbeitet.add(invoice_id)

        try:
            notice = buchhaltung.issue_dunning_notice(
                actor_app_user_id,
                invoice_id=invoice_id,
                level=expected,
                issued_at=stichtag,
            )
        except (ValueError, IntegrityError) as exc:
            # ValueError: Fachtor (z. B. Trigger-Verstoß als P0001 übersetzt).
            # IntegrityError: Nebenläufigkeits-Konflikt am UNIQUE(invoice_id, level)
            # — nicht als 500 durchreichen, sondern die eine Zeile als failed melden.
            failed += 1
            results.append(
                {"invoice_id": invoice_id, "status": "failed", "detail": str(exc)}
            )
            continue

        issued += 1
        row = {
            "invoice_id": invoice_id,
            "status": "issued",
            "level": expected,
            "notice_id": notice.id,
            "detail": None,
        }
        if send_email:
            try:
                beleg_versand.send_dunning_email(
                    actor_app_user_id, dunning_notice_id=notice.id
                )
                row["status"] = "sent"
                sent += 1
            except (ValueError, LookupError, MailSendError, MailKeyError) as exc:
                # Stufe bleibt ausgestellt; nur der Versand scheiterte (fehlende
                # Adresse, kein Mailkonto, SMTP-/Schlüsselfehler).
                row["detail"] = f"Ausgestellt, aber nicht versendet: {exc}"
        results.append(row)

    return {
        "results": results,
        "issued": issued,
        "sent": sent,
        "skipped": skipped,
        "failed": failed,
    }
