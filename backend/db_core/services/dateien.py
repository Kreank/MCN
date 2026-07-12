"""Datei-Ablage: Hochladen, Verknüpfen, Herunterladen (content.file/file_link).

Eine Datei hängt an GENAU EINEM Zielobjekt (DB-CHECK `num_nonnulls(...) = 1`):
Projekt, Liegenschaft, Einheit, Anlage, Kontakt, Vorgang, Auftrag, Einsatz,
Angebot oder Rechnung. Wer dieselbe Datei an mehreren Orten braucht, legt eine
zweite Verknüpfung auf dieselbe `file_id` an — der Inhalt liegt dann trotzdem nur
einmal im Objektspeicher.

Sicherheitsentscheidungen, die hier getroffen werden:

* **Der Dateiname bestimmt niemals den Speicherort.** `storage_key` entsteht aus
  einer UUID. Ein Upload namens `../../etc/passwd` landet unter
  `upload/<uuid>` — Path Traversal ist damit strukturell ausgeschlossen, nicht
  durch Filterung.
* **Der vom Client gemeldete Content-Type wird nicht geglaubt.** Der MIME-Typ
  wird aus der Dateiendung abgeleitet und gegen eine Whitelist geprüft. Ein
  als `image/png` deklariertes `.exe` fliegt raus.
* **Kein aktiver Inhalt.** HTML, SVG und Skripte sind ausgeschlossen: ein
  ausgeliefertes SVG kann JavaScript im Kontext der Anwendung ausführen.
* **Größenlimit** vor dem Lesen, nicht danach.
* **Der Download läuft durch die Anwendung**, nicht über eine vorsignierte URL.
  Eine URL wäre nach dem Erzeugen für jeden gültig, der sie hat — die
  Rechteprüfung würde ins Leere laufen.

Dateien sind physisch unveränderlich (`trg_file_immutable`): kein UPDATE, kein
DELETE. Eine Korrektur ist eine neue Datei.
"""
import hashlib
import uuid
from pathlib import PurePosixPath

from db_core import storage as storage_module
from db_core.db_context import business_transaction
from db_core.gate_errors import as_business_error
from db_core.models import File, FileLink

# 50 MB. Größere Dateien gehören nicht durch ein Formular, sondern in einen
# Direkt-Upload gegen den Objektspeicher (später).
MAX_BYTES = 50 * 1024 * 1024

# Endung -> MIME. Bewusst eine Whitelist: alles Unbekannte wird abgelehnt.
# Kein text/html, kein image/svg+xml (beide können Skripte ausführen), keine
# ausführbaren Formate.
ERLAUBTE_TYPEN = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".doc": "application/msword",
    ".docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    ),
    ".odt": "application/vnd.oasis.opendocument.text",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".zip": "application/zip",
    ".dwg": "image/vnd.dwg",
    ".ifc": "application/x-step",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
}

# Zielspalten von content.file_link. Genau eine darf gesetzt sein.
ZIELE = (
    "project_id",
    "property_id",
    "unit_id",
    "asset_id",
    "party_id",
    "service_case_id",
    "work_order_id",
    "service_job_id",
    "quote_id",
    "invoice_id",
    "communication_id",
    "article_id",
    "site_report_id",
)

# Fachliche Einordnung. Freitext in der DB; hier eine gepflegte Liste, damit die
# Kategorien nicht auseinanderlaufen. BELEG_PDF vergibt nur beleg_pdf.py.
LINK_KATEGORIEN = (
    "DOKUMENT",
    "FOTO_VORHER",
    "FOTO_NACHHER",
    "VIDEO_BEGEHUNG",
    "SCAN",
    "PLAN",
    "VERTRAG",
    # Artikelbild: höchstens eines je Artikel (partieller Unique-Index 0042).
    "ARTIKELBILD",
    "SONSTIGES",
)


class DateiFehler(ValueError):
    """Der Upload ist fachlich unzulässig (→ 422)."""


class VerknuepfungGesperrt(DateiFehler):
    """Ein DB-Tor verbietet das Lösen der Verknüpfung (→ 422, nicht 404).

    Eigener Typ, weil `verknuepfung_loesen` sonst nicht mehr unterscheidbar wäre:
    „Verknüpfung nicht gefunden" ist 404, „der Bericht ist besiegelt" ist 422.
    """


def _sicherer_name(name):
    """Nur der Basisname, ohne Pfadanteile, auf 255 Zeichen begrenzt.

    Der Name ist reine Anzeige — er bestimmt nie, wohin geschrieben wird.
    """
    name = (name or "").strip().replace("\\", "/")
    basis = PurePosixPath(name).name
    basis = basis.replace("\x00", "")
    if not basis or basis in (".", ".."):
        raise DateiFehler("Ungültiger Dateiname.")
    return basis[:255]


def _mime_aus_endung(dateiname):
    endung = PurePosixPath(dateiname.lower()).suffix
    mime = ERLAUBTE_TYPEN.get(endung)
    if mime is None:
        erlaubt = ", ".join(sorted(ERLAUBTE_TYPEN))
        raise DateiFehler(
            f"Dateityp '{endung or dateiname}' ist nicht zugelassen. "
            f"Erlaubt: {erlaubt}"
        )
    return mime


def _ziel_pruefen(ziel):
    """Genau ein Ziel, und es muss ein bekanntes sein."""
    gesetzt = {k: v for k, v in ziel.items() if v}
    unbekannt = set(gesetzt) - set(ZIELE)
    if unbekannt:
        raise DateiFehler(f"Unbekanntes Ziel: {', '.join(sorted(unbekannt))}")
    if len(gesetzt) != 1:
        raise DateiFehler(
            "Eine Datei hängt an genau einem Objekt "
            f"(angegeben: {len(gesetzt)}). Erlaubt: {', '.join(ZIELE)}"
        )
    return gesetzt


def datei_hochladen(
    actor_app_user_id, *, dateiname, inhalt, link_category="DOKUMENT", **ziel
):
    """Legt eine Datei im Objektspeicher ab und verknüpft sie mit einem Ziel.

    `inhalt` sind Bytes. Gibt (File, FileLink) zurück.

    Ist derselbe Inhalt (SHA-256) bereits vorhanden, wird er NICHT erneut
    gespeichert — es entsteht nur eine weitere Verknüpfung. Das spart nicht bloß
    Platz: dieselbe Rechnung, die an Projekt und Kontakt hängt, ist dann auch
    wirklich dieselbe Datei.
    """
    name = _sicherer_name(dateiname)
    mime = _mime_aus_endung(name)
    ziel_spalte = _ziel_pruefen(ziel)

    if link_category not in LINK_KATEGORIEN:
        raise DateiFehler(
            f"Unbekannte Kategorie '{link_category}'. "
            f"Erlaubt: {', '.join(LINK_KATEGORIEN)}"
        )
    if not inhalt:
        raise DateiFehler("Die Datei ist leer.")
    if len(inhalt) > MAX_BYTES:
        raise DateiFehler(
            f"Die Datei ist zu groß ({len(inhalt) / 1_048_576:.1f} MB). "
            f"Erlaubt sind {MAX_BYTES // 1_048_576} MB."
        )

    digest = hashlib.sha256(inhalt).hexdigest()
    vorhanden = File.objects.filter(sha256=digest, size_bytes=len(inhalt)).first()

    if vorhanden is None:
        # Der Speicherort entsteht aus einer UUID, nie aus dem Dateinamen.
        storage_key = f"upload/{uuid.uuid4()}"
        try:
            storage_module.get_storage().put_object(storage_key, inhalt, content_type=mime)
        except storage_module.StorageError as exc:
            raise DateiFehler(f"Die Datei konnte nicht gespeichert werden: {exc}")
        with business_transaction(actor_app_user_id):
            datei = File.objects.create(
                id=uuid.uuid4(),
                storage_key=storage_key,
                original_filename=name,
                mime_type=mime,
                size_bytes=len(inhalt),
                sha256=digest,
                media_metadata={},
                uploaded_by_id=actor_app_user_id,
            )
    else:
        datei = vorhanden

    # Die DB kann die Verknüpfung fachlich verweigern (z. B. Anhang an einem
    # UNTERZEICHNETEN Baustellenbericht, Migration 0065) → 422 statt 500.
    try:
        with as_business_error():
            with business_transaction(actor_app_user_id):
                link = FileLink.objects.create(
                    id=uuid.uuid4(),
                    file_id=datei.id,
                    link_category=link_category,
                    created_by_id=actor_app_user_id,
                    **ziel_spalte,
                )
    except ValueError as exc:
        raise DateiFehler(str(exc))
    return datei, link


def datei_inhalt(file_id):
    """Bytes einer Datei aus dem Objektspeicher. Wirft DateiFehler, wenn sie fehlt."""
    datei = File.objects.filter(id=file_id).first()
    if datei is None:
        raise DateiFehler("Datei nicht gefunden.")
    try:
        return datei, storage_module.get_storage().get_object(datei.storage_key)
    except storage_module.StorageError as exc:
        raise DateiFehler(f"Die Datei ist derzeit nicht abrufbar: {exc}")


def dateien_am_ziel(**ziel):
    """Alle Verknüpfungen an einem Zielobjekt, neueste zuerst."""
    ziel_spalte = _ziel_pruefen(ziel)
    return (
        FileLink.objects.filter(**ziel_spalte)
        .select_related("file", "created_by")
        .order_by("-created_at", "id")
    )


def verknuepfung_loesen(actor_app_user_id, *, link_id):
    """Entfernt eine Verknüpfung, NICHT die Datei.

    Der Inhalt bleibt im Objektspeicher und in `content.file` — das ist Absicht:
    andere Verknüpfungen können darauf zeigen, und `content.file` ist
    unveränderlich (`trg_file_immutable`). Ein echtes Löschen gehört ins
    Aufbewahrungs-/Löschkonzept (Beschluss C-07/C-08) und nicht hierher.
    """
    link = FileLink.objects.filter(id=link_id).first()
    if link is None:
        raise DateiFehler("Verknüpfung nicht gefunden.")
    # Am UNTERZEICHNETEN Baustellenbericht ist auch das Lösen gesperrt (0065) —
    # der Trigger meldet sich fachlich; hier als 422 weiterreichen, nicht als 500.
    try:
        with as_business_error():
            with business_transaction(actor_app_user_id):
                link.delete()
    except ValueError as exc:
        raise VerknuepfungGesperrt(str(exc))
