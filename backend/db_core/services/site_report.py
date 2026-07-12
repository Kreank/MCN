"""Baustellenbericht-Service (workflow.site_report, Migration 0054).

Tätigkeitsnachweis vor Ort zu einem Auftrag (optional Einsatz). Anlegen/Ändern nur
im ENTWURF; die Kundenunterschrift besiegelt den Bericht (ENTWURF →
UNTERZEICHNET) und macht ihn unveränderlich (DB-Trigger `protect_site_report`).
Fotos hängen als content.file_link (site_report_id) über den Datei-Service daran.

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


def list_reports(work_order_id):
    """Berichte eines Auftrags, neueste zuerst."""
    return (
        SiteReport.objects.filter(work_order_id=work_order_id)
        .select_related("author")
        .order_by("-report_date", "-created_at")
    )


def get_report(report_id):
    return SiteReport.objects.filter(id=report_id).select_related("author").first()


def create_report(actor_app_user_id, *, work_order_id, report_date, activity_text,
                  service_job_id=None, weather=None, hours_worked=None,
                  materials_note=None, remarks=None):
    """Legt einen Baustellenbericht (Status ENTWURF) an. `report_date` und
    `activity_text` sind Pflicht; der Autor ist der Akteur."""
    ensure_exists(WorkOrder, work_order_id, "Auftrag")
    if report_date is None:
        raise SiteReportError("Das Berichtsdatum ist erforderlich.")
    activity = _clean(activity_text)
    if not activity:
        raise SiteReportError("Die Tätigkeitsbeschreibung darf nicht leer sein.")
    if service_job_id is not None:
        job = ServiceJob.objects.filter(id=service_job_id).first()
        if job is None:
            raise SiteReportError("Der angegebene Einsatz existiert nicht.")
        if str(job.work_order_id) != str(work_order_id):
            raise SiteReportError("Der Einsatz gehört nicht zu diesem Auftrag.")
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
        if sj is not None:
            job = ServiceJob.objects.filter(id=sj).first()
            if job is None or str(job.work_order_id) != str(report.work_order_id):
                raise SiteReportError("Der Einsatz gehört nicht zu diesem Auftrag.")
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

    # Unterschrift ablegen (Dedup über SHA-256 wie beim Firmenlogo).
    digest = hashlib.sha256(signature_png).hexdigest()
    datei = File.objects.filter(sha256=digest, size_bytes=len(signature_png)).first()
    if datei is None:
        storage_key = f"signature/{uuid.uuid4()}"
        try:
            storage_module.get_storage().put_object(
                storage_key, signature_png, content_type="image/png"
            )
        except storage_module.StorageError as exc:
            raise SiteReportError(f"Die Unterschrift konnte nicht gespeichert werden: {exc}")
        with business_transaction(actor_app_user_id):
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

    with as_business_error():
        with business_transaction(actor_app_user_id):
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
