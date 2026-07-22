"""Beleg-Service: Angebote (invoicing.quote) und Rechnungen (invoicing.invoice)
bis Status ENTWURF anlegen.

Dieser Slice deckt die Anlage bis ENTWURF ab (Liste/Detail lesend). Der
Veröffentlichungs-/Versand-Workflow (Nummernvergabe, Snapshot/Hash, PDF,
Summen-/Auftrags-Gate) ist bewusst nicht Teil dieses Slices — er hat viele
Vorbedingungen (B-30/GoBD).

Alle Writes über business_transaction. Positionsbeträge (net_amount) werden
kaufmännisch gerundet vorberechnet, exakt wie der DB-CHECK sie erzwingt
(round(quantity*unit_price*(1-discount/100), 2)) und auf die DB-Spaltenskalen
quantisiert, bevor gerechnet wird. Kopf-Summen werden aus den Positionen
abgeleitet; die Steuer wird je Steuergruppe gerundet (wie assert_*_totals).
Angebot und Rechnung teilen dieselbe Positions-/Summenlogik (invoice_line ist
strukturgleich zu quote_line).
"""
import hashlib
import json
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Exists, OuterRef
from django.utils import timezone as dj_timezone

from db_core.betriebszeit import betriebs_datum
from db_core.db_context import business_transaction
from db_core.gate_errors import as_business_error
from db_core.models import (
    Article,
    Assembly,
    BelegRubrik,
    BillingLink,
    CompanyProfile,
    Invoice,
    InvoiceAdvance,
    InvoiceLine,
    InvoiceParty,
    Organization,
    PartyAddress,
    Project,
    Property,
    Quote,
    QuoteLine,
    SalePriceGroup,
    ServiceCase,
    SiteReportLine,
    TaxCode,
    WorkOrder,
)
from db_core.services._validation import ensure_exists, ensure_party_usable

INVOICE_PARTY_ROLES = (
    "INVOICE_DEBTOR",
    "INVOICE_RECIPIENT",
    "REPRESENTATIVE",
    "COST_BEARER",
)

LINE_TYPES = (
    "MATERIAL",
    "ARBEITSZEIT",
    "PAUSCHALE",
    "FREMDLEISTUNG",
    "FAHRT",
    "ZUSCHLAG",
    "TEXT",
    "ZWISCHENSUMME",
)
TEXT_TYPES = ("TEXT", "ZWISCHENSUMME")

# § 35a EStG (Migration 0076): aus welchen Positionsarten lässt sich der
# begünstigte Arbeitskostenanteil ABLEITEN — und aus welchen nicht?
#
# Begünstigt sind Lohn-, Maschinen- und Fahrtkosten (inkl. der darauf
# entfallenden USt), Material ist es nicht. ARBEITSZEIT und FAHRT sind damit voll
# begünstigt, MATERIAL gar nicht. Für PAUSCHALE, FREMDLEISTUNG und ZUSCHLAG ist
# der Anteil NICHT ableitbar: eine Pauschale („Bad komplett") enthält beides, eine
# Fremdleistung bringt die Aufteilung des Subunternehmers mit, ein Zuschlag kann
# auf Lohn wie auf Material gehen. Dort bleibt der Anteil UNBESTIMMT (None), bis
# ihn jemand setzt — ein geratener Anteil wäre eine Falschaussage gegenüber dem
# Finanzamt (zu hoch: Steuerverkürzung; zu niedrig: verschenkter Kundenbonus).
LABOUR_VOLL = ("ARBEITSZEIT", "FAHRT")
LABOUR_KEIN = ("MATERIAL",)

# Positionsart (Migration 0036): ALTERNATIV = Ausweichvariante, BEDARF =
# Eventualposition. Beide tragen einen Betrag, zählen aber NICHT in die Summe —
# im PDF stehen sie in Klammern. Die DB-Summenprüfung filtert identisch.
LINE_KINDS = ("NORMAL", "ALTERNATIV", "BEDARF")
SUMMENWIRKSAM = "NORMAL"
INVOICE_TYPES = (
    "RECHNUNG",
    "ABSCHLAGSRECHNUNG",
    "TEILRECHNUNG",
    "SCHLUSSRECHNUNG",
    "GUTSCHRIFT",
    "STORNO",
)
# Kreditbelege (Korrekturbelege): sie tragen NEGATIVE Summen und fordern nichts.
# EINE Liste im ganzen Repo — `auswertungen` und `buchhaltung` ziehen von hier
# (`_CREDIT_TYPES` bleibt der modulinterne Kurzname).
CREDIT_TYPES = ("GUTSCHRIFT", "STORNO")
_CREDIT_TYPES = CREDIT_TYPES
# Abschlags-/Teil-/Schlussrechnung (Migration 0060). Abschlag und Teilrechnung
# verhalten sich technisch identisch (gleiche Tore, gleiche Verkettung); der Typ
# bleibt getrennt, weil er fachlich etwas anderes aussagt (Abschlag = Zwischen-
# forderung während der Ausführung, Teilrechnung = endgültige Abrechnung eines
# abgeschlossenen Leistungsteils).
ADVANCE_TYPES = ("ABSCHLAGSRECHNUNG", "TEILRECHNUNG")
FINAL_TYPE = "SCHLUSSRECHNUNG"
# Positionsart der Anrechnungsposition. PAUSCHALE ist die neutrale Betragsart der
# Codeliste (MATERIAL/ARBEITSZEIT/FAHRT wären fachlich falsch, ZUSCHLAG suggeriert
# eine Erhöhung). Die Anrechnung ist ein Pauschalabzug — ein eigener line_type
# wäre die Alternative gewesen, hätte aber die Codeliste (und damit jede
# Auswertung, die auf ihr steht) für einen reinen Darstellungsfall erweitert.
ANRECHNUNG_LINE_TYPE = "PAUSCHALE"
# DB-Spaltenskalen (Migration 0018/0019): quantity numeric(15,3), unit_price
# numeric(15,2), discount_percent numeric(7,4), net_amount numeric(15,2).
_Q_QTY = Decimal("0.001")
_Q_PRICE = Decimal("0.01")
_Q_DISCOUNT = Decimal("0.0001")
_CENT = Decimal("0.01")
# Zahlungsbedingungen (Migration 0058): discount_percent numeric(5,2),
# payment_term_days/discount_days integer in [0, 365].
_Q_SKONTO = Decimal("0.01")
_MAX_TAGE = 365


def _dec(value):
    return Decimal(str(value))


def _round2(value):
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def _tage_pruefen(wert, label):
    """Ein Tagesfeld (Zahlungsziel/Skontofrist) auf den DB-Wertebereich prüfen."""
    if wert in (None, ""):
        return None
    try:
        tage = int(wert)
    except (TypeError, ValueError):
        raise ValueError(f"{label} muss eine ganze Zahl von Tagen sein.")
    if not (0 <= tage <= _MAX_TAGE):
        raise ValueError(f"{label} muss zwischen 0 und {_MAX_TAGE} Tagen liegen.")
    return tage


def _zahlungsbedingungen_pruefen(
    invoice_type, *, payment_term_days, discount_percent, discount_days
):
    """Validiert die Zahlungsbedingungen und quantisiert den Skontosatz.

    Läuft VOR dem Schreiben, damit ein Eingabefehler als klare Meldung (422)
    endet und nicht als DB-CHECK-Verletzung (500). Die Regeln spiegeln exakt die
    Constraints aus Migration 0058:

    - Wertebereiche (0..365 Tage, Skonto echt zwischen 0 und 100 %),
    - **Paarigkeit**: Skontosatz und Skontofrist nur gemeinsam,
    - **Frist <= Ziel**,
    - **Kreditbelege tragen keine Zahlungsbedingungen** (eine Gutschrift fordert
      kein Geld — es gibt nichts zu skontieren).

    Gibt das normalisierte dict zurück (alle drei Schlüssel, ggf. None).
    """
    ziel = _tage_pruefen(payment_term_days, "Zahlungsziel")
    frist = _tage_pruefen(discount_days, "Skontofrist")

    satz = None
    if discount_percent not in (None, ""):
        try:
            satz = _dec(discount_percent).quantize(_Q_SKONTO, rounding=ROUND_HALF_UP)
        except (ArithmeticError, ValueError):
            raise ValueError("Skontosatz muss eine Zahl sein.")
        if not (Decimal(0) < satz < Decimal(100)):
            raise ValueError("Skontosatz muss größer als 0 und kleiner als 100 % sein.")

    if (satz is None) != (frist is None):
        raise ValueError(
            "Skontosatz und Skontofrist können nur gemeinsam gesetzt werden."
        )
    if frist is not None and ziel is not None and frist > ziel:
        raise ValueError("Die Skontofrist darf nicht nach dem Zahlungsziel liegen.")
    if invoice_type in _CREDIT_TYPES and any(
        v is not None for v in (ziel, satz, frist)
    ):
        raise ValueError(
            "Gutschriften und Stornobelege tragen keine Zahlungsbedingungen."
        )
    return {
        "payment_term_days": ziel,
        "discount_percent": satz,
        "discount_days": frist,
    }


def _frist_gegen_faelligkeit_pruefen(invoice_date, due_date, discount_days):
    """Die Skontofrist darf nicht nach der Fälligkeit enden.

    `payment_term_days` allein reicht als Schranke nicht: `due_date` ist die
    maßgebliche Fälligkeit und kann von Hand gesetzt sein (auch früher als das
    Zahlungsziel). Ohne diese Prüfung stünde auf dem Beleg „Skonto bei Zahlung
    bis 11.07., sonst netto bis 05.07." — eine Frist, die nach der Fälligkeit
    endet. Wird hart abgelehnt (422) statt still auf die Fälligkeit gedeckelt:
    welche der beiden Angaben falsch ist, weiß nur der Bearbeiter.
    """
    if discount_days is None or invoice_date is None or due_date is None:
        return
    ende = invoice_date + timedelta(days=int(discount_days))
    if ende > due_date:
        raise ValueError(
            f"Die Skontofrist endet am {ende.isoformat()} und damit nach der "
            f"Fälligkeit ({due_date.isoformat()}). Skontofrist oder "
            "Fälligkeitsdatum anpassen."
        )


def zahlungsbedingungen(invoice):
    """Abgeleitete Skonto-Angaben einer Rechnung (oder None).

    Einzige Rechenstelle für Skonto — PDF, API und Buchhaltung greifen hierauf zu,
    damit dieselbe Rechnung nirgends zwei verschiedene Skontobeträge zeigt.

    None, wenn kein Skonto vereinbart ist oder die Rechenbasis fehlt (ohne
    Belegdatum gibt es kein Fristende, ohne Bruttobetrag keinen Skontobetrag).
    """
    if invoice.discount_percent is None or invoice.discount_days is None:
        return None
    if invoice.invoice_date is None or invoice.gross_total is None:
        return None
    betrag = _round2(invoice.gross_total * invoice.discount_percent / Decimal(100))
    return {
        "discount_percent": invoice.discount_percent,
        "discount_days": int(invoice.discount_days),
        "payment_term_days": invoice.payment_term_days,
        "skonto_bis": invoice.invoice_date + timedelta(days=int(invoice.discount_days)),
        "skonto_betrag": betrag,
        "skonto_zahlbetrag": invoice.gross_total - betrag,
        "zahlbar_bis": invoice.due_date,
    }


def _arbeitskosten_anteil(idx, line, line_type, net):
    """Der § 35a-Anteil einer Betragsposition: ausdrücklich gesetzt oder abgeleitet.

    Ein ausdrücklich übergebener Wert gewinnt IMMER — auch auf einer MATERIAL-
    Zeile: Verbrauchsmittel (Schmier-, Reinigungs-, Dichtmittel) sind nach § 35a
    begünstigt, obwohl sie Material sind. Umgekehrt lässt sich eine ARBEITSZEIT-
    Zeile herunterstufen, wenn sie ausnahmsweise nicht im Haushalt anfiel.

    Der Wert muss ein TEIL des Positionsbetrags sein (gleiches Vorzeichen, nicht
    größer) — dieselbe Regel erzwingt der DB-CHECK; hier kommt sie als klare
    Meldung (422) statt als IntegrityError (500).

    None = unbestimmt. Kein stiller Default auf 0: das verschenkte dem Kunden den
    Bonus, ohne dass es jemand merkt.
    """
    wert = line.get("labour_net_amount")
    if wert in (None, ""):
        if line_type in LABOUR_VOLL:
            return net
        if line_type in LABOUR_KEIN:
            return Decimal("0.00")
        return None
    try:
        anteil = _dec(wert).quantize(_Q_PRICE, rounding=ROUND_HALF_UP)
    except (ArithmeticError, ValueError):
        raise ValueError(
            f"Position {idx}: Arbeitskostenanteil muss eine Zahl sein."
        )
    if anteil * net < 0 or abs(anteil) > abs(net):
        raise ValueError(
            f"Position {idx}: Der Arbeitskostenanteil muss ein Teil des "
            f"Positionsbetrags sein (höchstens {net}, gleiches Vorzeichen)."
        )
    return anteil


def _kalkulation_pruefen(idx, line, unit_price):
    """Prüft und normalisiert den Kalkulations-Snapshot einer Position.

    `unit_cost` (EK) und `markup_percent` (Aufschlag) werden zum Zeitpunkt der
    Belegerstellung eingefroren — der Artikelstamm darf sich später ändern, ohne
    die Marge eines bereits gestellten Belegs rückwirkend zu verfälschen.

    Der Aufschlag darf negativ sein (bewusster Verlust, z. B. Lockangebot); der
    EK nicht (DB-CHECK `unit_cost >= 0`). Wird kein Aufschlag übergeben, aber ein
    EK, leiten wir ihn aus EK und VK ab — sonst stünde in der Kalkulations-
    übersicht eine Lücke, wo die Zahl bekannt ist.
    """
    out = {}
    ek = line.get("unit_cost")
    if ek not in (None, ""):
        ek = _dec(ek).quantize(_Q_PRICE, rounding=ROUND_HALF_UP)
        if ek < 0:
            raise ValueError(f"Position {idx}: unit_cost darf nicht negativ sein.")
        out["unit_cost"] = ek
    else:
        ek = None

    markup = line.get("markup_percent")
    if markup not in (None, ""):
        out["markup_percent"] = _dec(markup).quantize(
            Decimal("0.001"), rounding=ROUND_HALF_UP
        )
    elif ek is not None and ek > 0 and unit_price is not None:
        # Aufschlag aus EK und VK ableiten: (VK - EK) / EK * 100
        out["markup_percent"] = (
            (unit_price - ek) / ek * Decimal(100)
        ).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)

    for feld, model, label in (
        ("sale_price_group_id", SalePriceGroup, "Verkaufspreisgruppe"),
        ("source_article_id", Article, "Artikel"),
        ("source_assembly_id", Assembly, "Leistung"),
    ):
        wert = line.get(feld)
        if wert:
            ensure_exists(model, wert, f"Position {idx}: {label}")
            out[feld] = wert
    return out


def _prepare_lines(lines):
    """Validiert und berechnet Positionen; gibt (prepared, net, tax, gross).

    Wird vor der Transaktion aufgerufen, damit Eingabefehler als klare
    ValueError (→422) statt als DB-IntegrityError (→500) enden.

    Positionen mit `line_kind` ALTERNATIV oder BEDARF tragen einen Betrag, gehen
    aber NICHT in die Kopfsummen ein — exakt so filtert auch die DB-Prüfung
    `assert_*_totals` (Migration 0036). Weichen beide voneinander ab, weist das
    Veröffentlichungstor den Beleg ab.
    """
    prepared = []
    for idx, line in enumerate(lines or [], start=1):
        lt = line.get("line_type")
        desc = (line.get("description") or "").strip()
        if lt not in LINE_TYPES:
            raise ValueError(f"Ungültiger line_type '{lt}'.")
        if not desc:
            raise ValueError(f"Position {idx}: description darf nicht leer sein.")
        kind = line.get("line_kind") or SUMMENWIRKSAM
        if kind not in LINE_KINDS:
            raise ValueError(
                f"Position {idx}: ungültige line_kind '{kind}' "
                f"(erlaubt: {', '.join(LINE_KINDS)})."
            )
        if kind != SUMMENWIRKSAM and lt in TEXT_TYPES:
            raise ValueError(
                f"Position {idx}: {lt} trägt keinen Betrag und kann daher weder "
                "Alternativ- noch Bedarfsposition sein."
            )
        row = {
            "position_number": idx,
            "line_type": lt,
            "line_kind": kind,
            "description": desc,
        }
        # Abschnitt (Rubrik) als 1-basierter Index in die Rubrikenliste. Die
        # UUID kennen wir erst nach dem INSERT, deshalb erst hier merken.
        rubrik = line.get("rubrik")
        if rubrik not in (None, ""):
            try:
                rubrik = int(rubrik)
            except (TypeError, ValueError):
                raise ValueError(f"Position {idx}: rubrik muss eine Abschnittsnummer sein.")
            if rubrik < 1:
                raise ValueError(f"Position {idx}: rubrik muss >= 1 sein.")
            row["_rubrik"] = rubrik
        if lt not in TEXT_TYPES:
            if line.get("quantity") is None or line.get("unit_price") is None:
                raise ValueError(
                    f"Position {idx}: quantity und unit_price sind Pflicht."
                )
            tax_code = line.get("tax_code")
            tc = TaxCode.objects.filter(code=tax_code).first() if tax_code else None
            if tc is None:
                raise ValueError(
                    f"Position {idx}: gültiger tax_code Pflicht (z. B. DE_19)."
                )
            # Auf DB-Spaltenskalen quantisieren, BEVOR net berechnet wird.
            quantity = _dec(line["quantity"]).quantize(_Q_QTY, rounding=ROUND_HALF_UP)
            unit_price = _dec(line["unit_price"]).quantize(
                _Q_PRICE, rounding=ROUND_HALF_UP
            )
            has_discount = line.get("discount_percent") not in (None, "")
            discount = (
                _dec(line["discount_percent"]).quantize(
                    _Q_DISCOUNT, rounding=ROUND_HALF_UP
                )
                if has_discount
                else Decimal(0)
            )
            if quantity <= 0:
                raise ValueError(f"Position {idx}: quantity muss > 0 sein.")
            if unit_price < 0:
                raise ValueError(f"Position {idx}: unit_price darf nicht negativ sein.")
            if not (Decimal(0) <= discount < Decimal(100)):
                raise ValueError(
                    f"Position {idx}: discount_percent muss in [0, 100) liegen."
                )
            net = _round2(unit_price * quantity * (Decimal(1) - discount / Decimal(100)))
            row.update(
                quantity=quantity,
                unit=line.get("unit"),
                unit_price=unit_price,
                discount_percent=(discount if has_discount else None),
                tax_code_id=tc.code,
                tax_rate_percent=tc.rate_percent,
                net_amount=net,
                labour_net_amount=_arbeitskosten_anteil(idx, line, lt, net),
            )
            row.update(_kalkulation_pruefen(idx, line, unit_price))
        elif line.get("labour_net_amount") not in (None, ""):
            raise ValueError(
                f"Position {idx}: {lt} trägt keinen Betrag und damit auch keinen "
                "Arbeitskostenanteil."
            )
        prepared.append(row)

    net, tax, gross = _totals(prepared)
    return prepared, net, tax, gross


def _totals(prepared):
    """Kopf-Summen (netto, steuer, brutto) aus vorbereiteten Positionszeilen.

    Steuer je Steuergruppe (tax_code, tax_rate_percent) gerundet — exakt wie der
    DB-CHECK `assert_invoice_totals` (B-19). Alternativ-/Bedarfspositionen und
    Text-/Zwischensummenzeilen bleiben außen vor (Migration 0036).

    Negative Positionsbeträge (Kreditbeleg, Anrechnung eines Abschlags) laufen
    hier ohne Sonderfall durch: sie mindern die Gruppe, und die Steuer wird auf
    dem geminderten Gruppennetto gerundet. Genau so rechnet auch die DB.
    """
    net_total = Decimal("0.00")
    group_net = defaultdict(lambda: Decimal("0.00"))
    for row in prepared:
        if row["line_type"] in TEXT_TYPES or row["line_kind"] != SUMMENWIRKSAM:
            continue
        net_total += row["net_amount"]
        group_net[(row["tax_code_id"], row["tax_rate_percent"])] += row["net_amount"]
    tax_total = Decimal("0.00")
    for (_code, rate), net in group_net.items():
        tax_total += _round2(net * rate / Decimal(100))
    return net_total, tax_total, net_total + tax_total


def _prepare_rubriken(rubriken, prepared):
    """Validiert die Abschnitte und prüft, dass jede Positions-Referenz existiert.

    Gibt die normalisierten Rubriken zurück. Ohne diese Vorabprüfung liefe eine
    Position mit `rubrik=3` bei nur zwei Abschnitten in einen IntegrityError
    (500) statt in eine klare Meldung (422).
    """
    normalisiert = []
    for idx, r in enumerate(rubriken or [], start=1):
        titel = (r.get("title") or "").strip()
        if not titel:
            raise ValueError(f"Abschnitt {idx}: title darf nicht leer sein.")
        beschreibung = r.get("description")
        normalisiert.append(
            {
                "position_number": idx,
                "title": titel,
                "description": (beschreibung or "").strip() or None,
            }
        )
    hoechste = len(normalisiert)
    for row in prepared:
        ref = row.get("_rubrik")
        if ref is not None and ref > hoechste:
            raise ValueError(
                f"Position {row['position_number']}: Abschnitt {ref} existiert nicht "
                f"({hoechste} Abschnitt(e) angegeben)."
            )
    return normalisiert


def _write_lines(prepared, rubrik_ids, *, model, **beleg_fk):
    """Schreibt Positionen und löst dabei die Abschnitts-Referenz in die UUID auf."""
    for row in prepared:
        daten = dict(row)
        ref = daten.pop("_rubrik", None)
        if ref is not None:
            daten["rubrik_id"] = rubrik_ids[ref - 1]
        model.objects.create(id=uuid.uuid4(), **beleg_fk, **daten)


def _work_order_pruefen(work_order_id, *, property_id, project_id):
    """Der Auftragsbezug eines Angebots — der Anker des Soll-Ist-Abgleichs.

    Das Angebot ist das **Soll** eines Auftrags (Migration 0080). Damit sich kein
    fremdes Soll an eine Baustelle hängen lässt, muss der Auftrag

      * existieren,
      * zur **selben Liegenschaft** gehören (die DB erzwingt das ohnehin über den
        zusammengesetzten FK `(work_order_id, property_id)` aus Migration 0018 —
        hier wird daraus ein verständlicher Fachfehler statt eines 500ers),
      * und, wenn das Angebot ein Projekt nennt, zu **diesem Projekt** gehören.

    Ein Auftrag ohne Projekt an einem projektlosen Angebot ist der Normalfall.
    """
    if work_order_id is None:
        return None
    wo = WorkOrder.objects.filter(id=work_order_id).only(
        "id", "property_id", "project_id"
    ).first()
    if wo is None:
        raise ValueError("Auftrag nicht gefunden.")
    if str(wo.property_id) != str(property_id):
        raise ValueError(
            "Der Auftrag gehört zu einer anderen Liegenschaft als das Angebot."
        )
    if project_id is not None and str(wo.project_id or "") != str(project_id):
        raise ValueError(
            "Der Auftrag gehört zu einem anderen Projekt als das Angebot."
        )
    return wo.id


def _vorgang_pruefen(service_case_id, *, property_id):
    """Der Vorgangsbezug eines Belegs: existiert und gehört zur selben Liegenschaft.

    Der zusammengesetzte FK `(service_case_id, property_id)` (Migration 0113)
    erzwingt die Liegenschaftsgleichheit ohnehin — hier wird daraus ein
    verständlicher Fachfehler (422) statt eines 500ers. Gibt den geladenen Vorgang
    zurück (oder None), damit der Aufrufer sein Projekt erben/abgleichen kann.
    """
    if service_case_id is None:
        return None
    case = ServiceCase.objects.filter(id=service_case_id).only(
        "id", "property_id", "project_id"
    ).first()
    if case is None:
        raise ValueError("Vorgang nicht gefunden.")
    if str(case.property_id) != str(property_id):
        raise ValueError(
            "Der Vorgang gehört zu einer anderen Liegenschaft als der Beleg."
        )
    return case


def _beleg_bezug_aufloesen(
    *, property_id, service_case_id, work_order_id, project_id,
    pruefe_auftrag_projekt=True,
):
    """Löst Vorgangs-, Auftrags- und Projektbezug eines Belegs auf und erbt fehlende.

    Der Beleg (Angebot/Rechnung) hängt am **Vorgang** (service_case), optional
    zusätzlich an einem **Auftrag** und einem **Projekt** (Migration 0113). Die drei
    Bezüge müssen zueinander und zur Liegenschaft passen; wo möglich, wird der
    fehlende geerbt:

      * Der Auftrag muss existieren und zur selben Liegenschaft gehören. Nennt der
        Beleg einen Vorgang, muss der Auftrag zu DIESEM Vorgang gehören.
      * Ohne eigenen Vorgang, aber mit Auftrag an einem Vorgang: der Beleg ERBT den
        Vorgang vom Auftrag.
      * Hat der Vorgang ein Projekt und der Aufruf keins: der Beleg ERBT das Projekt.
        Ein abweichend übergebenes Projekt ist nur beim Angebot ein Fachfehler
        (`pruefe_auftrag_projekt`); die Rechnung lässt den Widerspruch wie vor
        0113 durch.

    `pruefe_auftrag_projekt` steuert die **Auftrag↔Projekt**-Prüfung — sie ist
    absichtlich pro Aufrufer unterschiedlich, weil sie es historisch war (kein
    DB-FK erzwingt sie, nur der Service):

      * **Angebot** (True): Der Auftrag muss zum (ggf. geerbten) Projekt gehören —
        wie bisher `_work_order_pruefen`.
      * **Rechnung** (False): keine Auftrag↔Projekt-Prüfung. Eine Rechnung wird
        regelmäßig einem Projekt zugeordnet, während ihr Auftrag (noch) projektlos
        ist — die Auswertungen bauen genau diese Konstellation. Erzwänge man die
        Gleichheit, bräche das die Projekt-Marge-Rechnung; die DB kennt diese Regel
        für die Rechnung ohnehin nicht (nur den `(work_order_id, property_id)`-FK).

    Gibt (service_case_id, work_order_id, project_id) mit aufgelösten Werten zurück.
    """
    wo = None
    if work_order_id is not None:
        wo = WorkOrder.objects.filter(id=work_order_id).only(
            "id", "property_id", "project_id", "service_case_id"
        ).first()
        if wo is None:
            raise ValueError("Auftrag nicht gefunden.")
        if str(wo.property_id) != str(property_id):
            raise ValueError(
                "Der Auftrag gehört zu einer anderen Liegenschaft als der Beleg."
            )

    # Vorgang bestimmen: übergeben ODER vom Auftrag geerbt.
    if service_case_id is None and wo is not None and wo.service_case_id is not None:
        service_case_id = wo.service_case_id
    case = _vorgang_pruefen(service_case_id, property_id=property_id)

    # Auftrag und Vorgang müssen zusammenpassen — auch, wenn der Vorgang übergeben
    # wurde und der Auftrag an einem anderen Vorgang hängt.
    if wo is not None and service_case_id is not None:
        if str(wo.service_case_id or "") != str(service_case_id):
            raise ValueError(
                "Der Auftrag gehört zu einem anderen Vorgang als der Beleg."
            )

    # Projekt: vom Vorgang erben bzw. gegen ihn abgleichen. Der Abgleich folgt
    # demselben Schalter wie Auftrag↔Projekt: Auf dem Rechnungspfad lief vor 0113
    # z. B. `rechnung_aus_auftrag` mit `project_id` des Auftrags durch, auch wenn
    # dessen Vorgang (inkonsistent, aber anlegbar) ein anderes Projekt trägt —
    # eine Rechnung darf daran nicht scheitern.
    if case is not None and case.project_id is not None:
        if project_id is None:
            project_id = case.project_id
        elif pruefe_auftrag_projekt and str(project_id) != str(case.project_id):
            raise ValueError(
                "Der Beleg nennt ein anderes Projekt als sein Vorgang."
            )

    # Auftrag muss zum (ggf. geerbten) Projekt gehören — nur beim Angebot (s. o.).
    if (
        pruefe_auftrag_projekt
        and wo is not None
        and project_id is not None
        and str(wo.project_id or "") != str(project_id)
    ):
        raise ValueError(
            "Der Auftrag gehört zu einem anderen Projekt als der Beleg."
        )

    return service_case_id, work_order_id, project_id


def create_quote(
    actor_app_user_id,
    *,
    property_id,
    title,
    project_id=None,
    work_order_id=None,
    service_case_id=None,
    quote_date=None,
    valid_until_date=None,
    cover_letter=None,
    lines=None,
    rubriken=None,
):
    """Legt ein Angebot (Status ENTWURF) mit Positionen und Abschnitten an.

    `work_order_id` ordnet das Angebot einem **Auftrag** zu. Diese Zuordnung ist
    die Aussage „das ist das Soll dieser Baustelle" — der Soll-Ist-Abgleich am
    Bericht (0080) stützt sich ausschließlich darauf.

    `service_case_id` verankert das Angebot am **Vorgang** (Migration 0113); fehlt
    es, aber der Auftrag hängt an einem Vorgang, wird dieser geerbt. Projekt und
    Vorgang werden gegeneinander abgeglichen bzw. geerbt (`_beleg_bezug_aufloesen`).
    """
    if not title or not title.strip():
        raise ValueError("title darf nicht leer sein.")
    ensure_exists(Property, property_id, "Liegenschaft")
    ensure_exists(Project, project_id, "Projekt")
    service_case_id, work_order_id, project_id = _beleg_bezug_aufloesen(
        property_id=property_id,
        service_case_id=service_case_id,
        work_order_id=work_order_id,
        project_id=project_id,
    )
    prepared, net_total, tax_total, gross_total = _prepare_lines(lines)
    rubriken_norm = _prepare_rubriken(rubriken, prepared)

    with business_transaction(actor_app_user_id):
        quote = Quote.objects.create(
            id=uuid.uuid4(),
            property_id=property_id,
            project_id=project_id,
            work_order_id=work_order_id,
            service_case_id=service_case_id,
            title=title.strip(),
            status="ENTWURF",
            quote_date=quote_date,
            valid_until_date=valid_until_date,
            cover_letter=(
                cover_letter.strip() if cover_letter and cover_letter.strip() else None
            ),
            net_total=net_total,
            tax_total=tax_total,
            gross_total=gross_total,
            version=1,
        )
        rubrik_ids = [
            BelegRubrik.objects.create(id=uuid.uuid4(), quote_id=quote.id, **r).id
            for r in rubriken_norm
        ]
        _write_lines(prepared, rubrik_ids, model=QuoteLine, quote_id=quote.id)
        quote.refresh_from_db()
    return quote


# Solange ein Angebot in diesen Status steht, sind Kopf, Abschnitte und Positionen
# änderbar (DB-Trigger protect_quote_lines / protect_beleg_rubrik). Ab VERSENDET
# friert die Datenbank den Beleg ein (B-30).
QUOTE_EDITIERBAR = ("ENTWURF", "INTERN_GEPRUEFT", "FREIGEGEBEN")


def _soll_referenzen_pruefen(quote, *, lines_ersetzt, zuordnung_geaendert):
    """Ein Soll, auf das sich ein Nachweis stützt, darf ihm nicht weggezogen werden.

    Referenziert eine Berichtsposition eine Zeile dieses Angebots
    (`site_report_line.source_quote_line_id`, Migration 0080), sind ZWEI Eingriffe
    gesperrt:

    * **Der Positionssatz.** Sein Ersatz ist ein Delete+Insert; das DELETE liefe in
      die Fremdschlüsselverletzung 23503 und schlüge als 500 durch.
    * **Die Auftragszuordnung** (`work_order_id`) — ändern *und* lösen. Sie ist die
      Bedingung dafür, dass die Angebotsposition überhaupt ein zulässiges Soll dieses
      Berichts ist (`site_report._erlaubte_angebote`). Fällt sie weg, fällt das Soll
      auf 0, die längst erfasste Position wird im Abgleich zum ZUSATZ — und der
      Monteur sitzt in der **Sackgasse**: sein Entwurfsbericht ist nicht mehr
      speicherbar („Die Angebotsposition gehört nicht zu einem Angebot dieses
      Auftrags"), und es gibt keinen Weg, die Herkunft einer Zeile zu lösen. Ein
      Ersatzangebot ist der vorgesehene Weg.

    **Diese Funktion läuft INNERHALB der `business_transaction`** — davor wäre sie
    nebenläufig wertlos.

    Beim Positionsersatz werden die Quellzeilen zusätzlich `FOR UPDATE` gesperrt:
    ohne die Sperre könnte parallel ein Bericht eine dieser Zeilen als Soll aufnehmen,
    ohne dass unser `exists()` ihn sähe (READ COMMITTED) — das DELETE liefe dann doch
    in den 23503. `FOR UPDATE` kollidiert mit dem `FOR KEY SHARE`, das der
    Fremdschlüssel beim Einfügen der Berichtsposition nimmt: der Nebenläufer wartet,
    und wir sehen ihn. Für die reine Zuordnungsänderung braucht es die Sperre nicht —
    dort wird keine Zeile gelöscht, es gibt also auch keinen 23503.
    """
    if not (lines_ersetzt or zuordnung_geaendert):
        return
    if lines_ersetzt:
        list(
            QuoteLine.objects.filter(quote_id=quote.id)
            .select_for_update()
            .values_list("id", flat=True)
        )
    if not SiteReportLine.objects.filter(
        source_quote_line__quote_id=quote.id
    ).exists():
        return
    if lines_ersetzt:
        raise ValueError(
            "Positionen dieses Angebots sind bereits als Soll in einem "
            "Baustellenbericht referenziert und können nicht mehr geändert werden. "
            "Bitte ein Ersatzangebot verwenden."
        )
    raise ValueError(
        "Positionen dieses Angebots sind bereits als Soll in einem "
        "Baustellenbericht referenziert; die Zuordnung kann nicht gelöst oder auf "
        "einen anderen Auftrag geändert werden."
    )


def update_quote(
    actor_app_user_id,
    *,
    quote_id,
    title=None,
    quote_date=...,
    valid_until_date=...,
    work_order_id=...,
    project_id=...,
    cover_letter=...,
    lines=None,
    rubriken=None,
):
    """Ändert ein Angebot, solange es nicht versendet ist.

    Positionen und Abschnitte werden **vollständig ersetzt**, wenn `lines`
    übergeben wird — der Editor schickt immer den ganzen Beleg. Ein Teil-Update
    einzelner Positionen wäre bei umsortierten Positionsnummern nicht eindeutig.

    `quote_date`/`valid_until_date`/`work_order_id`/`project_id` nutzen den Sentinel
    `...`, damit ein bewusstes Leeren (None) von „nicht ändern" unterscheidbar bleibt.

    **`project_id` (Verschieben in ein anderes Projekt)** ist — anders als
    `work_order_id` — nur im editierbaren Angebot (Entwurfsphase) setzbar: ab
    VERSENDET friert die DB ohnehin alle Spalten außer dem Status ein (B-30). Wird
    das Projekt gewechselt, während ein Auftrag hängt, muss dieser Auftrag zum neuen
    Projekt passen (der zusammengesetzte FK sichert nur die Liegenschaft) — sonst 422.

    **`work_order_id` ist in JEDEM Status setz- und lösbar** (Migration 0082). Der
    reale Ablauf ist „Angebot versenden → Kunde nimmt an → *dann* Auftrag anlegen";
    wäre die Zuordnung ab Versand gesperrt, wäre sie genau dann nicht möglich, wenn
    man sie braucht — und das Soll des Abgleichs bliebe leer. Die Zuordnung ist ein
    interner Verweis, kein Beleginhalt: sie ändert weder Betrag noch Position noch
    das Sichtbild des Kundendokuments, wird auditiert, und der zusammengesetzte FK
    `(work_order_id, property_id)` verhindert weiterhin die Zuordnung an einen
    Auftrag einer fremden Liegenschaft.

    Alles **andere** bleibt ab VERSENDET unveränderlich (B-30): Titel, Daten,
    Positionen, Abschnitte, Beträge. Der DB-Trigger `invoicing.freeze_sent_quote`
    ist dabei die letzte Instanz — der Service liefert nur die Fachmeldung.

    **Positionen, die bereits als Soll in einem Baustellenbericht referenziert sind,
    sperren den Positionssatz UND die Auftragszuordnung** — siehe
    `_soll_referenzen_pruefen`. Ein Ersatzangebot ist der vorgesehene Weg.
    """
    quote = Quote.objects.filter(id=quote_id).first()
    if quote is None:
        raise ValueError("Angebot nicht gefunden.")
    # Der Statuszwang gilt für den BELEGINHALT — nicht für die Auftragszuordnung.
    # Ein Aufruf, der nur `work_order_id` setzt, läuft auch am versendeten Angebot.
    inhalt_geaendert = (
        title is not None
        or quote_date is not ...
        or valid_until_date is not ...
        or cover_letter is not ...
        or lines is not None
    )
    if inhalt_geaendert and quote.status not in QUOTE_EDITIERBAR:
        raise ValueError(
            f"Angebot im Status {quote.status} ist unveränderlich (versendet). "
            "Nur die Auftragszuordnung lässt sich noch ändern."
        )

    kopf = {}
    if title is not None:
        if not title.strip():
            raise ValueError("title darf nicht leer sein.")
        kopf["title"] = title.strip()
    if quote_date is not ...:
        kopf["quote_date"] = quote_date
    if valid_until_date is not ...:
        kopf["valid_until_date"] = valid_until_date
    # Anschreiben-Freitext (Dokumente-9): Beleginhalt — durch das Gate oben ab
    # VERSENDET gesperrt; leer wird zu NULL normalisiert. Der DB-Trigger
    # invoicing.freeze_sent_quote friert die Spalte zusätzlich ein (letzte Instanz).
    if cover_letter is not ...:
        kopf["cover_letter"] = (
            cover_letter.strip() if cover_letter and cover_letter.strip() else None
        )
    # Zielprojekt zuerst bestimmen: die Auftragsprüfung darunter muss gegen das
    # NEUE Projekt laufen, nicht gegen das alte.
    ziel_project = quote.project_id
    if project_id is not ...:
        if quote.status not in QUOTE_EDITIERBAR:
            raise ValueError(
                f"Angebot im Status {quote.status} lässt sich keinem anderen Projekt "
                "mehr zuordnen (versendet); nur vor dem Versand verschiebbar "
                "(Entwurf/intern geprüft/freigegeben)."
            )
        ensure_exists(Project, project_id, "Projekt")
        kopf["project_id"] = project_id
        ziel_project = project_id

    zuordnung_geaendert = False
    if work_order_id is not ...:
        neu = _work_order_pruefen(
            work_order_id,
            property_id=quote.property_id,
            project_id=ziel_project,
        )
        kopf["work_order_id"] = neu
        zuordnung_geaendert = str(neu or "") != str(quote.work_order_id or "")
    elif project_id is not ... and quote.work_order_id is not None:
        # Projekt gewechselt, Auftrag bleibt: der hängende Auftrag muss zum neuen
        # Projekt passen — sonst stünde ein fremdes Soll an der Baustelle (422).
        _work_order_pruefen(
            quote.work_order_id,
            property_id=quote.property_id,
            project_id=ziel_project,
        )

    prepared = rubriken_norm = None
    if lines is not None:
        prepared, net_total, tax_total, gross_total = _prepare_lines(lines)
        rubriken_norm = _prepare_rubriken(rubriken, prepared)
        kopf.update(
            net_total=net_total, tax_total=tax_total, gross_total=gross_total
        )

    with as_business_error():
        with business_transaction(actor_app_user_id):
            # Erst das Tor, dann der Schreibvorgang — und beides in DERSELBEN
            # Transaktion (siehe `_soll_referenzen_pruefen`).
            _soll_referenzen_pruefen(
                quote,
                lines_ersetzt=prepared is not None,
                zuordnung_geaendert=zuordnung_geaendert,
            )
            if kopf:
                for feld, wert in kopf.items():
                    setattr(quote, feld, wert)
                quote.save(update_fields=list(kopf) + ["updated_at"])
            if prepared is not None:
                # Reihenfolge: erst Positionen (sie verweisen auf Rubriken), dann
                # die Rubriken selbst — sonst greift die Fremdschlüsselprüfung.
                QuoteLine.objects.filter(quote_id=quote.id).delete()
                BelegRubrik.objects.filter(quote_id=quote.id).delete()
                rubrik_ids = [
                    BelegRubrik.objects.create(
                        id=uuid.uuid4(), quote_id=quote.id, **r
                    ).id
                    for r in rubriken_norm
                ]
                _write_lines(prepared, rubrik_ids, model=QuoteLine, quote_id=quote.id)
    quote.refresh_from_db()
    return quote


def _angebot_vorlage_zeilen(quote):
    """Positionen und Abschnitte eines Angebots als create_quote-Eingabe.

    Eine **wertgleiche Vorlagenkopie**: Preis, Rabatt, EK/Aufschlag und der
    § 35a-Anteil werden 1:1 übernommen (kein Neuberechnen aus dem Stamm). Anders
    als die Rechnungs-Angebotskopie (`abrechnung._quote_zeilen_kopieren`) bleiben
    **ALTERNATIV- und BEDARF-Positionen erhalten**: die Kopie ist eine Arbeits-
    vorlage, kein Vertragsdokument — was am Ende beauftragt wird, entscheidet der
    Bearbeiter im neuen Entwurf.
    """
    rubriken = sorted(quote.rubriken.all(), key=lambda r: r.position_number)
    rubrik_nummer = {r.id: idx for idx, r in enumerate(rubriken, start=1)}
    lines = []
    for ql in sorted(quote.lines.all(), key=lambda l: l.position_number):
        row = {
            "line_type": ql.line_type,
            "line_kind": ql.line_kind,
            "description": ql.description,
            "rubrik": rubrik_nummer.get(ql.rubrik_id),
        }
        if ql.line_type not in TEXT_TYPES:
            row.update(
                quantity=ql.quantity,
                unit=ql.unit,
                unit_price=ql.unit_price,
                discount_percent=ql.discount_percent,
                tax_code=ql.tax_code_id,
                # None bleibt None — eine Kopie erfindet keine § 35a-Bestimmtheit.
                labour_net_amount=ql.labour_net_amount,
                unit_cost=ql.unit_cost,
                markup_percent=ql.markup_percent,
                sale_price_group_id=ql.sale_price_group_id,
                source_article_id=ql.source_article_id,
                source_assembly_id=ql.source_assembly_id,
            )
        lines.append(row)
    rubriken_out = [{"title": r.title, "description": r.description} for r in rubriken]
    return lines, rubriken_out


def kopiere_angebot(actor_app_user_id, *, quote_id, property_id=..., project_id=...):
    """Dupliziert ein Angebot als **neuen Entwurf** (Titel „… (Kopie)").

    Kopf, Abschnitte und Positionen werden wertgleich übernommen; das Ergebnis ist
    ein frischer ENTWURF **ohne Snapshot/Hash** (GoBD: eine Kopie ist ein neuer
    Beleg mit eigener Nummer beim Versand, kein Duplikat des festgeschriebenen
    Originals). Der **Auftragsbezug wird nicht mitkopiert** — das Soll gehört genau
    einer Baustelle und darf nicht doppelt hängen; der Bearbeiter ordnet neu zu.
    Belegdatum und Gültigkeit bleiben leer.

    Ziel-Liegenschaft/-Projekt sind wählbar; der Sentinel `...` erbt den Wert der
    Quelle. Aus jedem Status kopierbar (auch aus einem versendeten Angebot heraus) —
    die Quelle wird nur gelesen.
    """
    quote = (
        Quote.objects.filter(id=quote_id)
        .prefetch_related("lines", "rubriken")
        .first()
    )
    if quote is None:
        raise ValueError("Angebot nicht gefunden.")
    ziel_property = quote.property_id if property_id is ... else property_id
    ziel_project = quote.project_id if project_id is ... else project_id
    lines, rubriken = _angebot_vorlage_zeilen(quote)
    return create_quote(
        actor_app_user_id,
        property_id=ziel_property,
        title=f"{quote.title} (Kopie)",
        project_id=ziel_project,
        work_order_id=None,
        quote_date=None,
        valid_until_date=None,
        lines=lines,
        rubriken=rubriken,
    )


# Eine Rechnung ist nur im Entwurf editierbar; ab VEROEFFENTLICHT eingefroren (B-30).
INVOICE_EDITIERBAR = ("ENTWURF",)


def update_invoice(
    actor_app_user_id,
    *,
    invoice_id,
    invoice_date=...,
    due_date=...,
    payment_term_days=...,
    discount_percent=...,
    discount_days=...,
    show_labour_costs=...,
    lines=None,
    rubriken=None,
):
    """Ändert eine Rechnung, solange sie ENTWURF ist (danach eingefroren, B-30).

    Wie beim Angebot werden Positionen und Abschnitte **vollständig ersetzt**, wenn
    `lines` übergeben wird (der Editor schickt den ganzen Beleg). Die Rechnung hat
    keinen Titel (Identität über Typ + Nummer); `invoice_type`/Bezüge bleiben
    unverändert. `invoice_date`/`due_date` nutzen den Sentinel `...`, damit ein
    bewusstes Leeren (None) von „nicht ändern" unterscheidbar bleibt.
    """
    invoice = Invoice.objects.filter(id=invoice_id).first()
    if invoice is None:
        raise ValueError("Rechnung nicht gefunden.")
    if invoice.status not in INVOICE_EDITIERBAR:
        raise ValueError(
            f"Rechnung im Status {invoice.status} ist unveränderlich (veröffentlicht)."
        )
    # Defense-in-depth: Gutschriften/Stornos entstehen ausschließlich als
    # veröffentlichte Folgebelege (create_cancellation/create_correction) und sind
    # daher nie ENTWURF — der Editor ändert sie grundsätzlich nicht.
    if invoice.invoice_type in _CREDIT_TYPES:
        raise ValueError(
            "Gutschriften und Stornobelege werden nicht über den Editor geändert."
        )

    kopf = {}
    if invoice_date is not ...:
        kopf["invoice_date"] = invoice_date
    if due_date is not ...:
        kopf["due_date"] = due_date
    if show_labour_costs is not ...:
        kopf["show_labour_costs"] = bool(show_labour_costs)

    # Zahlungsbedingungen gegen den RESULTIERENDEN Zustand prüfen: wer nur den
    # Skontosatz schickt, während die Frist schon am Beleg steht, ändert einen
    # gültigen Beleg — die Paarigkeit gilt für das Ergebnis, nicht für den Payload.
    bedingungen = {
        "payment_term_days": (
            invoice.payment_term_days if payment_term_days is ... else payment_term_days
        ),
        "discount_percent": (
            invoice.discount_percent if discount_percent is ... else discount_percent
        ),
        "discount_days": (
            invoice.discount_days if discount_days is ... else discount_days
        ),
    }
    normiert = _zahlungsbedingungen_pruefen(invoice.invoice_type, **bedingungen)
    if any(f is not ... for f in (payment_term_days, discount_percent, discount_days)):
        kopf.update(normiert)
    # Auch ein reiner Datumswechsel kann die Skontofrist hinter die Fälligkeit
    # schieben — deshalb immer gegen den Ergebniszustand prüfen, nicht nur, wenn
    # ein Bedingungsfeld im Payload stand.
    _frist_gegen_faelligkeit_pruefen(
        invoice.invoice_date if invoice_date is ... else invoice_date,
        invoice.due_date if due_date is ... else due_date,
        normiert["discount_days"],
    )

    prepared = rubriken_norm = None
    if lines is not None:
        prepared, net_total, tax_total, gross_total = _prepare_lines(lines)
        rubriken_norm = _prepare_rubriken(rubriken, prepared)
        kopf.update(net_total=net_total, tax_total=tax_total, gross_total=gross_total)

    # Die Anrechnung einer Schlussrechnung überlebt das Ersetzen der Positionen:
    # sie steht in der Verkettungstabelle, nicht im Editor-Payload. Der Editor
    # kann sie deshalb nicht verlieren (und auch nicht verfälschen — negative
    # Einzelpreise weist `_prepare_lines` zurück). Sie wird aus der Verkettung
    # neu erzeugt und wieder hinten angehängt.
    abschlaege = anrechnung_rows = None
    if prepared is not None and invoice.invoice_type == FINAL_TYPE:
        anrechnung_rows = [
            {
                "advance_invoice_id": a.advance_invoice_id,
                "tax_code_id": a.tax_code_id,
                "tax_rate_percent": a.tax_rate_percent,
                "net_amount": a.net_amount,
                "tax_amount": a.tax_amount,
                "gross_amount": a.gross_amount,
            }
            for a in InvoiceAdvance.objects.filter(final_invoice_id=invoice.id)
            .select_related("advance_invoice")
            .order_by("advance_invoice__invoice_date",
                      "advance_invoice__invoice_number", "tax_rate_percent")
        ]
        abschlaege = list(
            Invoice.objects.filter(
                id__in={r["advance_invoice_id"] for r in anrechnung_rows}
            )
        )

    with as_business_error():
        with business_transaction(actor_app_user_id):
            if kopf:
                for feld, wert in kopf.items():
                    setattr(invoice, feld, wert)
                invoice.save(update_fields=list(kopf) + ["updated_at"])
            if prepared is not None:
                InvoiceLine.objects.filter(invoice_id=invoice.id).delete()
                BelegRubrik.objects.filter(invoice_id=invoice.id).delete()
                rubrik_ids = [
                    BelegRubrik.objects.create(
                        id=uuid.uuid4(), invoice_id=invoice.id, **r
                    ).id
                    for r in rubriken_norm
                ]
                _write_lines(prepared, rubrik_ids, model=InvoiceLine, invoice_id=invoice.id)
                if anrechnung_rows:
                    anrechnung_lines = _anrechnung_lines(
                        anrechnung_rows, abschlaege, len(prepared)
                    )
                    for row in anrechnung_lines:
                        InvoiceLine.objects.create(
                            id=uuid.uuid4(), invoice_id=invoice.id, **row
                        )
                    net_total, tax_total, gross_total = _totals(
                        prepared + anrechnung_lines
                    )
                    _anrechnung_pruefen(gross_total)
                    Invoice.objects.filter(id=invoice.id).update(
                        net_total=net_total,
                        tax_total=tax_total,
                        gross_total=gross_total,
                    )
    invoice.refresh_from_db()
    return invoice


# ---------------------------------------------------------------------------
# Einzelne Position anhängen / entfernen — der Weg für den GEBUNDENEN Entwurf
# ---------------------------------------------------------------------------
# `update_invoice` ersetzt den **ganzen** Positionssatz (Delete + Insert). Trägt
# die Rechnung eine Abrechnungsbindung (Migration 0084), trifft das DELETE die
# gebundene Zeile — und `invoicing.protect_billed_invoice_lines` weist es ab
# (422). Der Editor ist damit für einen gebundenen Entwurf verschlossen.
#
# Migration **0088** hat den Trigger aber bewusst verengt: Gesperrt sind nur
# UPDATE und DELETE einer **gebundenen Zeile**. Das **INSERT einer neuen** Zeile
# ist erlaubt — eine Zeile, die es noch nicht gibt, kann keine Bindung tragen und
# gefährdet die Doppelabrechnungssperre nicht. Genau dafür sind diese beiden
# Funktionen da: Anfahrtspauschale, Rabattzeile, Zusatztext auf einer aus dem
# Abrechnungslauf entstandenen Rechnung — **ohne** die Notbremse
# `bindungen_loesen` zu ziehen, die alle gebundenen Positionen verwürfe.
#
# Drei Grenzen, die aus dem Trigger und aus der Abschlagsverkettung folgen und
# deshalb nicht verhandelbar sind:
#
# * **Angehängt wird ans Ende der LEISTUNGSpositionen.** Trägt der Beleg
#   Anrechnungspositionen einer Schlussrechnung (`advance_invoice_id IS NOT NULL`),
#   so schließen die den Beleg ab (`_anrechnung_schreiben`) — die neue Zeile geht
#   also VOR sie, und die Anrechnung rückt um eins nach hinten. Sonst stünde eine
#   Leistungszeile hinter dem Abzug, und der nächste `set_invoice_advances` liefe
#   in die UNIQUE (invoice_id, position_number), weil er die Anrechnung hinter die
#   höchste Leistungsnummer setzt. Das Umnummerieren ist erlaubt: eine
#   Anrechnungszeile trägt nie eine `billing_link` (sie entsteht aus der
#   Verkettung, nicht aus dem Abrechnungslauf) — `protect_billed_invoice_lines`
#   sieht sie also nicht. Ein Einfügen mitten in die Leistungspositionen bleibt
#   dagegen verwehrt: das verschöbe gebundene Zeilen.
# * **Entfernt wird nur die LETZTE Leistungszeile, und nur wenn sie ungebunden
#   ist.** Eine Zeile aus der Mitte zu löschen hinterließe eine Lücke in der
#   Nummerierung (Schließen = Umnummerieren = UPDATE auf gebundene Zeilen). Das
#   ist die Rücknahme eines gerade gemachten Fehlers, nicht der allgemeine Editor.
# * **Eine Anrechnungsposition wird hier NIE angefasst.** Sie ist die Projektion
#   der Verkettung (`invoice_advance`); einzeln entfernt bliebe die Verkettung
#   stehen, der Abzug verschwände aus den Summen — der Entwurf forderte den bereits
#   gezahlten Abschlag ein zweites Mal. Gepflegt wird sie ausschließlich über
#   `set_invoice_advances`.
#
# **Sperre.** Jeder Schreiber, der Positionsnummern aus dem BESTAND ableitet,
# sperrt die Rechnung `FOR UPDATE` und liest die Zeilen erst DANACH — innerhalb
# derselben Transaktion. Das sind drei: `add_invoice_line` (`max + 1` bzw. das
# Umnummerieren), `remove_last_invoice_line` (die letzte Zeile) und
# `set_invoice_advances` (hängt die Anrechnung hinter die höchste Leistungsnummer).
# Nur beides zusammen serialisiert: Sperre ohne Lesen in der Transaktion hieße, mit
# einem Bestand zu rechnen, der vor der Sperre gelesen wurde — der Nebenläufer
# hätte inzwischen angehängt. Die UNIQUE (invoice_id, position_number) bleibt die
# letzte Instanz; sie ist auf einen 422 gemappt (`gate_errors`) und darf nie als
# 500 enden.

def _anrechnung_zeilen(bestehende):
    """Die Anrechnungspositionen (Projektion der Abschlagsverkettung) im Bestand."""
    return [l for l in bestehende if l.advance_invoice_id is not None]


def add_invoice_line(actor_app_user_id, *, invoice_id, line):
    """Hängt EINE Position an einen Rechnungsentwurf an (ans Ende der Leistung).

    Der einzige Weg, einen **gebundenen** Entwurf noch zu ergänzen. Die Summen
    rechnet — wie überall — der Server: aus **allen** Zeilen neu, nie aus einer
    Differenz.

    `rubrik` verweist auf einen **bestehenden** Abschnitt (Nummer); neue Abschnitte
    legt der Editor an, und der steht dem gebundenen Beleg nicht zur Verfügung.
    """
    with as_business_error():
        with business_transaction(actor_app_user_id):
            invoice = (
                Invoice.objects.select_for_update().filter(id=invoice_id).first()
            )
            if invoice is None:
                raise ValueError("Rechnung nicht gefunden.")
            if invoice.status not in INVOICE_EDITIERBAR:
                raise ValueError(
                    f"Rechnung im Status {invoice.status} ist unveränderlich "
                    "(veröffentlicht)."
                )
            if invoice.invoice_type in _CREDIT_TYPES:
                raise ValueError(
                    "Gutschriften und Stornobelege werden nicht über den Editor "
                    "geändert."
                )

            # Validierung und Geldrechnung der neuen Zeile laufen durch dieselbe
            # Rechenstelle wie im Editor (`_prepare_lines`) — kein zweiter Rechenweg.
            prepared, *_ = _prepare_lines([line])
            row = dict(prepared[0])
            ref = row.pop("_rubrik", None)
            if ref is not None:
                rubrik = BelegRubrik.objects.filter(
                    invoice_id=invoice.id, position_number=ref
                ).first()
                if rubrik is None:
                    raise ValueError(
                        f"Abschnitt {ref} gibt es auf diesem Beleg nicht. Eine "
                        "angehängte Position kann nur einem bestehenden Abschnitt "
                        "zugeordnet werden."
                    )
                row["rubrik_id"] = rubrik.id

            bestehende = list(InvoiceLine.objects.filter(invoice_id=invoice.id))
            anrechnung = _anrechnung_zeilen(bestehende)
            if anrechnung:
                # VOR den Abzug: er schließt den Beleg ab. Umnummeriert wird in
                # ABSTEIGENDER Reihenfolge — aufsteigend liefe das erste UPDATE in
                # die UNIQUE gegen seinen eigenen Nachfolger.
                row["position_number"] = min(l.position_number for l in anrechnung)
                for l in sorted(
                    anrechnung, key=lambda l: l.position_number, reverse=True
                ):
                    InvoiceLine.objects.filter(id=l.id).update(
                        position_number=l.position_number + 1
                    )
            else:
                row["position_number"] = (
                    max((l.position_number for l in bestehende), default=0) + 1
                )

            # Die Summen hängen nicht an der Nummerierung — der verschobene Abzug
            # zählt unverändert mit.
            net_total, tax_total, gross_total = _totals(
                [_totals_row(l) for l in bestehende] + [row]
            )
            if invoice.invoice_type == FINAL_TYPE:
                _anrechnung_pruefen(gross_total)

            InvoiceLine.objects.create(id=uuid.uuid4(), invoice_id=invoice.id, **row)
            Invoice.objects.filter(id=invoice.id).update(
                net_total=net_total, tax_total=tax_total, gross_total=gross_total
            )
    invoice.refresh_from_db()
    return invoice


def remove_last_invoice_line(actor_app_user_id, *, invoice_id):
    """Entfernt die **letzte** Position eines Entwurfs — nur, wenn sie ungebunden ist.

    Die Rücknahme einer gerade angehängten Zeile (Vertipper in der
    Anfahrtspauschale). Bewusst nur die letzte: Jede andere Zeile zu entfernen
    hieße umnummerieren, und das ist ein UPDATE, das an einer gebundenen Zeile
    scheitert. Ist die letzte Zeile gebunden, bleibt nur `bindungen_loesen` — oder
    eine ausgleichende Position.

    Ist die letzte Zeile die **Anrechnung** einer Schlussrechnung, wird ebenfalls
    abgewiesen: sie gehört zur Abschlagsverkettung und wird über
    `set_invoice_advances` gepflegt, nicht hier.
    """
    with as_business_error():
        with business_transaction(actor_app_user_id):
            invoice = (
                Invoice.objects.select_for_update().filter(id=invoice_id).first()
            )
            if invoice is None:
                raise ValueError("Rechnung nicht gefunden.")
            if invoice.status not in INVOICE_EDITIERBAR:
                raise ValueError(
                    f"Rechnung im Status {invoice.status} ist unveränderlich "
                    "(veröffentlicht)."
                )
            if invoice.invoice_type in _CREDIT_TYPES:
                raise ValueError(
                    "Gutschriften und Stornobelege werden nicht über den Editor "
                    "geändert."
                )
            bestehende = list(InvoiceLine.objects.filter(invoice_id=invoice.id))
            if not bestehende:
                raise ValueError("Die Rechnung hat keine Position.")
            letzte = max(bestehende, key=lambda l: l.position_number)
            if letzte.advance_invoice_id is not None:
                raise ValueError(
                    f"Position {letzte.position_number} ist die Anrechnung einer "
                    "Abschlags-/Teilrechnung und Teil der Abschlagsverkettung. Sie "
                    "kann nicht einzeln entfernt werden — sonst forderte die "
                    "Schlussrechnung den bereits berechneten Betrag ein zweites Mal. "
                    "Die Anrechnung wird über die Abschlagszuordnung gepflegt."
                )
            if BillingLink.objects.filter(
                invoice_line_id=letzte.id, released_at__isnull=True
            ).exists():
                raise ValueError(
                    f"Position {letzte.position_number} ist an die Abrechnung "
                    "gebunden (Bericht, Zeitbuchung oder Angebot) und kann nicht "
                    "entfernt werden. Sie ist der Nachweis, dass genau diese Leistung "
                    "berechnet wurde. Wenn der Abrechnungslauf falsch war: Bindungen "
                    "lösen."
                )
            rest = [l for l in bestehende if l.id != letzte.id]
            net_total, tax_total, gross_total = _totals(
                [_totals_row(l) for l in rest]
            )

            InvoiceLine.objects.filter(id=letzte.id).delete()
            Invoice.objects.filter(id=invoice.id).update(
                net_total=net_total, tax_total=tax_total, gross_total=gross_total
            )
    invoice.refresh_from_db()
    return invoice


def _totals_row(line):
    """Eine bestehende Positionszeile in der Form, die `_totals` liest.

    Text-/Zwischensummenzeilen und Alternativ-/Bedarfspositionen filtert `_totals`
    selbst heraus — ihre leeren Beträge werden nie gelesen.
    """
    return {
        "line_type": line.line_type,
        "line_kind": line.line_kind,
        "net_amount": line.net_amount,
        "tax_code_id": line.tax_code_id,
        "tax_rate_percent": line.tax_rate_percent,
    }


# ---------------------------------------------------------------------------
# Abschlags-/Teil-/Schlussrechnung (Migration 0060)
# ---------------------------------------------------------------------------
# Die Schlussrechnung listet die volle Leistung und zieht die bereits gestellten
# Abschlags-/Teilrechnungen desselben Auftrags ab. Der Abzug entsteht als
# **negative Position je Steuersatz** (nicht als Kopffeld): so trägt ihn die
# GoBD-Summenkette (assert_invoice_totals), der offene Posten
# (gross_total − Zahlungen) und das EN16931-XML ohne Umbau — und die USt-
# Aufteilung stimmt, weil je Steuersatz abgezogen wird (§ 14 Abs. 5 UStG).
#
# Die Verkettung führt `invoicing.invoice_advance` (eingefrorene Beträge je
# Steuersatz). Sie ist die Wahrheit: die Positionen werden aus ihr erzeugt, und
# das Veröffentlichungstor verlangt, dass beide deckungsgleich sind.


def _summenwirksame_gruppen(invoice):
    """Nettobeträge einer Rechnung je Steuergruppe (tax_code, tax_rate_percent).

    Nur summenwirksame Betragspositionen (wie assert_invoice_totals). Die Summe
    über alle Gruppen ist damit exakt `net_total`.
    """
    gruppen = {}
    for line in sorted(invoice.lines.all(), key=lambda l: l.position_number):
        if line.line_type in TEXT_TYPES or line.line_kind != SUMMENWIRKSAM:
            continue
        key = (line.tax_code_id, line.tax_rate_percent)
        gruppen[key] = gruppen.get(key, Decimal("0.00")) + line.net_amount
    return gruppen


def _arbeitskosten_gruppen(invoice):
    """§ 35a-Anteil je Steuergruppe — oder None für eine Gruppe mit Lücke.

    Die Unbestimmtheit PROPAGIERT: enthält eine Gruppe auch nur eine Position ohne
    bestimmten Anteil, ist die ganze Gruppe unbestimmt. Sie darf nicht stillschweigend
    als „0 in dieser Position" gelesen werden — sonst wiese der Beleg zu wenig
    Arbeitskosten aus, und niemand sähe es.
    """
    gruppen = {}
    for line in invoice.lines.all():
        if line.line_type in TEXT_TYPES or line.line_kind != SUMMENWIRKSAM:
            continue
        key = (line.tax_code_id, line.tax_rate_percent)
        if line.labour_net_amount is None:
            gruppen[key] = None
            continue
        vorher = gruppen.get(key, Decimal("0.00"))
        if vorher is None:
            continue
        gruppen[key] = vorher + line.labour_net_amount
    return gruppen


# Gründe, aus denen ein § 35a-Ausweis unterbleibt (das UI nennt sie im Klartext).
LOHN_OFFEN = "OFFENE_POSITIONEN"      # mindestens eine Position ohne bestimmten Anteil
LOHN_UNSTIMMIG = "UNSTIMMIG"          # Ergebnis ist kein Teil des Rechnungsbetrags


def arbeitskosten(invoice):
    """Der § 35a-Ausweis einer Rechnung — die EINZIGE Rechenstelle.

    PDF, API und Frontend ziehen von hier; damit steht auf derselben Rechnung
    nirgends ein zweiter, abweichender Arbeitskostenbetrag.

    Gibt IMMER ein dict zurück, auch wenn der Ausweis nicht möglich ist — das UI
    soll den Grund nennen können, statt den Block wortlos verschwinden zu lassen:

    - `grund=OFFENE_POSITIONEN` + `offen=[Positionsnummern]`, sobald eine
      summenwirksame Position keinen bestimmten Anteil trägt.
    - `grund=UNSTIMMIG`, wenn das Ergebnis kein Teil des Rechnungsbetrags ist
      (siehe unten).
    - Sonst Netto/Steuer/Brutto. Die Steuer wird **je Steuergruppe** gerundet (wie
      die Kopfsteuer): bei einer reinen Lohnrechnung ist der ausgewiesene
      Steuerbetrag damit exakt `tax_total`.

    Beträge sind bei `bestimmbar=False` **None (unbekannt), nicht 0**.

    **Die Belegprüfung ist nicht redundant zum DB-CHECK.** Der CHECK sichert je
    POSITION, dass der Anteil ein Teil des Positionsbetrags ist. Auf BELEGEBENE
    kann die Summe das trotzdem verletzen, weil die Anrechnung eines Abschlags
    dessen Arbeitskosten wieder abzieht (`_anrechnung_lines`):

    - Trug der Abschlag mehr Lohn, als die Schlussrechnung insgesamt abrechnet
      (z. B. ein als ARBEITSZEIT erfasster Abschlag über 10.000 € bei nur 5.000 €
      Lohnleistung), wäre der Ausweis **negativ**.
    - War der Abschlag reines Material, kann der Lohnanteil den **Zahlbetrag der
      Schlussrechnung übersteigen**.

    Beides sind Aussagen, die auf keinem Beleg stehen dürfen — und beide entstehen
    aus einem Erfassungsfehler in einem bereits veröffentlichten (und damit
    unveränderlichen) Abschlag. Also: kein Ausweis, aber ein benannter Grund.
    Ein Veröffentlichungsverbot wäre hier falsch — es machte die Schlussrechnung
    dauerhaft unstellbar, obwohl ihre Beträge stimmen.

    `show_labour_costs` wird hier NICHT geprüft — die Funktion sagt, was in dem
    Beleg steckt; ob es aufs Papier kommt, entscheidet der Renderer.
    """
    def _unbestimmt(grund, offen=()):
        return {
            "bestimmbar": False,
            "grund": grund,
            "offen": list(offen),
            "net_amount": None,
            "tax_amount": None,
            "gross_amount": None,
        }

    offen = []
    gruppen = {}
    for line in sorted(invoice.lines.all(), key=lambda l: l.position_number):
        if line.line_type in TEXT_TYPES or line.line_kind != SUMMENWIRKSAM:
            continue
        if line.labour_net_amount is None:
            offen.append(line.position_number)
            continue
        key = (line.tax_code_id, line.tax_rate_percent)
        gruppen[key] = gruppen.get(key, Decimal("0.00")) + line.labour_net_amount
    if offen:
        return _unbestimmt(LOHN_OFFEN, offen)

    netto = sum(gruppen.values(), Decimal("0.00"))
    gesamt = invoice.net_total
    # Ohne Belegsumme lässt sich die Aussage „darin enthalten" nicht prüfen —
    # dann wird sie auch nicht behauptet (fail-closed; erreichbar nur über
    # Altdaten oder einen Schreibweg an der Service-Schicht vorbei).
    if gesamt is None or netto * gesamt < 0 or abs(netto) > abs(gesamt):
        return _unbestimmt(LOHN_UNSTIMMIG)

    steuer = sum(
        (_round2(betrag * rate / Decimal(100)) for (_c, rate), betrag in gruppen.items()),
        Decimal("0.00"),
    )
    return {
        "bestimmbar": True,
        "grund": None,
        "offen": [],
        "net_amount": netto,
        "tax_amount": steuer,
        "gross_amount": netto + steuer,
    }


def _veroeffentlichte_stornos():
    """Die veröffentlichten STORNO-Belege — das eine Prädikat „ist storniert?".

    Es gibt genau **eine** Definition davon; sie wird nur in zwei Formen ausgeliefert
    (ID-Menge für `exclude`, `Exists` für die SQL-Annotation). Eine zweite Definition
    liefe irgendwann auseinander — und dann stünde eine stornierte Rechnung in der
    einen Ansicht als überfällig und in der anderen nicht.
    """
    return Invoice.objects.filter(invoice_type="STORNO", status="VEROEFFENTLICHT")


def _stornierte_belege():
    """IDs aller Belege, zu denen ein veröffentlichter STORNO existiert."""
    return set(_veroeffentlichte_stornos().values_list("reference_invoice_id", flat=True))


def storniert_exists():
    """`Exists`-Ausdruck: Trägt DIESE Rechnung einen veröffentlichten STORNO?

    Für Annotationen/Filter auf großen Rechnungsmengen (offene Posten, Mahnwesen) —
    dasselbe Prädikat wie `stornierte_belege()`, nur ohne die Ergebnisliste in den
    Python-Speicher zu ziehen.
    """
    return Exists(_veroeffentlichte_stornos().filter(reference_invoice_id=OuterRef("pk")))


def _korrigierte_belege():
    """IDs aller Belege mit veröffentlichtem STORNO **oder** GUTSCHRIFT.

    Beide machen einen Abschlag unanrechenbar: der Betrag steht nicht mehr (bzw.
    nicht mehr vollständig) in Rechnung, der eingefrorene Anrechnungsbetrag wäre
    falsch.
    """
    return set(
        Invoice.objects.filter(
            invoice_type__in=_CREDIT_TYPES, status="VEROEFFENTLICHT"
        ).values_list("reference_invoice_id", flat=True)
    )


def stornierte_belege():
    """Öffentlicher Zugang zu `_stornierte_belege` (IDs stornierter Belege).

    Leser außerhalb dieses Moduls (Dossier) brauchen dieselbe Menge: eine
    stornierte Rechnung ist keine Forderung mehr. Die Frage „ist dieser Beleg
    storniert?" darf es genau **einmal** im Repo geben — eine zweite Definition
    liefe irgendwann auseinander, und dann stünde eine stornierte Rechnung in der
    einen Ansicht als überfällig und in der anderen nicht.
    """
    return _stornierte_belege()


def _gebundene_abschlaege(advance_ids, *, exclude_final_id=None):
    """Abschläge, die eine veröffentlichte (nicht stornierte) SR bereits anrechnet.

    Die Storno-Ausnahme ist wesentlich: wird eine Schlussrechnung storniert, wird
    ihre Anrechnung wieder frei — sonst ließe sich der Auftrag nach dem Storno nie
    wieder schlussrechnen. Dieselbe Regel setzt die DB durch
    (`invoicing.advance_blocking_final`).
    """
    qs = InvoiceAdvance.objects.filter(
        advance_invoice_id__in=list(advance_ids), final_invoice__status="VEROEFFENTLICHT"
    ).exclude(final_invoice_id__in=_stornierte_belege())
    if exclude_final_id is not None:
        qs = qs.exclude(final_invoice_id=exclude_final_id)
    return dict(qs.values_list("advance_invoice_id", "final_invoice_id"))


def offene_abschlaege_gesamt():
    """Alle **noch nicht schlussgerechneten** Abschlags-/Teilrechnungen (auftrags-
    übergreifend).

    Also: veröffentlicht, mit anrechenbarem Betrag (> 0,00 €), nicht storniert/
    gutgeschrieben und von keiner veröffentlichten (nicht stornierten)
    Schlussrechnung angerechnet — exakt die Menge, die eine spätere
    Schlussrechnung noch anrechnen WIRD.

    Genutzt vom DATEV-Abschlagsmodus (0063): solange solche Abschläge offen sind,
    darf die Kontierung nicht umgestellt werden. Sonst löste die Schlussrechnung
    eine Anzahlung auf, die nie als Anzahlung gebucht wurde (bzw. umgekehrt), und
    auf dem Anzahlungskonto bliebe ein Saldo stehen.
    """
    kandidaten = list(
        Invoice.objects.filter(
            invoice_type__in=ADVANCE_TYPES,
            status="VEROEFFENTLICHT",
            gross_total__gt=0,
        )
        .exclude(id__in=_korrigierte_belege())
        .order_by("invoice_date", "invoice_number")
    )
    gebunden = _gebundene_abschlaege([i.id for i in kandidaten])
    return [i for i in kandidaten if i.id not in gebunden]


def anrechenbare_abschlaege(work_order_id, *, final_invoice_id=None):
    """Die anrechenbaren Abschlags-/Teilrechnungen eines Auftrags (für das UI).

    Geliefert werden alle **veröffentlichten**, **nicht stornierten/gutgeschriebenen**
    AR/TR des Auftrags, die noch **keine veröffentlichte Schlussrechnung** anrechnet.

    Abschläge, die bereits in einem anderen Schlussrechnungs-ENTWURF vorgemerkt
    sind, bleiben in der Liste (`vorgemerkt=True`) — ein Entwurf bindet nichts, und
    zwei Entwürfe zum selben Auftrag sind ein legitimer Zwischenzustand. Erst die
    Veröffentlichung entscheidet; die DB serialisiert sie (Zeilensperre auf dem
    Abschlag).

    `angerechnet=True` markiert die Abschläge, die die übergebene Schlussrechnung
    (`final_invoice_id`) bereits anrechnet — das UI hakt sie an.
    """
    ensure_exists(WorkOrder, work_order_id, "Auftrag")
    kandidaten = list(
        Invoice.objects.filter(
            work_order_id=work_order_id,
            invoice_type__in=ADVANCE_TYPES,
            status="VEROEFFENTLICHT",
            # Ein Abschlag über 0,00 EUR trägt nichts zum Anrechnen bei (jede
            # Anrechnungszeile müsste einen positiven Betrag haben, DB-CHECK
            # net_amount > 0). Er ist deshalb weder anrechenbar noch blockierend —
            # sonst wäre die Schlussrechnung unveröffentlichbar: nicht anrechenbar
            # („kein anrechenbarer Betrag") und zugleich nicht übergehbar
            # („übergeht 1 anrechenbare Rechnung"). Dieselbe Bedingung steht im
            # DB-Tor (Migration 0060), damit beide dieselbe Menge meinen.
            gross_total__gt=Decimal("0.00"),
        )
        .exclude(id__in=_korrigierte_belege())
        .order_by("invoice_date", "invoice_number")
    )
    ids = [i.id for i in kandidaten]
    gebunden = _gebundene_abschlaege(ids, exclude_final_id=final_invoice_id)
    vorgemerkt = set(
        InvoiceAdvance.objects.filter(
            advance_invoice_id__in=ids, final_invoice__status="ENTWURF"
        )
        .exclude(final_invoice_id=final_invoice_id)
        .values_list("advance_invoice_id", flat=True)
    )
    bereits = (
        set(
            InvoiceAdvance.objects.filter(
                final_invoice_id=final_invoice_id
            ).values_list("advance_invoice_id", flat=True)
        )
        if final_invoice_id
        else set()
    )
    return [
        {
            "id": inv.id,
            "invoice_number": inv.invoice_number,
            "invoice_type": inv.invoice_type,
            "invoice_date": inv.invoice_date,
            "net_total": inv.net_total,
            "tax_total": inv.tax_total,
            "gross_total": inv.gross_total,
            "vorgemerkt": inv.id in vorgemerkt,
            "angerechnet": inv.id in bereits,
        }
        for inv in kandidaten
        if inv.id not in gebunden
    ]


def _abschlaege_laden(final_work_order_id, advance_invoice_ids, *, final_invoice_id=None):
    """Lädt die anzurechnenden Abschläge und prüft sie fachlich vor.

    Alles, was die DB-Trigger ohnehin ablehnen würden, wird hier als klare
    Fehlermeldung (→422) abgefangen — nicht als kryptischer Tor-Fehler.
    """
    ids = list(dict.fromkeys(advance_invoice_ids or []))
    if not ids:
        return []
    if final_work_order_id is None:
        raise ValueError(
            "Eine Schlussrechnung mit Anrechnung braucht den Auftrag, zu dem die "
            "Abschlagsrechnungen gehören."
        )
    gefunden = {
        inv.id: inv
        for inv in Invoice.objects.filter(id__in=ids).prefetch_related("lines")
    }
    korrigiert = _korrigierte_belege()
    gebunden = _gebundene_abschlaege(ids, exclude_final_id=final_invoice_id)
    abschlaege = []
    for advance_id in ids:
        inv = gefunden.get(advance_id)
        if inv is None:
            raise ValueError(f"Abschlagsrechnung {advance_id} nicht gefunden.")
        bezeichnung = inv.invoice_number or str(inv.id)
        if inv.invoice_type not in ADVANCE_TYPES:
            raise ValueError(
                f"Beleg {bezeichnung} ist keine Abschlags- oder Teilrechnung."
            )
        if inv.status != "VEROEFFENTLICHT":
            raise ValueError(
                f"Abschlagsrechnung {bezeichnung} ist nicht veröffentlicht und "
                "kann nicht angerechnet werden."
            )
        if inv.work_order_id != final_work_order_id:
            raise ValueError(
                f"Abschlagsrechnung {bezeichnung} gehört zu einem anderen Auftrag."
            )
        if inv.id in korrigiert:
            raise ValueError(
                f"Abschlagsrechnung {bezeichnung} ist storniert oder gutgeschrieben "
                "und kann nicht angerechnet werden."
            )
        if inv.id in gebunden:
            raise ValueError(
                f"Abschlagsrechnung {bezeichnung} ist bereits in einer "
                "veröffentlichten Schlussrechnung angerechnet."
            )
        if (inv.gross_total or Decimal("0.00")) <= 0:
            # 0-EUR-Beleg: nichts anzurechnen. Er ist auch nicht blockierend
            # (anrechenbare_abschlaege und das DB-Tor blenden ihn aus) — die
            # Schlussrechnung lässt sich also ohne ihn stellen.
            raise ValueError(
                f"Abschlagsrechnung {bezeichnung} trägt keinen anrechenbaren Betrag "
                "(0,00 EUR) und muss nicht angerechnet werden."
            )
        abschlaege.append(inv)
    return sorted(
        abschlaege, key=lambda i: (i.invoice_date or date.max, i.invoice_number or "")
    )


def _anrechnung_rows(abschlaege):
    """Verkettungszeilen (invoice_advance) für die übergebenen Abschläge.

    Je Abschlag und Steuersatz eine Zeile mit dem eingefrorenen Betrag. Die
    Steuer wird je Gruppe gerundet — dieselbe Regel wie `_totals` und der
    DB-CHECK; die Summe über alle Gruppen eines Abschlags ist damit exakt sein
    `tax_total`.
    """
    rows = []
    for inv in abschlaege:
        gruppen = _summenwirksame_gruppen(inv)
        for (code, rate), netto in gruppen.items():
            if netto == 0:
                # Steuergruppe ohne Betrag (z. B. reine 0-EUR-Zeile): nichts
                # anzurechnen, keine Zeile — sonst verletzte sie den DB-CHECK
                # net_amount > 0.
                continue
            if netto < 0:
                raise ValueError(
                    f"Abschlagsrechnung {inv.invoice_number}: die Steuergruppe "
                    f"{code} ist negativ und lässt sich nicht anrechnen."
                )
            steuer = _round2(netto * rate / Decimal(100))
            rows.append(
                {
                    "advance_invoice_id": inv.id,
                    "tax_code_id": code,
                    "tax_rate_percent": rate,
                    "net_amount": netto,
                    "tax_amount": steuer,
                    "gross_amount": netto + steuer,
                }
            )
    if not rows and abschlaege:
        raise ValueError(
            "Die gewählten Abschlagsrechnungen tragen keinen anrechenbaren Betrag."
        )
    return rows


def _anrechnung_lines(rows, abschlaege, start_position):
    """Negative Anrechnungspositionen aus den Verkettungszeilen.

    `quantity = 1`, `unit_price = −Netto` (die DB verlangt `quantity > 0`; der
    Einzelpreis darf negativ sein). Für die AUSGABE (PDF/XML) legt
    `anzeige_menge_preis` das Vorzeichen auf die Menge — EN16931 verbietet
    negative Einzelpreise (BR-27), negative Mengen dagegen nicht.

    **§ 35a:** Die Anrechnungsposition nimmt auch die Arbeitskosten des Abschlags
    zurück (negativ, je Steuergruppe). Nur so weist die Schlussrechnung genau die
    Arbeitskosten aus, die MIT IHR bezahlt werden — die des Abschlags standen auf
    dem Abschlagsbeleg und wurden dort bereits geltend gemacht. Ohne den Abzug
    zählte der Kunde dieselben Arbeitskosten zweimal. War der Anteil im Abschlag
    unbestimmt, ist er es hier auch (die Unbestimmtheit propagiert bis in den
    Ausweis der Schlussrechnung).
    """
    namen = {i.id: i for i in abschlaege}
    arbeitskosten_je_abschlag = {i.id: _arbeitskosten_gruppen(i) for i in abschlaege}
    lines = []
    pos = start_position
    for row in rows:
        inv = namen[row["advance_invoice_id"]]
        pos += 1
        netto = row["net_amount"]
        gruppe = arbeitskosten_je_abschlag[inv.id].get(
            (row["tax_code_id"], row["tax_rate_percent"])
        )
        datum = inv.invoice_date.strftime("%d.%m.%Y") if inv.invoice_date else "-"
        titel = (
            "Abschlagsrechnung"
            if inv.invoice_type == "ABSCHLAGSRECHNUNG"
            else "Teilrechnung"
        )
        lines.append(
            {
                "position_number": pos,
                "line_type": ANRECHNUNG_LINE_TYPE,
                "line_kind": SUMMENWIRKSAM,
                "description": (
                    f"Abzüglich {titel} {inv.invoice_number} vom {datum} "
                    f"({_prozent_text(row['tax_rate_percent'])} % USt)"
                ),
                "quantity": Decimal("1.000"),
                "unit": None,
                "unit_price": -netto,
                "discount_percent": None,
                "tax_code_id": row["tax_code_id"],
                "tax_rate_percent": row["tax_rate_percent"],
                "net_amount": -netto,
                "labour_net_amount": (None if gruppe is None else -gruppe),
                "advance_invoice_id": inv.id,
            }
        )
    return lines


def _prozent_text(rate):
    """Steuersatz in deutscher Schreibweise ohne unnötige Nachkommastellen."""
    r = Decimal(rate).normalize()
    return f"{r:f}".replace(".", ",")


def _anrechnung_schreiben(invoice, rows, abschlaege, *, prepared_user_lines):
    """Schreibt Verkettung + Anrechnungspositionen und gibt die Kopfsummen zurück.

    Läuft innerhalb einer laufenden `business_transaction`. Die Anrechnungs-
    positionen stehen IMMER hinter den Leistungspositionen (der Abzug schließt den
    Beleg ab) und sind keinem Abschnitt zugeordnet.

    Die Anrechnung setzt hinter der HÖCHSTEN Leistungsnummer auf — nicht hinter
    `len(...)`. Beides fällt nur zusammen, solange die Leistungsnummern lückenlos
    sind. `abrechnung.bindungen_loesen` löscht Zeilen ohne Umnummerieren und
    hinterlässt Lücken; träfe eine solche Rechnung je auf eine Anrechnung, vergäbe
    `len(...)` eine bereits belegte Nummer (UNIQUE-Verletzung).
    """
    for row in rows:
        InvoiceAdvance.objects.create(
            id=uuid.uuid4(), final_invoice_id=invoice.id, **row
        )
    start = max(
        (r["position_number"] for r in prepared_user_lines), default=0
    )
    lines = _anrechnung_lines(rows, abschlaege, start)
    for row in lines:
        InvoiceLine.objects.create(id=uuid.uuid4(), invoice_id=invoice.id, **row)
    return _totals(list(prepared_user_lines) + lines)


def anrechnungen(invoice):
    """Die Anrechnung einer Schlussrechnung, je angerechnetem Abschlag gebündelt.

    Quelle ist die Verkettungstabelle (nach der Veröffentlichung unveränderlich).
    Einzige Ableitungsstelle — PDF, API und E-Rechnung greifen hierauf zu.
    """
    rows = list(
        InvoiceAdvance.objects.filter(final_invoice_id=invoice.id)
        .select_related("advance_invoice")
        .order_by("advance_invoice__invoice_date", "advance_invoice__invoice_number",
                  "tax_rate_percent")
    )
    gebuendelt = {}
    for row in rows:
        adv = row.advance_invoice
        eintrag = gebuendelt.setdefault(
            adv.id,
            {
                "advance_invoice_id": adv.id,
                "invoice_number": adv.invoice_number,
                "invoice_type": adv.invoice_type,
                "invoice_date": adv.invoice_date,
                "net_amount": Decimal("0.00"),
                "tax_amount": Decimal("0.00"),
                "gross_amount": Decimal("0.00"),
                "steuergruppen": [],
            },
        )
        eintrag["net_amount"] += row.net_amount
        eintrag["tax_amount"] += row.tax_amount
        eintrag["gross_amount"] += row.gross_amount
        eintrag["steuergruppen"].append(
            {
                "tax_code": row.tax_code_id,
                "tax_rate_percent": row.tax_rate_percent,
                "net_amount": row.net_amount,
                "tax_amount": row.tax_amount,
                "gross_amount": row.gross_amount,
            }
        )
    return list(gebuendelt.values())


def leistungssummen(invoice, anrechnung=None):
    """(Leistung, Anrechnung) einer Schlussrechnung — oder None ohne Anrechnung.

    Die Leistungssumme wird **aus den Kopfsummen plus Anrechnung** zurückgerechnet,
    nicht aus den Leistungspositionen neu summiert. Grund: Steuer wird je
    Steuergruppe gerundet, und `round(Leistung·r) − round(Abschlag·r)` kann um
    einen Cent von `round((Leistung−Abschlag)·r)` abweichen. Der Beleg schuldet
    aber genau `tax_total`; die ausgewiesene Kette
    „Leistung − Anrechnung = Zahlbetrag" muss deshalb auf diesen Wert aufgehen und
    nicht auf einen zweiten, unabhängig gerundeten.
    """
    posten = anrechnung if anrechnung is not None else anrechnungen(invoice)
    if not posten:
        return None
    netto = sum((p["net_amount"] for p in posten), Decimal("0.00"))
    steuer = sum((p["tax_amount"] for p in posten), Decimal("0.00"))
    brutto = sum((p["gross_amount"] for p in posten), Decimal("0.00"))
    return {
        "leistung_net": (invoice.net_total or Decimal("0.00")) + netto,
        "leistung_tax": (invoice.tax_total or Decimal("0.00")) + steuer,
        "leistung_gross": (invoice.gross_total or Decimal("0.00")) + brutto,
        "anrechnung_net": netto,
        "anrechnung_tax": steuer,
        "anrechnung_gross": brutto,
        "zahlbetrag": invoice.gross_total,
        "posten": posten,
    }


def create_invoice(
    actor_app_user_id,
    *,
    property_id,
    invoice_type="RECHNUNG",
    project_id=None,
    work_order_id=None,
    service_case_id=None,
    reference_invoice_id=None,
    invoice_date=None,
    due_date=None,
    payment_term_days=None,
    discount_percent=None,
    discount_days=None,
    show_labour_costs=True,
    lines=None,
    rubriken=None,
    advance_invoice_ids=None,
):
    """Legt eine Rechnung (Status ENTWURF) mit Positionen an.

    `invoice_type` deckt RECHNUNG, ABSCHLAGSRECHNUNG, TEILRECHNUNG und
    SCHLUSSRECHNUNG ab. Gutschrift/Storno entstehen ausschließlich als Folgebeleg
    (create_cancellation/create_correction).

    Bei einer SCHLUSSRECHNUNG rechnen `advance_invoice_ids` die genannten
    Abschlags-/Teilrechnungen an: die Verkettung (invoicing.invoice_advance) und
    die negativen Anrechnungspositionen je Steuersatz entstehen dabei automatisch.
    Der Zahlbetrag (gross_total) ist damit die Differenz.

    Ein work_order-Bezug ist für die spätere Veröffentlichung erforderlich (B-08),
    beim Anlegen aber optional — außer bei einer Anrechnung (die Abschläge hängen
    am Auftrag). Belegnummer und Veröffentlichung: siehe publish_invoice.
    """
    if invoice_type not in INVOICE_TYPES:
        raise ValueError(
            f"Ungültiger invoice_type '{invoice_type}'. "
            f"Erlaubt: {', '.join(INVOICE_TYPES)}."
        )
    # Gutschrift/Storno entstehen ausschließlich als Folgebeleg (mit invertierten
    # Positionen, negative Summen) über create_cancellation/create_correction —
    # nicht direkt hier, sonst entstünde die Inkonsistenz „positive Gutschrift".
    if invoice_type in _CREDIT_TYPES:
        raise ValueError(
            f"{invoice_type} wird nicht direkt angelegt, sondern über "
            "create_cancellation/create_correction erzeugt."
        )
    if advance_invoice_ids and invoice_type != FINAL_TYPE:
        raise ValueError(
            "Abschlagsrechnungen kann nur eine Schlussrechnung anrechnen."
        )
    ensure_exists(Property, property_id, "Liegenschaft")
    ensure_exists(Project, project_id, "Projekt")
    service_case_id, work_order_id, project_id = _beleg_bezug_aufloesen(
        property_id=property_id,
        service_case_id=service_case_id,
        work_order_id=work_order_id,
        project_id=project_id,
        # Rechnung: keine Auftrag↔Projekt-Prüfung (wie vor 0113) — eine Rechnung
        # kann einem Projekt zugeordnet sein, während ihr Auftrag projektlos ist.
        pruefe_auftrag_projekt=False,
    )
    ensure_exists(Invoice, reference_invoice_id, "Referenzrechnung")
    bedingungen = _zahlungsbedingungen_pruefen(
        invoice_type,
        payment_term_days=payment_term_days,
        discount_percent=discount_percent,
        discount_days=discount_days,
    )
    _frist_gegen_faelligkeit_pruefen(
        invoice_date, due_date, bedingungen["discount_days"]
    )
    prepared, net_total, tax_total, gross_total = _prepare_lines(lines)
    rubriken_norm = _prepare_rubriken(rubriken, prepared)
    abschlaege = _abschlaege_laden(work_order_id, advance_invoice_ids)
    anrechnung_rows = _anrechnung_rows(abschlaege)

    with business_transaction(actor_app_user_id):
        invoice = Invoice.objects.create(
            id=uuid.uuid4(),
            property_id=property_id,
            project_id=project_id,
            work_order_id=work_order_id,
            service_case_id=service_case_id,
            invoice_type=invoice_type,
            reference_invoice_id=reference_invoice_id,
            status="ENTWURF",
            invoice_date=invoice_date,
            due_date=due_date,
            **bedingungen,
            show_labour_costs=bool(show_labour_costs),
            net_total=net_total,
            tax_total=tax_total,
            gross_total=gross_total,
            version=1,
        )
        rubrik_ids = [
            BelegRubrik.objects.create(id=uuid.uuid4(), invoice_id=invoice.id, **r).id
            for r in rubriken_norm
        ]
        _write_lines(prepared, rubrik_ids, model=InvoiceLine, invoice_id=invoice.id)
        if anrechnung_rows:
            net_total, tax_total, gross_total = _anrechnung_schreiben(
                invoice, anrechnung_rows, abschlaege, prepared_user_lines=prepared,
            )
            _anrechnung_pruefen(gross_total)
            Invoice.objects.filter(id=invoice.id).update(
                net_total=net_total, tax_total=tax_total, gross_total=gross_total
            )
        invoice.refresh_from_db()
    return invoice


def _anrechnung_pruefen(gross_total):
    """Die Anrechnung darf die Leistung nicht übersteigen.

    Sonst wäre die „Rechnung" eine Erstattung — dafür gibt es die Gutschrift. Die
    DB lehnt das beim Veröffentlichen ohnehin ab (Tor in Migration 0060); hier
    kommt die Meldung schon beim Speichern, wo sie behebbar ist.
    """
    if gross_total is not None and gross_total < 0:
        raise ValueError(
            "Die Anrechnung der Abschlagsrechnungen übersteigt die abgerechnete "
            "Leistung. Für eine Erstattung ist eine Gutschrift zu stellen, keine "
            "Schlussrechnung."
        )


def _vergessene_anrechnung_pruefen(invoice):
    """Eine Schlussrechnung darf keinen anrechenbaren Abschlag übergehen.

    Der teuerste Bedienfehler der Domäne: Wer die Abschläge im Dialog abwählt (oder
    `PUT /advances` mit leerer Liste schickt), stellt eine Schlussrechnung über die
    **volle** Leistung — der Kunde zahlt den Abschlag ein zweites Mal, und der Beleg
    ist danach unveränderlich (GoBD). Kein stiller Default, keine Übergehungs-
    Option: gibt es zum Auftrag noch veröffentlichte, nicht stornierte und nicht
    anderweitig gebundene AR/TR, wird die Veröffentlichung abgelehnt.

    Eine bewusste Ausnahme wäre nur über einen eigenen, begründungspflichtigen
    Vorgang vertretbar (Vier-Augen); bis es einen gibt, gilt: alles anrechnen.
    Dieselbe Regel setzt die DB durch (Tor in Migration 0060) — hier kommt sie
    mit Belegnummern statt UUIDs.
    """
    if invoice.invoice_type != FINAL_TYPE or invoice.work_order_id is None:
        return
    offen = [
        a
        for a in anrechenbare_abschlaege(
            invoice.work_order_id, final_invoice_id=invoice.id
        )
        if not a["angerechnet"]
    ]
    if not offen:
        return
    nummern = ", ".join((a["invoice_number"] or str(a["id"])) for a in offen)
    raise ValueError(
        f"Diese Schlussrechnung übergeht {len(offen)} anrechenbare "
        f"Abschlags-/Teilrechnung(en): {nummern}. Sie müssen angerechnet werden — "
        "sonst wird der bereits berechnete Betrag ein zweites Mal gefordert."
    )


def set_invoice_advances(actor_app_user_id, *, invoice_id, advance_invoice_ids):
    """Setzt die angerechneten Abschläge einer Schlussrechnung im ENTWURF neu.

    Ersetzt die Verkettung vollständig (das UI schickt die ganze Auswahl) und baut
    die Anrechnungspositionen daraus neu auf. Die Leistungspositionen bleiben
    unangetastet; die Kopfsummen werden aus allen Positionen neu abgeleitet.

    Sperrt die Rechnung `FOR UPDATE` und liest die Leistungspositionen DANACH
    (innerhalb der Transaktion): Die Anrechnung setzt hinter der höchsten
    Leistungsnummer auf, und `add_invoice_line` verschiebt genau diese Nummern.
    Gelesen vor der Sperre, rechnete diese Funktion mit einem überholten Bestand
    und vergäbe eine bereits belegte Positionsnummer.
    """
    with as_business_error():
        with business_transaction(actor_app_user_id):
            invoice = (
                Invoice.objects.select_for_update().filter(id=invoice_id).first()
            )
            if invoice is None:
                raise ValueError("Rechnung nicht gefunden.")
            if invoice.invoice_type != FINAL_TYPE:
                raise ValueError(
                    "Abschlagsrechnungen kann nur eine Schlussrechnung anrechnen."
                )
            if invoice.status not in INVOICE_EDITIERBAR:
                raise ValueError(
                    f"Rechnung im Status {invoice.status} ist unveränderlich "
                    "(veröffentlicht)."
                )
            abschlaege = _abschlaege_laden(
                invoice.work_order_id, advance_invoice_ids,
                final_invoice_id=invoice.id,
            )
            rows = _anrechnung_rows(abschlaege)
            # Leistungspositionen = alles, was keine Anrechnung ist. Sie behalten
            # ihre Nummern; die Anrechnung hängt sich hinter die höchste.
            user_lines = [
                {
                    "position_number": l.position_number,
                    "line_type": l.line_type,
                    "line_kind": l.line_kind,
                    "net_amount": l.net_amount,
                    "tax_code_id": l.tax_code_id,
                    "tax_rate_percent": l.tax_rate_percent,
                }
                for l in InvoiceLine.objects.filter(
                    invoice_id=invoice.id, advance_invoice__isnull=True
                ).order_by("position_number")
            ]
            InvoiceLine.objects.filter(
                invoice_id=invoice.id, advance_invoice__isnull=False
            ).delete()
            InvoiceAdvance.objects.filter(final_invoice_id=invoice.id).delete()
            net_total, tax_total, gross_total = _anrechnung_schreiben(
                invoice, rows, abschlaege, prepared_user_lines=user_lines,
            )
            _anrechnung_pruefen(gross_total)
            Invoice.objects.filter(id=invoice.id).update(
                net_total=net_total, tax_total=tax_total, gross_total=gross_total
            )
    invoice.refresh_from_db()
    return invoice


def add_invoice_party(
    actor_app_user_id,
    *,
    invoice_id,
    party_id,
    role,
    is_primary=False,
    allocation_percent=None,
    liability_group=None,
    liability_basis=None,
):
    """Fügt einen Rechnungsbeteiligten hinzu (nur im Entwurf; DB erzwingt das).

    Eine dokumentierte Gesamtschuld-Gruppe (liability_group) verlangt eine
    Grundlage (liability_basis) — A-29.
    """
    if role not in INVOICE_PARTY_ROLES:
        raise ValueError(
            f"Ungültige role '{role}'. Erlaubt: {', '.join(INVOICE_PARTY_ROLES)}."
        )
    if liability_group is not None and not (liability_basis or "").strip():
        raise ValueError(
            "liability_group erfordert eine dokumentierte liability_basis (A-29)."
        )
    ensure_exists(Invoice, invoice_id, "Rechnung")
    # party_id muss existieren und darf nicht MERGED sein (trg_invoice_party_no_merged).
    ensure_party_usable(party_id, "Partei")
    with business_transaction(actor_app_user_id):
        party = InvoiceParty.objects.create(
            id=uuid.uuid4(),
            invoice_id=invoice_id,
            party_id=party_id,
            role=role,
            is_primary=is_primary,
            allocation_percent=allocation_percent,
            liability_group=liability_group,
            liability_basis=liability_basis,
        )
    return party


# ---------------------------------------------------------------------------
# Stammdaten-Snapshot (Aussteller, Beteiligte, Leistungsort)
# ---------------------------------------------------------------------------
# Ein veröffentlichter Beleg muss sich allein aus seinem Snapshot rekonstruieren
# lassen (B-21/B-30). Bis zum E-Rechnungs-Slice fror `billing_snapshot` von den
# Beteiligten nur die `party_id` + Rolle ein — Name, Anschrift und USt-IdNr.
# kamen beim Rendern aus den LIVE-Stammdaten. Ein Umzug des Kunden oder eine
# geänderte Firmenanschrift hätte damit einen längst gestellten Beleg
# nachträglich verändert (und beim ZUGFeRD-XML ein anderes XML erzeugt als das
# archivierte PDF zeigt). Seit `SNAPSHOT_VERSION = 2` friert die
# Veröffentlichung diese Stammdaten mit ein.
#
# Der Hash ändert sich dadurch NUR für neu veröffentlichte Belege — bestehende
# Belege werden nicht rehasht (das wäre eine nachträgliche Änderung an einem
# festgeschriebenen Beleg). Leser (Beleg-PDF, ZUGFeRD) müssen deshalb den
# Live-Fallback für Altbelege behalten.
#
# `SNAPSHOT_VERSION = 3` friert zusätzlich den § 35a-Arbeitskostenanteil je
# Position ein (Migration 0076). Er steht auf dem Kundenbeleg und ist damit Teil
# dessen, was der Beleg aussagt — ohne ihn ließe sich der ausgewiesene
# Steuerbonus aus dem Snapshot nicht rekonstruieren. Altbelege tragen den
# Schlüssel nicht; sie weisen (korrekterweise) auch keine Arbeitskosten aus.
SNAPSHOT_VERSION = 3

# Reihenfolge = Vorrang bei der Adresswahl für den Beleg: die Rechnungsadresse
# schlägt die Geschäfts-/Post-/Privatadresse.
_ADDRESS_PREFERENCE = ("BILLING", "BUSINESS", "POSTAL", "PRIVATE")


def _address_snapshot(address):
    if address is None:
        return None
    return {
        "street": address.street,
        "house_number": address.house_number,
        "address_addition": address.address_addition,
        "postal_code": address.postal_code,
        "city": address.city,
        "country_code": address.country_code,
    }


def party_address(party_id, on_date=None):
    """Die zum Stichtag gültige Belegadresse einer Partei (Address) oder None.

    Vorrang: BILLING > BUSINESS > POSTAL > PRIVATE, innerhalb eines Typs die
    primäre Zuordnung. Ohne Stichtag zählt die heute gültige Zuordnung.

    **Stichtag ist der Zeitpunkt der Veröffentlichung, nicht das Belegdatum.**
    Ein Beleg darf zurückdatiert werden (Monatsabrechnung zum Ersten); die
    Adresszuordnung trägt aber das Datum ihrer Erfassung. Mit dem Belegdatum als
    Stichtag fiele die Anschrift eines erst danach erfassten Kunden still weg —
    der Beleg ginge ohne Empfängeranschrift raus. Maßgeblich ist, wohin der Beleg
    JETZT geht.

    Stichtag ist das **Betriebsdatum** (`betriebs_datum()`), NICHT
    `dj_timezone.localdate()`: `settings.TIME_ZONE` ist UTC, `localdate()` liefert
    also das UTC-Datum. Zwischen 00:00 und 02:00 MESZ liegt das einen Tag zurück —
    eine am selben lokalen Tag erfasste Adresse (`identity.add_address` bekommt das
    Datum vom Menschen bzw. vom Frontend) fiele dann still aus dem Gültigkeits-
    fenster, und der Beleg ginge **ohne Empfängeranschrift** raus. Genau davor warnte
    dieser Docstring schon, während `localdate()` den Schutz gar nicht leisten konnte.

    (Die Ableitung von Beleg- und Fälligkeitsdatum in `publish_invoice` bleibt
    bewusst bei UTC: sie muss deckungsgleich mit dem DB-Trigger bleiben, der
    `(now() AT TIME ZONE 'UTC')::date` setzt.)
    """
    stichtag = on_date or betriebs_datum()
    zuordnungen = [
        pa
        for pa in PartyAddress.objects.filter(party_id=party_id).select_related("address")
        if pa.valid_from <= stichtag
        and (pa.valid_until is None or pa.valid_until > stichtag)
    ]
    if not zuordnungen:
        return None

    def rang(pa):
        typ = (
            _ADDRESS_PREFERENCE.index(pa.address_type)
            if pa.address_type in _ADDRESS_PREFERENCE
            else len(_ADDRESS_PREFERENCE)
        )
        return (typ, 0 if pa.is_primary else 1, pa.valid_from)

    return sorted(zuordnungen, key=rang)[0].address


def party_stammdaten(party, on_date=None):
    """Stammdaten-Snapshot einer Partei: Name, Typ, Steuer-IDs, Anschrift.

    USt-IdNr./Steuernummer gibt es nur an Organisationen (identity.organization);
    eine natürliche Person trägt keine.
    """
    org = Organization.objects.filter(party_id=party.id).first()
    return {
        "display_name": party.display_name,
        "party_type": party.party_type,
        "vat_id": org.vat_id if org else None,
        "tax_number": org.tax_number if org else None,
        "address": _address_snapshot(party_address(party.id, on_date)),
    }


def issuer_stammdaten():
    """Stammdaten-Snapshot des Ausstellers (company.company_profile, Singleton).

    None, wenn noch kein Firmenprofil gepflegt ist — der Beleg bleibt dann
    ausstellbar (das PDF zeigt seinen Fallback), die E-Rechnung verweigert
    ehrlich (ohne Verkäuferanschrift gibt es kein gültiges EN16931-XML).
    """
    profile = CompanyProfile.objects.first()
    if profile is None:
        return None
    return {
        "company_name": profile.company_name,
        "legal_form": profile.legal_form,
        "street": profile.street,
        "postal_code": profile.postal_code,
        "city": profile.city,
        "country": profile.country,
        "vat_id": profile.vat_id,
        "tax_number": profile.tax_number,
        "iban": profile.iban,
        "bic": profile.bic,
        "bank_name": profile.bank_name,
        "email": profile.email,
        "phone": profile.phone,
        "commercial_register": profile.commercial_register,
        "managing_director": profile.managing_director,
        "managing_director_title": profile.managing_director_title,
    }


def delivery_stammdaten(prop):
    """Snapshot des Leistungsorts (Liegenschaft) — im CII die ShipToTradeParty.

    Die Liegenschaft ist am Beleg Pflicht (invoice.property_id NOT NULL); sie ist
    der Ort, an dem die Leistung erbracht wurde. Sie wandert deshalb ebenfalls in
    den Snapshot, statt beim Rendern live nachgeschlagen zu werden.
    """
    if prop is None:
        return None
    return {
        "name": prop.name,
        "property_number": prop.property_number,
        "address": _address_snapshot(prop.address),
    }


def beleg_stammdaten(invoice):
    """Aussteller, Beteiligte und Leistungsort eines Belegs für die Ausgabe.

    **Quelle ist der eingefrorene Snapshot** (SNAPSHOT_VERSION >= 2): PDF und
    ZUGFeRD-XML zeigen damit genau die Stammdaten, die bei der Veröffentlichung
    galten — und beide dasselbe.

    **Live-Fallback JE FELD, nicht je Snapshot-Version.** Er greift für Entwürfe
    (die haben noch gar keinen Snapshot), für Altbelege vor der Snapshot-Härtung
    — und auch dann, wenn ein v2-Snapshot ein Feld gar nicht füllen KONNTE: wurde
    ein Beleg veröffentlicht, bevor das Firmenprofil gepflegt war, steht dort
    `issuer: null`. Ein reiner Versions-Fallback hätte diesen Beleg für immer
    ausstellerlos gelassen (PDF-Kopf „Firmenprofil noch nicht gepflegt", E-Rechnung
    dauerhaft 422) — und ein veröffentlichter Beleg lässt sich nicht neu ausstellen.
    Ein NULL-Feld ist keine eingefrorene Aussage, sondern eine Lücke; sie darf
    nachgezogen werden, ohne den Snapshot anzufassen (B-30 bleibt unberührt).

    Was der Snapshot WIRKLICH trägt, gewinnt dagegen immer — ein späterer Umzug
    des Kunden oder der Firma ändert keinen gestellten Beleg.

    Erwartet ein Invoice mit vorgeladenem `property__address` und `parties__party`.
    """
    snapshot = invoice.billing_snapshot or {}
    header = snapshot.get("header") or {}
    gefroren = {p.get("party_id"): p for p in (snapshot.get("parties") or [])}

    def _partei(p):
        eingefroren = gefroren.get(str(p.party_id)) or {}
        return {
            "party_id": str(p.party_id),
            "role": eingefroren.get("role", p.role),
            "is_primary": eingefroren.get("is_primary", p.is_primary),
            "allocation_percent": eingefroren.get(
                "allocation_percent",
                str(p.allocation_percent) if p.allocation_percent is not None else None,
            ),
            # Fehlt der eingefrorene Stammdatensatz (Altbeleg), live nachschlagen.
            "snapshot": eingefroren.get("snapshot") or party_stammdaten(p.party),
        }

    return {
        # Sagt dem Aufrufer, ob die Stammdaten aus dem eingefrorenen Beleg kommen.
        "aus_snapshot": bool(header.get("issuer")),
        "issuer": header.get("issuer") or issuer_stammdaten(),
        "delivery": header.get("delivery") or delivery_stammdaten(invoice.property),
        "parties": [
            _partei(p)
            for p in sorted(
                invoice.parties.all(), key=lambda p: (p.role, str(p.party_id))
            )
        ],
    }


def beteiligter(stammdaten, role):
    """Der (primäre) Beteiligte einer Rolle aus `beleg_stammdaten`, sonst None."""
    chosen = None
    for p in stammdaten["parties"]:
        if p.get("role") == role:
            chosen = p
            if p.get("is_primary"):
                break
    return chosen


def ausstellerzeilen(issuer):
    """Absenderblock aus dem Aussteller-Stammdatensatz.

    Dieselbe Zusammensetzung wie die Rücksendezeile des PDF-Anschriftfelds
    (Firma · Straße · PLZ Ort), nur zeilenweise statt mit Trennpunkten.
    """
    if not issuer:
        return []
    ort = " ".join(t for t in (issuer.get("postal_code"), issuer.get("city")) if t)
    return [t for t in (issuer.get("company_name"), issuer.get("street"), ort) if t]


def dokumentkopf(beleg):
    """Briefkopf eines Belegs für die **Bildschirm**-Darstellung.

    Befund G1. Sascha über die Angebots-Leseansicht: „Es sieht in der Übersicht
    halt nicht aus wie ein Dokument, sondern wie statisch auf der Seite
    eingebacken." Was fehlte, war der Briefkopf — ohne Absender, Empfänger und
    Betreff ist eine Positionstabelle eben eine Tabelle und kein Schriftstück.

    **Dieselbe Quelle wie das PDF, ausdrücklich.** Der Aufbau greift auf
    `beleg_stammdaten` / `issuer_stammdaten` / `quote_recipient_party` zu — also
    genau das, woraus auch die Ausfertigung entsteht. Ein zweiter, „schnellerer"
    Weg über die Live-Modelldaten wäre der eigentliche Fehler gewesen: Bei einer
    veröffentlichten Rechnung stünde nach einem Kundenumzug auf dem Schirm eine
    andere Anschrift als auf dem Beleg, den der Kunde in Händen hält. Der
    Snapshot ist die Wahrheit (B-30, GoBD), und diese Ansicht zeigt ihn.

    Gibt `None` zurück, wenn der Belegtyp keinen Kopf kennt — die Ansicht fällt
    dann auf ihre bisherige Darstellung zurück, statt zu brechen.
    """
    from db_core.models import Invoice, Quote
    # Lokaler Import: `beleg_pdf` importiert seinerseits aus diesem Modul.
    from db_core.services.beleg_pdf import empfaenger_zeilen, quote_recipient_party

    if isinstance(beleg, Invoice):
        # Die Frage ist „ist der Beleg gestellt?", nicht „hat der Snapshot einen
        # Aussteller?". Ein Beleg, der veröffentlicht wurde, BEVOR das
        # Firmenprofil gepflegt war, trägt `header.issuer = null` — seine
        # Beteiligten-Stammdaten sind aber sehr wohl eingefroren. Fragte man
        # nach dem Aussteller, fiele genau dieser Beleg in den Live-Zweig und
        # zeigte nach einem Kundenumzug eine andere Anschrift als das PDF, das
        # der Kunde in Händen hält.
        if beleg.status != "ENTWURF":
            # Gestellter Beleg: Der Snapshot gewinnt, und `beleg_stammdaten`
            # kostet dafür keine einzige zusätzliche Abfrage. Fehlt ein
            # einzelnes Feld (Altbeleg), zieht es dort seinen Live-Fallback.
            stamm = beleg_stammdaten(beleg)
            debtor = beteiligter(stamm, "INVOICE_DEBTOR")
            recipient = beteiligter(stamm, "INVOICE_RECIPIENT") or debtor
            return {
                "aussteller": ausstellerzeilen(stamm.get("issuer")),
                "empfaenger": empfaenger_zeilen((recipient or {}).get("snapshot")),
                "aus_snapshot": True,
            }

        # Entwurf: `beleg_stammdaten` löste hier JEDE Beteiligtenzeile live auf
        # (zwei Abfragen je Partei) — für einen Kopf, der genau eine davon
        # braucht. Diese Antwort wird von dreizehn Endpunkten gebaut, auch von
        # jedem POST. Deshalb nur der Empfänger, und der auch nur einmal.
        #
        # Die Sortierung ist NICHT kosmetisch: `beteiligter` liest aus
        # `beleg_stammdaten`, das nach `(role, party_id)` sortiert. Ohne
        # dieselbe Ordnung entschieden bei zwei nicht-primären Empfängern
        # (Erbengemeinschaft, WEG-Beirat) zwei Codepfade verschieden — und der
        # Empfänger auf dem Schirm spränge im Moment der Veröffentlichung um.
        # `InvoiceParty` trägt kein `Meta.ordering`; die Queryset-Reihenfolge
        # wäre die von PostgreSQL, also undefiniert.
        parteien = sorted(beleg.parties.all(), key=lambda p: (p.role, str(p.party_id)))
        empfaenger_partei = None
        for rolle in ("INVOICE_RECIPIENT", "INVOICE_DEBTOR"):
            for p in parteien:
                if p.role == rolle:
                    empfaenger_partei = p.party
                    if p.is_primary:
                        break
            if empfaenger_partei is not None:
                break
        return {
            "aussteller": ausstellerzeilen(issuer_stammdaten()),
            "empfaenger": (
                empfaenger_zeilen(party_stammdaten(empfaenger_partei))
                if empfaenger_partei is not None
                else []
            ),
            "aus_snapshot": False,
        }

    if isinstance(beleg, Quote):
        # Angebote tragen keinen Stammdaten-Snapshot; sie sind bis zur Annahme
        # veränderlich, und ihr Kopf ist damit zulässigerweise live. Das
        # Angebots-PDF baut denselben Block aus denselben zwei Funktionen.
        party = quote_recipient_party(beleg)
        return {
            "aussteller": ausstellerzeilen(issuer_stammdaten()),
            "empfaenger": empfaenger_zeilen(party_stammdaten(party)) if party else [],
            "aus_snapshot": False,
        }

    return None


def anzeige_menge_preis(line):
    """(Menge, Einzelpreis) einer Position **für die Ausgabe** — PDF wie XML.

    Ein Kreditbeleg (Gutschrift/Storno) speichert seine Umkehr im Einzelpreis:
    `_negated_lines` negiert den Preis, die Menge bleibt positiv (DB-CHECK
    `quantity > 0`). Für die Ausgabe taugt das nicht:

    - EN16931 verbietet einen negativen Nettoeinzelpreis (BR-27).
    - ZUGFeRD verlangt, dass Sichtbild (PDF) und Daten (XML) DENSELBEN Inhalt
      tragen. Zeigte das PDF „100 × −2,40" und das XML „−100 × 2,40", wäre das
      Hybrid-Dokument in sich widersprüchlich.

    Deshalb wird das Vorzeichen für die Ausgabe einheitlich auf die MENGE gelegt.
    Der Positionsbetrag (`net_amount`) bleibt unangetastet — er ist in beiden
    Darstellungen derselbe (negativ), und Menge × Preis geht weiterhin auf.

    **Einzige Vorzeichenstelle für die Ausgabe** — beide Renderer rufen sie auf,
    damit sie nicht auseinanderlaufen können.
    """
    menge = line.quantity
    preis = line.unit_price
    if preis is not None and preis < 0:
        return (None if menge is None else -menge), -preis
    return menge, preis


def _line_snapshot(line, rubrik_nummern=None):
    def s(v):
        return None if v is None else str(v)

    rubrik = (rubrik_nummern or {}).get(line.rubrik_id) if line.rubrik_id else None
    return {
        "position_number": line.position_number,
        "line_type": line.line_type,
        # Ohne line_kind wäre nicht vom Hash gedeckt, OB eine Position in die
        # Summe zählte — der Snapshot muss den Beleg vollständig rekonstruieren.
        "line_kind": line.line_kind,
        "rubrik": rubrik,
        "description": line.description,
        "quantity": s(line.quantity),
        "unit": line.unit,
        "unit_price": s(line.unit_price),
        "discount_percent": s(line.discount_percent),
        "tax_code": line.tax_code_id,
        "tax_rate_percent": s(line.tax_rate_percent),
        "net_amount": s(line.net_amount),
        # § 35a-Anteil (SNAPSHOT_VERSION 3): steht auf dem Kundenbeleg, gehört
        # also in den gehashten Inhalt. None = unbestimmt (kein Ausweis).
        "labour_net_amount": s(line.labour_net_amount),
    }


def _advance_snapshot(posten):
    """Die Anrechnung für den Beleg-Snapshot (nur Schlussrechnungen).

    Sie gehört in den gehashten Beleg: sie steht auf dem Dokument, das der Kunde
    bekommt („abzüglich Abschlagsrechnung RE-… vom …"), und ohne sie ließe sich
    der Beleg aus dem Snapshot nicht rekonstruieren.
    """
    def s(v):
        return None if v is None else str(v)

    return [
        {
            "advance_invoice_id": str(p["advance_invoice_id"]),
            "invoice_number": p["invoice_number"],
            "invoice_type": p["invoice_type"],
            "invoice_date": (
                p["invoice_date"].isoformat() if p["invoice_date"] else None
            ),
            "net_amount": s(p["net_amount"]),
            "tax_amount": s(p["tax_amount"]),
            "gross_amount": s(p["gross_amount"]),
            "steuergruppen": [
                {
                    "tax_code": g["tax_code"],
                    "tax_rate_percent": s(g["tax_rate_percent"]),
                    "net_amount": s(g["net_amount"]),
                    "tax_amount": s(g["tax_amount"]),
                    "gross_amount": s(g["gross_amount"]),
                }
                for g in p["steuergruppen"]
            ],
        }
        for p in posten
    ]


def _snapshot_and_hash(header, lines, parties=None, rubriken=None, advances=None):
    """Baut einen unveränderlichen Beleg-Snapshot (dict) und dessen SHA-256-Hash.

    Der Hash läuft über die kanonische JSON-Serialisierung (sortierte Schlüssel,
    keine Zeitstempel) — er identifiziert den Inhalt reproduzierbar (B-21/B-30).

    Die Abschnitte (Rubriken) gehören in den Snapshot: sie tragen Titel und
    Beschreibung, die der Kunde im PDF liest. Positionen referenzieren sie über
    die Abschnittsnummer, nicht die UUID — der Snapshot soll ohne Fremdschlüssel
    lesbar bleiben. Die internen Kalkulationsfelder (EK, Aufschlag, Herkunft)
    bleiben BEWUSST draußen: sie stehen nicht auf dem Kundenbeleg, und ein
    korrigierter EK-Snapshot dürfte den Belegzustand nicht verändern.
    """
    rubriken = sorted(rubriken or [], key=lambda r: r.position_number)
    rubrik_nummern = {r.id: r.position_number for r in rubriken}
    snapshot = {
        "header": header,
        "rubriken": [
            {
                "position_number": r.position_number,
                "title": r.title,
                "description": r.description,
            }
            for r in rubriken
        ],
        "lines": [_line_snapshot(l, rubrik_nummern) for l in lines],
        "parties": parties or [],
    }
    # Nur setzen, wenn es eine Anrechnung gibt: sonst änderte sich der Hash jedes
    # gewöhnlichen Belegs ohne inhaltlichen Grund (und Angebote trügen ein Feld,
    # das es dort nicht gibt).
    if advances:
        snapshot["advances"] = advances
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return snapshot, digest


def publish_invoice(actor_app_user_id, *, invoice_id):
    """Veröffentlicht eine Rechnung (ENTWURF → VEROEFFENTLICHT).

    Legt Snapshot + Inhalts-Hash an und flippt den Status in einem Zug; die DB
    vergibt die Belegnummer (RE-/GS-Kreis) und prüft die Tore (Auftrag
    kaufmännisch geprüft B-08, Rechnungsschuldner + genau ein primärer
    Empfänger A-27/A-28). Fachliche Tor-Verstöße werden zu ValueError (→422).
    """
    invoice = (
        Invoice.objects.filter(id=invoice_id)
        .select_related("property__address")
        .prefetch_related("lines", "parties__party")
        .first()
    )
    if invoice is None:
        raise ValueError("Rechnung nicht gefunden.")
    if invoice.status != "ENTWURF":
        raise ValueError("Nur Rechnungen im Entwurf können veröffentlicht werden.")
    if invoice.invoice_type not in _CREDIT_TYPES and invoice.work_order_id is None:
        raise ValueError(
            "Veröffentlichung erfordert einen zugeordneten Auftrag (B-08)."
        )
    _vergessene_anrechnung_pruefen(invoice)

    # Das Belegdatum wird IMMER hier festgeschrieben, wenn es fehlt — nicht erst
    # vom DB-Trigger (der bleibt das Sicherheitsnetz). Sonst trüge der gehashte
    # Snapshot `invoice_date: null`, während die Zeile (und das ausgelieferte PDF,
    # und die daraus abgeleitete Skontofrist) das heutige Datum zeigt: der
    # Snapshot rekonstruierte den Beleg dann nicht mehr (B-21/B-30).
    #
    # Auf derselben Basis leitet sich die Fälligkeit aus dem Zahlungsziel ab, wenn
    # kein Datum gesetzt wurde. `due_date` bleibt die maßgebliche Spalte
    # (Mahnwesen/offene Posten/DATEV); ein bereits gesetztes Datum bleibt
    # unangetastet.
    kopf_extra = {}
    if invoice.invoice_date is None:
        invoice.invoice_date = datetime.now(timezone.utc).date()
        kopf_extra["invoice_date"] = invoice.invoice_date
    if invoice.due_date is None and invoice.payment_term_days is not None:
        invoice.due_date = invoice.invoice_date + timedelta(
            days=int(invoice.payment_term_days)
        )
        kopf_extra["due_date"] = invoice.due_date
    # Jetzt stehen Belegdatum und Fälligkeit endgültig fest — erst hier lässt sich
    # abschließend prüfen, dass die Skontofrist nicht nach der Fälligkeit endet.
    _frist_gegen_faelligkeit_pruefen(
        invoice.invoice_date, invoice.due_date, invoice.discount_days
    )

    header = {
        # Versionsstempel des Snapshot-Aufbaus: Leser erkennen daran, ob die
        # Stammdaten (Aussteller/Beteiligte/Leistungsort) eingefroren sind oder
        # ob sie für einen Altbeleg live nachgeschlagen werden müssen.
        "snapshot_version": SNAPSHOT_VERSION,
        "invoice_type": invoice.invoice_type,
        "property_id": str(invoice.property_id),
        "project_id": str(invoice.project_id) if invoice.project_id else None,
        "work_order_id": (
            str(invoice.work_order_id) if invoice.work_order_id else None
        ),
        "reference_invoice_id": (
            str(invoice.reference_invoice_id)
            if invoice.reference_invoice_id
            else None
        ),
        "invoice_date": invoice.invoice_date.isoformat() if invoice.invoice_date else None,
        "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
        # Zahlungsbedingungen gehören in den Snapshot: sie stehen auf dem Beleg,
        # den der Kunde bekommt, und sind damit GoBD-fest einzufrieren.
        "payment_term_days": invoice.payment_term_days,
        "discount_percent": (
            str(invoice.discount_percent) if invoice.discount_percent is not None else None
        ),
        "discount_days": invoice.discount_days,
        "currency": invoice.currency,
        "net_total": str(invoice.net_total),
        "tax_total": str(invoice.tax_total),
        "gross_total": str(invoice.gross_total),
        # Stammdaten einfrieren (SNAPSHOT_VERSION 2): Aussteller und Leistungsort
        # gehören auf den Beleg — eine spätere Änderung am Firmenprofil oder an der
        # Liegenschaft darf einen gestellten Beleg nicht rückwirkend umschreiben.
        "issuer": issuer_stammdaten(),
        "delivery": delivery_stammdaten(invoice.property),
    }
    parties = [
        {
            "party_id": str(p.party_id),
            "role": p.role,
            "is_primary": p.is_primary,
            "allocation_percent": (
                str(p.allocation_percent) if p.allocation_percent is not None else None
            ),
            # Name/Anschrift/USt-IdNr. des Beteiligten einfrieren (Stichtag =
            # jetzt, siehe party_address: das Belegdatum kann zurückdatiert sein).
            "snapshot": party_stammdaten(p.party),
        }
        for p in sorted(invoice.parties.all(), key=lambda p: (p.role, str(p.party_id)))
    ]
    lines = sorted(invoice.lines.all(), key=lambda l: l.position_number)
    posten = anrechnungen(invoice) if invoice.invoice_type == FINAL_TYPE else []
    snapshot, digest = _snapshot_and_hash(
        header, lines, parties, rubriken=list(invoice.rubriken.all()),
        advances=_advance_snapshot(posten),
    )

    with as_business_error():
        with business_transaction(actor_app_user_id):
            updated = Invoice.objects.filter(id=invoice_id, status="ENTWURF").update(
                billing_snapshot=snapshot,
                content_hash=digest,
                status="VEROEFFENTLICHT",
                **kopf_extra,
            )
            if not updated:
                # Zwischenzeitlich veröffentlicht (Wettlauf): kein stiller Erfolg.
                raise ValueError(
                    "Rechnung ist nicht mehr im Entwurf (bereits veröffentlicht?)."
                )
    invoice.refresh_from_db()
    return invoice


def send_quote(actor_app_user_id, *, quote_id):
    """Versendet ein Angebot (ENTWURF → … → VERSENDET).

    Durchläuft die erlaubten Zwischenstatus (INTERN_GEPRUEFT, FREIGEGEBEN) und
    setzt beim Versand Snapshot + Inhalts-Hash; die DB vergibt die AN-Nummer und
    friert den Beleg ein (B-30).
    """
    quote = Quote.objects.filter(id=quote_id).prefetch_related("lines").first()
    if quote is None:
        raise ValueError("Angebot nicht gefunden.")
    if quote.status != "ENTWURF":
        raise ValueError("Nur Angebote im Entwurf können versendet werden.")

    header = {
        "title": quote.title,
        "property_id": str(quote.property_id),
        "project_id": str(quote.project_id) if quote.project_id else None,
        "quote_date": quote.quote_date.isoformat() if quote.quote_date else None,
        "valid_until_date": (
            quote.valid_until_date.isoformat() if quote.valid_until_date else None
        ),
        "currency": quote.currency,
        "net_total": str(quote.net_total),
        "tax_total": str(quote.tax_total),
        "gross_total": str(quote.gross_total),
    }
    lines = sorted(quote.lines.all(), key=lambda l: l.position_number)
    snapshot, digest = _snapshot_and_hash(
        header, lines, rubriken=list(quote.rubriken.all())
    )

    with as_business_error():
        with business_transaction(actor_app_user_id):
            Quote.objects.filter(id=quote_id).update(status="INTERN_GEPRUEFT")
            Quote.objects.filter(id=quote_id).update(status="FREIGEGEBEN")
            Quote.objects.filter(id=quote_id).update(
                billing_snapshot=snapshot,
                content_hash=digest,
                status="VERSENDET",
            )
    quote.refresh_from_db()
    return quote


# --- Der Ausgang des Angebots: angenommen, abgelehnt, abgelaufen ------------
#
# Wörtliche Spiegelung von `workflow.status_transition` für entity='quote'
# (Migration 0016), **ohne die Kanten nach ERSETZT** — und das ist kein Versehen:
# Der DB-CHECK verlangt für ERSETZT einen `replaced_by_quote_id` (0018), also ein
# **existierendes Nachfolgeangebot**. „Ersetzen" ist damit kein Statuswechsel,
# sondern der Vorgang „Ersatzangebot anlegen und verknüpfen" — ein eigener Slice.
# Ihn hier als nackten Statuswechsel anzubieten hieße, dem Nutzer einen Knopf zu
# geben, der zuverlässig an einem CHECK scheitert.
#
# Die Kante FREIGEGEBEN → VERSENDET gehört `send_quote` (sie vergibt die Nummer und
# friert den Beleg ein) und steht deshalb nicht in dieser Tabelle: Zwei Wege in
# denselben Status wären zwei Wahrheiten.
QUOTE_AUSGANG = {
    "VERSENDET": {
        "ANGENOMMEN": False,   # {Zielstatus: begründungspflichtig}
        "ABGELEHNT": False,
        "ABGELAUFEN": False,
    },
}


def set_quote_status(actor_app_user_id, *, quote_id, to_status, reason=None):
    """Der Ausgang eines versendeten Angebots: ANGENOMMEN | ABGELEHNT | ABGELAUFEN.

    Die Übergänge lagen seit Migration 0016 in der Statustabelle — **gesetzt hat
    sie nie jemand**. Ein Angebot blieb für immer „versendet", auch wenn der Kunde
    längst zugesagt hatte. Damit war der Auftrag, der daraus entsteht, ohne Beleg
    dafür, *dass* er vereinbart wurde.

    **Der Inhalt bleibt unangetastet (B-30).** Geändert wird ausschließlich der
    Status; `billing_snapshot` und `content_hash` des versendeten Angebots bleiben
    Zeichen für Zeichen dieselben — der Trigger `freeze_sent_quote` lässt genau das
    zu und nichts sonst. Deshalb wird hier bewusst **nur die Statusspalte**
    geschrieben (`update(status=…)`), nicht das Model gespeichert: Ein
    `quote.save()` schriebe alle Felder erneut und liefe Gefahr, den eingefrorenen
    Inhalt anzufassen.

    Das Angebot bleibt in **jedem** dieser Status ein Soll des Auftrags — außer
    ABGELEHNT: Ein abgelehntes Angebot wurde nie vereinbart und bildet kein Soll
    (`site_report.SOLL_AUSGESCHLOSSENE_STATUS`). Der Soll-Ist-Abgleich rechnet sich
    dadurch neu; das ist gewollt und die eigentliche Wirkung von „abgelehnt".
    """
    quote = Quote.objects.filter(id=quote_id).first()
    if quote is None:
        raise ValueError("Angebot nicht gefunden.")

    allowed = QUOTE_AUSGANG.get(quote.status, {})
    if to_status not in allowed:
        if to_status == "ERSETZT":
            raise ValueError(
                "Ein Angebot wird nicht per Statuswechsel ersetzt: Der Status "
                "ERSETZT verlangt ein Nachfolgeangebot (DB-Regel B-30/P3-08). "
                "Dafür ist ein Ersatzangebot anzulegen und zu verknüpfen — das ist "
                "ein eigener Vorgang, kein Statuswechsel."
            )
        raise ValueError(
            f"Übergang {quote.status} → {to_status} ist nicht erlaubt. "
            "Der Ausgang eines Angebots wird am versendeten Angebot festgehalten "
            "(angenommen, abgelehnt oder abgelaufen)."
        )
    if allowed[to_status] and not (reason and reason.strip()):
        raise ValueError(
            f"Übergang {quote.status} → {to_status} erfordert eine Begründung."
        )

    from db_core.models import BillingLink, WorkOrder

    with as_business_error():
        with business_transaction(
            actor_app_user_id, status_reason=reason.strip() if reason else None
        ):
            # **Serialisierung über die Klammer** (Review-Befund, Nebenläufigkeit).
            # ABGELEHNT/ABGELAUFEN nehmen das Angebot aus dem Soll — der Guard unten
            # liest die Abrechnungsbindungen, die eine GLEICHZEITIGE Angebotsrechnung
            # gerade erst schreibt. Ohne die Auftragssperre entginge ihm diese
            # Bindung, und Angebot wäre danach abgelehnt UND fakturiert. Dieselbe
            # `work_order`-Zeile sperren die Abrechnungswege zuerst; wer sie hält,
            # sieht den anderen. Ein Angebot ohne Auftrag trägt keine auftragsweite
            # Mengengrenze — dann ist nichts zu serialisieren.
            if quote.work_order_id is not None:
                list(
                    WorkOrder.objects.filter(id=quote.work_order_id)
                    .select_for_update()
                    .values_list("id", flat=True)
                )

            # **Ein bereits fakturiertes Angebot kann nicht abgelehnt/abgelaufen
            # werden** (Review-Befund, KRITISCH 1 — an der Wurzel). Fiele das Soll
            # nach der Angebotsrechnung auf 0, hielte der Nachtrag plötzlich die ganze
            # Ist-Menge für offen. Die Nachtragsformel (`max(A, Soll)`) fängt das
            # inzwischen ab; hier wird der Widerspruch schon an der Quelle benannt.
            # ANGENOMMEN ist erlaubt — ein fakturiertes Angebot ist angenommen.
            if to_status in ("ABGELEHNT", "ABGELAUFEN"):
                fakturiert = BillingLink.objects.filter(
                    quote_line__quote_id=quote_id,
                    source_kind="ANGEBOTSPOSITION",
                    released_at__isnull=True,
                ).exists()
                if fakturiert:
                    raise ValueError(
                        "Aus diesem Angebot wurde bereits eine Rechnung erzeugt — es "
                        f"lässt sich nicht mehr auf {to_status} setzen. Ein "
                        "abgerechnetes Angebot aus dem Soll zu nehmen, öffnete die "
                        "Doppelabrechnung des Nachtrags. Wenn die Rechnung falsch "
                        "war, ist sie zuerst zu stornieren (das löst die "
                        "Abrechnungsbindung)."
                    )

            updated = Quote.objects.filter(
                id=quote_id, status=quote.status
            ).update(status=to_status)
            if not updated:
                # Zwischenzeitlich hat jemand anderes entschieden (Wettlauf).
                # Kein stiller Erfolg — der Nutzer sähe sonst „angenommen", während
                # in Wahrheit „abgelehnt" gespeichert ist.
                raise ValueError(
                    "Der Angebotsstatus hat sich zwischenzeitlich geändert. Bitte "
                    "die Ansicht neu laden."
                )
    quote.refresh_from_db()
    return quote


# --- Storno / Rechnungskorrektur (Folgebelege) -----------------------------
# GoBD: eine veröffentlichte Rechnung ist unveränderlich; „Löschen"/„Korrigieren"
# gibt es nicht — nur ein Folgebeleg (STORNO = voller Ausgleich, GUTSCHRIFT =
# Teilkorrektur) mit reference_invoice_id auf den Ursprung. Die Positionen werden
# invertiert: quantity bleibt positiv (DB-CHECK quantity > 0), der unit_price wird
# negiert → negativer net_amount. Die DB verlangt zur Veröffentlichung, dass die
# Schuldner des Korrekturbelegs Schuldner des Ursprungs sind (P3-06); STORNO/
# GUTSCHRIFT sind von der Auftrags-Vorbedingung (B-08) befreit.

def _negated_lines(origin_lines, positions=None):
    """Invertierte Positionen aus den Ursprungszeilen (negativer unit_price).

    positions (Menge von position_number) begrenzt auf eine Teilkorrektur; None =
    alle Positionen (Vollstorno). Text-/Zwischensummenzeilen werden übernommen.
    Gibt (prepared, net_total, tax_total, gross_total) wie _prepare_lines.

    `line_kind` wird mitgeführt: eine Alternativ-/Bedarfsposition wurde nie
    berechnet, ihre Invertierung darf folglich auch nichts gutschreiben. Sie
    bleibt im Folgebeleg eine Alternative und geht nicht in die Summe ein — sonst
    entstünde eine Gutschrift über Beträge, die nie in Rechnung standen.
    """
    prepared = []
    new_pos = 0
    for line in sorted(origin_lines, key=lambda l: l.position_number):
        if positions is not None and line.position_number not in positions:
            continue
        new_pos += 1
        if line.line_type in TEXT_TYPES:
            prepared.append(
                {
                    "position_number": new_pos,
                    "line_type": line.line_type,
                    "line_kind": SUMMENWIRKSAM,
                    "description": line.description,
                }
            )
            continue
        neg_price = (-line.unit_price).quantize(_Q_PRICE, rounding=ROUND_HALF_UP)
        discount = line.discount_percent or Decimal(0)
        net = _round2(line.quantity * neg_price * (Decimal(1) - discount / Decimal(100)))
        prepared.append(
            {
                "position_number": new_pos,
                "line_type": line.line_type,
                "line_kind": line.line_kind,
                "description": line.description,
                "quantity": line.quantity,
                "unit": line.unit,
                "unit_price": neg_price,
                "discount_percent": line.discount_percent,
                "tax_code_id": line.tax_code_id,
                "tax_rate_percent": line.tax_rate_percent,
                "net_amount": net,
                # Der § 35a-Anteil kehrt sich mit dem Betrag um: die Gutschrift
                # nimmt genau die Arbeitskosten zurück, die die Ursprungsrechnung
                # ausgewiesen hat. War er dort unbestimmt, bleibt er es hier —
                # eine Umkehr erfindet keine Bestimmtheit.
                "labour_net_amount": (
                    None if line.labour_net_amount is None else -line.labour_net_amount
                ),
            }
        )

    net, tax, gross = _totals(prepared)
    return prepared, net, tax, gross


def _create_credit(actor_app_user_id, origin, *, invoice_type, positions):
    """Erzeugt einen Storno-/Gutschriftbeleg zum Ursprung und veröffentlicht ihn.

    Kopiert Schuldner und Empfänger des Ursprungs (Voraussetzung für die
    Veröffentlichung: A-27 Schuldner/Empfänger, P3-06 Schuldner-Übereinstimmung).
    """
    prepared, net, tax, gross = _negated_lines(list(origin.lines.all()), positions)
    # Summenwirksam heißt: keine Text-/Zwischensummenzeile und keine Alternativ-/
    # Bedarfsposition. Eine Korrektur nur über Alternativen gutschriebe nichts —
    # die DB wiese sie ab, hier gibt es die klare Meldung.
    if not any(
        r["line_type"] not in TEXT_TYPES and r["line_kind"] == SUMMENWIRKSAM
        for r in prepared
    ):
        raise ValueError(
            "Der Korrekturbeleg enthält keine summenwirksame Betragsposition "
            "(Alternativ- und Bedarfspositionen wurden nie berechnet)."
        )
    parties = list(origin.parties.all())
    debtors = [p for p in parties if p.role == "INVOICE_DEBTOR"]
    recipients = [p for p in parties if p.role == "INVOICE_RECIPIENT"]
    if not debtors:
        raise ValueError("Der Ursprungsbeleg hat keinen Rechnungsschuldner (A-27).")

    def _copy_party(credit_id, p):
        # liability_group/-basis und allocation_percent mitführen, sonst scheitert
        # ein Mehr-Schuldner-Beleg an A-29 (Gesamtschuld ohne Grundlage).
        InvoiceParty.objects.create(
            id=uuid.uuid4(), invoice_id=credit_id, party_id=p.party_id,
            role=p.role, is_primary=p.is_primary,
            allocation_percent=p.allocation_percent,
            liability_group=p.liability_group, liability_basis=p.liability_basis,
        )

    # Anlage UND Veröffentlichung in EINER Transaktion: scheitert ein Tor beim
    # Publish, wird auch der ENTWURF-Folgebeleg zurückgerollt (kein Waise).
    # invoice_date vom Ursprung übernehmen, damit der historische Steuersatz zum
    # Belegdatum passt (P3-05) — ein Storno kehrt die Ursprungsbeträge um.
    with business_transaction(actor_app_user_id):
        credit = Invoice.objects.create(
            id=uuid.uuid4(),
            property_id=origin.property_id,
            project_id=origin.project_id,
            work_order_id=None,  # STORNO/GUTSCHRIFT sind von B-08 befreit
            invoice_type=invoice_type,
            reference_invoice_id=origin.id,
            status="ENTWURF",
            invoice_date=origin.invoice_date,
            # Der Ausweis folgt dem Ursprung: das Storno einer B2B-Rechnung trägt
            # keinen § 35a-Block, das einer Privatkundenrechnung nimmt genau den
            # dort ausgewiesenen Betrag wieder zurück.
            show_labour_costs=origin.show_labour_costs,
            net_total=net,
            tax_total=tax,
            gross_total=gross,
            version=1,
        )
        for row in prepared:
            InvoiceLine.objects.create(id=uuid.uuid4(), invoice_id=credit.id, **row)
        for p in debtors:
            _copy_party(credit.id, p)
        for p in recipients:
            _copy_party(credit.id, p)
        # Veröffentlichung durchläuft die DB-Tore (P3-06/A-27) und vergibt die
        # GS-Nummer; Tor-Verstöße werden in ValueError (→422) übersetzt und rollen
        # die ganze Transaktion zurück.
        published = publish_invoice(actor_app_user_id, invoice_id=credit.id)
    return published


def _anrechnung_sperre_pruefen(origin):
    """Ein angerechneter Abschlag ist nicht korrigierbar (Migration 0060).

    Solange eine veröffentlichte Schlussrechnung ihn anrechnet, wäre ein Storno
    (oder eine Teilgutschrift) ein Widerspruch: die unveränderliche
    Schlussrechnung wiese einen Abzug für einen Beleg aus, den es nicht mehr gibt
    — der Kunde bekäme die Abschlagssumme geschenkt.

    Der Weg zurück führt **ausschließlich über das STORNO der Schlussrechnung**.
    Nur das Storno löst die Bindung (`invoicing.advance_blocking_final` prüft
    genau darauf); eine Gutschrift lässt die Schlussrechnung bestehen — und wäre
    auf einer Schlussrechnung MIT Anrechnung ohnehin unzulässig (siehe
    create_correction). Die Meldung nennt deshalb nur das Storno: der frühere
    Hinweis „Storno/Gutschrift" führte in eine Sackgasse (Gutschrift erstellt,
    Abschlag bleibt trotzdem für immer gebunden).

    Die DB lehnt es ohnehin ab (Tor im Veröffentlichungspfad des Kreditbelegs);
    hier kommt die Meldung mit der Belegnummer der Schlussrechnung.
    """
    if origin.invoice_type not in ADVANCE_TYPES:
        return
    gebunden = _gebundene_abschlaege([origin.id])
    final_id = gebunden.get(origin.id)
    if final_id is None:
        return
    final = Invoice.objects.filter(id=final_id).only("invoice_number").first()
    nummer = (final.invoice_number if final else None) or str(final_id)
    raise ValueError(
        f"Dieser Abschlag ist in der Schlussrechnung {nummer} angerechnet und "
        "kann nicht storniert oder gutgeschrieben werden. Stornieren Sie zuerst "
        "die Schlussrechnung — danach ist der Abschlag wieder frei."
    )


def _erteilte_gutschriften(origin_id):
    """Die veröffentlichten GUTSCHRIFTEN zu einer Rechnung: (Summe, Belegnummern).

    Die Summe ist ein **positiver** Betrag (Kreditbelege führen negative Summen).
    Nur veröffentlichte zählen — ein Entwurf hat noch nichts erstattet.
    """
    rows = list(
        Invoice.objects.filter(
            reference_invoice_id=origin_id,
            invoice_type="GUTSCHRIFT",
            status="VEROEFFENTLICHT",
        ).values_list("invoice_number", "gross_total")
    )
    summe = sum(
        (abs(g) for _nr, g in rows if g is not None), Decimal("0.00")
    )
    nummern = sorted(nr for nr, _g in rows if nr)
    return summe, nummern


def _vollgutschrift_sperre_pruefen(origin, positions):
    """Eine **Vollgutschrift** auf eine abgerechnungsgebundene Rechnung ist ein
    verkappter Storno — und wird abgelehnt (Entscheidung des Users).

    Der Unterschied ist keine Formsache, er entscheidet über die Leistung:

    * Der **Storno** hebt die Rechnung auf und **löst die Bindungen**
      (`invoicing.release_billing_links_on_cancel`). Die Stunden und
      Berichtspositionen werden wieder abrechenbar.
    * Die **Gutschrift** ist eine Teilkorrektur. Die Ursprungsrechnung besteht
      weiter und fordert weiterhin Geld — die Leistung bleibt abgerechnet, und die
      Bindung bleibt (bewusst) bestehen. Das ist fachlich richtig: Eine Kulanz oder
      ein Preisnachlass heißt nicht, dass nicht gearbeitet wurde.

    Wer den **vollen Betrag** gutschreibt, meint aber den Storno — und bekäme mit
    der Gutschrift das Gegenteil: eine Rechnung über 0 €, deren Leistung für immer
    als abgerechnet gilt und **nie wieder** in Rechnung gestellt werden kann. Der
    zweideutige Fall wird deshalb **verboten statt interpretiert**.

    **Die Grenze wird über den Betrag gezogen, nicht über die Positionen**: Zwei
    Teilgutschriften, die zusammen den Rechnungsbetrag ausschöpfen, sind derselbe
    verkappte Storno wie eine Gutschrift über alle Positionen. Maßgeblich ist also

        Summe bereits erteilter Gutschriften + diese Korrektur >= Rechnungsbetrag

    Muster und Ton folgen `_anrechnung_sperre_pruefen`: Der zweideutige Vorgang
    wird abgelehnt und der Weg genannt, der zum Ziel führt.

    **Zweite, schwächere Grenze — sie gilt IMMER, auch ohne Bindung:** Die Summe
    der Gutschriften darf den Rechnungsbetrag nicht **übersteigen**. Auf einer
    ungebundenen Rechnung (z. B. handgeschriebener Beleg) ist die Vollgutschrift
    zulässig — sie verschenkt keine Leistung, weil keine gebunden ist. Aber 120 %
    zurückerstatten kann kein Fall rechtfertigen; das wäre eine Überzahlung
    zulasten des Hauses.
    """
    brutto = origin.gross_total or Decimal("0.00")
    if brutto <= 0:  # pragma: no cover — eine Rechnung über 0 € gibt es nicht
        return
    _prepared, _net, _tax, gross = _negated_lines(
        list(origin.lines.all()), set(positions)
    )
    bereits, nummern = _erteilte_gutschriften(origin.id)
    summe = bereits + abs(gross)
    vorher = (
        f" (bereits gutgeschrieben: {', '.join(nummern)})" if nummern else ""
    )
    gebunden = BillingLink.objects.filter(
        invoice_id=origin.id, released_at__isnull=True
    ).exists()
    if gebunden and summe >= brutto:
        raise ValueError(
            "Diese Korrektur schöpft den vollen Rechnungsbetrag aus"
            f"{vorher} — das ist ein Storno, keine Gutschrift. Eine Gutschrift lässt "
            "die Rechnung bestehen: Die abgerechneten Leistungen (Berichtspositionen, "
            "Zeitbuchungen, Angebotspositionen) blieben gebunden und wären nie wieder "
            "abrechenbar. Wenn die Rechnung falsch war, ist sie zu STORNIEREN — das "
            "hebt sie auf und gibt die Leistungen wieder frei."
        )
    if summe > brutto:
        raise ValueError(
            "Diese Korrektur überschriebe den Rechnungsbetrag "
            f"({brutto} €){vorher}: Es würde mehr erstattet, als je in Rechnung "
            "gestellt wurde. Eine Gutschrift kann eine Rechnung höchstens "
            "vollständig aufzehren."
        )


def _gutschrift_nach_storno_pruefen(origin):
    """Auf eine **stornierte** Rechnung wird **keine Gutschrift** mehr erteilt.

    Der Spiegelfall zu `_storno_nach_gutschrift_pruefen` (Review-Befund H-1): Der
    Storno hat die Rechnung bereits **vollständig** umgekehrt — der Kunde schuldet
    nichts mehr. Eine Gutschrift daneben ist ein **zweiter** Kreditbeleg über
    dieselbe Leistung: Rechnung 975,80 €, STORNO −975,80 €, Gutschrift −975,80 €
    ergibt eine Erstattung von 1.951,60 € auf eine Forderung, die es nicht mehr
    gibt.

    Die Doppelabrechnungssperre (`_vollgutschrift_sperre_pruefen`) fängt das
    **nicht** ab: Sie steigt bei einer stornierten Rechnung sofort aus, weil der
    Storno die Bindungen ja gerade **gelöst** hat — sie sieht keine aktive
    `billing_link` mehr und lässt die Vollgutschrift durch. Genau hier reißt das
    Loch auf, und genau hier wird es geschlossen.

    Der richtige Weg nach einem Storno ist die **neue, korrigierte Rechnung**: Die
    Leistungen sind durch das Storno wieder frei und lassen sich erneut (und dann
    richtig) fakturieren. Auf einem aufgehobenen Beleg gibt es nichts mehr zu
    korrigieren.
    """
    nummern = sorted(
        nr
        for nr in Invoice.objects.filter(
            reference_invoice_id=origin.id,
            invoice_type="STORNO",
            status="VEROEFFENTLICHT",
        ).values_list("invoice_number", flat=True)
        if nr
    )
    if not nummern:
        return
    raise ValueError(
        f"Diese Rechnung ist bereits storniert ({', '.join(nummern)}) — der volle "
        "Betrag wurde damit schon zurückgenommen. Eine Gutschrift darauf gäbe dem "
        "Kunden denselben Betrag ein zweites Mal zurück. Der stornierte Beleg wird "
        "nicht korrigiert: Die Leistungen sind wieder frei, stellen Sie die "
        "Rechnung neu."
    )


def _storno_nach_gutschrift_pruefen(origin):
    """Eine Rechnung mit bereits erteilter Gutschrift wird **nicht** storniert.

    Sonst wird derselbe Betrag **zweimal** erstattet (Review-Befund H-1b): Der
    Stornobeleg kehrt die **vollen** Ursprungsbeträge um — die bereits erteilte
    Gutschrift steht als eigener Beleg daneben und bleibt bestehen. Rechnung 1.000
    €, Gutschrift −100 €, Storno −1.000 € ergibt einen Saldo von −100 € zugunsten
    eines Kunden, der nur 900 € zu zahlen hatte.

    **Warum kein „Restbetrags-Storno"?** Er wäre die fachlich schönere Antwort,
    lässt sich aus dem Bestand aber nicht **belastbar** rechnen: Die Positionen
    eines Kreditbelegs werden neu durchnummeriert (`_negated_lines` vergibt
    `new_pos`), und es gibt keine Spalte, die eine Gutschriftposition auf die
    Ursprungsposition zurückführt. Welche Positionen noch offen sind, ließe sich nur
    über Textvergleiche **raten** — im GoBD-Belegpfad ist Raten keine Option. Und
    ein Storno, der nur einen Teilbetrag umkehrt, wäre kein Storno mehr: Er ist der
    Beleg „diese Rechnung gilt nicht", und die DB hängt genau daran die Freigabe der
    Abrechnungsbindungen.

    Also die amtssichere Grenze: Storno **vor** jeder Gutschrift, oder gar nicht.
    Der verbleibende Weg auf einer teilgutgeschriebenen Rechnung ist die weitere
    Gutschrift über den Rest.

    **Bewusst in Kauf genommen:** Eine Rechnung, die schon eine Teilgutschrift
    trägt, lässt sich nicht mehr stornieren — ihre Abrechnungsbindungen bleiben
    also bestehen. Das ist die konservative Seite des Fehlers: Lieber eine Leistung,
    die abgerechnet bleibt (sie WURDE ja in Rechnung gestellt und nur teilweise
    erlassen), als eine doppelte Erstattung an den Kunden.
    """
    _summe, nummern = _erteilte_gutschriften(origin.id)
    if not nummern:
        return
    raise ValueError(
        f"Zu dieser Rechnung besteht bereits eine Gutschrift ({', '.join(nummern)}). "
        "Ein Storno kehrt die VOLLEN Ursprungsbeträge um — der bereits "
        "gutgeschriebene Anteil würde dem Kunden ein zweites Mal erstattet. Eine "
        "teilweise gutgeschriebene Rechnung wird nicht storniert; korrigieren Sie "
        "den Restbetrag mit einer weiteren Gutschrift."
    )


def create_cancellation(actor_app_user_id, *, invoice_id):
    """Storniert eine veröffentlichte Rechnung durch einen Stornobeleg (STORNO)
    mit vollständig invertierten Positionen.

    Nicht möglich, wenn zur Rechnung bereits eine **Gutschrift** besteht: Der
    Storno kehrte den vollen Betrag um, die Gutschrift bliebe daneben bestehen —
    der Kunde bekäme denselben Betrag zweimal erstattet
    (siehe `_storno_nach_gutschrift_pruefen`).
    """
    origin = (
        Invoice.objects.filter(id=invoice_id)
        .prefetch_related("lines", "parties")
        .first()
    )
    if origin is None:
        raise ValueError("Ursprungsrechnung nicht gefunden.")
    if origin.status != "VEROEFFENTLICHT":
        raise ValueError("Nur veröffentlichte Rechnungen können storniert werden (B-21).")
    if origin.invoice_type in _CREDIT_TYPES:
        raise ValueError("Eine Gutschrift/Storno kann nicht erneut storniert werden.")
    _anrechnung_sperre_pruefen(origin)
    if Invoice.objects.filter(
        reference_invoice_id=origin.id, invoice_type="STORNO", status="VEROEFFENTLICHT"
    ).exists():
        raise ValueError("Diese Rechnung wurde bereits storniert.")
    _storno_nach_gutschrift_pruefen(origin)
    return _create_credit(actor_app_user_id, origin, invoice_type="STORNO", positions=None)


def create_correction(actor_app_user_id, *, invoice_id, positions):
    """Erzeugt eine Rechnungskorrektur (GUTSCHRIFT) über die angegebenen
    Positionen (position_number) einer veröffentlichten Rechnung.

    **Eine Schlussrechnung MIT Anrechnung ist nicht teilgutschriftfähig.** Ihre
    Positionen sind kein unabhängiger Satz: die Anrechnungspositionen sind
    negative Abzüge, die nur zusammen mit der Leistung Sinn ergeben. Wer
    ausgerechnet den Abzug „gutschreibt", dreht ihn um — es entstünde eine
    GUTSCHRIFT mit POSITIVEM Betrag, die den Abschlag ein zweites Mal einfordert
    (Review-Befund). Und selbst eine korrekt gewählte Teilkorrektur ließe die
    Anrechnung unangetastet, während der Abschlag gebunden bliebe. Zulässig ist
    dort nur das **Storno** der Schlussrechnung (das dreht die Anrechnung
    vollständig mit um und gibt den Abschlag frei); danach lässt sich die
    Schlussrechnung neu und richtig stellen.

    **Und eine Vollgutschrift auf eine abrechnungsgebundene Rechnung ist ein
    verkappter Storno** — sie wird abgelehnt (`_vollgutschrift_sperre_pruefen`):
    Die Gutschrift lässt die Rechnung bestehen, die gebundenen Leistungen blieben
    für immer abgerechnet. Teilgutschriften bleiben zulässig; eine Kulanz heißt
    nicht, dass nicht gearbeitet wurde.

    ## Der Zustandsraum der Folgebelege, einmal vollständig

    Ein Kreditbeleg (STORNO/GUTSCHRIFT) verweist über `reference_invoice_id` auf
    genau eine veröffentlichte Rechnung. Auf einer Rechnung sind damit vier
    Übergänge denkbar — jeder ist entschieden, keiner offen:

    | Bestand → Neuer Beleg | Ergebnis | Wo entschieden |
    |---|---|---|
    | (nichts) → STORNO | **erlaubt**, kehrt alles um, löst die Bindungen | `create_cancellation` |
    | (nichts) → GUTSCHRIFT | **erlaubt**, solange sie den Betrag nicht ausschöpft | `_vollgutschrift_sperre_pruefen` |
    | STORNO → STORNO | **422** — „bereits storniert" | `create_cancellation` |
    | GUTSCHRIFT → STORNO | **422** — der Storno kehrte die VOLLEN Beträge um, die Gutschrift stünde daneben | `_storno_nach_gutschrift_pruefen` |
    | STORNO → GUTSCHRIFT | **422** — doppelte Erstattung (Review-Befund H-1) | `_gutschrift_nach_storno_pruefen` |
    | GUTSCHRIFT → GUTSCHRIFT | **erlaubt**, bis die Summe aller Gutschriften den Rechnungsbetrag erreicht (gebunden) bzw. übersteigt (ungebunden) | `_vollgutschrift_sperre_pruefen` |

    Der Kreditbeleg selbst ist in **beiden** Richtungen Endstation: Weder Storno
    noch Gutschrift lassen sich stornieren oder korrigieren (`_CREDIT_TYPES`-Tor
    hier und in `create_cancellation`). Und ein **Entwurf** eines Kreditbelegs
    zählt nirgends mit — er hat nichts erstattet; alle Prüfungen sehen nur
    `status = VEROEFFENTLICHT`. Damit ist der Raum lückenlos abgedeckt.
    """
    if not positions:
        raise ValueError(
            "Rechnungskorrektur erfordert mindestens eine zu korrigierende Position."
        )
    origin = (
        Invoice.objects.filter(id=invoice_id)
        .prefetch_related("lines", "parties")
        .first()
    )
    if origin is None:
        raise ValueError("Ursprungsrechnung nicht gefunden.")
    if origin.status != "VEROEFFENTLICHT":
        raise ValueError("Nur veröffentlichte Rechnungen können korrigiert werden (B-21).")
    if origin.invoice_type in _CREDIT_TYPES:
        raise ValueError("Eine Gutschrift/Storno kann nicht korrigiert werden.")
    _gutschrift_nach_storno_pruefen(origin)
    _anrechnung_sperre_pruefen(origin)
    if origin.invoice_type == FINAL_TYPE and InvoiceAdvance.objects.filter(
        final_invoice_id=origin.id
    ).exists():
        raise ValueError(
            "Eine Schlussrechnung mit angerechneten Abschlägen lässt sich nicht "
            "teilweise gutschreiben — die Anrechnung bliebe dabei stehen. "
            "Stornieren Sie die Schlussrechnung vollständig und stellen Sie sie neu."
        )
    valid_positions = {
        l.position_number
        for l in origin.lines.all()
        # Anrechnungspositionen sind NICHT korrigierbar: ihre Invertierung wäre
        # eine Forderung, keine Gutschrift.
        if l.line_type not in TEXT_TYPES and l.advance_invoice_id is None
    }
    unknown = set(positions) - valid_positions
    if unknown:
        raise ValueError(
            f"Unbekannte oder nicht korrigierbare Position(en): {sorted(unknown)}."
        )
    _vollgutschrift_sperre_pruefen(origin, positions)
    return _create_credit(
        actor_app_user_id, origin, invoice_type="GUTSCHRIFT", positions=set(positions)
    )


# ---------------------------------------------------------------------------
# Kalkulationsübersicht je Abschnitt (Rubrik)
# ---------------------------------------------------------------------------
# Sie wird bei jedem Aufruf aus den eingefrorenen Positionswerten gerechnet und
# NICHT gespeichert: eine gespeicherte Marge könnte von den Positionen abdriften,
# und der Beleg ist die Wahrheit. Der EK-Snapshot (`unit_cost`) ist optional —
# fehlt er, wird die Marge NICHT geraten, sondern die Position als „ohne EK"
# gezählt. Lieber eine ehrliche Lücke als eine erfundene Zahl.

def _leere_gruppe(nummer, titel, beschreibung=None):
    return {
        "rubrik": nummer,
        "title": titel,
        "description": beschreibung,
        "netto": Decimal("0.00"),          # summenwirksamer VK (netto)
        "ek": Decimal("0.00"),             # Einkaufswert, soweit bekannt
        "positionen": 0,
        "positionen_ohne_ek": 0,
        "alternativ_netto": Decimal("0.00"),  # nicht summenwirksam
        "bedarf_netto": Decimal("0.00"),      # nicht summenwirksam
        "arbeitszeit": Decimal("0.000"),      # Menge der ARBEITSZEIT-Positionen
    }


def _gruppe_abschliessen(g):
    """Ergänzt die abgeleiteten Kennzahlen. Marge nur, wenn sie belastbar ist."""
    ek_vollstaendig = g["positionen"] > 0 and g["positionen_ohne_ek"] == 0
    deckungsbeitrag = g["netto"] - g["ek"] if ek_vollstaendig else None
    marge = None
    if ek_vollstaendig and g["netto"] > 0:
        marge = _round2(deckungsbeitrag / g["netto"] * Decimal(100))
    return {
        **g,
        "deckungsbeitrag": deckungsbeitrag,
        "marge_prozent": marge,
        # Sagt dem UI ausdrücklich, dass die Marge nicht berechenbar war, statt
        # eine 0 zu zeigen, die wie „kein Gewinn" aussieht.
        "ek_vollstaendig": ek_vollstaendig,
    }


# Die Kalkulation liest je Position nur fünf Werte und ordnet sie über einen
# **Abschnitts-Schlüssel** einer Rubrik zu. Persistierte Positionen tragen den
# Schlüssel als `rubrik_id` (UUID), noch-nicht-gespeicherte Vorschauzeilen als
# 1-basierten Rubrikindex (`_rubrik`) — der Kern (`_kalkulation_core`) kennt beide
# nicht, er vergleicht nur Schlüssel gegen Schlüssel. So rechnet dieselbe Formel
# aus DB-Zeilen (GET) wie aus vorbereiteten Zeilen-Dicts (Editor-Vorschau), ohne
# Duplikat.


def _kalk_zeile_db(line):
    """Normalisiert eine persistierte Positionszeile für `_kalkulation_core`."""
    return {
        "line_type": line.line_type,
        "line_kind": line.line_kind,
        "net_amount": line.net_amount,
        "unit_cost": line.unit_cost,
        "quantity": line.quantity,
        "rubrik_key": line.rubrik_id,
    }


def _kalk_zeile_prepared(row):
    """Normalisiert eine vorbereitete (nicht persistierte) Zeile für die Vorschau.

    `_prepare_lines`/`_anrechnung_lines` liefern dieselben Feldnamen; der
    Abschnitts-Schlüssel ist hier der 1-basierte Rubrikindex `_rubrik`. Textzeilen
    und Anrechnungspositionen ohne EK tragen `unit_cost`/`quantity` nicht — daher
    `.get()`.
    """
    return {
        "line_type": row["line_type"],
        "line_kind": row["line_kind"],
        "net_amount": row.get("net_amount"),
        "unit_cost": row.get("unit_cost"),
        "quantity": row.get("quantity"),
        "rubrik_key": row.get("_rubrik"),
    }


def _kalkulation_core(zeilen, rubriken):
    """Rechnet die Kalkulationsübersicht aus normalisierten Zeilen und Rubriken.

    `zeilen`: Dicts mit line_type/line_kind/net_amount/unit_cost/quantity/rubrik_key.
    `rubriken`: Dicts mit key/position_number/title/description; `key` matcht den
    `rubrik_key` der Zeilen.
    """
    gruppen = {
        r["key"]: _leere_gruppe(r["position_number"], r["title"], r["description"])
        for r in rubriken
    }
    ohne = _leere_gruppe(None, "Ohne Abschnitt")

    for line in zeilen:
        if line["line_type"] in TEXT_TYPES:
            continue
        key = line["rubrik_key"]
        g = gruppen.get(key, ohne) if key else ohne
        netto = line["net_amount"] or Decimal("0.00")
        if line["line_kind"] == "ALTERNATIV":
            g["alternativ_netto"] += netto
            continue
        if line["line_kind"] == "BEDARF":
            g["bedarf_netto"] += netto
            continue
        g["netto"] += netto
        g["positionen"] += 1
        if line["unit_cost"] is None:
            g["positionen_ohne_ek"] += 1
        else:
            g["ek"] += _round2(line["unit_cost"] * (line["quantity"] or Decimal(0)))
        if line["line_type"] == "ARBEITSZEIT" and line["quantity"]:
            g["arbeitszeit"] += line["quantity"]

    abschnitte = [_gruppe_abschliessen(g) for g in sorted(
        gruppen.values(), key=lambda g: g["rubrik"]
    )]
    if ohne["positionen"] or ohne["alternativ_netto"] or ohne["bedarf_netto"]:
        abschnitte.append(_gruppe_abschliessen(ohne))

    gesamt = _leere_gruppe(None, "Gesamt")
    for g in (*gruppen.values(), ohne):
        for feld in ("netto", "ek", "alternativ_netto", "bedarf_netto", "arbeitszeit"):
            gesamt[feld] += g[feld]
        gesamt["positionen"] += g["positionen"]
        gesamt["positionen_ohne_ek"] += g["positionen_ohne_ek"]
    return {"abschnitte": abschnitte, "gesamt": _gruppe_abschliessen(gesamt)}


def _kalkulation(lines, rubriken):
    """Kalkulation aus persistierten Model-Instanzen (GET /…/kalkulation)."""
    return _kalkulation_core(
        [_kalk_zeile_db(l) for l in lines],
        [
            {
                "key": r.id,
                "position_number": r.position_number,
                "title": r.title,
                "description": r.description,
            }
            for r in rubriken
        ],
    )


def _kalkulation_aus_prepared(prepared, rubriken_norm):
    """Kalkulation aus vorbereiteten Zeilen-Dicts (Editor-Vorschau, nicht gespeichert).

    `rubriken_norm` stammt aus `_prepare_rubriken` (1-basiert, ohne UUID); der
    Schlüssel ist deshalb der Index — genau das, was `_prepare_lines` als `_rubrik`
    an die Zeilen schreibt.
    """
    return _kalkulation_core(
        [_kalk_zeile_prepared(r) for r in prepared],
        [
            {
                "key": idx,
                "position_number": r["position_number"],
                "title": r["title"],
                "description": r["description"],
            }
            for idx, r in enumerate(rubriken_norm, start=1)
        ],
    )


def quote_kalkulation(quote_id):
    """Interne Kalkulationsübersicht eines Angebots, je Abschnitt und gesamt."""
    quote = (
        Quote.objects.filter(id=quote_id)
        .prefetch_related("lines", "rubriken")
        .first()
    )
    if quote is None:
        raise ValueError("Angebot nicht gefunden.")
    return _kalkulation(list(quote.lines.all()), list(quote.rubriken.all()))


def invoice_kalkulation(invoice_id):
    """Interne Kalkulationsübersicht einer Rechnung, je Abschnitt und gesamt."""
    invoice = (
        Invoice.objects.filter(id=invoice_id)
        .prefetch_related("lines", "rubriken")
        .first()
    )
    if invoice is None:
        raise ValueError("Rechnung nicht gefunden.")
    return _kalkulation(list(invoice.lines.all()), list(invoice.rubriken.all()))


# ---------------------------------------------------------------------------
# Live-Vorschau des Editors (leseartig, persistiert NICHT)
# ---------------------------------------------------------------------------
# Der Angular-Editor rechnet bewusst kein Geld — Netto/Summen/Kalkulation kommen
# heute erst nach dem PUT zurück. Diese beiden Funktionen nehmen denselben Payload
# wie das PUT und führen DIESELBE Rechnung aus (`_prepare_lines`/`_totals`/
# `_kalkulation`), ohne zu schreiben: kein `business_transaction`, es entsteht
# keine Zeile. Damit bekommt der Editor Positionsnetto, Kopfsummen und
# Kalkulationsleiste sofort — verlässlich identisch zu dem, was nach dem Speichern
# gestellt würde.


class BelegNichtGefunden(LookupError):
    """Der vorzuschauende Beleg existiert nicht (→ 404, nicht 422).

    Getrennt vom `ValueError` der Payload-Prüfung: ein unbekannter Beleg ist ein
    fehlendes Ziel (404), ein kaputter Payload ein Eingabefehler (422). Muster wie
    `dossier.DossierNichtGefunden`.
    """


def _vorschau_zeile(row):
    """Die vier Rechenwerte einer Position für die Editor-Vorschau.

    In Payload-Reihenfolge; Textzeilen (TEXT/ZWISCHENSUMME) tragen keinen Betrag
    und liefern durchweg null.
    """
    return {
        "net_amount": row.get("net_amount"),
        "markup_percent": row.get("markup_percent"),
        "tax_rate_percent": row.get("tax_rate_percent"),
        "labour_net_amount": row.get("labour_net_amount"),
    }


def vorschau_quote(quote_id, *, lines, rubriken, mit_kalkulation):
    """Rechnet einen Angebots-Payload durch, ohne ihn zu speichern.

    Dieselbe Positions-/Summenrechnung wie `update_quote` (`_prepare_lines`), nur
    ohne Schreibvorgang. Der Beleg muss existieren (`BelegNichtGefunden` → 404),
    darf aber in JEDEM Status sein: eine Vorschau verändert nichts und ist damit
    auch am eingefrorenen Beleg harmlos (das Frontend ruft sie dort schlicht nicht).

    `mit_kalkulation=False` (Aufrufer ohne pricing/LESEN) blendet die Kalkulation
    aus (null) — dieselbe Sicht, die GET /quotes/{id}/kalkulation mit 403 verwehrt,
    nur ohne den Gesamtendpunkt zu sperren.
    """
    if not Quote.objects.filter(id=quote_id).exists():
        raise BelegNichtGefunden("Angebot nicht gefunden.")
    prepared, net_total, tax_total, gross_total = _prepare_lines(lines)
    rubriken_norm = _prepare_rubriken(rubriken, prepared)
    return {
        "lines": [_vorschau_zeile(r) for r in prepared],
        "net_total": net_total,
        "tax_total": tax_total,
        "gross_total": gross_total,
        "kalkulation": (
            _kalkulation_aus_prepared(prepared, rubriken_norm)
            if mit_kalkulation
            else None
        ),
    }


def vorschau_invoice(invoice_id, *, lines, rubriken, mit_kalkulation):
    """Rechnet einen Rechnungs-Payload durch, ohne ihn zu speichern.

    Spiegelt `update_invoice` — insbesondere den Schlussrechnungs-Sonderfall: eine
    SCHLUSSRECHNUNG mit angerechneten Abschlägen hängt Anrechnungspositionen aus
    der Verkettung (`invoice_advance`) an. Die Vorschau bildet das GLEICH ab, sonst
    wichen ihre Summen von den nach dem Speichern gestellten ab — genau der
    Vertrauensbruch, den der Endpunkt beheben soll. Die Anrechnung steht nicht im
    Editor-Payload; sie fließt in Kopfsummen UND Kalkulation ein, erscheint aber
    nicht in der zeilenweisen Payload-Antwort (die bleibt 1:1 zum Payload).
    """
    invoice = (
        Invoice.objects.filter(id=invoice_id).only("id", "invoice_type").first()
    )
    if invoice is None:
        raise BelegNichtGefunden("Rechnung nicht gefunden.")
    prepared, net_total, tax_total, gross_total = _prepare_lines(lines)
    rubriken_norm = _prepare_rubriken(rubriken, prepared)

    # Anrechnungspositionen der Schlussrechnung wie in update_invoice: aus der
    # bestehenden Verkettung erzeugt (nicht aus dem Payload) und ans Ende gehängt.
    kalk_lines = prepared
    if invoice.invoice_type == FINAL_TYPE:
        anrechnung_rows = [
            {
                "advance_invoice_id": a.advance_invoice_id,
                "tax_code_id": a.tax_code_id,
                "tax_rate_percent": a.tax_rate_percent,
                "net_amount": a.net_amount,
                "tax_amount": a.tax_amount,
                "gross_amount": a.gross_amount,
            }
            for a in InvoiceAdvance.objects.filter(final_invoice_id=invoice.id)
            .select_related("advance_invoice")
            .order_by("advance_invoice__invoice_date",
                      "advance_invoice__invoice_number", "tax_rate_percent")
        ]
        if anrechnung_rows:
            abschlaege = list(
                Invoice.objects.filter(
                    id__in={r["advance_invoice_id"] for r in anrechnung_rows}
                ).prefetch_related("lines")
            )
            anrechnung_lines = _anrechnung_lines(
                anrechnung_rows, abschlaege, len(prepared)
            )
            net_total, tax_total, gross_total = _totals(prepared + anrechnung_lines)
            _anrechnung_pruefen(gross_total)
            kalk_lines = prepared + anrechnung_lines

    return {
        "lines": [_vorschau_zeile(r) for r in prepared],
        "net_total": net_total,
        "tax_total": tax_total,
        "gross_total": gross_total,
        "kalkulation": (
            _kalkulation_aus_prepared(kalk_lines, rubriken_norm)
            if mit_kalkulation
            else None
        ),
    }
