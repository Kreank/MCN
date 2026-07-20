"""Entitäts-Dossiers: alles zu EINER Entität in EINEM Aufruf — deterministisch.

Vier Dossiers (Konzeptpapier `docs/ki-first-konzept.html`, Abschnitte 4 und 9):
Kontakt · Liegenschaft · Projekt · Auftrag. Ein Dossier ist ein **rein lesender,
rechtegefilterter Read-Service**. Es enthält **keine KI** — und genau das ist der
Punkt: ~80 % der späteren Auskunftsqualität hängen nicht am Modell, sondern daran,
dass die Zahlen exakt, prüfbar und rechtegefiltert an einer Stelle
zusammenkommen. Ohne KI ist das Dossier trotzdem wertvoll (Detailansicht, Druck,
Export); mit KI ist es die Voraussetzung dafür, dass sie je zuverlässig antwortet.

## Die vier Invarianten dieses Moduls

**1. Der Service RECHNET NICHTS NEU, was schon eine Rechenstelle hat.**
Das ist die teuerste Fehlerquelle dieses Projekts (zwei Wahrheiten für dieselbe
Zahl). Deshalb zieht das Dossier ausnahmslos von den vorhandenen Stellen:

| Baustein | Rechenstelle |
|---|---|
| „was schuldet der Kunde?" (Forderung) | `buchhaltung.forderungen` |
| offener Betrag / Zahlungsstand | `buchhaltung.zahlungsspiegel` (+ `PAYMENT_SIGN`) |
| „ist der Beleg storniert?" | `beleg.stornierte_belege` |
| anrechenbare Abschläge | `beleg.anrechenbare_abschlaege` |
| Marge/Deckungsbeitrag | `auswertungen.marge_je_projekt` (dieselbe wie im Dashboard) |
| Soll-Ist | `site_report.soll_ist` |
| „was ist noch nicht abgerechnet?" | `abrechnung.offene_abrechnung` |
| Fälligkeiten (Wartung/Prüfung/Gewährleistung) | `faelligkeit.liste` |
| Kontaktwege/Adressen/Ansprechpartner | `identity.list_*` |
| Dokumente | `dateien.dateien_am_ziel` |
| mögliche Statusübergänge | `auftrag.WORK_ORDER_TRANSITIONS` |

Neu **gerechnet** wird hier genau eines: das **Zahlungsverhalten** — dafür gab es
im Repo keine Stelle. Es benutzt aber die vorhandene Zahlungsableitung; es baut
keine zweite.

**2. UNBEKANNT IST NULL, NIE 0.**
Keine Zahlung → Zahlungsverzögerung `None` (nicht „0 Tage Verzug"; das hieße
„zahlt pünktlich" und wäre eine Behauptung über einen Kunden, von dem wir nichts
wissen). Kein EK → Marge `None` + `ek_vollstaendig=False`, nie 0 % und nie 100 %.
Keine abgeschlossene Zeitbuchung → Stundensumme `None`, nicht 0,0 h.

**3. Rechte: der KERN ist hart getort, JEDER Baustein einzeln.**
Der Kern der Entität hängt am `require()`/`require_scoped()` des eigenen Moduls
(Kontakt→identity, Liegenschaft→property, Projekt/Auftrag→workflow) — fehlt es, gibt
es 403 und keine Antwort. Jeder **weitere** Baustein prüft sein **eigenes** Modul
weich: Fehlt das Recht, ist der Baustein `None` und ein Flag
`<baustein>_sichtbar=False` sagt, warum. Ein Dossier weist also nie die ganze
Antwort ab, weil ein Teil fehlt — und es liefert nie einen ungetorten Teil mit.
Die Zuordnung Baustein → Modul ist bewusst dieselbe wie an den bestehenden
Endpunkten; wo sie strenger ist, steht es am Baustein dabei.

**3a. Die OBJEKTSICHT (row_scope 'EIGENE', Migration 0099).**
Bis zu diesem Slice galt: „Ein MONTEUR bekommt gar kein Dossier." Das war falsch —
und es war der Grund, aus dem er zur „Heizkörper kalt"-Meldung fuhr, ohne zu wissen,
dass zwei Tage zuvor am Nachbar-Heizkörper ein Leck war und dass im Haus eine
Zentralanlage steht. Er bekommt jetzt das Dossier **seiner Objekte** — der
Liegenschaften, an denen er je einen Einsatz hatte (`services/objektsicht.py`).

Zwei Dinge halten das dicht:

  * **Die Geld-Bausteine hängen an `sicht.invoicing` (Scope ALLE).** Offene Posten,
    Zahlungsverhalten, Marge, Rechnungen und der Abrechnungsstand (er führt
    Einzelpreise) bleiben damit für die Objektsicht `None` + Flag `False`.
    Seit **Migration 0102** trägt MONTEUR zwar `invoicing/LESEN`, aber mit row_scope
    EIGENE — und `sicht.invoicing` bedeutet ausdrücklich **ALLE**. Der einzige
    Baustein mit einer EIGENE-Variante ist das **Angebot**, und zwar in einer
    **eigenen, preisfreien Liste** (`angebote_mengen`): eigenes Feld, eigenes Flag,
    eigene Zeilenform ohne `net_total`/`gross_total`. Die preisführende Liste
    (`angebote`) bleibt unangetastet — sie ist nicht „dieselbe Liste mit Nullen",
    sondern eine andere Liste. Wer beide zusammenlegt, gibt Beträge frei.
  * **Wo eine Entität über MEHRERE Objekte läuft** (das Projekt, der Kontakt), wird
    innerhalb des Dossiers **noch einmal** gefiltert: Ein Projekt ist schon „meins",
    wenn EINE seiner Liegenschaften meine ist — seine übrigen Objekte, deren
    Vorgänge und deren Aufträge dürfen darin nicht auftauchen. Genau hier läge sonst
    der Nebeneingang zum fremden Objekt.

**4. Rein lesend.** Kein `business_transaction`, kein Schreibpfad, kein
Seiteneffekt. Ein Dossier darf nichts verändern — ein statischer Test hält das
fest (`db_core/tests/test_dossier_readonly.py`).

## DSGVO

Dokumente kommen ausschließlich über `dateien.dateien_am_ziel` und werden
zusätzlich gegen Attest-Verknüpfungen gefiltert. Ein **Attest** hängt per DB-CHECK
(`num_nonnulls = 1`) an genau einer Abwesenheit — es kann in keiner der hier
abgefragten Dateilisten (Projekt/Liegenschaft/Kontakt/Auftrag) vorkommen. Der
zusätzliche `exclude` ist der Gürtel zum Hosenträger: Gesundheitsdaten gehören in
kein Dossier, auch nicht versehentlich. Abwesenheitsarten kommen ohnehin nicht vor
(dieses Modul liest `hr` überhaupt nicht).
"""
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Max, Q

from db_core.models import (
    Checklist,
    Communication,
    Invoice,
    MaintenanceContract,
    MaterialEntry,
    Party,
    Payment,
    Project,
    ProjectLog,
    Property,
    PropertyPartyRole,
    Quote,
    ServiceCase,
    ServiceJob,
    Task,
    TechnicalAsset,
    TimeEntry,
    WorkOrder,
)
from db_core.services import abrechnung as abrechnung_service
from db_core.services import auswertungen as auswertungen_service
from db_core.services import beleg as beleg_service
from db_core.services import buchhaltung as buchhaltung_service
from db_core.services import dateien as dateien_service
from db_core.services import faelligkeit as faelligkeit_service
from db_core.services import identity as identity_service
from db_core.services import objektsicht
from db_core.services import site_report as report_service
from db_core.services.auftrag import WORK_ORDER_TRANSITIONS

# Kategorie der Attest-Verknüpfung (DSGVO Art. 9). Sie darf in KEINEM Dossier
# auftauchen; siehe Modul-Docstring.
ATTEST_KATEGORIE = "ATTEST"

# Vorgangs-/Auftragsstatus, die erledigt sind. „Offen" heißt: alles andere.
VORGANG_ENDSTATUS = ("ABGESCHLOSSEN", "ABGELEHNT")
AUFTRAG_ENDSTATUS = ("ABGERECHNET", "STORNIERT")

_ZERO = Decimal("0.00")
_Q_STUNDEN = Decimal("0.001")
_SEKUNDEN_JE_STUNDE = Decimal(3600)

# Zahlungsarten, die ein Geldeingang sind (PAYMENT_SIGN > 0) — aus der einen
# Vorzeichenkonvention abgeleitet, nicht abgeschrieben.
_EINGANGS_TYPEN = tuple(
    t for t, s in buchhaltung_service.PAYMENT_SIGN.items() if s > 0
)

# Wie viele Zeilen die „letzten …"-Listen führen (Kommunikation, Logbuch).
_LETZTE = 10


class DossierNichtGefunden(LookupError):
    """Die Entität gibt es nicht (→ 404, nie 403: keine Existenzaussage)."""


@dataclass(frozen=True)
class Sicht:
    """Welche Module darf dieses Konto lesen — und mit welcher Reichweite?

    Zwei Ebenen je Modul, und sie dürfen nie verwechselt werden:

    * `identity`/`property`/`workflow`/`content` = row_scope **ALLE** (das ganze Haus).
    * `*_eigene` = row_scope **EIGENE**: dasselbe Modul, aber ausschließlich auf
      **meinen Objekten** (`services/objektsicht.py`). `actor_id` ist dann Pflicht —
      ohne Akteur gibt es keine „eigenen" Zeilen, also gar keine (fail-closed).

    `invoicing` heißt in dieser Klasse **immer „Scope ALLE"** — jeder Geld-Baustein
    (offene Posten, Zahlungsverhalten, Marge, Rechnungen, Abrechnungsstand) fragt
    genau dieses Flag ab und bleibt damit für die Objektsicht zu. Die EIGENE-Variante
    `invoicing_eigene` (Migration 0102) schaltet **einen einzigen** Baustein frei: die
    **preisfreie** Angebotsliste `angebote_mengen`. Sie ist absichtlich kein Schalter
    an den bestehenden Listen, sondern ein eigenes Feld — ein Flag, das an zwei
    Bausteinen hinge, wäre genau der Weg, auf dem Beträge herausrutschen.

    `pricing` hat **keine** EIGENE-Variante: Der Artikelstamm führt EK und
    Aufschlagsmatrix.

    `maintenance` **hat** eine EIGENE-Variante (Migration 0100): Ein Wartungsvertrag
    ist keine Rechnung. Das Schema führt keine einzige Geldspalte, und die Auskunft
    „diese Anlage steht unter Wartungsvertrag, nächste Fälligkeit im März" ist genau
    das, was der Monteur vor der Zentralanlage braucht.
    """

    identity: bool = False
    property: bool = False
    workflow: bool = False
    invoicing: bool = False
    pricing: bool = False
    content: bool = False
    maintenance: bool = False
    # --- Objektsicht (row_scope EIGENE) --------------------------------------
    identity_eigene: bool = False
    property_eigene: bool = False
    workflow_eigene: bool = False
    content_eigene: bool = False
    maintenance_eigene: bool = False
    # Öffnet AUSSCHLIESSLICH `angebote_mengen` (preisfrei, objektbegrenzt).
    invoicing_eigene: bool = False
    actor_id: object = None

    # Achtung: Das Feld heißt `property` (das Modul heißt so). Im Klassenkörper ist
    # der Builtin `property` damit verschattet — die folgenden sind deshalb ganz
    # normale Methoden und keine @property.
    def darf_identity(self):
        return self.identity or self.identity_eigene

    def darf_property(self):
        return self.property or self.property_eigene

    def darf_workflow(self):
        return self.workflow or self.workflow_eigene

    def darf_content(self):
        return self.content or self.content_eigene

    def darf_maintenance(self):
        return self.maintenance or self.maintenance_eigene

    def objektgrenze(self):
        """`actor_id`, wenn irgendein Modul nur die Objektsicht hat — sonst None.

        `None` heißt „nicht filtern" (Scope ALLE). Ein Aufrufer, der diesen Wert
        ignoriert, während eine `*_eigene`-Flagge gesetzt ist, baut ein Datenleck.
        """
        if (
            self.identity_eigene
            or self.property_eigene
            or self.workflow_eigene
            or self.maintenance_eigene
        ):
            return self.actor_id
        return None

    def darf_marge(self):
        """Marge = Umsatz (invoicing) MINUS Einkauf (pricing) → beide Rechte nötig.

        Das Auswertungs-Dashboard zieht dieselbe Grenze: der Umsatz hängt an
        `invoicing/LESEN`, der EK zusätzlich an `pricing/LESEN`. Keine
        EIGENE-Variante — siehe Klassen-Docstring.
        """
        return self.invoicing and self.pricing


# ===========================================================================
# Geld: offene Posten und Zahlungsverhalten
# ===========================================================================
# Grundmenge beider Bausteine ist dieselbe und heißt **Forderung**. Sie ist NICHT
# hier definiert, sondern genau einmal in `buchhaltung.forderungen` — dieselbe
# Grenze, von der auch offene Posten, Mahnwesen und Mahnlauf ziehen. Das Dossier
# war die erste Stelle, die sie richtig zog; jetzt zieht sie das ganze Haus.


def _forderungen(invoice_qs):
    """Die Rechnungen, die tatsächlich Geld fordern — aus DER einen Rechenstelle."""
    return buchhaltung_service.forderungen(invoice_qs)


def _posten_zeile(inv, heute):
    """Eine Zeile aus dem einen Zahlungsspiegel (buchhaltung), nichts neu gerechnet."""
    spiegel = buchhaltung_service.zahlungsspiegel(inv, heute=heute)
    return {
        "invoice_id": inv.id,
        "invoice_number": inv.invoice_number,
        "invoice_type": inv.invoice_type,
        "invoice_date": inv.invoice_date,
        "due_date": inv.due_date,
        "gross_total": spiegel["gross_total"],
        "paid_total": spiegel["paid_total"],
        # Gutschriften mindern die Forderung (Storno hebt sie ganz auf).
        "credit_total": spiegel["credit_total"],
        "open_amount": spiegel["open_amount"],
        "payment_status": spiegel["payment_status"],
        "is_overdue": spiegel["is_overdue"],
        "days_overdue": spiegel["days_overdue"],
        "dunning_level": spiegel["dunning_level"],
    }


def _offene_posten(invoice_qs, heute):
    """Offene Forderungen mit Summen. Keine offene Forderung → leere Liste + 0,00 €.

    Hier ist 0 **kein** geratener Wert: „nichts offen" ist eine bekannte Tatsache,
    kein Unbekanntes. (Anders als die Zahlungsverzögerung weiter unten.)
    """
    posten = [
        z
        for z in (
            _posten_zeile(inv, heute)
            for inv in buchhaltung_service.mit_zahlungsstand(
                _forderungen(invoice_qs)
            ).order_by("due_date", "invoice_number", "id")
        )
        if z["open_amount"] > _ZERO
    ]
    summe = sum((z["open_amount"] for z in posten), _ZERO)
    ueberfaellig = [z for z in posten if z["is_overdue"]]
    return {
        "posten": posten,
        "anzahl": len(posten),
        "summe_offen": summe,
        "anzahl_ueberfaellig": len(ueberfaellig),
        "summe_ueberfaellig": sum((z["open_amount"] for z in ueberfaellig), _ZERO),
    }


def _zahlungsverhalten(invoice_qs, heute):
    """Wie zahlt dieser Kunde? — neu gerechnet, aber auf der EINEN Zahlungslogik.

    Es gab dafür bisher keine Rechenstelle. Die Kennzahlen:

    * `rechnungen_gesamt` / `bezahlt_anzahl` / `offen_anzahl` / `ueberfaellig_anzahl`
    * `durchschnittliche_verzoegerung_tage` — Mittel aus (letzte Zahlung − Fälligkeit)
      über die **vollständig bezahlten** Rechnungen mit Fälligkeitsdatum. Negativ =
      im Schnitt zu früh gezahlt (das ist eine echte Aussage, keine 0).
    * `bewertete_rechnungen` — worauf sich dieser Durchschnitt stützt. Ohne diese
      Angabe wäre eine Zahl aus einer einzigen Rechnung von einer aus vierzig nicht
      zu unterscheiden.

    **Ohne jede Zahlung ist die Verzögerung `None` — nicht 0.** Eine 0 hieße
    „zahlt exakt pünktlich" und wäre eine Behauptung über einen Kunden, über den
    wir nichts wissen. Das ist die Invariante des Hauses (fehlender EK → VK
    unbekannt, nie 0), hier auf Tage angewandt.
    """
    rechnungen = list(
        buchhaltung_service.mit_zahlungsstand(_forderungen(invoice_qs))
    )
    ids = [r.id for r in rechnungen]

    # Letzter Geldeingang je Rechnung — EINE Query (kein N+1). Nur eingehende
    # Zahlungen (PAYMENT_SIGN > 0): eine Rückerstattung ist kein Zahlungseingang.
    letzte_zahlung = {}
    if ids:
        letzte_zahlung = {
            r["invoice_id"]: r["m"]
            for r in Payment.objects.filter(
                invoice_id__in=ids, payment_type__in=_EINGANGS_TYPEN
            )
            .values("invoice_id")
            .annotate(m=Max("paid_at"))
        }

    bezahlt = offen = ueberfaellig = 0
    summe_offen = summe_ueberfaellig = _ZERO
    verzuege = []
    for inv in rechnungen:
        spiegel = buchhaltung_service.zahlungsspiegel(inv, heute=heute)
        # Maßgeblich ist der Forderungsbetrag (Brutto nach Gutschriften), nicht das
        # Brutto: Wer den um eine Kulanz geminderten Rest zahlt, hat bezahlt — und
        # ist kein säumiger Kunde.
        rest = spiegel["open_amount"]
        if rest <= _ZERO and spiegel["forderungsbetrag"] > _ZERO:
            bezahlt += 1
            gezahlt_am = letzte_zahlung.get(inv.id)
            if gezahlt_am is not None and inv.due_date is not None:
                verzuege.append((gezahlt_am - inv.due_date).days)
        if rest > _ZERO:
            offen += 1
            summe_offen += rest
            if inv.due_date and inv.due_date < heute:
                ueberfaellig += 1
                summe_ueberfaellig += rest

    return {
        "rechnungen_gesamt": len(rechnungen),
        "bezahlt_anzahl": bezahlt,
        "offen_anzahl": offen,
        "ueberfaellig_anzahl": ueberfaellig,
        "summe_offen": summe_offen,
        "summe_ueberfaellig": summe_ueberfaellig,
        # None = unbekannt (keine bezahlte Rechnung mit Fälligkeit), NIE 0.
        "durchschnittliche_verzoegerung_tage": (
            round(sum(verzuege) / len(verzuege), 1) if verzuege else None
        ),
        "groesste_verzoegerung_tage": max(verzuege) if verzuege else None,
        "bewertete_rechnungen": len(verzuege),
    }


# ===========================================================================
# Angebote OHNE Preise — der eine Baustein der Objektsicht am Geld (0102)
# ===========================================================================

def _angebote_mengen(quote_qs, actor_id):
    """Die **preisfreie** Angebotsliste der Objektsicht: was ist beauftragt?

    Zwei Grenzen, beide aus der einen Heimat der Regel (`services/objektsicht.py`):
    die Zeilen (**meine** Objekte, Status VERSENDET/ANGENOMMEN) und — hier — die
    **Felder**: Diese Zeilenform führt **kein** `net_total` und **kein**
    `gross_total`. Sie ist nicht `_angebot_zeile()` mit Nullen, sie ist eine andere
    Zeile; deshalb kann hier auch kein Betrag „vergessen" werden.

    Die Positionen (Menge, Einheit) hängen nicht daran — die holt das UI über
    `GET /invoicing/quotes/{id}/mengen`. Ein Dossier listet Belege, es entfaltet sie
    nicht.
    """
    qs = objektsicht.angebote_begrenzen(quote_qs, "EIGENE", actor_id)
    return [
        {
            "id": q.id,
            "quote_number": q.quote_number,
            "title": q.title,
            "status": q.status,
            "quote_date": q.quote_date,
            "work_order_id": q.work_order_id,
        }
        for q in qs.order_by("-created_at", "id")
    ]


# ===========================================================================
# Dokumente (content) — nie ein Attest
# ===========================================================================

def _dokumente(**ziel):
    """Dateien an genau einem Zielobjekt — über den vorhandenen Datei-Service.

    Der `exclude` auf Attest-Verknüpfungen ist redundant (ein `file_link` hängt per
    DB-CHECK an genau einem Objekt, ein Attest also nie an einem Projekt) und steht
    trotzdem da: Ein Guard fällt auf sicher, nicht auf offen — und ein
    Gesundheitsdatum in einem Dossier wäre der teuerste denkbare Fehler dieser Art.
    """
    links = (
        dateien_service.dateien_am_ziel(**ziel)
        .exclude(absence_id__isnull=False)
        .exclude(link_category=ATTEST_KATEGORIE)
    )
    return [
        {
            "file_id": l.file_id,
            "link_id": l.id,
            "original_filename": l.file.original_filename,
            "mime_type": l.file.mime_type,
            "size_bytes": l.file.size_bytes,
            "link_category": l.link_category,
            "uploaded_at": l.file.uploaded_at,
            "uploaded_by": (
                l.created_by.display_name if l.created_by_id else None
            ),
        }
        for l in links
    ]


# ===========================================================================
# Gemeinsame Bausteine
# ===========================================================================

def _vorgang_zeile(case):
    return {
        "id": case.id,
        "case_number": case.case_number,
        "subject": case.subject,
        "status": case.status,
        "priority": case.priority,
        "received_at": case.received_at,
        "is_offen": case.status not in VORGANG_ENDSTATUS,
        "property_id": case.property_id,
        "project_id": case.project_id,
    }


def _auftrag_zeile(order):
    return {
        "id": order.id,
        "order_number": order.order_number,
        "title": order.title,
        "status": order.status,
        "priority": order.priority,
        "billing_mode": order.billing_mode,
        "is_offen": order.status not in AUFTRAG_ENDSTATUS,
        "desired_date": order.desired_date,
        "property_id": order.property_id,
        "project_id": order.project_id,
    }


def _einsatz_zeile(job):
    return {
        "id": job.id,
        "job_number": job.job_number,
        "title": job.title or (job.work_order.title if job.work_order_id else None),
        "status": job.status,
        "scheduled_start": job.scheduled_start,
        "scheduled_end": job.scheduled_end,
        "work_order_id": job.work_order_id,
        "zugewiesen": sorted(
            a.assignee.display_name for a in job.assignments.all()
        ),
    }


def _aufgaben(**filter_kv):
    """Offene Aufgaben (workflow.task) zu einem Bezug — verworfene bleiben draußen."""
    return [
        {
            "id": t.id,
            "title": t.title,
            "status": t.status,
            "due_date": t.due_date,
            "assigned_to": (
                t.assigned_to.display_name if t.assigned_to_id else None
            ),
        }
        for t in Task.objects.filter(status="OFFEN", **filter_kv)
        .select_related("assigned_to")
        .order_by("due_date", "-created_at", "id")
    ]


def _stunden(sekunden):
    """Sekunden → Stunden auf der DB-Spaltenskala (wie `abrechnung._stunden`).

    **Erst summieren, dann umrechnen** — 20 Minuten sind 0,333… h; würde je Buchung
    gerundet, summierten sich die Fehler.
    """
    return (Decimal(sekunden) / _SEKUNDEN_JE_STUNDE).quantize(
        _Q_STUNDEN, rounding=ROUND_HALF_UP
    )


def _kern_party(party):
    person = getattr(party, "person", None) if party.party_type == "PERSON" else None
    org = (
        getattr(party, "organization", None)
        if party.party_type == "ORGANIZATION"
        else None
    )
    return {
        "id": party.id,
        "party_type": party.party_type,
        "display_name": party.display_name,
        "status": party.status,
        "first_name": person.first_name if person else None,
        "last_name": person.last_name if person else None,
        "salutation": person.salutation if person else None,
        "legal_name": org.legal_name if org else None,
        "organization_type": org.organization_type if org else None,
        "vat_id": org.vat_id if org else None,
        "acquisition_source": (
            party.acquisition_source.label if party.acquisition_source_id else None
        ),
    }


# ===========================================================================
# Kontakt-Dossier — Kern: identity/LESEN
# ===========================================================================

def kontakt_dossier(party_id, sicht: Sicht):
    """Alles zu einem Kontakt: Stammdaten, Kontaktwege, Ansprechpartner, Rollen an
    Liegenschaften, offene Vorgänge/Aufträge, offene Posten + Zahlungsverhalten,
    letzte Kommunikation, offene Aufgaben, Dokumente.

    Kern (immer da, weil `identity/LESEN` hart getort ist): Stammdaten, Adressen,
    Kontaktwege, Ansprechpartner.
    """
    party = (
        Party.objects.filter(id=party_id)
        .select_related("person", "organization", "acquisition_source")
        .first()
    )
    if party is None:
        raise DossierNichtGefunden("Kontakt nicht gefunden.")
    heute = date.today()

    adressen = [
        {
            "address_type": pa.address_type,
            "is_primary": pa.is_primary,
            "street": pa.address.street,
            "house_number": pa.address.house_number,
            "postal_code": pa.address.postal_code,
            "city": pa.address.city,
            "country_code": pa.address.country_code,
        }
        for pa in identity_service.list_addresses(party_id)
    ]
    kontaktwege = [
        {
            "contact_type": cp.contact_type,
            "value": cp.value,
            "label": cp.label,
            "is_primary": cp.is_primary,
        }
        for cp in identity_service.list_contact_points(party_id)
    ]
    ansprechpartner = [
        {
            "person_party_id": r.from_party_id,
            "display_name": r.from_party.display_name,
            "valid_from": r.valid_from,
        }
        for r in identity_service.list_contact_persons(party_id)
    ]

    # --- Liegenschaftsrollen (property/LESEN) ------------------------------
    # Objektsicht: nur die Rollen an MEINEN Objekten. Ein Verwalter kann 40 Häuser
    # betreuen — der Monteur sieht seine Rolle an dem einen, an dem er war.
    liegenschaften = None
    if sicht.darf_property():
        rollen_qs = PropertyPartyRole.objects.filter(party_id=party_id)
        if sicht.property_eigene:
            rollen_qs = rollen_qs.filter(
                objektsicht.objekt_q(sicht.actor_id, "property_id")
            )
        liegenschaften = [
            {
                "property_id": r.property_id,
                "property_number": r.property.property_number,
                "name": r.property.name,
                "city": r.property.address.city,
                "role": r.role,
                "valid_from": r.valid_from,
                "valid_until": r.valid_until,
                # daterange ist [) — eine Rolle mit valid_until = heute gilt heute
                # nicht mehr (gleiche Regel wie in api/property.py).
                "is_current": r.valid_until is None or r.valid_until > heute,
            }
            for r in rollen_qs.select_related("property__address").order_by(
                "-valid_from", "property__property_number"
            )
        ]

    # --- Vorgänge/Aufträge/Aufgaben (workflow/LESEN) -----------------------
    vorgaenge = auftraege = aufgaben = None
    if sicht.darf_workflow():
        faelle = ServiceCase.objects.filter(reported_by_party_id=party_id).exclude(
            status__in=VORGANG_ENDSTATUS
        )
        orders = (
            WorkOrder.objects.filter(parties__party_id=party_id)
            .exclude(status__in=AUFTRAG_ENDSTATUS)
            .distinct()
        )
        if sicht.workflow_eigene:
            faelle = objektsicht.begrenzen(
                faelle, "EIGENE", sicht.actor_id, "property_id"
            )
            orders = objektsicht.begrenzen(
                orders, "EIGENE", sicht.actor_id, "property_id"
            )
        vorgaenge = [_vorgang_zeile(c) for c in faelle.order_by("-received_at")]
        auftraege = [_auftrag_zeile(o) for o in orders.order_by("-created_at")]

    # Aufgaben hängen an KEINEM Objekt (`workflow.task` kennt keine Liegenschaft) —
    # sie lassen sich für die Objektsicht nicht begrenzen und bleiben deshalb an
    # `workflow` mit Scope ALLE. Der Monteur sieht seine Aufgaben dort, wo sie ihm
    # gehören: unter `GET /workflow/tasks` (eigene Zuweisung).
    if sicht.workflow:
        aufgaben = _aufgaben(party_id=party_id)

    # --- Geld (invoicing/LESEN) --------------------------------------------
    # Attributionsregel wie im Kunden-Dashboard (`auswertungen.kunden_summary`):
    # der **primäre** Rechnungsschuldner. Der partielle Unique-Index lässt höchstens
    # einen je Rechnung zu → keine Doppelzählung.
    offene_posten = zahlungsverhalten = None
    if sicht.invoicing:
        rechnungen = Invoice.objects.filter(
            parties__party_id=party_id,
            parties__role="INVOICE_DEBTOR",
            parties__is_primary=True,
        )
        offene_posten = _offene_posten(rechnungen, heute)
        zahlungsverhalten = _zahlungsverhalten(rechnungen, heute)

    # --- Kommunikation + Dokumente (content/LESEN) -------------------------
    kommunikation = dokumente = None
    if sicht.content:
        kommunikation = [
            {
                "id": c.id,
                "channel": c.channel,
                "direction": c.direction,
                "subject": c.subject,
                "occurred_at": c.occurred_at,
                "counterpart": c.counterpart_raw,
            }
            for c in Communication.objects.filter(counterpart_party_id=party_id)
            .order_by("-occurred_at", "id")[:_LETZTE]
        ]
        dokumente = _dokumente(party_id=party_id)

    return {
        "kontakt": _kern_party(party),
        "adressen": adressen,
        "kontaktwege": kontaktwege,
        "ansprechpartner": ansprechpartner,
        "liegenschaften_sichtbar": sicht.darf_property(),
        "liegenschaften": liegenschaften,
        "vorgaenge_sichtbar": sicht.darf_workflow(),
        "vorgaenge": vorgaenge,
        "auftraege": auftraege,
        "aufgaben_sichtbar": sicht.workflow,
        "aufgaben": aufgaben,
        "offene_posten_sichtbar": sicht.invoicing,
        "offene_posten": offene_posten,
        "zahlungsverhalten_sichtbar": sicht.invoicing,
        "zahlungsverhalten": zahlungsverhalten,
        "kommunikation_sichtbar": sicht.content,
        "kommunikation": kommunikation,
        "dokumente_sichtbar": sicht.content,
        "dokumente": dokumente,
    }


# ===========================================================================
# Liegenschafts-Dossier — Kern: property/LESEN
# ===========================================================================

def liegenschaft_dossier(property_id, sicht: Sicht):
    """Alles zu einer Liegenschaft: Struktur (Gebäude/Einheiten/Anlagen),
    Beteiligte, Vorgangs-/Auftragshistorie, Einsätze mit **Zutrittshinweisen**,
    Wartung/Prüffristen/Gewährleistung, offene Posten, Dokumente.

    **Zutrittshinweise gibt es NICHT an der Liegenschaft.** Das Schema führt sie
    ausschließlich am Einsatz (`service_job.access_instructions`) — dort, wo sie
    entstehen. Sie werden deshalb aus den Einsätzen der Liegenschaft geliefert,
    **mit benannter Herkunft** (welcher Einsatz, wann, von welchem Auftrag). Ein
    Objektfeld dafür zu erfinden hieße, einen Wert zu behaupten, den niemand
    gepflegt hat; einen der Hinweise als „den" Zutrittshinweis der Liegenschaft
    auszugeben, hieße raten, welcher noch gilt. Der Nutzer sieht die Hinweise samt
    Datum und entscheidet selbst.
    """
    prop = (
        Property.objects.filter(id=property_id)
        .select_related("address")
        .prefetch_related("buildings__units", "party_roles__party")
        .first()
    )
    if prop is None:
        raise DossierNichtGefunden("Liegenschaft nicht gefunden.")
    heute = date.today()

    struktur = [
        {
            "building_id": b.id,
            "building_number": b.building_number,
            "name": b.name,
            "units": [
                {
                    "unit_id": u.id,
                    "unit_type": u.unit_type,
                    "unit_number": u.unit_number,
                }
                for u in sorted(b.units.all(), key=lambda u: u.unit_number)
            ],
        }
        for b in sorted(prop.buildings.all(), key=lambda b: b.building_number)
    ]
    anlagen = [
        {
            "id": a.id,
            "name": a.name,
            "asset_type": a.asset_type,
            "building_id": a.building_id,
            "unit_id": a.unit_id,
        }
        for a in TechnicalAsset.objects.filter(property_id=property_id).order_by("name")
    ]
    # Beteiligte gehören zum property-Kern: Das tut die bestehende Liegenschafts-
    # Mappe (`api/property.py::_property_detail`) unter genau diesem Recht ebenso.
    # Ein eigenes identity-Tor hier wäre eine zweite, widersprüchliche Regel für
    # dieselben Daten.
    beteiligte = [
        {
            "party_id": r.party_id,
            "display_name": r.party.display_name,
            "role": r.role,
            "valid_from": r.valid_from,
            "valid_until": r.valid_until,
            "is_current": r.valid_until is None or r.valid_until > heute,
        }
        for r in sorted(
            prop.party_roles.all(),
            key=lambda r: (r.valid_until is None or r.valid_until > heute, r.valid_from),
            reverse=True,
        )
    ]

    # --- Vorgänge/Aufträge/Einsätze + Zutritt (workflow/LESEN) -------------
    # Objektsicht: KEIN zusätzlicher Filter nötig — jede Zeile hier hängt per
    # Konstruktion an *dieser* Liegenschaft, und dass sie meine ist, hat der
    # Endpunkt bereits geprüft (`guard_objekt`). Deshalb sieht der Monteur hier den
    # Vorgang, den Auftrag, den Einsatz und den Bericht **des Kollegen** — genau
    # dafür ist dieser Slice gebaut.
    vorgaenge = auftraege = einsaetze = zutrittshinweise = None
    if sicht.darf_workflow():
        vorgaenge = [
            _vorgang_zeile(c)
            for c in ServiceCase.objects.filter(property_id=property_id).order_by(
                "-received_at"
            )
        ]
        auftraege = [
            _auftrag_zeile(o)
            for o in WorkOrder.objects.filter(property_id=property_id).order_by(
                "-created_at"
            )
        ]
        # Ein Einsatz hängt entweder direkt an der Liegenschaft (freier Termin) oder
        # über seinen Auftrag — `service_job.property_id` ist nullable (0062).
        jobs = list(
            ServiceJob.objects.filter(
                Q(property_id=property_id) | Q(work_order__property_id=property_id)
            )
            .select_related("work_order")
            .prefetch_related("assignments__assignee")
            .order_by("-scheduled_start", "-created_at")
        )
        einsaetze = [_einsatz_zeile(j) for j in jobs]
        zutrittshinweise = [
            {
                "service_job_id": j.id,
                "job_number": j.job_number,
                "scheduled_start": j.scheduled_start,
                "work_order_id": j.work_order_id,
                "work_order_number": (
                    j.work_order.order_number if j.work_order_id else None
                ),
                "hinweis": j.access_instructions,
            }
            for j in jobs
            if (j.access_instructions or "").strip()
        ]

    # --- Wartung/Prüfung/Gewährleistung (maintenance/LESEN) ----------------
    # Objektsicht: KEIN zusätzlicher Filter nötig — beide Abfragen sind auf *diese*
    # Liegenschaft gebunden, und dass sie meine ist, hat der Endpunkt bereits
    # geprüft. Genau dieser Baustein fehlte dem Monteur vor der Zentralanlage.
    faelligkeiten = wartungsvertraege = None
    if sicht.darf_maintenance():
        faelligkeiten = [
            {
                "id": d.id,
                "kind": d.kind,
                "title": d.title,
                "due_date": d.due_date,
                "status": d.status,
                "is_ueberfaellig": d.due_date < heute,
            }
            for d in faelligkeit_service.liste(property_id=property_id)
        ]
        wartungsvertraege = [
            {
                "id": c.id,
                "contract_number": c.contract_number,
                "name": c.name,
                "status": c.status,
                "interval_kind": c.interval_kind,
                "next_due_date": c.next_due_date,
                "due_action": c.due_action,
            }
            for c in MaintenanceContract.objects.filter(
                property_id=property_id
            ).order_by("-created_at")
        ]

    # --- Geld (invoicing/LESEN) --------------------------------------------
    offene_posten = None
    if sicht.invoicing:
        offene_posten = _offene_posten(
            Invoice.objects.filter(property_id=property_id), heute
        )

    # Dokumente am Objekt: für die Objektsicht lesbar — deckungsgleich mit dem
    # Ziel-Guard der Datei-API (`property_id` ist dort für 'EIGENE' lesbar). Wer das
    # hier öffnet und dort nicht (oder umgekehrt), erzeugt eine Liste mit Dateien,
    # die sich nicht herunterladen lassen.
    dokumente = _dokumente(property_id=property_id) if sicht.darf_content() else None

    return {
        "liegenschaft": {
            "id": prop.id,
            "property_number": prop.property_number,
            "name": prop.name,
            "property_type": prop.property_type,
            "status": prop.status,
            "street": prop.address.street,
            "house_number": prop.address.house_number,
            "postal_code": prop.address.postal_code,
            "city": prop.address.city,
        },
        "gebaeude": struktur,
        "anlagen": anlagen,
        "beteiligte": beteiligte,
        "vorgaenge_sichtbar": sicht.darf_workflow(),
        "vorgaenge": vorgaenge,
        "auftraege": auftraege,
        "einsaetze": einsaetze,
        # Herkunft mitgeliefert: welcher Einsatz, wann. Kein Objektfeld erfunden.
        "zutrittshinweise": zutrittshinweise,
        "wartung_sichtbar": sicht.darf_maintenance(),
        "faelligkeiten": faelligkeiten,
        "wartungsvertraege": wartungsvertraege,
        # Geld: `sicht.invoicing` heißt row_scope **ALLE**. Der Monteur trägt seit
        # 0102 zwar `invoicing/LESEN`, aber mit EIGENE — daraus wird `invoicing_eigene`,
        # und das schaltet ausschließlich die preisfreie Angebotsliste frei (Projekt-
        # und Auftrags-Dossier). Offene Posten bleiben hier `None`.
        "offene_posten_sichtbar": sicht.invoicing,
        "offene_posten": offene_posten,
        "dokumente_sichtbar": sicht.darf_content(),
        "dokumente": dokumente,
    }


# ===========================================================================
# Projekt-Dossier — Kern: workflow/LESEN
# ===========================================================================

def _abschlagslage(project_id):
    """Anrechenbare Abschläge je Auftrag des Projekts — aus `beleg`, nicht neu.

    Gefragt wird **nur** für die Aufträge, die überhaupt eine veröffentlichte
    Abschlags-/Teilrechnung tragen (eine Query). Sonst liefe `anrechenbare_abschlaege`
    für jeden Auftrag des Projekts, auch für die ohne Abschlag — ein N+1 ohne Ertrag.
    """
    kandidaten = (
        Invoice.objects.filter(
            project_id=project_id,
            invoice_type__in=beleg_service.ADVANCE_TYPES,
            status="VEROEFFENTLICHT",
            work_order_id__isnull=False,
        )
        .values_list("work_order_id", flat=True)
        .distinct()
    )
    zeilen = []
    for wo_id in kandidaten:
        for a in beleg_service.anrechenbare_abschlaege(wo_id):
            zeilen.append({
                "work_order_id": wo_id,
                "invoice_id": a["id"],
                "invoice_number": a["invoice_number"],
                "invoice_type": a["invoice_type"],
                "invoice_date": a["invoice_date"],
                "gross_total": a["gross_total"],
                "net_total": a["net_total"],
                "vorgemerkt": a["vorgemerkt"],
            })
    return zeilen


def _projektsteuerung(project_id):
    """(checklisten, logbuch) — **nur für Scope ALLE**. Siehe `projekt_dossier`."""
    checklisten = [
        {
            "id": cl.id,
            "name": cl.name,
            "items": [
                {
                    "id": i.id,
                    "position": i.position,
                    "label": i.label,
                    # „Erledigt" ist im Schema kein Flag, sondern done_at/done_by
                    # (DB-CHECK: beide gemeinsam). Hier abgeleitet, nicht erfunden.
                    "is_done": i.done_at is not None,
                    "done_at": i.done_at,
                }
                for i in sorted(cl.items.all(), key=lambda i: i.position)
            ],
        }
        for cl in Checklist.objects.filter(project_id=project_id)
        .prefetch_related("items")
        .order_by("created_at")
    ]
    logbuch = [
        {
            "id": e.id,
            "category": e.category,
            "entry": e.entry,
            "created_at": e.created_at,
            "author": e.created_by.display_name if e.created_by_id else None,
        }
        for e in ProjectLog.objects.filter(project_id=project_id)
        .select_related("created_by")
        .order_by("-created_at")[:_LETZTE]
    ]
    return checklisten, logbuch


def projekt_dossier(project_id, sicht: Sicht):
    """Alles zu einem Projekt: Vorgänge, Aufträge, Liegenschaften, Checklisten,
    Logbuch, Aufgaben (Kern) · Angebote/Rechnungen inkl. Abschlagslage und offene
    Posten (invoicing) · realisierte und geplante Marge (invoicing + pricing) ·
    Dokumente (content).

    **Objektsicht: Checklisten und Logbuch fallen weg** (`projektsteuerung_sichtbar`
    = False) — als einzige Bausteine, die sich **nicht** auf ein Objekt begrenzen
    lassen. Begründung am Baustein.
    """
    project = (
        Project.objects.filter(id=project_id)
        .select_related("category")
        .prefetch_related("property_links__property__address")
        .first()
    )
    if project is None:
        raise DossierNichtGefunden("Projekt nicht gefunden.")
    heute = date.today()

    # Objektsicht: Ein Projekt ist schon „meins", wenn EINE seiner Liegenschaften
    # meine ist. Seine übrigen Objekte — und deren Vorgänge und Aufträge — dürfen
    # deshalb NICHT im Dossier stehen: Genau hier läge sonst der Nebeneingang zum
    # fremden Objekt. Bei Scope ALLE ist `grenze` None und es wird nicht gefiltert.
    grenze = sicht.objektgrenze()
    links = sorted(
        project.property_links.all(), key=lambda l: l.property.property_number
    )
    faelle = ServiceCase.objects.filter(project_id=project_id)
    orders = WorkOrder.objects.filter(project_id=project_id)
    if grenze is not None:
        meine = set(
            r["objekt_id"] for r in objektsicht.eigene_property_ids(grenze)
        )
        links = [l for l in links if l.property_id in meine]
        faelle = faelle.filter(property_id__in=meine)
        orders = orders.filter(property_id__in=meine)

    liegenschaften = [
        {
            "property_id": l.property.id,
            "property_number": l.property.property_number,
            "name": l.property.name,
            "city": l.property.address.city,
        }
        for l in links
    ]
    vorgaenge = [_vorgang_zeile(c) for c in faelle.order_by("-received_at")]
    auftraege = [_auftrag_zeile(o) for o in orders.order_by("-created_at")]
    # --- Projektsteuerung: Checklisten + Logbuch --------------------------------
    # **Für die Objektsicht fail-closed** (`grenze is not None` → None + Flag).
    # Beide sind FREITEXT OHNE OBJEKTBEZUG: Ein Logbucheintrag („Abstimmung mit der
    # Verwaltung wegen Badensche 53") oder ein Checklistenpunkt („Zählerstand
    # Badensche 53 ablesen") nennt ein fremdes Objekt beim Namen — und keine Spalte
    # sagt mir das vorher. Ein Projekt gilt aber schon als „meins", wenn EINE seiner
    # Liegenschaften meine ist. Genau hier gingen Projektinhalte ungefiltert an die
    # Objektsicht; dieselbe Grenze zieht `api/projekt.py` (403 auf Logbuch und
    # Checklisten). Fachlich ist beides Projektsteuerung, kein Baustellenwissen.
    checklisten = logbuch = None
    if grenze is None:
        checklisten, logbuch = _projektsteuerung(project_id)
    aufgaben = _aufgaben(project_id=project_id)

    # --- Belege + Geld (invoicing/LESEN) -----------------------------------
    angebote = rechnungen = offene_posten = abschlaege = None
    if sicht.invoicing:
        angebote = [
            {
                "id": q.id,
                "quote_number": q.quote_number,
                "title": q.title,
                "status": q.status,
                "quote_date": q.quote_date,
                "net_total": q.net_total,
                "gross_total": q.gross_total,
                "work_order_id": q.work_order_id,
            }
            for q in Quote.objects.filter(project_id=project_id).order_by(
                "-created_at"
            )
        ]
        rechnungen = [
            {
                "id": i.id,
                "invoice_number": i.invoice_number,
                "invoice_type": i.invoice_type,
                "status": i.status,
                "invoice_date": i.invoice_date,
                "net_total": i.net_total,
                "gross_total": i.gross_total,
                "work_order_id": i.work_order_id,
            }
            for i in Invoice.objects.filter(project_id=project_id).order_by(
                "-created_at"
            )
        ]
        offene_posten = _offene_posten(
            Invoice.objects.filter(project_id=project_id), heute
        )
        abschlaege = _abschlagslage(project_id)

    # --- Angebote OHNE Preise (invoicing/LESEN mit row_scope EIGENE, 0102) ---
    # Das Projekt kann über MEHRERE Objekte laufen; `angebote_begrenzen` schneidet
    # die Angebote fremder Liegenschaften desselben Projekts weg — genau wie oben
    # die Vorgänge und Aufträge. Ohne diesen Schnitt wäre die Projektakte der
    # Nebeneingang zum fremden Angebot.
    angebote_mengen = None
    if sicht.invoicing_eigene:
        angebote_mengen = _angebote_mengen(
            Quote.objects.filter(project_id=project_id), sicht.actor_id
        )

    # --- Marge (invoicing + pricing) ---------------------------------------
    # Dieselbe Rechenstelle wie das Auswertungs-Dashboard, nur auf DIESES Projekt
    # vorgefiltert. Kein EK → Deckungsbeitrag/Marge None + ek_vollstaendig=False;
    # niemals 0 % und niemals 100 %.
    marge = geplante_marge = None
    if sicht.darf_marge():
        marge = auswertungen_service.marge_je_projekt(project_id)
        geplante_marge = auswertungen_service.geplante_marge_je_projekt(project_id)

    # Projektdokumente hängen am PROJEKT, nicht an einem Objekt — die Datei-API
    # verweigert `project_id` für Scope 'EIGENE' (403). Hier deshalb bewusst
    # `sicht.content` (ALLE) und nicht `darf_content()`: Die beiden Grenzen müssen
    # deckungsgleich bleiben, sonst listet das Dossier Dateien auf, die der Abrufer
    # nicht herunterladen kann.
    dokumente = _dokumente(project_id=project_id) if sicht.content else None

    return {
        "projekt": {
            "id": project.id,
            "project_number": project.project_number,
            "name": project.name,
            "status": project.status,
            "start_date": project.start_date,
            "target_end_date": project.target_end_date,
            "category": project.category.name if project.category_id else None,
        },
        "liegenschaften": liegenschaften,
        "vorgaenge": vorgaenge,
        "auftraege": auftraege,
        # Freitext ohne Objektbezug → für die Objektsicht null + Flag=False, nie eine
        # stille leere Liste („es gibt nichts" ist etwas anderes als „du darfst nicht").
        "projektsteuerung_sichtbar": grenze is None,
        "checklisten": checklisten,
        "logbuch": logbuch,
        "aufgaben": aufgaben,
        "belege_sichtbar": sicht.invoicing,
        "angebote": angebote,
        "rechnungen": rechnungen,
        "anrechenbare_abschlaege": abschlaege,
        # Preisfreie Angebotsliste (Objektsicht). Eigenes Flag, eigene Liste — nie
        # dieselbe wie `angebote`.
        "angebote_mengen_sichtbar": sicht.invoicing_eigene,
        "angebote_mengen": angebote_mengen,
        "offene_posten_sichtbar": sicht.invoicing,
        "offene_posten": offene_posten,
        "marge_sichtbar": sicht.darf_marge(),
        "marge": marge,
        "geplante_marge": geplante_marge,
        "dokumente_sichtbar": sicht.content,
        "dokumente": dokumente,
    }


# ===========================================================================
# Auftrags-Dossier — Kern: workflow/LESEN
# ===========================================================================

def _moegliche_uebergaenge(status):
    """Die erlaubten Statusübergänge — AUS `auftrag.WORK_ORDER_TRANSITIONS`.

    Der Service **gibt sie aus, er erfindet sie nicht**: Die Tabelle spiegelt
    wörtlich `workflow.status_transition` (Migration 0010), und die DB bleibt die
    letzte Instanz. Sie lag bisher da, ohne dass ein Endpunkt sie je ausgeliefert
    hätte — die KI (und jedes UI) muss aber wissen, was als Nächstes möglich ist.

    **Ein möglicher Übergang ist keine Erlaubnis**: Ob der Akteur ihn ausführen
    darf (Recht) und ob die fachlichen Tore ihn zulassen (Beauftragungsnachweis,
    Verantwortungsbereich, Beteiligte), entscheidet sich erst beim Ausführen.
    """
    return [
        {"to_status": ziel, "begruendung_pflicht": pflicht}
        for ziel, pflicht in sorted(WORK_ORDER_TRANSITIONS.get(status, {}).items())
    ]


def auftrag_dossier(work_order_id, sicht: Sicht):
    """Alles zu einem Auftrag: Status + mögliche Übergänge, Beteiligte, Einsätze
    und Termine, erfasste Zeiten und Material, Baustellenberichte, Soll-Ist (Kern) ·
    Abrechnungsstand, Angebote/Rechnungen, offene Posten (invoicing) · Dokumente
    (content).
    """
    order = (
        WorkOrder.objects.filter(id=work_order_id)
        .select_related("property__address", "project", "service_case")
        .prefetch_related("parties__party")
        .first()
    )
    if order is None:
        raise DossierNichtGefunden("Auftrag nicht gefunden.")
    heute = date.today()

    beteiligte = [
        {
            "party_id": wp.party_id,
            "display_name": wp.party.display_name,
            "role": wp.role,
            "is_primary": wp.is_primary,
        }
        for wp in sorted(
            order.parties.all(), key=lambda wp: (wp.role, not wp.is_primary)
        )
    ]
    jobs = list(
        ServiceJob.objects.filter(work_order_id=order.id)
        .select_related("work_order")
        .prefetch_related("assignments__assignee")
        .order_by("scheduled_start", "created_at")
    )
    einsaetze = [_einsatz_zeile(j) for j in jobs]

    # --- Zeiten -------------------------------------------------------------
    # Der Auftragsbezug läuft über den Einsatz (time_entry kennt keinen Auftrag) —
    # dieselbe Definition wie `abrechnung._zeitbuchungen`.
    zeit_rows = list(
        TimeEntry.objects.filter(service_job__work_order_id=order.id)
        .select_related("category", "user")
        .order_by("started_at")
    )
    sekunden = 0
    abgeschlossene_arbeitszeit = 0
    laufende = 0
    eintraege = []
    for t in zeit_rows:
        if t.ended_at is None:
            laufende += 1
            dauer = None
        else:
            dauer = int((t.ended_at - t.started_at).total_seconds())
            if t.category.is_work_time:
                sekunden += dauer
                abgeschlossene_arbeitszeit += 1
        eintraege.append({
            "id": t.id,
            "started_at": t.started_at,
            "ended_at": t.ended_at,
            # Laufende Buchung: Dauer UNBEKANNT (None), nicht 0 h.
            "stunden": _stunden(dauer) if dauer is not None else None,
            "kategorie": t.category.name,
            "is_work_time": t.category.is_work_time,
            "mitarbeiter": t.user.display_name,
            "note": t.note,
        })
    zeiten = {
        "eintraege": eintraege,
        "laufende": laufende,
        # Keine abgeschlossene Arbeitszeitbuchung → Summe UNBEKANNT (None), nie 0,0 h.
        "summe_arbeitsstunden": (
            _stunden(sekunden) if abgeschlossene_arbeitszeit else None
        ),
    }

    material = [
        {
            "id": m.id,
            "description": m.description,
            "quantity": m.quantity,
            "unit": m.unit,
            "note": m.note,
            "service_job_id": m.service_job_id,
        }
        for m in MaterialEntry.objects.filter(
            service_job__work_order_id=order.id
        ).order_by("created_at")
    ]

    berichte = [
        {
            "id": r.id,
            "report_date": r.report_date,
            "status": r.status,
            "activity_text": r.activity_text,
            "hours_worked": r.hours_worked,
            "author": r.author.display_name if r.author_id else None,
            "signed_at": r.signed_at,
            "signed_by_name": r.signed_by_name,
        }
        for r in report_service.list_reports(work_order_id=order.id)
    ]
    # Soll-Ist: die vorhandene Rechenstelle, unverändert (keine Geldbeträge).
    soll_ist = report_service.soll_ist(order.id)

    # --- Abrechnung + Belege (invoicing/LESEN) -----------------------------
    # Bewusst STRENGER als der bestehende Einzel-Endpunkt
    # (`GET /workflow/work_orders/{id}/offene-abrechnung`, workflow/LESEN): Der
    # Abrechnungsstand führt **Einzelpreise** und Preisvorschläge aus früheren
    # Rechnungen — das sind Umsatzdaten. Ein Dossier, das mehr Bausteine an einer
    # Stelle bündelt, darf beim Tor nicht das schwächste Glied gelten lassen.
    # Die Werte selbst sind identisch (dieselbe Funktion).
    abrechnung = angebote = rechnungen = offene_posten = None
    if sicht.invoicing:
        abrechnung = abrechnung_service.offene_abrechnung(order.id)
        angebote = [
            {
                "id": q.id,
                "quote_number": q.quote_number,
                "title": q.title,
                "status": q.status,
                "quote_date": q.quote_date,
                "net_total": q.net_total,
                "gross_total": q.gross_total,
                "work_order_id": q.work_order_id,
            }
            for q in Quote.objects.filter(work_order_id=order.id).order_by(
                "-created_at"
            )
        ]
        rechnungen = [
            {
                "id": i.id,
                "invoice_number": i.invoice_number,
                "invoice_type": i.invoice_type,
                "status": i.status,
                "invoice_date": i.invoice_date,
                "net_total": i.net_total,
                "gross_total": i.gross_total,
                "work_order_id": i.work_order_id,
            }
            for i in Invoice.objects.filter(work_order_id=order.id).order_by(
                "-created_at"
            )
        ]
        offene_posten = _offene_posten(
            Invoice.objects.filter(work_order_id=order.id), heute
        )

    # --- Angebote OHNE Preise (invoicing/LESEN mit row_scope EIGENE, 0102) ---
    # **Der Kern dieses Slices**: Der Monteur öffnet seinen Auftrag und sieht, was
    # beauftragt ist — 12 m Kupferrohr DN20, sechs Thermostatventile. Der Auftrag ist
    # bereits als „meiner" geprüft (`guard_auftrag`); `angebote_begrenzen` hält
    # trotzdem beide Grenzen (Objekt + Status), damit diese Liste dieselbe Regel
    # spricht wie die Suche und die Beleg-API. Zwei Filter, eine Formulierung.
    angebote_mengen = None
    if sicht.invoicing_eigene:
        angebote_mengen = _angebote_mengen(
            Quote.objects.filter(work_order_id=order.id), sicht.actor_id
        )

    # Auftragsdokumente: für die Objektsicht lesbar (die Datei-API lässt
    # `work_order_id` an meinen Objekten zu) — deckungsgleich mit `_ziel_guard`.
    dokumente = _dokumente(work_order_id=order.id) if sicht.darf_content() else None

    return {
        "auftrag": {
            "id": order.id,
            "order_number": order.order_number,
            "title": order.title,
            "description": order.description,
            "status": order.status,
            "priority": order.priority,
            "billing_mode": order.billing_mode,
            "is_emergency": order.is_emergency,
            "desired_date": order.desired_date,
            "responsibility_scope": order.responsibility_scope,
            "responsibility_confirmed_at": order.responsibility_confirmed_at,
            "order_evidence_reference": order.order_evidence_reference,
            "property_id": order.property_id,
            "property_name": order.property.name,
            "property_city": order.property.address.city,
            "project_id": order.project_id,
            "project_name": order.project.name if order.project_id else None,
            "service_case_number": (
                order.service_case.case_number if order.service_case_id else None
            ),
        },
        "moegliche_uebergaenge": _moegliche_uebergaenge(order.status),
        "beteiligte": beteiligte,
        "einsaetze": einsaetze,
        "zeiten": zeiten,
        "material": material,
        "berichte": berichte,
        "soll_ist": soll_ist,
        "abrechnung_sichtbar": sicht.invoicing,
        "abrechnung": abrechnung,
        "belege_sichtbar": sicht.invoicing,
        "angebote": angebote,
        "rechnungen": rechnungen,
        # Preisfreie Angebotsliste (Objektsicht). Eigenes Flag, eigene Liste.
        "angebote_mengen_sichtbar": sicht.invoicing_eigene,
        "angebote_mengen": angebote_mengen,
        "offene_posten_sichtbar": sicht.invoicing,
        "offene_posten": offene_posten,
        "dokumente_sichtbar": sicht.darf_content(),
        "dokumente": dokumente,
    }


# ===========================================================================
# „Was ist hier offen?" — der Offen-Überblick eines Objekts
# ===========================================================================
#
# Gebaut für den KI-Assistenten (`db_core/ai/assistent.py`), aber bewusst hier und
# nicht dort: Wer entscheidet, welche Zeile ein Konto sehen darf, ist **eine** Frage
# mit **einer** Antwort, und die steht in diesem Modul. Ein zweiter Rechtefilter im
# KI-Pfad wäre die Stelle, an der die beiden eines Tages auseinanderlaufen.
#
# Zwei Ebenen, weil die Frage „Was ist alles offen?" zwei verschiedene Antworten hat:
#
#   * `offen_ueberblick()` — nur **Zählungen** je Kategorie. Das ist das Menü, mit
#     dem der Assistent zurückfragt („3 Vorgänge, 2 Aufträge, 1.240 € offen — was
#     genau?"). Klein genug, dass es das Prompt-Fenster nicht anfasst.
#   * `offen_detail()` — die **Zeilen einer** Kategorie, wenn der Nutzer sich
#     entschieden hat. So trägt jede Antwort nur eine Sache, statt das Modell mit
#     der gesamten Objekthistorie zu erschlagen.
#
# Eine Kategorie, die das Konto nicht lesen darf, fehlt **komplett** (kein Schlüssel
# mit Anzahl 0). Sonst verriete schon das Menü, dass es offene Posten gibt — an der
# Geldsperre vorbei, die `liegenschaft_dossier` sorgfältig zieht.

# Angebotsstatus, die keine Entscheidung mehr erwarten. „Offen" heißt: alles andere.
ANGEBOT_ENDSTATUS = ("ANGENOMMEN", "ABGELEHNT", "ABGELAUFEN", "ERSETZT")

# Prioritäten, die im Überblick als „dringend" zählen. Die Stufen stehen in
# `workflow.priority_level` (NOTFALL=1, DRINGEND=2, NORMAL=3) — hier die beiden oberen.
DRINGENDE_PRIORITAETEN = ("NOTFALL", "DRINGEND")

# Die Kategorien des Überblicks — Reihenfolge ist Anzeigereihenfolge.
OFFEN_KATEGORIEN = ("VORGANG", "AUFTRAG", "ANGEBOT", "FAELLIGKEIT", "POSTEN")

# So viele Zeilen liefert `offen_detail` je Kategorie höchstens.
OFFEN_DETAIL_LIMIT = 15


def _offene_vorgaenge_qs(property_id):
    return (
        ServiceCase.objects.filter(property_id=property_id)
        .exclude(status__in=VORGANG_ENDSTATUS)
        .order_by("-received_at")
    )


def _offene_auftraege_qs(property_id):
    return (
        WorkOrder.objects.filter(property_id=property_id)
        .exclude(status__in=AUFTRAG_ENDSTATUS)
        .order_by("-created_at")
    )


def _offene_angebote_qs(property_id, sicht):
    """Offene Angebote am Objekt — objektbegrenzt, wenn das Konto nur EIGENE hat.

    `angebote_begrenzen` ist fail-closed: Alles außer dem ausdrücklichen 'ALLE'
    landet in der Objektgrenze. Der Scope wird deshalb aus `sicht.invoicing`
    abgeleitet, nicht geraten.
    """
    qs = (
        Quote.objects.filter(property_id=property_id)
        .exclude(status__in=ANGEBOT_ENDSTATUS)
        .order_by("-created_at")
    )
    scope = "ALLE" if sicht.invoicing else "EIGENE"
    return objektsicht.angebote_begrenzen(qs, scope, sicht.actor_id)


def _darf_angebote(sicht):
    return sicht.invoicing or sicht.invoicing_eigene


def offen_ueberblick(property_id, sicht: Sicht) -> dict:
    """Zählungen der offenen Punkte eines Objekts — je Kategorie eine Zeile.

    Rückgabe: `{"VORGANG": {"anzahl": 3, "hinweis": "…"}, …}`. Kategorien ohne
    Recht fehlen ganz; Kategorien mit Anzahl 0 fehlen ebenfalls (der Assistent
    soll „nichts offen" sagen, nicht fünf Nullen vorlesen).

    **Beträge nur mit `invoicing` (Scope ALLE)** — dieselbe Grenze wie bei
    `liegenschaft_dossier.offene_posten`.
    """
    heute = date.today()
    aus: dict = {}

    if sicht.darf_workflow():
        vorgaenge = list(_offene_vorgaenge_qs(property_id))
        if vorgaenge:
            dringend = sum(
                1 for c in vorgaenge if c.priority in DRINGENDE_PRIORITAETEN)
            aus["VORGANG"] = {
                "anzahl": len(vorgaenge),
                "hinweis": f"{dringend} dringend" if dringend else None,
            }
        auftraege = list(_offene_auftraege_qs(property_id))
        if auftraege:
            aus["AUFTRAG"] = {"anzahl": len(auftraege), "hinweis": None}

    if _darf_angebote(sicht):
        anzahl = _offene_angebote_qs(property_id, sicht).count()
        if anzahl:
            aus["ANGEBOT"] = {"anzahl": anzahl, "hinweis": None}

    if sicht.darf_maintenance():
        faellig = list(faelligkeit_service.liste(
            status="OFFEN", property_id=property_id, stichtag=heute))
        if faellig:
            ueberfaellig = sum(1 for d in faellig if d.due_date < heute)
            aus["FAELLIGKEIT"] = {
                "anzahl": len(faellig),
                "hinweis": f"{ueberfaellig} überfällig" if ueberfaellig else None,
            }

    if sicht.invoicing:
        posten = _offene_posten(Invoice.objects.filter(property_id=property_id), heute)
        if posten["anzahl"]:
            hinweis = f"{posten['summe_offen']} € offen"
            if posten["anzahl_ueberfaellig"]:
                hinweis += f", davon {posten['summe_ueberfaellig']} € überfällig"
            aus["POSTEN"] = {"anzahl": posten["anzahl"], "hinweis": hinweis}

    return aus


def offen_detail(property_id, sicht: Sicht, kategorie: str) -> list[dict] | None:
    """Die offenen Zeilen EINER Kategorie — oder `None`, wenn das Recht fehlt.

    `None` heißt „darfst du nicht", `[]` heißt „nichts offen". Der Assistent muss
    beides unterscheiden können, sonst sagt er „nichts offen", wo er in Wahrheit
    nichts sehen darf.
    """
    heute = date.today()

    if kategorie == "VORGANG":
        if not sicht.darf_workflow():
            return None
        return [_vorgang_zeile(c)
                for c in _offene_vorgaenge_qs(property_id)[:OFFEN_DETAIL_LIMIT]]

    if kategorie == "AUFTRAG":
        if not sicht.darf_workflow():
            return None
        return [_auftrag_zeile(o)
                for o in _offene_auftraege_qs(property_id)[:OFFEN_DETAIL_LIMIT]]

    if kategorie == "ANGEBOT":
        if not _darf_angebote(sicht):
            return None
        qs = _offene_angebote_qs(property_id, sicht)[:OFFEN_DETAIL_LIMIT]
        # Preisfrei, wenn das Konto nur `invoicing_eigene` trägt — die Betragsspalte
        # ist genau der Weg, auf dem Geld an der Objektsicht vorbei herausrutscht.
        return [
            {
                "id": q.id,
                "quote_number": q.quote_number,
                "title": q.title,
                "status": q.status,
                "valid_until_date": q.valid_until_date,
                **({"gross_total": q.gross_total} if sicht.invoicing else {}),
            }
            for q in qs
        ]

    if kategorie == "FAELLIGKEIT":
        if not sicht.darf_maintenance():
            return None
        return [
            {
                "id": d.id,
                "kind": d.kind,
                "title": d.title,
                "due_date": d.due_date,
                "status": d.status,
                "is_ueberfaellig": d.due_date < heute,
            }
            for d in faelligkeit_service.liste(
                status="OFFEN", property_id=property_id, stichtag=heute
            )[:OFFEN_DETAIL_LIMIT]
        ]

    if kategorie == "POSTEN":
        if not sicht.invoicing:
            return None
        posten = _offene_posten(Invoice.objects.filter(property_id=property_id), heute)
        return posten["posten"][:OFFEN_DETAIL_LIMIT]

    return None
