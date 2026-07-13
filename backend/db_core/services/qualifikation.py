"""Qualifikationen und Zuweisungs-Vorlagen (Migration 0078).

Zwei User-Entscheidungen tragen dieses Modul:

**Qualifikationen sind STAMMDATEN, nicht Code.** Der Betrieb pflegt seinen
Katalog selbst — Gewerke, nachweispflichtige Befähigungen (Gasschein, Kälteschein
§ 5 ChemKlimaschutzV) und Herstellerschulungen (Viessmann, Vaillant) liegen in
DERSELBEN Tabelle und unterscheiden sich nur durch `kind`, einen **freien
Datenwert ohne CHECK**. Eine neue Schulungsart kostet damit keinen Deploy.

**Der Abgleich WARNT, er BLOCKIERT NICHT** — dieselbe weiche Invariante wie die
Doppelbelegung (Migration 0025). Es gibt keinen Trigger, der eine Zuweisung ohne
Nachweis verhindert: Sonst stünde der Notdienst am Sonntag vor einem gesperrten
Board, und die Disposition führte ihre Wahrheit wieder auf Papier. Der Service
macht die Lücke sichtbar; der Mensch entscheidet.

**Der wirksame Bedarf eines Termins ist die VEREINIGUNG** aus dem Bedarf seiner
Terminkategorie (der Regelfall: „Wartung Gastherme" braucht den Gasschein) und
dem Bedarf des einzelnen Termins (der Sonderfall am Objekt).
"""
import uuid
from zoneinfo import ZoneInfo

from django.db.models import Q
from django.utils import timezone

from db_core.db_context import business_transaction
from db_core.gate_errors import as_business_error
from db_core.models import (
    AppointmentCategory,
    AppointmentCategoryQualification,
    AppUser,
    AssignmentTemplate,
    AssignmentTemplateMember,
    Employee,
    EmployeeQualification,
    JobAssignment,
    Qualification,
    ServiceJob,
    ServiceJobQualification,
)
from db_core.services._validation import ensure_exists

ASSIGNMENT_ROLES = ("TECHNICIAN", "LEAD")

# Die Betriebszeitzone. Bewusst hier dupliziert statt aus `planung` importiert:
# Der Import baute einen Zyklus (planung ruft diesen Service für die
# Board-Warnungen). Beide meinen dasselbe — wer eine ändert, ändert beide.
BETRIEBS_TZ = ZoneInfo("Europe/Berlin")


def _stichtag(job):
    """Der Tag, gegen den Gültigkeit geprüft wird — der TERMINBEGINN in ORTSZEIT.

    Nicht „heute": Ein Nachweis, der bis März gilt, taugt nicht für einen Termin
    im Mai, und ein Gasschein, der erst nächste Woche erteilt wird, nicht für
    morgen.

    Und nicht der UTC-Tag: Ein Handwerkstermin ist eine Uhrzeit auf der WANDUHR.
    Ein Notdiensttermin am 01.05. um 01:00 Ortszeit ist in UTC noch der 30.04. —
    ein Nachweis, der am 30.04. abläuft, galt damit fälschlich noch, und der
    Monteur führe mit abgelaufenem Gasschein zur Gastherme. (Review-Fund.)

    Ohne Beginn (Termin im Rückstand) bleibt nur der heutige Tag — ein ungeplanter
    Termin hat keinen Zeitpunkt, gegen den sich Gültigkeit prüfen ließe.
    """
    if job.scheduled_start is not None:
        return job.scheduled_start.astimezone(BETRIEBS_TZ).date()
    return timezone.now().astimezone(BETRIEBS_TZ).date()


# ===========================================================================
# Katalog
# ===========================================================================

def _code_taken(code, *, exclude_id=None):
    qs = Qualification.objects.filter(code__iexact=code.strip())
    if exclude_id is not None:
        qs = qs.exclude(id=exclude_id)
    return qs.exists()


def create_qualification(
    actor_app_user_id, *, code, label, kind=None, description=None,
    expires=False, sort_order=0,
):
    """Legt eine Qualifikation an.

    `kind` ist eine **freie Gruppierung** (GEWERK | ZERTIFIKAT |
    HERSTELLERSCHULUNG | …) — der Betrieb legt seine Arten selbst an; es gibt
    bewusst keine Codeliste im Code.
    """
    if not (code or "").strip():
        raise ValueError("code darf nicht leer sein.")
    if not (label or "").strip():
        raise ValueError("label darf nicht leer sein.")
    if _code_taken(code):
        raise ValueError(f"Eine Qualifikation '{code.strip()}' existiert bereits.")
    with as_business_error():
        with business_transaction(actor_app_user_id):
            q = Qualification.objects.create(
                id=uuid.uuid4(),
                code=code.strip(),
                label=label.strip(),
                kind=((kind or "").strip() or None),
                description=((description or "").strip() or None),
                expires=bool(expires),
                active=True,
                sort_order=sort_order or 0,
                created_by_id=actor_app_user_id,
                version=1,
            )
            q.refresh_from_db()
    return q


def update_qualification(
    actor_app_user_id, *, qualification_id, label=None, kind=None,
    description=None, expires=None, active=None, sort_order=None,
):
    """Ändert eine Qualifikation.

    Der `code` bleibt unveränderlich — er ist der fachliche Schlüssel, auf den
    Bedarf und Nachweise zeigen.

    **`expires` von false auf true zu setzen, ist abgesichert:** Es gibt
    bestehende Nachweise ohne Gültig-bis; die wären dann schlagartig regelwidrig.
    Der Wechsel wird abgelehnt, solange solche Zeilen existieren — sonst wäre der
    DB-Trigger beim nächsten Speichern eines unbeteiligten Feldes plötzlich im
    Weg, und niemand verstünde warum.
    """
    q = Qualification.objects.filter(id=qualification_id).first()
    if q is None:
        raise ValueError("Qualifikation nicht gefunden.")

    felder = {}
    if label is not None:
        if not label.strip():
            raise ValueError("label darf nicht leer sein.")
        felder["label"] = label.strip()
    if kind is not None:
        felder["kind"] = kind.strip() or None
    if description is not None:
        felder["description"] = description.strip() or None
    if sort_order is not None:
        felder["sort_order"] = sort_order
    if active is not None:
        felder["active"] = bool(active)
    umstellung_auf_ablauf = (
        expires is not None and bool(expires) and not q.expires
    )
    if expires is not None:
        felder["expires"] = bool(expires)

    if not felder:
        return q
    with as_business_error():
        with business_transaction(actor_app_user_id):
            if umstellung_auf_ablauf:
                # Die Katalogzeile SPERREN, bevor wir die Bestandszeilen zählen.
                # Sonst legt eine parallele Transaktion zeitgleich einen Nachweis
                # ohne Gültig-bis an (ihr Trigger liest in seinem Snapshot noch
                # `expires = false`), und beide committen — die verbotene Zeile
                # existiert. Der Trigger auf `employee_qualification` nimmt
                # dieselbe Zeile FOR SHARE; damit serialisieren die beiden Wege
                # gegeneinander. Die DB weist es zusätzlich physisch ab
                # (`hr.enforce_qualification_expires`) — der Zähler hier liefert
                # nur die freundlichere Meldung.
                gesperrt = (
                    Qualification.objects.select_for_update()
                    .filter(id=q.id)
                    .first()
                )
                if gesperrt is None:
                    raise ValueError("Qualifikation nicht gefunden.")
                offen = EmployeeQualification.objects.filter(
                    qualification_id=q.id, valid_until__isnull=True
                ).count()
                if offen:
                    raise ValueError(
                        f"{offen} Mitarbeiter tragen diesen Nachweis ohne "
                        "Gültig-bis. Trage dort zuerst ein Ablaufdatum nach, dann "
                        "lässt sich die Qualifikation auf ablaufpflichtig umstellen."
                    )
            Qualification.objects.filter(id=q.id).update(**felder)
    q.refresh_from_db()
    return q


# ===========================================================================
# Nachweise am Mitarbeiter
# ===========================================================================

def set_employee_qualification(
    actor_app_user_id, *, employee_id, qualification_id,
    valid_from=None, valid_until=None, evidence_note=None,
):
    """Trägt einen Nachweis ein oder schreibt ihn fort (Verlängerung).

    **Eine Zeile je (Mitarbeiter, Qualifikation)** — eine Verlängerung schreibt
    `valid_until` fort, statt eine zweite Zeile anzulegen. Sonst wäre „gültig?"
    mehrdeutig (welche Zeile gilt?), und genau diese Frage stellt die Plantafel.

    Ist die Qualifikation ablaufpflichtig (`expires`), verlangt die DB ein
    Gültig-bis; hier kommt der Fehler schon als klare Meldung (422).
    """
    ensure_exists(Employee, employee_id, "Mitarbeiter")
    q = Qualification.objects.filter(id=qualification_id).first()
    if q is None:
        raise ValueError("Qualifikation nicht gefunden.")
    if not q.active:
        raise ValueError(
            f"Die Qualifikation '{q.label}' ist stillgelegt und kann nicht mehr "
            "zugeordnet werden."
        )
    if q.expires and valid_until is None:
        raise ValueError(
            f"'{q.label}' ist ablaufpflichtig — ein Gültig-bis ist Pflicht."
        )
    if valid_from and valid_until and valid_until < valid_from:
        raise ValueError("Das Gültig-bis darf nicht vor dem Gültig-ab liegen.")

    with as_business_error():
        with business_transaction(actor_app_user_id):
            vorhanden = EmployeeQualification.objects.filter(
                employee_id=employee_id, qualification_id=qualification_id
            ).first()
            if vorhanden is None:
                EmployeeQualification.objects.create(
                    id=uuid.uuid4(),
                    employee_id=employee_id,
                    qualification_id=qualification_id,
                    valid_from=valid_from,
                    valid_until=valid_until,
                    evidence_note=((evidence_note or "").strip() or None),
                    created_by_id=actor_app_user_id,
                    version=1,
                )
            else:
                EmployeeQualification.objects.filter(id=vorhanden.id).update(
                    valid_from=valid_from,
                    valid_until=valid_until,
                    evidence_note=((evidence_note or "").strip() or None),
                )
    return EmployeeQualification.objects.get(
        employee_id=employee_id, qualification_id=qualification_id
    )


def remove_employee_qualification(
    actor_app_user_id, *, employee_id, qualification_id
):
    """Entfernt einen Nachweis (Fehleintrag).

    Bewusst löschbar: Ein falsch gesetzter Haken ist kein Geschäftsvorfall, und
    eine Karteileiche „Gasschein, aber eigentlich nicht" wäre gefährlicher als
    die Löschung. Die Änderung steht im Audit-Log.
    """
    with as_business_error():
        with business_transaction(actor_app_user_id):
            EmployeeQualification.objects.filter(
                employee_id=employee_id, qualification_id=qualification_id
            ).delete()


def employee_qualifications(employee_id):
    return list(
        EmployeeQualification.objects.filter(employee_id=employee_id)
        .select_related("qualification")
        .order_by("qualification__sort_order", "qualification__label")
    )


# ===========================================================================
# Bedarf (Terminkategorie + einzelner Termin)
# ===========================================================================

def set_category_qualifications(
    actor_app_user_id, *, category_id, qualification_ids
):
    """Setzt den Bedarf einer Terminkategorie neu (Vollersetzung)."""
    ensure_exists(AppointmentCategory, category_id, "Terminkategorie")
    ids = _pruefe_qualifikationen(qualification_ids)
    with as_business_error():
        with business_transaction(actor_app_user_id):
            AppointmentCategoryQualification.objects.filter(
                appointment_category_id=category_id
            ).delete()
            for qid in ids:
                AppointmentCategoryQualification.objects.create(
                    id=uuid.uuid4(),
                    appointment_category_id=category_id,
                    qualification_id=qid,
                    created_by_id=actor_app_user_id,
                )
    return category_qualifications(category_id)


def set_job_qualifications(actor_app_user_id, *, service_job_id, qualification_ids):
    """Setzt den ZUSÄTZLICHEN Bedarf eines einzelnen Termins neu (Vollersetzung).

    Der Bedarf der Kategorie bleibt davon unberührt — der wirksame Bedarf ist die
    Vereinigung beider (`bedarf`).
    """
    ensure_exists(ServiceJob, service_job_id, "Einsatz")
    ids = _pruefe_qualifikationen(qualification_ids)
    with as_business_error():
        with business_transaction(actor_app_user_id):
            ServiceJobQualification.objects.filter(
                service_job_id=service_job_id
            ).delete()
            for qid in ids:
                ServiceJobQualification.objects.create(
                    id=uuid.uuid4(),
                    service_job_id=service_job_id,
                    qualification_id=qid,
                    created_by_id=actor_app_user_id,
                )
    return job_qualifications(service_job_id)


def _pruefe_qualifikationen(qualification_ids):
    """Prüft die IDs vorab (unbekannt/stillgelegt → 422 statt IntegrityError)."""
    ids = list(dict.fromkeys(qualification_ids or []))
    if not ids:
        return []
    gefunden = {
        q.id: q for q in Qualification.objects.filter(id__in=ids)
    }
    for qid in ids:
        q = gefunden.get(qid)
        if q is None:
            raise ValueError(f"Qualifikation {qid} existiert nicht.")
        if not q.active:
            raise ValueError(
                f"Die Qualifikation '{q.label}' ist stillgelegt und kann nicht "
                "mehr gefordert werden."
            )
    return ids


def category_qualifications(category_id):
    return list(
        Qualification.objects.filter(
            category_links__appointment_category_id=category_id
        ).order_by("sort_order", "label")
    )


def job_qualifications(service_job_id):
    return list(
        Qualification.objects.filter(
            job_links__service_job_id=service_job_id
        ).order_by("sort_order", "label")
    )


def bedarf(job):
    """Der WIRKSAME Bedarf eines Termins: Kategorie **vereinigt mit** Termin.

    Die Kategorie trägt den Regelfall, der Termin den Sonderfall. Die Vereinigung
    ist die einzige Auslegung, die nicht überrascht: Wer am Termin eine
    Zusatzqualifikation fordert, will die der Kategorie nicht abwählen.
    """
    return list(
        Qualification.objects.filter(
            Q(job_links__service_job_id=job.id)
            | Q(category_links__appointment_category_id=job.appointment_category_id)
        )
        .distinct()
        .order_by("sort_order", "label")
    )


# ===========================================================================
# Der Abgleich — WARNT, blockiert nicht
# ===========================================================================

def warnungen_fuer_jobs(jobs):
    """`{job_id: [{kind, text}]}` — der Abgleich für VIELE Termine auf einmal.

    Das Board zeigt bis zu vierzig Kacheln; ein Aufruf je Kachel wäre ein N+1 im
    heißesten Lesepfad des Produkts. Diese Fassung braucht **drei Abfragen
    insgesamt** (Kategoriebedarf, Terminbedarf, Nachweise).

    Erwartet Termine, deren `assignments` schon geladen sind (das Board
    prefetcht sie ohnehin).
    """
    jobs = list(jobs)
    if not jobs:
        return {}

    kategorien = {j.appointment_category_id for j in jobs if j.appointment_category_id}
    job_ids = [j.id for j in jobs]

    # 1) Bedarf der Kategorien.
    kat_bedarf = {}
    if kategorien:
        for link in AppointmentCategoryQualification.objects.filter(
            appointment_category_id__in=kategorien
        ).select_related("qualification"):
            kat_bedarf.setdefault(link.appointment_category_id, []).append(
                link.qualification
            )

    # 2) Zusatzbedarf der einzelnen Termine.
    job_bedarf = {}
    for link in ServiceJobQualification.objects.filter(
        service_job_id__in=job_ids
    ).select_related("qualification"):
        job_bedarf.setdefault(link.service_job_id, []).append(link.qualification)

    # 3) Nachweise aller zugewiesenen Mitarbeiter.
    user_ids = {a.assignee_id for j in jobs for a in j.assignments.all()}
    nachweise = {}
    if user_ids:
        for eq in EmployeeQualification.objects.filter(
            employee__app_user_id__in=user_ids
        ).select_related("employee"):
            nachweise.setdefault(eq.employee.app_user_id, {})[eq.qualification_id] = eq

    ergebnis = {}
    for j in jobs:
        # Wirksamer Bedarf = Kategorie VEREINIGT mit Termin.
        gefordert = {
            q.id: q
            for q in kat_bedarf.get(j.appointment_category_id, [])
            + job_bedarf.get(j.id, [])
        }
        if not gefordert:
            continue
        zuweisungen = list(j.assignments.all())
        if not zuweisungen:
            continue
        stichtag = _stichtag(j)
        for a in zuweisungen:
            eigene = nachweise.get(a.assignee_id, {})
            name = a.assignee.display_name
            for q in sorted(gefordert.values(), key=lambda x: (x.sort_order, x.label)):
                eq = eigene.get(q.id)
                # DATENSCHUTZ: Der Text nennt die FOLGE, nicht den Akteninhalt.
                # Das Board hängt an `workflow`/LESEN — ein Disponent OHNE
                # hr-Recht darf hier nicht das exakte Gültig-bis aus der
                # Personalakte erfahren (genau den Feldwert, den ihm
                # GET /planung/mitarbeiter/{id}/qualifikationen mit 403
                # verweigert). Dieselbe Grenze wie bei der Abwesenheitsart:
                # „abwesend, von–bis" ja, „warum" nein.
                if eq is None:
                    text = f"{name} hat keinen Nachweis für „{q.label}“."
                elif eq.valid_until is not None and eq.valid_until < stichtag:
                    text = (
                        f"„{q.label}“ von {name} ist zum Terminzeitpunkt "
                        "abgelaufen."
                    )
                elif eq.valid_from is not None and eq.valid_from > stichtag:
                    text = (
                        f"„{q.label}“ von {name} gilt zum Terminzeitpunkt noch "
                        "nicht."
                    )
                else:
                    continue
                ergebnis.setdefault(j.id, []).append(
                    {"kind": "QUALIFIKATION", "text": text}
                )
    return ergebnis


def qualifikations_warnungen(service_job_id):
    """Fehlende oder abgelaufene Nachweise EINES Termins.

    Liste von `{kind, text}` — `kind` ist immer `QUALIFIKATION`, damit die
    Plantafel sie wie jeden anderen weichen Konflikt behandelt (Text UND Symbol,
    nie nur Farbe).

    **Blockiert nichts** (weiche Invariante, siehe Modul-Docstring).
    """
    job = (
        ServiceJob.objects.filter(id=service_job_id)
        .select_related("appointment_category")
        .prefetch_related("assignments__assignee")
        .first()
    )
    if job is None:
        return []
    return warnungen_fuer_jobs([job]).get(job.id, [])


# BEWUSST NICHT GEBAUT: eine Funktion „wer passt auf diesen Termin?", die ALLE
# Mitarbeiter gegen den Bedarf prüft. Sie wäre inhaltlich die Personalakte (wer
# hat welchen Schein) und hinge an einem `workflow`-Endpunkt — genau der
# Datenschutz-Leak, den die Board-Warnung oben vermeidet. Der Dialog blendet
# ohnehin niemanden aus: Die Entscheidung gehört dem Disponenten (Notdienst,
# Einarbeitung, „fährt heute mit dem Meister mit"), er bekommt nur die Warnung.


# ===========================================================================
# Zuweisungs-Vorlagen (lose Gruppen)
# ===========================================================================

def create_template(actor_app_user_id, *, name, description=None, members=(),
                    sort_order=0):
    """Legt eine Zuweisungs-Vorlage an (benannte Personengruppe).

    `members` ist eine Liste von `{app_user_id, role}` (role: TECHNICIAN | LEAD).
    Die Vorlage ist ein **Vorschlag** — sie bindet keinen Termin.
    """
    if not (name or "").strip():
        raise ValueError("name darf nicht leer sein.")
    if AssignmentTemplate.objects.filter(active=True, name__iexact=name.strip()).exists():
        raise ValueError(f"Eine Vorlage '{name.strip()}' existiert bereits.")
    normiert = _pruefe_mitglieder(members)
    with as_business_error():
        with business_transaction(actor_app_user_id):
            t = AssignmentTemplate.objects.create(
                id=uuid.uuid4(),
                name=name.strip(),
                description=((description or "").strip() or None),
                active=True,
                sort_order=sort_order or 0,
                created_by_id=actor_app_user_id,
                version=1,
            )
            _mitglieder_schreiben(actor_app_user_id, t.id, normiert)
            t.refresh_from_db()
    return t


def update_template(
    actor_app_user_id, *, template_id, name=None, description=None,
    members=None, active=None, sort_order=None,
):
    """Ändert eine Vorlage. `members=None` heißt „Mitglieder nicht anfassen"."""
    t = AssignmentTemplate.objects.filter(id=template_id).first()
    if t is None:
        raise ValueError("Vorlage nicht gefunden.")
    felder = {}
    if name is not None:
        if not name.strip():
            raise ValueError("name darf nicht leer sein.")
        if (
            AssignmentTemplate.objects.filter(active=True, name__iexact=name.strip())
            .exclude(id=template_id)
            .exists()
        ):
            raise ValueError(f"Eine Vorlage '{name.strip()}' existiert bereits.")
        felder["name"] = name.strip()
    if description is not None:
        felder["description"] = description.strip() or None
    if active is not None:
        felder["active"] = bool(active)
    if sort_order is not None:
        felder["sort_order"] = sort_order

    normiert = None if members is None else _pruefe_mitglieder(members)
    with as_business_error():
        with business_transaction(actor_app_user_id):
            if felder:
                AssignmentTemplate.objects.filter(id=template_id).update(**felder)
            if normiert is not None:
                AssignmentTemplateMember.objects.filter(
                    template_id=template_id
                ).delete()
                _mitglieder_schreiben(actor_app_user_id, template_id, normiert)
    t.refresh_from_db()
    return t


def _pruefe_mitglieder(members):
    normiert = []
    gesehen = set()
    for m in members or ():
        uid = m.get("app_user_id") if isinstance(m, dict) else m
        rolle = (m.get("role") if isinstance(m, dict) else None) or "TECHNICIAN"
        if rolle not in ASSIGNMENT_ROLES:
            raise ValueError(
                f"Ungültige Rolle '{rolle}'. Erlaubt: {', '.join(ASSIGNMENT_ROLES)}."
            )
        if uid in gesehen:
            continue
        u = AppUser.objects.filter(id=uid).first()
        if u is None:
            raise ValueError(f"Mitarbeiter {uid} existiert nicht.")
        if u.status != "ACTIVE":
            raise ValueError(
                f"{u.display_name} ist nicht aktiv und kann nicht in eine Vorlage."
            )
        gesehen.add(uid)
        normiert.append({"app_user_id": uid, "role": rolle})
    return normiert


def _mitglieder_schreiben(actor_app_user_id, template_id, normiert):
    for m in normiert:
        AssignmentTemplateMember.objects.create(
            id=uuid.uuid4(),
            template_id=template_id,
            assignee_id=m["app_user_id"],
            role=m["role"],
            created_by_id=actor_app_user_id,
        )


def templates(*, include_inactive=False):
    qs = AssignmentTemplate.objects.all()
    if not include_inactive:
        qs = qs.filter(active=True)
    return list(
        qs.prefetch_related("members__assignee").order_by("sort_order", "name", "id")
    )
