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

from django.utils import timezone as dj_timezone

from db_core.db_context import business_transaction
from db_core.gate_errors import as_business_error
from db_core.models import (
    Article,
    Assembly,
    BelegRubrik,
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
_CREDIT_TYPES = ("GUTSCHRIFT", "STORNO")
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
            )
            row.update(_kalkulation_pruefen(idx, line, unit_price))
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


def create_quote(
    actor_app_user_id,
    *,
    property_id,
    title,
    project_id=None,
    quote_date=None,
    valid_until_date=None,
    lines=None,
    rubriken=None,
):
    """Legt ein Angebot (Status ENTWURF) mit Positionen und Abschnitten an."""
    if not title or not title.strip():
        raise ValueError("title darf nicht leer sein.")
    ensure_exists(Property, property_id, "Liegenschaft")
    ensure_exists(Project, project_id, "Projekt")
    prepared, net_total, tax_total, gross_total = _prepare_lines(lines)
    rubriken_norm = _prepare_rubriken(rubriken, prepared)

    with business_transaction(actor_app_user_id):
        quote = Quote.objects.create(
            id=uuid.uuid4(),
            property_id=property_id,
            project_id=project_id,
            title=title.strip(),
            status="ENTWURF",
            quote_date=quote_date,
            valid_until_date=valid_until_date,
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


def update_quote(
    actor_app_user_id,
    *,
    quote_id,
    title=None,
    quote_date=...,
    valid_until_date=...,
    lines=None,
    rubriken=None,
):
    """Ändert ein Angebot, solange es nicht versendet ist.

    Positionen und Abschnitte werden **vollständig ersetzt**, wenn `lines`
    übergeben wird — der Editor schickt immer den ganzen Beleg. Ein Teil-Update
    einzelner Positionen wäre bei umsortierten Positionsnummern nicht eindeutig.

    `quote_date`/`valid_until_date` nutzen den Sentinel `...`, damit ein bewusstes
    Leeren (None) von „nicht ändern" unterscheidbar bleibt.
    """
    quote = Quote.objects.filter(id=quote_id).first()
    if quote is None:
        raise ValueError("Angebot nicht gefunden.")
    if quote.status not in QUOTE_EDITIERBAR:
        raise ValueError(
            f"Angebot im Status {quote.status} ist unveränderlich (versendet)."
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

    prepared = rubriken_norm = None
    if lines is not None:
        prepared, net_total, tax_total, gross_total = _prepare_lines(lines)
        rubriken_norm = _prepare_rubriken(rubriken, prepared)
        kopf.update(
            net_total=net_total, tax_total=tax_total, gross_total=gross_total
        )

    with as_business_error():
        with business_transaction(actor_app_user_id):
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


def _stornierte_belege():
    """IDs aller Belege, zu denen ein veröffentlichter STORNO existiert."""
    return set(
        Invoice.objects.filter(
            invoice_type="STORNO", status="VEROEFFENTLICHT"
        ).values_list("reference_invoice_id", flat=True)
    )


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
    """
    namen = {i.id: i for i in abschlaege}
    lines = []
    pos = start_position
    for row in rows:
        inv = namen[row["advance_invoice_id"]]
        pos += 1
        netto = row["net_amount"]
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
    """
    for row in rows:
        InvoiceAdvance.objects.create(
            id=uuid.uuid4(), final_invoice_id=invoice.id, **row
        )
    lines = _anrechnung_lines(rows, abschlaege, len(prepared_user_lines))
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
    reference_invoice_id=None,
    invoice_date=None,
    due_date=None,
    payment_term_days=None,
    discount_percent=None,
    discount_days=None,
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
    ensure_exists(WorkOrder, work_order_id, "Auftrag")
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
            invoice_type=invoice_type,
            reference_invoice_id=reference_invoice_id,
            status="ENTWURF",
            invoice_date=invoice_date,
            due_date=due_date,
            **bedingungen,
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
    """
    invoice = (
        Invoice.objects.filter(id=invoice_id).prefetch_related("lines").first()
    )
    if invoice is None:
        raise ValueError("Rechnung nicht gefunden.")
    if invoice.invoice_type != FINAL_TYPE:
        raise ValueError("Abschlagsrechnungen kann nur eine Schlussrechnung anrechnen.")
    if invoice.status not in INVOICE_EDITIERBAR:
        raise ValueError(
            f"Rechnung im Status {invoice.status} ist unveränderlich (veröffentlicht)."
        )
    abschlaege = _abschlaege_laden(
        invoice.work_order_id, advance_invoice_ids, final_invoice_id=invoice.id
    )
    rows = _anrechnung_rows(abschlaege)
    # Leistungspositionen = alles, was keine Anrechnung ist. Sie behalten ihre
    # Nummern; die Anrechnung hängt sich hinten an.
    user_lines = [
        {
            "line_type": l.line_type,
            "line_kind": l.line_kind,
            "net_amount": l.net_amount,
            "tax_code_id": l.tax_code_id,
            "tax_rate_percent": l.tax_rate_percent,
        }
        for l in sorted(invoice.lines.all(), key=lambda l: l.position_number)
        if l.advance_invoice_id is None
    ]
    with as_business_error():
        with business_transaction(actor_app_user_id):
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
SNAPSHOT_VERSION = 2

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

    `localdate()` und NICHT `utcnow().date()`: `party_address.valid_from` wird von
    `identity.add_address` mit dem LOKALEN Datum gesetzt. Zwischen 00:00 und 02:00
    MESZ liegt das UTC-Datum einen Tag zurück — eine am selben lokalen Tag erfasste
    Adresse fiele dann still aus dem Gültigkeitsfenster.

    (Die Ableitung von Beleg- und Fälligkeitsdatum in `publish_invoice` bleibt
    bewusst bei UTC: sie muss deckungsgleich mit dem DB-Trigger bleiben, der
    `(now() AT TIME ZONE 'UTC')::date` setzt.)
    """
    stichtag = on_date or dj_timezone.localdate()
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


def create_cancellation(actor_app_user_id, *, invoice_id):
    """Storniert eine veröffentlichte Rechnung durch einen Stornobeleg (STORNO)
    mit vollständig invertierten Positionen."""
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


def _kalkulation(lines, rubriken):
    gruppen = {r.id: _leere_gruppe(r.position_number, r.title, r.description) for r in rubriken}
    ohne = _leere_gruppe(None, "Ohne Abschnitt")

    for line in lines:
        if line.line_type in TEXT_TYPES:
            continue
        g = gruppen.get(line.rubrik_id, ohne) if line.rubrik_id else ohne
        netto = line.net_amount or Decimal("0.00")
        if line.line_kind == "ALTERNATIV":
            g["alternativ_netto"] += netto
            continue
        if line.line_kind == "BEDARF":
            g["bedarf_netto"] += netto
            continue
        g["netto"] += netto
        g["positionen"] += 1
        if line.unit_cost is None:
            g["positionen_ohne_ek"] += 1
        else:
            g["ek"] += _round2(line.unit_cost * (line.quantity or Decimal(0)))
        if line.line_type == "ARBEITSZEIT" and line.quantity:
            g["arbeitszeit"] += line.quantity

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
