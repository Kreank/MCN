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
from decimal import ROUND_HALF_UP, Decimal

from db_core.db_context import business_transaction
from db_core.gate_errors import as_business_error
from db_core.models import (
    Article,
    Assembly,
    BelegRubrik,
    Invoice,
    InvoiceLine,
    InvoiceParty,
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
# DB-Spaltenskalen (Migration 0018/0019): quantity numeric(15,3), unit_price
# numeric(15,2), discount_percent numeric(7,4), net_amount numeric(15,2).
_Q_QTY = Decimal("0.001")
_Q_PRICE = Decimal("0.01")
_Q_DISCOUNT = Decimal("0.0001")
_CENT = Decimal("0.01")


def _dec(value):
    return Decimal(str(value))


def _round2(value):
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


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

    # Kopf-Summen: Steuer je Steuergruppe (tax_code, tax_rate_percent) gerundet.
    # Alternativ-/Bedarfspositionen bleiben außen vor (Migration 0036).
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
    return prepared, net_total, tax_total, net_total + tax_total


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
    lines=None,
    rubriken=None,
):
    """Legt eine Rechnung/Gutschrift (Status ENTWURF) mit Positionen an.

    Gutschrift/Storno verlangen eine Referenzrechnung (DB-CHECK). Ein
    work_order-Bezug ist für die spätere Veröffentlichung erforderlich (B-08),
    beim Anlegen aber optional. Belegnummer und Veröffentlichung: siehe
    publish_invoice.
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
    ensure_exists(Property, property_id, "Liegenschaft")
    ensure_exists(Project, project_id, "Projekt")
    ensure_exists(WorkOrder, work_order_id, "Auftrag")
    ensure_exists(Invoice, reference_invoice_id, "Referenzrechnung")
    prepared, net_total, tax_total, gross_total = _prepare_lines(lines)
    rubriken_norm = _prepare_rubriken(rubriken, prepared)

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


def _snapshot_and_hash(header, lines, parties=None, rubriken=None):
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
        .prefetch_related("lines", "parties")
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

    header = {
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
        "currency": invoice.currency,
        "net_total": str(invoice.net_total),
        "tax_total": str(invoice.tax_total),
        "gross_total": str(invoice.gross_total),
    }
    parties = [
        {
            "party_id": str(p.party_id),
            "role": p.role,
            "is_primary": p.is_primary,
            "allocation_percent": (
                str(p.allocation_percent) if p.allocation_percent is not None else None
            ),
        }
        for p in sorted(invoice.parties.all(), key=lambda p: (p.role, str(p.party_id)))
    ]
    lines = sorted(invoice.lines.all(), key=lambda l: l.position_number)
    snapshot, digest = _snapshot_and_hash(
        header, lines, parties, rubriken=list(invoice.rubriken.all())
    )

    with as_business_error():
        with business_transaction(actor_app_user_id):
            updated = Invoice.objects.filter(id=invoice_id, status="ENTWURF").update(
                billing_snapshot=snapshot,
                content_hash=digest,
                status="VEROEFFENTLICHT",
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
    return prepared, net_total, tax_total, net_total + tax_total


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
    if Invoice.objects.filter(
        reference_invoice_id=origin.id, invoice_type="STORNO", status="VEROEFFENTLICHT"
    ).exists():
        raise ValueError("Diese Rechnung wurde bereits storniert.")
    return _create_credit(actor_app_user_id, origin, invoice_type="STORNO", positions=None)


def create_correction(actor_app_user_id, *, invoice_id, positions):
    """Erzeugt eine Rechnungskorrektur (GUTSCHRIFT) über die angegebenen
    Positionen (position_number) einer veröffentlichten Rechnung."""
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
    valid_positions = {
        l.position_number for l in origin.lines.all() if l.line_type not in TEXT_TYPES
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
