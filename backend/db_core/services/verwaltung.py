"""Das Verwaltungsmandat — `management.management_mandate` (+ Einheiten, Zuständige).

## Die fachliche Kernaussage dieses Moduls

**Die Verwaltung ist KEINE Beteiligtenrolle an der Liegenschaft.**
`property.property_party_role` kennt nur COMMUNITY_OF_OWNERS, PROPERTY_OWNER,
OPERATOR und CARETAKER — und der Kommentar in `0004_property.sql` sagt wörtlich:
„Die Verwaltung wird ausschließlich über ein Mandat verbunden."

Das ist keine Formalie. Im Demo-Szenario gehört die Badensche Straße 53 der
**WEG Badensche Straße 53** (Rolle COMMUNITY_OF_OWNERS — *sie* beauftragt und
zahlt), verwaltet wird sie von der **Stegos Immobilien GmbH** (Mandat — *sie* ist
der Ansprechpartner). Wer beides in dieselbe Rollenliste wirft, verliert genau die
Unterscheidung, die bei der Rechnung scharf wird: **wer beauftragt, wer zahlt, wer
den Beleg bekommt** (`PRINCIPAL` / `INVOICE_DEBTOR` / `INVOICE_RECIPIENT`).

## Was die DATENBANK durchsetzt (dieser Service prüft nur vor)

| Regel | Ort |
|---|---|
| `ENTIRE_PROPERTY` hat **keine** Mandatseinheiten | deferred Trigger `assert_mandate_valid` |
| `SELECTED_UNITS` hat **mindestens eine** | derselbe Trigger |
| Kein Zeitraumkonflikt desselben Mandatstyps (Voll- gegen Teilmandat, Teil- gegen Teilmandat) | derselbe Trigger + `excl_mandate_entire` |
| Mandat und Einheit gehören zur **selben Liegenschaft** | zusammengesetzter FK |
| **Standardkontakt ist Pflicht** (A-10) | `NOT NULL` |
| Verwalter ≠ Auftraggeber | CHECK |
| Ein beendetes Mandat hat immer ein Enddatum | CHECK |
| Keine MERGED-Party | Trigger (0009) |
| **Kein Löschen**; Mandatseinheiten sogar **immutable** | Trigger (0009) |

Die Trigger sind **DEFERRED** — Mandat und Einheiten entstehen deshalb in
**einer** Transaktion, und der Verstoß schlägt erst beim COMMIT auf. Genau dafür
muss `business_transaction` **innerhalb** von `as_business_error()` liegen (der
Kontextmanager sagt es ausdrücklich): Sonst käme der Constraint-Fehler erst nach
dem Verlassen des Fängers und würde zum 500 statt zum 422.

## Der Umfang eines laufenden Mandats ist unveränderlich

`management_mandate_unit` trägt seit 0009 `trg_mandate_unit_immutable` — UPDATE
**und** DELETE sind verboten (A-11). Es gibt hier deshalb bewusst **keine**
Funktion „Einheit zum Mandat hinzufügen/entfernen". Wer den Umfang ändern will,
**beendet das Mandat und legt ein Nachfolgemandat an**. Das ist keine Härte,
sondern die Entscheidung des Schemas: Ein Verwaltungsvertrag, dessen Umfang sich
rückwirkend still ändert, ist kein Vertrag.
"""
import uuid
from datetime import date

from django.db.models import Prefetch, Q

from db_core.db_context import business_transaction
from db_core.gate_errors import as_business_error
from db_core.models import (
    ManagementMandate,
    ManagementMandateUnit,
    ManagementResponsibility,
    Property,
    Unit,
)
from db_core.services._validation import ensure_exists, ensure_party_usable

#: Codeliste `management_mandate.mandate_type` (CHECK aus 0006).
MANDATE_TYPES = (
    "WEG_MANAGEMENT",
    "RENTAL_MANAGEMENT",
    "SPECIAL_PROPERTY_MANAGEMENT",
    "SPECIAL_MANDATE",
)

#: Codeliste `management_mandate.scope_type` (CHECK aus 0006).
SCOPE_TYPES = ("ENTIRE_PROPERTY", "SELECTED_UNITS")

#: Codeliste `management_responsibility.responsibility_type` (CHECK aus 0006).
RESPONSIBILITY_TYPES = (
    "TECHNICAL_CONTACT",
    "COMMERCIAL_CONTACT",
    "ACCOUNTING_CONTACT",
    "EMERGENCY_CONTACT",
    "APPROVER",
)


# ---------------------------------------------------------------------------
# Lesen
# ---------------------------------------------------------------------------

def _aktiv_q(stichtag):
    return Q(valid_from__lte=stichtag) & (
        Q(valid_until__isnull=True) | Q(valid_until__gt=stichtag)
    )


def _basis_qs():
    return ManagementMandate.objects.select_related(
        "management_party", "principal_party", "default_contact_party"
    ).prefetch_related(
        Prefetch(
            "mandate_units",
            queryset=ManagementMandateUnit.objects.select_related("unit").order_by(
                "unit__unit_number"
            ),
        ),
        Prefetch(
            "responsibilities",
            queryset=ManagementResponsibility.objects.select_related(
                "responsible_party"
            ).order_by("priority", "responsibility_type"),
        ),
    )


def mandate_der_liegenschaft(property_id, *, nur_aktive=True, stichtag=None):
    """Die Mandate einer Liegenschaft — standardmäßig **nur die geltenden**.

    „Geltend" ist mehr als `status='ACTIVE'`: Ein Mandat mit einem in der
    Vergangenheit liegenden `valid_until` gilt nicht mehr, auch wenn niemand den
    Status nachgezogen hat. Der Stichtag entscheidet, nicht das Statusfeld —
    sonst zeigte die Mappe einen Verwalter an, der seit einem Jahr weg ist.
    """
    stichtag = stichtag or date.today()
    qs = _basis_qs().filter(property_id=property_id)
    if nur_aktive:
        qs = qs.filter(_aktiv_q(stichtag), status="ACTIVE")
    return list(qs.order_by("-valid_from"))


def get_mandat(mandate_id):
    return _basis_qs().filter(pk=mandate_id).first()


def property_id_des_mandats(mandate_id):
    """Die Liegenschaft hinter einem Mandat — für die Objektgrenze (404)."""
    return (
        ManagementMandate.objects.filter(pk=mandate_id)
        .values_list("property_id", flat=True)
        .first()
    )


def mandate_id_der_zustaendigkeit(responsibility_id):
    """Das Mandat hinter einer Zuständigkeit — für die Objektgrenze (404)."""
    return (
        ManagementResponsibility.objects.filter(pk=responsibility_id)
        .values_list("mandate_id", flat=True)
        .first()
    )


def aktive_zustaendigkeiten(mandat, stichtag=None):
    """Die am Stichtag geltenden Zuständigkeiten (auf dem Prefetch, kein N+1)."""
    stichtag = stichtag or date.today()
    return [
        r
        for r in mandat.responsibilities.all()
        if r.valid_from <= stichtag
        and (r.valid_until is None or r.valid_until > stichtag)
    ]


# ---------------------------------------------------------------------------
# Schreiben
# ---------------------------------------------------------------------------

def create_mandat(
    actor_app_user_id,
    *,
    property_id,
    management_party_id,
    principal_party_id,
    default_contact_party_id,
    mandate_type,
    scope_type,
    valid_from,
    valid_until=None,
    contract_reference=None,
    unit_ids=None,
):
    """Ein Verwaltungsmandat anlegen (Mandat + Mandatseinheiten in EINER Transaktion).

    Die Scope-Regeln stehen in **DEFERRED Constraint-Triggern** — sie prüfen beim
    COMMIT, nicht beim INSERT. Deshalb ist es überhaupt möglich, ein
    SELECTED_UNITS-Mandat anzulegen (das im Moment des INSERT noch keine Einheit
    hat). Und deshalb liegt `business_transaction` **innerhalb** von
    `as_business_error()`: Der Trigger feuert am Transaktionsende.

    Die Vorprüfungen hier erzeugen die lesbare Meldung; die **Entscheidung**
    trifft die DB. Ein `ENTIRE_PROPERTY` mit Einheitenliste und ein
    `SELECTED_UNITS` ohne Einheiten scheitern **beide** — auch wenn dieser Service
    umgangen würde.
    """
    if mandate_type not in MANDATE_TYPES:
        raise ValueError(
            f"Ungültige Mandatsart '{mandate_type}'. "
            f"Erlaubt: {', '.join(MANDATE_TYPES)}."
        )
    if scope_type not in SCOPE_TYPES:
        raise ValueError(
            f"Ungültiger Umfang '{scope_type}'. Erlaubt: {', '.join(SCOPE_TYPES)}."
        )
    if valid_until is not None and valid_until <= valid_from:
        raise ValueError(
            "Das Gültig-bis-Datum muss nach dem Gültig-ab-Datum liegen."
        )

    ensure_exists(Property, property_id, "Liegenschaft")
    # Alle drei Parteien: existent und nicht MERGED (trg_mandate_no_merged).
    ensure_party_usable(management_party_id, "Verwaltung")
    ensure_party_usable(principal_party_id, "Auftraggeber")
    # A-10: Pflicht — ein Mandat ohne Ansprechpartner ist eine Nummer, die
    # niemand hat. Die DB sagt NOT NULL; hier wird daraus eine Meldung.
    if default_contact_party_id is None:
        raise ValueError(
            "Ein Mandat braucht einen Standardkontakt (Beschluss A-10)."
        )
    ensure_party_usable(default_contact_party_id, "Standardkontakt")

    if management_party_id == principal_party_id:
        raise ValueError(
            "Verwaltung und Auftraggeber dürfen nicht dieselbe Partei sein."
        )

    unit_ids = list(dict.fromkeys(unit_ids or []))  # Reihenfolge halten, dedupliziert
    _pruefe_scope(scope_type, unit_ids, property_id)

    with as_business_error():
        with business_transaction(actor_app_user_id):
            mandat = ManagementMandate.objects.create(
                id=uuid.uuid4(),
                property_id=property_id,
                management_party_id=management_party_id,
                principal_party_id=principal_party_id,
                default_contact_party_id=default_contact_party_id,
                mandate_type=mandate_type,
                scope_type=scope_type,
                valid_from=valid_from,
                valid_until=valid_until,
                status="ACTIVE",
                contract_reference=_text(contract_reference),
                version=1,
            )
            for unit_id in unit_ids:
                ManagementMandateUnit.objects.create(
                    mandate_id=mandat.id,
                    property_id=property_id,
                    unit_id=unit_id,
                )
    return get_mandat(mandat.id)


def end_mandat(actor_app_user_id, mandate_id, *, valid_until):
    """Mandat beenden — `status='ENDED'` **und** `valid_until` (CHECK verlangt beides).

    Kein Löschen (Trigger 0009). Die Aufträge und Rechnungen von damals liefen
    über diesen Verwalter; seine Zeile verschwinden zu lassen wäre
    Geschichtsfälschung.

    Beides wird in **einem** UPDATE geschrieben: Der CHECK
    `status <> 'ENDED' OR valid_until IS NOT NULL` würde ein Zwischenspeichern in
    zwei Schritten abweisen — richtigerweise.
    """
    mandat = ManagementMandate.objects.filter(pk=mandate_id).first()
    if mandat is None:
        raise ValueError(f"Mandat {mandate_id} existiert nicht")
    if mandat.status == "ENDED":
        raise ValueError("Dieses Mandat ist bereits beendet.")
    if valid_until is None:
        raise ValueError("Ein beendetes Mandat braucht ein Enddatum.")
    if valid_until <= mandat.valid_from:
        raise ValueError(
            "Das Enddatum muss nach dem Beginn des Mandats liegen "
            f"(Beginn: {mandat.valid_from:%d.%m.%Y})."
        )

    mandat.status = "ENDED"
    mandat.valid_until = valid_until
    with as_business_error():
        with business_transaction(actor_app_user_id):
            mandat.save(update_fields=["status", "valid_until"])
    return get_mandat(mandat.id)


def update_mandat(actor_app_user_id, mandate_id, felder):
    """Mandat ändern — **nur die korrigierbaren Felder**.

    Änderbar sind Standardkontakt und Vertragsreferenz. **Nicht** änderbar sind
    Verwalter, Auftraggeber, Liegenschaft, Mandatsart und Umfang: Das wären
    andere Mandate, keine Korrekturen — und der Umfang ist DB-seitig ohnehin
    unveränderlich (`trg_mandate_unit_immutable`). Wer das ändern will, beendet
    das Mandat und legt ein Nachfolgemandat an.

    Das Beenden läuft über `end_mandat` (nicht über ein `status`-Feld hier):
    Status und Enddatum hängen per CHECK aneinander, und ein Statuswechsel ist
    eine eigene, bestätigungspflichtige Handlung.
    """
    mandat = ManagementMandate.objects.filter(pk=mandate_id).first()
    if mandat is None:
        raise ValueError(f"Mandat {mandate_id} existiert nicht")

    erlaubt = {"default_contact_party_id", "contract_reference"}
    unbekannt = set(felder) - erlaubt
    if unbekannt:
        raise ValueError(
            "Diese Felder lassen sich an einem laufenden Mandat nicht ändern: "
            + ", ".join(sorted(unbekannt))
            + ". Umfang, Verwalter und Auftraggeber ändern sich nur über ein "
            "Nachfolgemandat (Beschluss A-11/A-12)."
        )
    if "default_contact_party_id" in felder:
        kontakt = felder["default_contact_party_id"]
        if kontakt is None:
            raise ValueError(
                "Ein Mandat braucht einen Standardkontakt (Beschluss A-10)."
            )
        ensure_party_usable(kontakt, "Standardkontakt")
        mandat.default_contact_party_id = kontakt
    if "contract_reference" in felder:
        mandat.contract_reference = _text(felder["contract_reference"])

    with as_business_error():
        with business_transaction(actor_app_user_id):
            mandat.save(
                update_fields=["default_contact_party_id", "contract_reference"]
            )
    return get_mandat(mandat.id)


def add_zustaendigkeit(
    actor_app_user_id,
    mandate_id,
    *,
    responsibility_type,
    responsible_party_id,
    valid_from,
    valid_until=None,
    priority=100,
):
    """Eine weitere Zuständigkeit am Mandat (technisch, kaufmännisch, Notfall …).

    Der **Standardkontakt** steht am Mandat selbst; hier stehen die zusätzlichen
    Kontakte mit ihrer Eskalationsreihenfolge (`priority`, kleiner = früher).
    """
    if responsibility_type not in RESPONSIBILITY_TYPES:
        raise ValueError(
            f"Ungültige Zuständigkeit '{responsibility_type}'. "
            f"Erlaubt: {', '.join(RESPONSIBILITY_TYPES)}."
        )
    if priority is None or priority < 1:
        raise ValueError("Die Priorität muss mindestens 1 sein (kleiner = früher).")
    if valid_until is not None and valid_until <= valid_from:
        raise ValueError(
            "Das Gültig-bis-Datum muss nach dem Gültig-ab-Datum liegen."
        )
    ensure_exists(ManagementMandate, mandate_id, "Mandat")
    ensure_party_usable(responsible_party_id, "Zuständiger")

    with as_business_error():
        with business_transaction(actor_app_user_id):
            ManagementResponsibility.objects.create(
                id=uuid.uuid4(),
                mandate_id=mandate_id,
                responsibility_type=responsibility_type,
                responsible_party_id=responsible_party_id,
                priority=priority,
                valid_from=valid_from,
                valid_until=valid_until,
            )
    return get_mandat(mandate_id)


def end_zustaendigkeit(actor_app_user_id, responsibility_id, *, valid_until):
    """Eine Zuständigkeit beenden (`valid_until`). Kein Löschen (Trigger 0009)."""
    zeile = ManagementResponsibility.objects.filter(pk=responsibility_id).first()
    if zeile is None:
        raise ValueError(f"Zuständigkeit {responsibility_id} existiert nicht")
    if valid_until is None:
        raise ValueError("Ein Enddatum ist erforderlich.")
    if valid_until <= zeile.valid_from:
        raise ValueError("Das Enddatum muss nach dem Beginn liegen.")

    zeile.valid_until = valid_until
    with as_business_error():
        with business_transaction(actor_app_user_id):
            zeile.save(update_fields=["valid_until"])
    return get_mandat(zeile.mandate_id)


# ---------------------------------------------------------------------------
# Intern
# ---------------------------------------------------------------------------

def _text(wert):
    if wert is None:
        return None
    wert = wert.strip()
    return wert or None


def _pruefe_scope(scope_type, unit_ids, property_id):
    """Die Scope-Regeln aus `assert_mandate_valid` — vorgeprüft, nicht ersetzt.

    Der Trigger entscheidet beim COMMIT. Diese Prüfung sorgt nur dafür, dass der
    Normalfall eine Meldung bekommt, die sagt, **was** falsch ist. Sie fällt
    absichtlich nicht weg, wenn sie „doppelt" wirkt: Ein 422 mit klarer Meldung
    ist etwas anderes als ein durchgereichter Triggertext.
    """
    if scope_type == "ENTIRE_PROPERTY" and unit_ids:
        raise ValueError(
            "Ein Mandat über die gesamte Liegenschaft kann keine einzelnen "
            "Einheiten führen. Entweder gesamte Liegenschaft oder ausgewählte "
            "Einheiten — nicht beides."
        )
    if scope_type == "SELECTED_UNITS" and not unit_ids:
        raise ValueError(
            "Ein Mandat über ausgewählte Einheiten braucht mindestens eine "
            "Einheit."
        )
    if not unit_ids:
        return
    # Der zusammengesetzte FK verlangt, dass jede Einheit zur Liegenschaft des
    # Mandats gehört — sonst IntegrityError (500) statt Meldung.
    gefunden = dict(
        Unit.objects.filter(pk__in=unit_ids).values_list("id", "property_id")
    )
    fehlend = [u for u in unit_ids if u not in gefunden]
    if fehlend:
        raise ValueError(
            "Unbekannte Einheit(en): " + ", ".join(str(u) for u in fehlend)
        )
    fremd = [u for u in unit_ids if gefunden[u] != property_id]
    if fremd:
        raise ValueError(
            "Diese Einheit(en) gehören nicht zu dieser Liegenschaft: "
            + ", ".join(str(u) for u in fremd)
        )
