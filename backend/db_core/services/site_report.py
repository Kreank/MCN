"""Baustellenbericht-Service (workflow.site_report, Migration 0054/0064).

Tätigkeitsnachweis vor Ort. Anlegen/Ändern nur im ENTWURF; die Kundenunterschrift
besiegelt den Bericht (ENTWURF → UNTERZEICHNET) und macht ihn unveränderlich
(DB-Trigger `protect_site_report`). Fotos hängen als content.file_link
(site_report_id) über den Datei-Service daran.

**Anker (0064): Auftrag ODER Einsatz.** Ein Bericht hängt am Auftrag (Baustelle),
am Einsatz (Termin) oder an beidem — nie im Leeren (DB-CHECK). Damit trägt auch der
**freie Termin** (Einsatz ohne Auftrag, 0062) ein Begehungsprotokoll. Ist ein
Einsatz angegeben, **leitet der Service den Auftrag daraus ab**: der Bericht am
auftragsgebundenen Einsatz erscheint dadurch zwingend auch in der Auftragsliste,
der Bericht am freien Termin trägt keinen Auftrag. Ein widersprüchlich
mitgeschickter `work_order_id` ist ein Fachfehler (422), keine stille Korrektur.
Die DB setzt dieselbe Regel unabhängig durch (`check_site_report_anchor`).

Die Bezeichnung des Berichts entsteht aus Datum + Tätigkeit; den Kontext liefert
der Anker (`work_order.title` bzw. `service_job.title` — Letzteres ist beim freien
Termin Pflichtfeld, 0062). Der Bericht führt deshalb kein eigenes Titelfeld.

Alle Writes über business_transaction. Fachfehler → ValueError (API übersetzt in
422). Die Unterschrift wird als PNG im Objektspeicher abgelegt (content.file,
SHA-256-Dedup wie beim Firmenlogo) und über `signature_file_id` referenziert; der
DB-CHECK erzwingt, dass ein unterzeichneter Bericht Name + Zeitpunkt + Unterschrift
vollständig trägt.
"""
import hashlib
import uuid
from datetime import datetime, timezone as dt_timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from db_core import storage as storage_module
from db_core.db_context import business_transaction
from db_core.gate_errors import as_business_error
from db_core.models import File, ServiceJob, SiteReport, WorkOrder
from db_core.services._validation import ensure_exists

_EDITIERBAR = ("ENTWURF",)


class SiteReportError(ValueError):
    """Der Baustellenbericht-Vorgang ist fachlich unzulässig (→ 422)."""


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _hours(value):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        h = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise SiteReportError("Arbeitsstunden sind keine gültige Zahl.")
    if not h.is_finite() or h < 0:
        raise SiteReportError("Arbeitsstunden dürfen nicht negativ sein.")
    # Auf die Spaltenpräzision numeric(6,2) runden, BEVOR die Obergrenze geprüft
    # wird — sonst würde z. B. 9999.999 den Test bestehen, in der DB aber auf
    # 10000.00 gerundet (SQLSTATE 22003 → 500 statt 422).
    h = h.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if h > Decimal("9999.99"):
        raise SiteReportError("Arbeitsstunden sind zu groß.")
    return h


def list_reports(work_order_id=None, service_job_id=None):
    """Berichte eines Auftrags ODER eines Einsatzes, neueste zuerst.

    Genau eine der beiden Angaben ist zu setzen. Die Auftragsliste enthält dank
    der Ableitung (s. Modulkopf) auch die Berichte aller Einsätze des Auftrags.
    """
    if (work_order_id is None) == (service_job_id is None):
        raise SiteReportError(
            "Genau eines von Auftrag oder Einsatz ist anzugeben."
        )
    qs = (
        SiteReport.objects.filter(work_order_id=work_order_id)
        if service_job_id is None
        else SiteReport.objects.filter(service_job_id=service_job_id)
    )
    return qs.select_related("author").order_by("-report_date", "-created_at")


def get_report(report_id):
    return SiteReport.objects.filter(id=report_id).select_related("author").first()


def _anker(work_order_id, service_job_id):
    """Prüft den Anker und leitet den Auftrag aus dem Einsatz ab.

    Rückgabe: der zu speichernde `work_order_id` (beim freien Termin None).
    Der Einsatz ist die stärkere Angabe — er trägt seinen Auftrag (oder eben
    keinen). Ein davon abweichend mitgeschickter Auftrag wird NICHT stillschweigend
    überschrieben, sondern als Fachfehler abgelehnt: der Aufrufer meint etwas
    anderes, als er sagt.
    """
    if work_order_id is None and service_job_id is None:
        raise SiteReportError(
            "Ein Bericht braucht einen Bezug: Auftrag oder Einsatz."
        )
    if service_job_id is None:
        ensure_exists(WorkOrder, work_order_id, "Auftrag")
        return work_order_id

    job = ServiceJob.objects.filter(id=service_job_id).only(
        "id", "work_order_id"
    ).first()
    if job is None:
        raise SiteReportError("Der angegebene Einsatz existiert nicht.")
    if work_order_id is not None and str(job.work_order_id or "") != str(work_order_id):
        if job.work_order_id is None:
            raise SiteReportError(
                "Der Einsatz ist ein freier Termin (ohne Auftrag) — ein Bericht "
                "daran kann keinem Auftrag zugeordnet werden."
            )
        raise SiteReportError("Der Einsatz gehört nicht zu diesem Auftrag.")
    return job.work_order_id


def create_report(actor_app_user_id, *, report_date, activity_text,
                  work_order_id=None, service_job_id=None, weather=None,
                  hours_worked=None, materials_note=None, remarks=None):
    """Legt einen Baustellenbericht (Status ENTWURF) an.

    Anker: Auftrag und/oder Einsatz — mindestens eins (freier Termin: nur der
    Einsatz). `report_date` und `activity_text` sind Pflicht; der Autor ist der
    Akteur.
    """
    work_order_id = _anker(work_order_id, service_job_id)
    if report_date is None:
        raise SiteReportError("Das Berichtsdatum ist erforderlich.")
    activity = _clean(activity_text)
    if not activity:
        raise SiteReportError("Die Tätigkeitsbeschreibung darf nicht leer sein.")
    with as_business_error():
        with business_transaction(actor_app_user_id):
            report = SiteReport.objects.create(
                id=uuid.uuid4(),
                work_order_id=work_order_id,
                service_job_id=service_job_id,
                report_date=report_date,
                author_id=actor_app_user_id,
                weather=_clean(weather),
                activity_text=activity,
                hours_worked=_hours(hours_worked),
                materials_note=_clean(materials_note),
                remarks=_clean(remarks),
                status="ENTWURF",
                version=1,
            )
    report.refresh_from_db()
    return report


def update_report(actor_app_user_id, *, report_id, **fields):
    """Ändert einen Bericht — nur im ENTWURF (unterzeichnet = eingefroren)."""
    report = SiteReport.objects.filter(id=report_id).first()
    if report is None:
        raise SiteReportError("Bericht nicht gefunden.")
    if report.status not in _EDITIERBAR:
        raise SiteReportError("Ein unterzeichneter Bericht ist unveränderlich.")

    allowed = ("report_date", "service_job_id", "weather", "activity_text",
               "hours_worked", "materials_note", "remarks")
    unknown = set(fields) - set(allowed)
    if unknown:
        raise SiteReportError(f"Unbekannte Felder: {', '.join(sorted(unknown))}")

    changed = []
    if "report_date" in fields:
        if fields["report_date"] is None:
            raise SiteReportError("Das Berichtsdatum ist erforderlich.")
        report.report_date = fields["report_date"]
        changed.append("report_date")
    if "activity_text" in fields:
        activity = _clean(fields["activity_text"])
        if not activity:
            raise SiteReportError("Die Tätigkeitsbeschreibung darf nicht leer sein.")
        report.activity_text = activity
        changed.append("activity_text")
    if "service_job_id" in fields:
        sj = fields["service_job_id"]
        if str(sj or "") != str(report.service_job_id or ""):
            if report.work_order_id is None:
                # Der Einsatz ist der einzige Anker des Berichts (freier Termin):
                # Umhängen verfälschte den Nachweis, Leeren risse den Anker auf.
                raise SiteReportError(
                    "Der Bericht hängt am freien Termin — sein Einsatzbezug ist "
                    "unveränderlich."
                )
            if sj is not None:
                job = ServiceJob.objects.filter(id=sj).only(
                    "id", "work_order_id"
                ).first()
                if job is None or str(job.work_order_id or "") != str(report.work_order_id):
                    raise SiteReportError(
                        "Der Einsatz gehört nicht zu diesem Auftrag."
                    )
            report.service_job_id = sj
            changed.append("service_job_id")
    if "weather" in fields:
        report.weather = _clean(fields["weather"])
        changed.append("weather")
    if "hours_worked" in fields:
        report.hours_worked = _hours(fields["hours_worked"])
        changed.append("hours_worked")
    if "materials_note" in fields:
        report.materials_note = _clean(fields["materials_note"])
        changed.append("materials_note")
    if "remarks" in fields:
        report.remarks = _clean(fields["remarks"])
        changed.append("remarks")

    if changed:
        with as_business_error():
            with business_transaction(actor_app_user_id):
                report.save(update_fields=changed + ["updated_at"])
        report.refresh_from_db()
    return report


def sign_report(actor_app_user_id, *, report_id, signed_by_name, signature_png):
    """Besiegelt den Bericht mit der Kundenunterschrift (ENTWURF → UNTERZEICHNET).

    `signature_png` sind die PNG-Bytes der Unterschrift (Canvas). Sie werden im
    Objektspeicher abgelegt (content.file, SHA-256-Dedup) und referenziert. Danach
    ist der Bericht unveränderlich (Trigger). Fehlt Name oder Unterschrift, oder ist
    der Bericht nicht mehr im ENTWURF → SiteReportError.
    """
    report = SiteReport.objects.filter(id=report_id).first()
    if report is None:
        raise SiteReportError("Bericht nicht gefunden.")
    if report.status != "ENTWURF":
        raise SiteReportError("Der Bericht ist bereits unterzeichnet.")
    name = _clean(signed_by_name)
    if not name:
        raise SiteReportError("Der Name des Unterzeichnenden ist erforderlich.")
    if not signature_png:
        raise SiteReportError("Es wurde keine Unterschrift erfasst.")
    if signature_png[:8] != b"\x89PNG\r\n\x1a\n":
        raise SiteReportError("Die Unterschrift muss ein PNG-Bild sein.")

    # Unterschrift ablegen (Dedup über SHA-256 wie beim Firmenlogo). Der
    # Objektspeicher-Write muss vor der Transaktion liegen (er ist nicht
    # transaktional), der DATENBANK-Teil gehört aber in DIESELBE Transaktion wie
    # der Statuswechsel: scheitert das Besiegeln am Tor, bleibt sonst eine
    # verwaiste content.file-Zeile ohne Bericht zurück.
    digest = hashlib.sha256(signature_png).hexdigest()
    datei = File.objects.filter(sha256=digest, size_bytes=len(signature_png)).first()
    storage_key = None
    if datei is None:
        storage_key = f"signature/{uuid.uuid4()}"
        try:
            storage_module.get_storage().put_object(
                storage_key, signature_png, content_type="image/png"
            )
        except storage_module.StorageError as exc:
            raise SiteReportError(f"Die Unterschrift konnte nicht gespeichert werden: {exc}")

    with as_business_error():
        with business_transaction(actor_app_user_id):
            if datei is None:
                datei = File.objects.create(
                    id=uuid.uuid4(),
                    storage_key=storage_key,
                    original_filename="unterschrift.png",
                    mime_type="image/png",
                    size_bytes=len(signature_png),
                    sha256=digest,
                    media_metadata={},
                    uploaded_by_id=actor_app_user_id,
                )
            report.signed_by_name = name
            report.signed_at = datetime.now(dt_timezone.utc)
            report.signature_file_id = datei.id
            report.status = "UNTERZEICHNET"
            report.save(update_fields=[
                "signed_by_name", "signed_at", "signature_file_id", "status",
                "updated_at",
            ])
    report.refresh_from_db()
    return report
