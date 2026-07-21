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

## Berichtspositionen und Soll-Ist (Migration 0080)

Der Bericht führt Positionen aus dem Artikel-/Leistungsstamm (Artikel/Leistung,
Menge, Einheit) — **niemals Preise** (siehe Migration 0080: ein unterschriebener
Bericht mit Preisen wäre eine Preisvereinbarung). Daraus entsteht der
**Soll-Ist-Abgleich**: Angebots-Soll gegen Berichts-Ist, rein rechnerisch, ohne KI.

Drei Bausteine:

* `set_report_lines`  — ersetzt den kompletten Positionssatz (Delete+Insert, wie
  beim Beleg-Editor: ein Teil-Update wäre bei umsortierten Positionsnummern nicht
  eindeutig). Nur im ENTWURF; der DB-Trigger ist die letzte Instanz.
* `vorbelegen_aus_angebot` — kopiert die NORMAL-Positionen eines Angebots als
  **Soll** in einen noch leeren Bericht (`planned_quantity`), mit dem Ist
  gleichlautend vorbelegt: der Monteur korrigiert nur die Abweichungen.
* `soll_ist` — vergleicht über ALLE Berichte eines Auftrags. Das Soll kommt aus
  den **Angebotspositionen**, nicht aus `planned_quantity` — sonst fehlte der Fall
  „angeboten, aber nie eingebaut" (ENTFALLEN) vollständig: dafür gibt es gar keine
  Berichtsposition.

**Das Soll steht und fällt mit der Zuordnung `quote.work_order_id`.** Sie wird im
Angebot gesetzt (`beleg.create_quote`/`update_quote`) und ist die Aussage „dieses
Angebot ist das Soll dieser Baustelle". Es gibt keinen Projekt-Fallback: bei einem
Projekt mit mehreren Aufträgen wäre dasselbe Projektangebot das Soll *jedes*
Auftrags, während das Ist nur aus dessen eigenen Berichten käme.
"""
import hashlib
import uuid
from collections import OrderedDict
from datetime import date, datetime, timezone as dt_timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.db.models import Q

from db_core import storage as storage_module
from db_core.db_context import business_transaction
from db_core.gate_errors import as_business_error
from db_core.models import (
    Article,
    Assembly,
    File,
    Occupancy,
    PartyAddress,
    Quote,
    QuoteLine,
    ServiceJob,
    SiteReport,
    SiteReportLine,
    WorkOrder,
    WorkOrderParty,
)
from db_core.services._validation import ensure_exists
# Fertige Bausteine fuer den Briefkopf (Befund B8) — sie existierten alle,
# waren nur nie am Bericht verdrahtet.
from db_core.services.belegung import aktive_mieter
from db_core.services.property_steckbrief import adresszeile, steckbriefe

_EDITIERBAR = ("ENTWURF",)

# Positionsarten der BERICHTSposition (Migration 0080). Bewusst OHNE
# 'ZWISCHENSUMME' — der Bericht summiert nichts (er führt keine Beträge, und
# Mengen verschiedener Einheiten sind nicht summierbar).
BERICHT_LINE_TYPES = (
    "MATERIAL",
    "ARBEITSZEIT",
    "PAUSCHALE",
    "FREMDLEISTUNG",
    "FAHRT",
    "ZUSCHLAG",
    "TEXT",
)
TEXT_TYPE = "TEXT"
# Belegseitige Textarten, die kein Soll sind (Migration 0018).
BELEG_TEXT_TYPES = ("TEXT", "ZWISCHENSUMME")
# Nur summenwirksame Angebotspositionen sind ein Soll: ALTERNATIV (Ausweich-
# variante) und BEDARF (Eventualposition) wurden gerade NICHT beauftragt.
SUMMENWIRKSAM = "NORMAL"

# Welche Angebote bilden das Soll eines Auftrags?
#
# * ABGELEHNT / ERSETZT — tote Angebote: das abgelehnte wurde nie vereinbart, das
#   ersetzte trüge sein Soll doppelt (der Nachfolger trägt es bereits).
# * ENTWURF / INTERN_GEPRUEFT — noch nie hinausgegangen und damit keine
#   Vereinbarung. Sonst genügte es, neben ein versendetes Angebot einen zweiten
#   Entwurf zu legen, um das Soll der Baustelle zu verdoppeln.
#
# Es bleiben: FREIGEGEBEN, VERSENDET, ANGENOMMEN, ABGELAUFEN.
SOLL_AUSGESCHLOSSENE_STATUS = (
    "ENTWURF", "INTERN_GEPRUEFT", "ABGELEHNT", "ERSETZT",
)

# DB-Spaltenskala: quantity/planned_quantity numeric(15,3).
_Q_MENGE = Decimal("0.001")
_MAX_MENGE = Decimal("999999999999.999")

# Ergebnisarten des Soll-Ist-Abgleichs.
MEHRVERBRAUCH = "MEHRVERBRAUCH"
MINDERVERBRAUCH = "MINDERVERBRAUCH"
ZUSATZ = "ZUSATZ"
ENTFALLEN = "ENTFALLEN"
UNVERAENDERT = "UNVERAENDERT"


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
    # Die Briefkopf-Kette gleich mitladen (Befund B8) — sonst wäre `kopfdaten`
    # ein Dutzend Einzelabfragen je Bericht.
    return (
        SiteReport.objects.filter(id=report_id)
        .select_related(
            "author",
            "work_order__property__address",
            "work_order__building",
            "work_order__unit",
            "service_job__property__address",
            "service_job__building",
            "service_job__unit",
        )
        .first()
    )


def kopfdaten(report):
    """Briefkopf eines Baustellenberichts — Befund B3/B8 aus Runde 2.

    Der Bericht kannte seinen Auftrag bisher nur als UUID. Im PDF stand
    „Auftrag: <Titel>" und „Objekt: <Name · Stadt>" — kein Auftraggeber, keine
    Auftragsnummer, keine Straße, kein Mieter, keine Wohnungsnummer, kein
    Eigentümer. Sascha zu dem, was auf einem Bericht stehen muss: „halt das
    übliche Briefkopf-Gedöns", genau wie bei Angebot und Rechnung.

    Alle Angaben lagen bereits im Datenmodell und in fertigen Services; sie
    waren nur nie verdrahtet. Hier laufen sie zusammen.

    **Der freie Termin muss das aushalten** (`work_order_id` ist seit 0064
    nullable): Ein Begehungsprotokoll ohne Auftrag hat keinen Auftraggeber und
    keine Auftragsnummer. Dann bleiben die Felder schlicht leer, statt dass
    etwas erfunden wird — die Liegenschaft kommt in dem Fall über den Einsatz.

    **Kein Snapshot.** Anders als der Beleg friert der Bericht nichts ein
    (`site_report_pdf` sagt das ausdrücklich: „kein GoBD-Beleg mit
    eingefrorenem Stammdaten-Snapshot"). Ein Mieterwechsel ändert damit
    rückwirkend, was auf einem bereits unterschriebenen Bericht steht — das ist
    als Befund B9 notiert und bewusst noch nicht gelöst, weil es eine eigene
    Entscheidung über die Versiegelung ist.
    """
    leer = {
        "order_number": None, "order_title": None,
        "auftraggeber": None, "auftraggeber_adresse": None,
        "objekt_name": None, "objekt_nummer": None, "objekt_adresse": None,
        "gebaeude": None, "einheit": None, "etage": None,
        "mieter": [], "eigentuemer": [],
    }
    if report is None:
        return leer

    wo = report.work_order
    job = report.service_job
    # Die Liegenschaft kommt vom Auftrag; beim freien Termin vom Einsatz.
    prop = (wo.property if wo else None) or (job.property if job else None)
    building = (wo.building if wo else None) or (job.building if job else None)
    unit = (wo.unit if wo else None) or (job.unit if job else None)

    daten = dict(leer)
    if wo is not None:
        daten["order_number"] = wo.order_number
        daten["order_title"] = wo.title
        principal = (
            WorkOrderParty.objects.filter(work_order_id=wo.id, role="PRINCIPAL")
            .select_related("party")
            .order_by("-is_primary", "created_at")
            .first()
        )
        if principal is not None:
            daten["auftraggeber"] = principal.party.display_name
            zuordnung = (
                PartyAddress.objects.filter(
                    party_id=principal.party_id, valid_until__isnull=True
                )
                .select_related("address")
                .order_by("-is_primary", "address_type")
                .first()
            )
            if zuordnung is not None:
                daten["auftraggeber_adresse"] = adresszeile(zuordnung.address)

    if prop is not None:
        daten["objekt_name"] = prop.name
        daten["objekt_nummer"] = prop.property_number
        daten["objekt_adresse"] = adresszeile(prop.address)
        steckbrief = steckbriefe([prop.id]).get(prop.id)
        if steckbrief is not None:
            # `Steckbrief.eigentuemer` ist eine Liste von Anzeigenamen (Strings),
            # bereits dublettenfrei — siehe `property_steckbrief._bauen`.
            daten["eigentuemer"] = list(steckbrief.eigentuemer)

    if building is not None:
        daten["gebaeude"] = building.name or f"Gebäude {building.building_number}"
    if unit is not None:
        daten["einheit"] = unit.unit_number
        daten["etage"] = unit.storey
        # Mieter über die Belegung der Einheit — mehrere sind der Normalfall
        # (Ehepaar = zwei Beteiligte). COMMON_AREA/TECHNICAL_ROOM tragen laut
        # Trigger (F-12) gar keine Belegung; dann bleibt die Liste leer.
        heute = date.today()
        for belegung in (
            Occupancy.objects.filter(unit_id=unit.id)
            .filter(Q(valid_until__isnull=True) | Q(valid_until__gt=heute))
            .filter(valid_from__lte=heute)
            .prefetch_related("parties__party")
        ):
            for beteiligt in aktive_mieter(belegung, heute):
                name = beteiligt.party.display_name
                if name and name not in daten["mieter"]:
                    daten["mieter"].append(name)

    return daten


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


# ---------------------------------------------------------------------------
# Berichtspositionen (Migration 0080)
# ---------------------------------------------------------------------------

def list_report_lines(report_id):
    """Die Positionen eines Berichts in Positionsreihenfolge."""
    return SiteReportLine.objects.filter(site_report_id=report_id).order_by(
        "position_number"
    )


def _menge(wert, feld):
    """Eine Menge auf die DB-Spaltenskala numeric(15,3) bringen.

    Gerundet wird VOR der Bereichsprüfung — sonst bestünde 999999999999.9995 den
    Test und liefe in der DB in einen numerischen Überlauf (22003 → 500 statt 422).
    """
    if wert is None or (isinstance(wert, str) and not wert.strip()):
        return None
    try:
        m = Decimal(str(wert))
    except (InvalidOperation, ValueError):
        raise SiteReportError(f"{feld} ist keine gültige Zahl.")
    if not m.is_finite():
        raise SiteReportError(f"{feld} ist keine gültige Zahl.")
    m = m.quantize(_Q_MENGE, rounding=ROUND_HALF_UP)
    if m < 0:
        raise SiteReportError(f"{feld} darf nicht negativ sein.")
    if m > _MAX_MENGE:
        raise SiteReportError(f"{feld} ist zu groß.")
    return m


def _quelle(model, obj_id, label, *, aktiv_pflicht):
    """Herkunft aus dem Stamm: muss existieren — und bei NEUEN Verweisen AKTIV sein.

    Ein INAKTIV gesetzter Artikel ist ausgemustert: in einen Nachweis darf er nicht
    NEU hineinkommen. Führt der Bericht ihn aber bereits (etwa aus der Vorbelegung,
    und der Artikel wurde danach ausgemustert), bleibt die Position gültig — sie ist
    eine **Kopie**, kein Verweis. Sonst legte ein Stammdatenpflegevorgang jeden
    weiteren Speichervorgang des Monteurs lahm, auch wenn er eine ganz andere Zeile
    ändert (Sackgasse).

    **Eine** Query: das Objekt selbst ist der Existenzbeweis (ein zusätzliches
    `ensure_exists` liefe je Position ein zweites Mal in dieselbe Tabelle).
    """
    obj = model.objects.filter(pk=obj_id).first()
    if obj is None:
        # Wortlaut wie ensure_exists — die API übersetzt beides in 422.
        raise SiteReportError(f"{label} {obj_id} existiert nicht")
    if aktiv_pflicht and obj.status != "AKTIV":
        raise SiteReportError(f"{label} {obj_id} ist nicht aktiv.")
    return obj


def _bestandsquellen(report):
    """Die Herkunftsverweise, die der Bericht HEUTE schon führt.

    Nur was neu hinzukommt, wird gegen den Aktivstatus geprüft (siehe `_quelle`).
    """
    rows = SiteReportLine.objects.filter(site_report_id=report.id).values_list(
        "source_article_id", "source_assembly_id"
    )
    artikel = {str(a) for a, _b in rows if a}
    leistungen = {str(b) for _a, b in rows if b}
    return artikel, leistungen


def _angebote_des_auftrags(work_order):
    """Die Angebote, die das Soll eines Auftrags bilden.

    **Ausschließlich die dem Auftrag ZUGEORDNETEN Angebote** (`quote.work_order_id`).
    Kein Projekt-Fallback: bei einem Projekt mit mehreren Aufträgen wäre dasselbe
    Projektangebot das Soll *jedes* Auftrags, während das Ist nur aus dessen eigenen
    Berichten käme — jeder Auftrag zeigte dann einen frei erfundenen MINDERVERBRAUCH.
    Die Zuordnung ist die Aussage; sie wird im Angebot gesetzt
    (`beleg.create_quote`/`update_quote`, Feld `work_order_id`).

    Diese eine Definition benutzen `vorbelegen_aus_angebot` UND `soll_ist`: würden
    sie auseinanderlaufen, stünde eine vorbelegte Position im Abgleich plötzlich
    als ZUSATZ da.
    """
    return Quote.objects.filter(work_order_id=work_order.id).exclude(
        status__in=SOLL_AUSGESCHLOSSENE_STATUS
    )


def _erlaubte_angebote(report):
    """Die Angebots-IDs, aus denen dieser Bericht ein Soll übernehmen darf.

    Einmal je Speichervorgang ermittelt (nicht je Position) — sonst liefe die
    Prüfung als N+1 durch die Positionsliste.
    """
    if report.work_order_id is None:
        return set()
    return set(
        _angebote_des_auftrags(report.work_order).values_list("id", flat=True)
    )


def _quote_line_pruefen(quote_line_id, report, erlaubte_angebote, idx):
    """Die Herkunftsposition muss ein **Soll** sein — nicht irgendeine Belegzeile.

    Drei Bedingungen, jede aus einem konkreten Missbrauch heraus:

    * Sie muss zu einem Angebot **dieses Auftrags** gehören — sonst ließe sich ein
      fremdes Angebot als Soll unterschieben.
    * Sie muss **summenwirksam** sein (`line_kind = 'NORMAL'`). Eine ALTERNATIV-
      (Ausweichvariante) oder BEDARF-Position (Eventualposition) wurde gerade
      **nicht** vereinbart; ihre Menge als Soll zu übernehmen behauptete eine
      Vereinbarung, die es nie gab.
    * Sie darf **keine TEXT-/ZWISCHENSUMME-Zeile** sein: die trägt gar keine Menge
      (DB-CHECK auf `quote_line`). Das Soll wäre NULL — und die Berichtsposition
      führte eine Herkunft ohne Soll, also eine Zuordnung, die nichts aussagt.

    Damit ist die Menge der zulässigen Herkünfte **deckungsgleich** mit dem, was
    `vorbelegen_aus_angebot` und `soll_ist` als Soll rechnen (dieselben Filter
    `SUMMENWIRKSAM` / `BELEG_TEXT_TYPES`). Liefen sie auseinander, stünde eine von
    Hand gesetzte Herkunft im Abgleich plötzlich als ZUSATZ da.
    """
    ql = QuoteLine.objects.filter(id=quote_line_id).first()
    if ql is None:
        raise SiteReportError(f"Position {idx}: Die Angebotsposition existiert nicht.")
    if report.work_order_id is None:
        raise SiteReportError(
            f"Position {idx}: Der Bericht hängt an keinem Auftrag — er kann keine "
            "Angebotsposition als Soll führen."
        )
    if ql.quote_id not in erlaubte_angebote:
        raise SiteReportError(
            f"Position {idx}: Die Angebotsposition gehört nicht zu einem Angebot "
            "dieses Auftrags."
        )
    if ql.line_type in BELEG_TEXT_TYPES:
        raise SiteReportError(
            f"Position {idx}: Eine Text- oder Zwischensummenzeile des Angebots "
            "trägt keine Menge und ist deshalb kein Soll."
        )
    if ql.line_kind != SUMMENWIRKSAM:
        raise SiteReportError(
            f"Position {idx}: Die Angebotsposition ist eine "
            f"{ql.line_kind}-Position und wurde nicht beauftragt — sie ist kein Soll."
        )
    if ql.quantity is None:
        raise SiteReportError(
            f"Position {idx}: Die Angebotsposition trägt keine Menge und ist "
            "deshalb kein Soll."
        )
    if not (ql.unit or "").strip():
        # Ohne Einheit im Angebot gäbe es keine Einheit für die Berichtsposition:
        # sie wird aus der Quelle abgeleitet, und die DB verlangt sie (CHECK). Eine
        # vom Client mitgeschickte Einheit einzusetzen wäre die Rückkehr genau des
        # Fehlers, den die Ableitung behebt.
        raise SiteReportError(
            f"Position {idx}: Die Angebotsposition trägt keine Einheit — sie lässt "
            "sich nicht als Soll übernehmen. Bitte die Einheit im Angebot ergänzen."
        )
    return ql


def _herkunftstreue_pruefen(ql, *, artikel_id, leistung_id, einheit, beschreibung,
                            idx):
    """Die Identität einer Position mit Herkunft wird ABGELEITET, nicht behauptet.

    Der DB-Fremdschlüssel garantiert nur, DASS eine Angebotsposition referenziert
    ist — nicht, dass sie **dieselbe Sache** beschreibt. Ohne diese Prüfung ließe
    sich die Kessel-Position als Herkunft an eine Rohr-Zeile hängen: `planned_quantity`
    stünde dann als „angeboten: 500" neben *Rohr DN20* auf einem Dokument, das der
    Kunde unterschreibt und das danach versiegelt wird.

    Deshalb kommen **alle identitätsstiftenden Felder** aus der Angebotsposition:
    `source_article_id`, `source_assembly_id`, `unit`, `planned_quantity` — und die
    `description`. Schickt der Client abweichende Werte, wird das nicht
    stillschweigend überschrieben, sondern als Fachfehler abgewiesen: er meint etwas
    anderes, als er sagt (Muster wie `_anker`).

    **Auch die Bezeichnung ist eingefroren.** Sie war zunächst freigegeben („der
    Monteur darf präzisieren") — das war ein Loch: eine Zeile *Rohr DN20 · 5 Stk*
    mit der Kessel-Zeile als Herkunft trug die Sollmenge 500 des Kessels neben dem
    Rohr-Text, und kein Feld widersprach. Auf einem unterschriebenen, versiegelten
    Kundendokument stünde dann „Rohr DN20 · 5 Stk · angeboten 500". Die Präzisierung
    des Monteurs gehört deshalb in die **Notiz** (`note`) — sie ist frei und steht
    neben der Zeile, ohne die Identität zu verfälschen.

    Erst damit ist die Herkunftstreue **vollständig** prüfbar (Trigger 0083 prüft
    genau diese fünf Gleichungen): Ein weggelassenes Feld leitet sich aus der Quelle
    ab, ein abweichendes wird abgewiesen — es gibt keinen dritten Fall mehr.
    """
    def _gleich(client_wert, quell_wert):
        if client_wert is None:
            return True          # nicht angegeben ⇒ wird abgeleitet
        return str(client_wert) == str(quell_wert or "")

    if not _gleich(artikel_id, ql.source_article_id):
        raise SiteReportError(
            f"Position {idx}: Der Artikelbezug weicht von der Angebotsposition ab. "
            "Bei gesetzter Herkunft wird er aus dem Angebot übernommen."
        )
    if not _gleich(leistung_id, ql.source_assembly_id):
        raise SiteReportError(
            f"Position {idx}: Der Leistungsbezug weicht von der Angebotsposition ab. "
            "Bei gesetzter Herkunft wird er aus dem Angebot übernommen."
        )
    if einheit is not None and einheit.strip().lower() != (ql.unit or "").strip().lower():
        raise SiteReportError(
            f"Position {idx}: Die Einheit '{einheit}' weicht von der Einheit der "
            f"Angebotsposition ('{ql.unit}') ab. Bei gesetzter Herkunft wird sie aus "
            "dem Angebot übernommen."
        )
    if beschreibung is not None and beschreibung != (_clean(ql.description) or ""):
        raise SiteReportError(
            f"Position {idx}: Die Bezeichnung weicht von der Angebotsposition "
            f"('{ql.description}') ab. Die Bezeichnung einer angebotenen Position "
            "ist fest; Ergänzungen gehören in die Notiz."
        )


def _prepare_report_lines(lines, report):
    """Validiert die Positionen und normalisiert sie (1-basiert neu nummeriert).

    Läuft VOR der Transaktion, damit Eingabefehler als klare Meldung (422) enden
    und nicht als IntegrityError (500). Die Bezeichnung wird — wie bei der
    Belegposition — aus dem Stamm **kopiert**, nicht referenziert.
    """
    prepared = []
    erlaubte_angebote = None  # lazy: nur, wenn eine Position eine Herkunft nennt
    bestand_artikel, bestand_leistungen = _bestandsquellen(report)
    for idx, line in enumerate(lines or [], start=1):
        lt = line.get("line_type")
        if lt not in BERICHT_LINE_TYPES:
            raise SiteReportError(
                f"Position {idx}: ungültige Positionsart '{lt}' "
                f"(erlaubt: {', '.join(BERICHT_LINE_TYPES)})."
            )
        artikel_id = line.get("source_article_id") or None
        leistung_id = line.get("source_assembly_id") or None
        if artikel_id and leistung_id:
            raise SiteReportError(
                f"Position {idx}: Artikel und Leistung schließen sich aus."
            )
        quote_line_id = line.get("source_quote_line_id") or None

        if lt == TEXT_TYPE:
            # Eine Textzeile ist ein Kommentar im Nachweis: keine Menge, keine
            # Einheit (DB-CHECK), damit auch keine Herkunft und kein Soll.
            if any(
                line.get(f) not in (None, "")
                for f in ("quantity", "unit", "planned_quantity",
                          "source_quote_line_id")
            ) or artikel_id or leistung_id:
                raise SiteReportError(
                    f"Position {idx}: Eine Textzeile trägt weder Menge noch "
                    "Einheit, Herkunft oder Sollmenge."
                )
            beschreibung = _clean(line.get("description"))
            if not beschreibung:
                raise SiteReportError(
                    f"Position {idx}: Die Bezeichnung darf nicht leer sein."
                )
            prepared.append({
                "position_number": idx,
                "line_type": lt,
                "description": beschreibung,
                "note": _clean(line.get("note")),
                "source_article_id": None,
                "source_assembly_id": None,
                "quantity": None,
                "unit": None,
                "planned_quantity": None,
                "source_quote_line_id": None,
            })
            continue

        menge = _menge(line.get("quantity"), f"Position {idx}: Menge")
        if menge is None:
            raise SiteReportError(f"Position {idx}: Die Menge ist erforderlich.")

        if quote_line_id:
            # --- Position MIT Herkunft: die Identität wird ABGELEITET ------------
            #
            # Soll UND Identität kommen aus der Angebotsposition — nie vom Client.
            # Ein mitgeschicktes `planned_quantity` fliegt raus (sonst ließe sich
            # per `planned_quantity: 99` ein frei erfundenes Soll auf ein vom Kunden
            # unterschriebenes, versiegeltes Dokument schreiben). Artikel, Leistung,
            # Einheit UND Bezeichnung werden aus `ql` **kopiert**; weichen die
            # Client-Werte ab, ist das ein Fachfehler (siehe `_herkunftstreue_pruefen`).
            # Das gilt auch für ein **weggelassenes** Feld: es wird abgeleitet, nicht
            # geraten — sonst hinge an einer selbst getippten Zeile die Sollmenge einer
            # ganz anderen Angebotsposition.
            #
            # Das ist zugleich die Grundlage des Abgleichs: Soll- und Ist-Schlüssel
            # sind damit **per Konstruktion deckungsgleich**. Die Präzisierung des
            # Monteurs („Steigstrang, 2. OG") gehört in die **Notiz** — sie steht neben
            # der Zeile, ohne deren Identität zu verändern.
            if erlaubte_angebote is None:
                erlaubte_angebote = _erlaubte_angebote(report)
            ql = _quote_line_pruefen(quote_line_id, report, erlaubte_angebote, idx)
            _herkunftstreue_pruefen(
                ql, artikel_id=artikel_id, leistung_id=leistung_id,
                einheit=_clean(line.get("unit")),
                beschreibung=_clean(line.get("description")), idx=idx,
            )
            artikel_id = ql.source_article_id
            leistung_id = ql.source_assembly_id
            einheit = ql.unit          # `_quote_line_pruefen` garantiert: gesetzt
            soll = ql.quantity         # dito
            # Wortgleich aus der Quelle — nicht `_clean`-normalisiert: der Trigger
            # (0083) vergleicht zeichengenau gegen `quote_line.description`.
            beschreibung = ql.description
        else:
            # --- Position OHNE Herkunft: Stammdaten oder Freitext ----------------
            if line.get("planned_quantity") not in (None, ""):
                raise SiteReportError(
                    f"Position {idx}: Eine Sollmenge gibt es nur mit Herkunft aus "
                    "einer Angebotsposition — sie wird von dort übernommen und "
                    "kann nicht frei gesetzt werden."
                )
            soll = None
            artikel = leistung = None
            if artikel_id:
                artikel = _quelle(
                    Article, artikel_id, "Artikel",
                    aktiv_pflicht=str(artikel_id) not in bestand_artikel,
                )
            if leistung_id:
                leistung = _quelle(
                    Assembly, leistung_id, "Leistung",
                    aktiv_pflicht=str(leistung_id) not in bestand_leistungen,
                )
            # Bezeichnung/Einheit: Vorgabe des Aufrufers gewinnt, sonst KOPIE aus
            # dem Stamm. Verwiesen wird nie, damit ein späterer Stammtext den
            # unterschriebenen Nachweis nicht rückwirkend verändert.
            stamm_text = (
                artikel.description if artikel
                else (leistung.name if leistung else None)
            )
            stamm_einheit = (
                artikel.unit if artikel else (leistung.unit if leistung else None)
            )
            beschreibung = _clean(line.get("description")) or _clean(stamm_text)
            einheit = _clean(line.get("unit")) or _clean(stamm_einheit)
            if not einheit:
                raise SiteReportError(f"Position {idx}: Die Einheit ist erforderlich.")

        if not beschreibung:
            raise SiteReportError(
                f"Position {idx}: Die Bezeichnung darf nicht leer sein."
            )

        prepared.append({
            "position_number": idx,
            "line_type": lt,
            "description": beschreibung,
            "note": _clean(line.get("note")),
            "source_article_id": artikel_id,
            "source_assembly_id": leistung_id,
            "quantity": menge,
            "unit": einheit,
            "planned_quantity": soll,
            "source_quote_line_id": quote_line_id,
        })
    return prepared


def set_report_lines(actor_app_user_id, *, report_id, lines):
    """Ersetzt ALLE Positionen eines Berichts (Delete + Insert).

    Muster wie der Beleg-Editor (`beleg.update_quote`): der Aufrufer schickt immer
    den ganzen Positionssatz; ein Teil-Update einzelner Positionen wäre bei
    umsortierten Positionsnummern nicht eindeutig. Neu durchnummeriert wird
    1-basiert in Übergabereihenfolge.

    Nur im ENTWURF. Der Service prüft das — die letzte Instanz ist aber der Trigger
    `workflow.protect_site_report_lines`, der auch am Service vorbei greift.
    """
    report = SiteReport.objects.filter(id=report_id).first()
    if report is None:
        raise SiteReportError("Bericht nicht gefunden.")
    if report.status not in _EDITIERBAR:
        raise SiteReportError(
            "Der Bericht ist unterzeichnet — seine Positionen sind unveränderlich."
        )
    prepared = _prepare_report_lines(lines, report)

    with as_business_error():
        with business_transaction(actor_app_user_id):
            SiteReportLine.objects.filter(site_report_id=report.id).delete()
            for row in prepared:
                SiteReportLine.objects.create(
                    id=uuid.uuid4(), site_report_id=report.id, **row
                )
    return list_report_lines(report.id)


def angebote_zur_vorbelegung(report_id):
    """Die Angebote, aus denen dieser Bericht vorbelegt werden kann.

    Auswahlliste für das UI — **dieselbe** Definition wie `vorbelegen_aus_angebot`
    (`_angebote_des_auftrags`). Ein Bericht am freien Termin (ohne Auftrag) hat
    keine: leere Liste, kein Fehler.
    """
    report = SiteReport.objects.filter(id=report_id).select_related(
        "work_order"
    ).first()
    if report is None:
        raise SiteReportError("Bericht nicht gefunden.")
    if report.work_order_id is None:
        return []
    return list(
        _angebote_des_auftrags(report.work_order).order_by("-created_at", "id")
    )


def vorbelegen_aus_angebot(actor_app_user_id, *, report_id, quote_id):
    """Übernimmt die NORMAL-Positionen eines Angebots als Soll in den Bericht.

    Der Monteur startet damit nicht bei null: Ist = Soll, und er korrigiert nur die
    Abweichungen. `planned_quantity` friert die angebotene Menge ein.

    Tore:
    * Nur im ENTWURF (Trigger ist die letzte Instanz).
    * Nur in einen **leeren** Bericht — sonst überschriebe die Vorbelegung
      stillschweigend, was der Monteur vor Ort schon erfasst hat.
    * Nur Angebote, die **diesem Auftrag zugeordnet** sind (`quote.work_order_id`)
      und einen Status tragen, der eine Vereinbarung ist (nicht ENTWURF/
      INTERN_GEPRUEFT/ABGELEHNT/ERSETZT) — sonst ließe sich ein fremdes oder nie
      hinausgegangenes Angebot als Soll einschleusen.
    * Nur `line_kind = 'NORMAL'`: ALTERNATIV (Ausweichvariante) und BEDARF
      (Eventualposition) wurden gerade nicht beauftragt und sind kein Soll.
    * TEXT-/ZWISCHENSUMME-Zeilen werden übersprungen (sie tragen keine Menge).

    **Preise werden NICHT übernommen** — der Bericht führt keine (Migration 0080).
    """
    report = SiteReport.objects.filter(id=report_id).select_related(
        "work_order"
    ).first()
    if report is None:
        raise SiteReportError("Bericht nicht gefunden.")
    if report.status not in _EDITIERBAR:
        raise SiteReportError(
            "Der Bericht ist unterzeichnet — seine Positionen sind unveränderlich."
        )
    if report.work_order_id is None:
        raise SiteReportError(
            "Der Bericht hängt an keinem Auftrag (freier Termin) — es gibt kein "
            "Angebot, aus dem vorbelegt werden könnte."
        )
    if SiteReportLine.objects.filter(site_report_id=report.id).exists():
        raise SiteReportError(
            "Der Bericht führt bereits Positionen. Die Vorbelegung würde sie "
            "überschreiben und ist deshalb nur bei einem leeren Bericht möglich."
        )
    if not _angebote_des_auftrags(report.work_order).filter(id=quote_id).exists():
        raise SiteReportError(
            "Das Angebot gehört nicht zu diesem Auftrag."
        )

    quelle = (
        QuoteLine.objects.filter(quote_id=quote_id, line_kind=SUMMENWIRKSAM)
        .exclude(line_type__in=BELEG_TEXT_TYPES)
        .order_by("position_number")
    )
    rows = []
    for ql in quelle:
        if ql.quantity is None:
            continue
        if not (ql.unit or "").strip():
            # Keine Einheit erfinden. Eine Berichtsposition ohne Einheit wäre eine
            # Sackgasse: die DB nimmt sie nicht (CHECK), und der Monteur bekäme den
            # Bericht nie mehr gespeichert. Lieber hier klar sagen, was fehlt.
            raise SiteReportError(
                f"Angebotsposition {ql.position_number} ({ql.description}) trägt "
                "keine Einheit — sie lässt sich nicht in den Bericht übernehmen. "
                "Bitte die Einheit im Angebot ergänzen."
            )
        rows.append({
            "position_number": len(rows) + 1,
            "line_type": ql.line_type,
            "description": ql.description,
            "quantity": ql.quantity,
            "unit": ql.unit,
            "source_article_id": ql.source_article_id,
            "source_assembly_id": ql.source_assembly_id,
            # Ist startet gleich dem Soll — der Monteur korrigiert nur Abweichungen.
            "planned_quantity": ql.quantity,
            "source_quote_line_id": ql.id,
        })
    if not rows:
        raise SiteReportError(
            "Das Angebot enthält keine übernehmbaren Positionen "
            "(Alternativ-, Bedarfs- und Textzeilen sind kein Soll)."
        )

    with as_business_error():
        with business_transaction(actor_app_user_id):
            for row in rows:
                SiteReportLine.objects.create(
                    id=uuid.uuid4(), site_report_id=report.id, **row
                )
    return list_report_lines(report.id)


# ---------------------------------------------------------------------------
# Soll-Ist-Abgleich (Angebot gegen Bericht)
# ---------------------------------------------------------------------------

def _abgleich_key(article_id, assembly_id, description, unit):
    """Der Schlüssel, über den Soll- und Ist-Position zusammenfinden.

    **Aufgerufen wird er auf der IDENTITÄTSQUELLE der Zeile**: bei einer
    Berichtsposition mit Herkunft ist das die Angebotsposition selbst (nicht die
    bearbeitbare Kopie im Bericht), bei allen anderen die Zeile selbst. Siehe
    `soll_ist`.

    Der Artikel-/Leistungsbezug ist die belastbare Identität. Ohne ihn (Freitext,
    Handeingabe) bleibt nur die normalisierte Bezeichnung — unscharf, aber besser
    als jede Position einzeln als ZUSATZ/ENTFALLEN auszuweisen.

    **Die Einheit gehört in den Schlüssel.** „Montage" 3 h und „Montage" 1 psch sind
    nicht dieselbe Größe; verrechnete man sie, käme eine Differenz von -2 heraus, die
    nichts bedeutet. Bei Einheitenkonflikt stehen die Positionen deshalb getrennt da
    — zwei ehrliche Zeilen statt einer falschen Differenz.
    """
    einheit = (unit or "").strip().lower()
    if article_id:
        return ("ARTIKEL", str(article_id), einheit)
    if assembly_id:
        return ("LEISTUNG", str(assembly_id), einheit)
    return ("TEXT", (description or "").strip().lower(), einheit)


def abgleich(work_order, *, nur_unterzeichnet=False):
    """Der Soll-Ist-Abgleich — **die eine Rechenstelle**, roh (mit den Ist-Zeilen).

    `soll_ist` (Anzeige) und `abrechnung.nachtrag_*` (Geld) ziehen von hier. Liefen
    sie auseinander, wiese der Bildschirm einen MEHRVERBRAUCH aus, den der
    Nachtragslauf nicht fände — oder schlimmer: umgekehrt.

    Jede Position trägt zusätzlich `lines`: die **Berichtspositionen**, aus denen
    ihr Ist entstanden ist. Der Nachtrag braucht sie, weil die Abrechnungsbindung
    (`invoicing.billing_link`) auf die Berichtsposition zeigt — eine Bindung an den
    aggregierten Schlüssel gibt es nicht, und ohne Bindung wäre dieselbe Mehrmenge
    ein zweites Mal fakturierbar.

    `nur_unterzeichnet=True` rechnet das Ist **allein aus unterzeichneten
    Berichten**. Das ist die Abrechnungssicht: Ein nicht abgenommener Nachweis ist
    keine Abrechnungsgrundlage (dieselbe Grenze wie `abrechnung._berichtspositionen`).
    Die Anzeige (`soll_ist`) zeigt dagegen auch den Entwurfsstand — sie sagt dazu,
    dass er vorläufig ist (`enthaelt_entwuerfe`).
    """
    angebote = list(
        _angebote_des_auftrags(work_order).order_by("created_at", "id")
    )
    soll = OrderedDict()
    angebots_lines = (
        QuoteLine.objects.filter(
            quote_id__in=[q.id for q in angebote],
            line_kind=SUMMENWIRKSAM,
        )
        .exclude(line_type__in=BELEG_TEXT_TYPES)
        .order_by("quote__created_at", "position_number")
    )
    for ql in angebots_lines:
        key = _abgleich_key(
            ql.source_article_id, ql.source_assembly_id, ql.description, ql.unit
        )
        eintrag = soll.setdefault(
            key,
            {"bezeichnung": ql.description, "einheit": ql.unit,
             "menge": Decimal("0.000")},
        )
        eintrag["menge"] += ql.quantity or Decimal("0.000")

    ist = OrderedDict()
    enthaelt_entwuerfe = False
    bericht_lines = (
        SiteReportLine.objects.filter(site_report__work_order_id=work_order.id)
        .exclude(line_type=TEXT_TYPE)
        # source_quote_line wird mitgeladen: der Ist-Schlüssel einer Zeile MIT
        # Herkunft kommt aus der Quellzeile (sonst N+1 durch die Positionsliste).
        .select_related("site_report", "source_quote_line")
        .order_by("site_report__report_date", "position_number")
    )
    for bl in bericht_lines:
        unterzeichnet = bl.site_report.status == "UNTERZEICHNET"
        if not unterzeichnet:
            enthaelt_entwuerfe = True
            if nur_unterzeichnet:
                continue
        # **Trägt die Zeile eine Herkunft, bildet die QUELLZEILE den Schlüssel** —
        # nicht die (vom Monteur bearbeitbare) Kopie. Sonst fiele eine vorbelegte
        # Zeile, deren Text er präzisiert („Rohr" → „Rohr DN20, Steigstrang"), aus
        # dem Soll heraus: sie stünde als ZUSATZ da und das Angebots-Soll daneben
        # als ENTFALLEN — das Büro fakturierte die ganze Menge als Zusatzleistung
        # statt nur die Mehrmenge. Mit der Ableitung sind Soll- und Ist-Schlüssel
        # per Konstruktion deckungsgleich (dieselbe Zeile, dieselbe Funktion).
        # Nur Zeilen OHNE Herkunft fallen auf den eigenen Schlüssel zurück.
        ql = bl.source_quote_line
        ident = ql if ql is not None else bl
        key = _abgleich_key(
            ident.source_article_id, ident.source_assembly_id,
            ident.description, ident.unit,
        )
        eintrag = ist.setdefault(
            key,
            {"bezeichnung": ident.description, "einheit": ident.unit,
             "menge": Decimal("0.000"), "lines": []},
        )
        eintrag["menge"] += bl.quantity or Decimal("0.000")
        eintrag["lines"].append(bl)

    positionen = []
    for key in list(soll) + [k for k in ist if k not in soll]:
        s = soll.get(key)
        i = ist.get(key)
        soll_menge = s["menge"] if s else Decimal("0.000")
        ist_menge = i["menge"] if i else Decimal("0.000")
        differenz = ist_menge - soll_menge
        if s is None:
            art = ZUSATZ
        elif i is None:
            art = ENTFALLEN
        elif differenz > 0:
            art = MEHRVERBRAUCH
        elif differenz < 0:
            art = MINDERVERBRAUCH
        else:
            art = UNVERAENDERT
        quelle = s or i
        positionen.append({
            "schluessel": f"{key[0]}:{key[1]}:{key[2]}",
            "source_article_id": key[1] if key[0] == "ARTIKEL" else None,
            "source_assembly_id": key[1] if key[0] == "LEISTUNG" else None,
            "bezeichnung": quelle["bezeichnung"],
            "einheit": quelle["einheit"],
            "soll": soll_menge,
            "ist": ist_menge,
            "differenz": differenz,
            "art": art,
            "lines": i["lines"] if i else [],
        })

    return {
        "work_order_id": work_order.id,
        "positionen": positionen,
        # Worauf stützt sich das Soll? Der Nutzer muss die Grundlage sehen können —
        # sonst ist jede Differenz eine Behauptung.
        "angebote": [
            {"id": q.id, "quote_number": q.quote_number, "title": q.title,
             "status": q.status}
            for q in angebote
        ],
        "enthaelt_entwuerfe": enthaelt_entwuerfe,
    }


def abgleich_schluessel(article_id, assembly_id, description, unit):
    """Öffentlicher Zugang zum Abgleich-Schlüssel (Tupel).

    Die Abrechnung braucht ihn, um die **quellenunabhängige** Menge je Posten zu
    bilden: Dieselbe Sache (Artikel/Leistung + Einheit) kann über die Angebotskopie
    und über den Nachtrag fakturiert werden — beide müssen auf **denselben**
    Schlüssel abbilden, sonst überlappen die Quellen unbemerkt. Deshalb genau diese
    eine Funktion, nicht eine zweite Nachbildung.
    """
    return _abgleich_key(article_id, assembly_id, description, unit)


def soll_ist(work_order_id):
    """Vergleicht Angebots-Soll und Berichts-Ist über ALLE Berichte eines Auftrags.

    Reine Rechenarbeit, keine KI. **Keine Geldbeträge** — der Bericht führt keine
    Preise, also kann auch der Abgleich keine ausweisen.

    * **Soll** = Summe der Mengen der NORMAL-Positionen der **dem Auftrag
      zugeordneten** Angebote (`_angebote_des_auftrags`) — bewusst NICHT aus
      `planned_quantity` der Berichtspositionen: für eine angebotene, aber nie
      eingebaute Leistung gibt es gar keine Berichtsposition, der Fall ENTFALLEN
      fiele sonst komplett unter den Tisch.
    * **Ist** = Summe der Mengen aller Berichtspositionen des Auftrags. Trägt eine
      Berichtsposition eine **Herkunft** (`source_quote_line_id`), bildet die
      **Quell-Angebotszeile** ihren Schlüssel — nicht die Kopie im Bericht. Beide
      sind seit Trigger 0083 ohnehin wortgleich (Artikel, Leistung, Einheit,
      Bezeichnung sind eingefroren); der Rückgriff auf die Quelle bleibt trotzdem
      stehen: er ist die Definition, nicht bloß ihre Folge, und hält den Abgleich
      auch dann heil, wenn eine künftige Änderung ein Feld der Kopie wieder freigibt.
    * TEXT-/ZWISCHENSUMME-Zeilen bleiben auf beiden Seiten außen vor.

    `angebote` weist aus, **worauf sich das Soll stützt** (Nummer + Status). Ohne
    diese Angabe müsste der Nutzer raten, warum eine Zahl so dasteht.

    `enthaelt_entwuerfe` sagt, ob **unsignierte** Berichte eingeflossen sind. Dann
    ist das Ergebnis vorläufig — das wird ausgewiesen, nicht verschwiegen.

    Gerechnet wird in `abgleich` (eine Rechenstelle, auch für den Nachtrag); hier
    fallen nur die rohen Berichtszeilen (`lines`) wieder heraus — sie sind die
    **Bindungsgrundlage der Abrechnung**, keine Anzeigegröße.
    """
    work_order = WorkOrder.objects.filter(id=work_order_id).first()
    if work_order is None:
        raise SiteReportError("Auftrag nicht gefunden.")

    ergebnis = abgleich(work_order)
    ergebnis["positionen"] = [
        {k: v for k, v in pos.items() if k != "lines"}
        for pos in ergebnis["positionen"]
    ]
    return ergebnis
