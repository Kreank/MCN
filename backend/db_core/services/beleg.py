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
    Invoice,
    InvoiceLine,
    InvoiceParty,
    Quote,
    QuoteLine,
    TaxCode,
)

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


def _prepare_lines(lines):
    """Validiert und berechnet Positionen; gibt (prepared, net, tax, gross).

    Wird vor der Transaktion aufgerufen, damit Eingabefehler als klare
    ValueError (→422) statt als DB-IntegrityError (→500) enden.
    """
    prepared = []
    for idx, line in enumerate(lines or [], start=1):
        lt = line.get("line_type")
        desc = (line.get("description") or "").strip()
        if lt not in LINE_TYPES:
            raise ValueError(f"Ungültiger line_type '{lt}'.")
        if not desc:
            raise ValueError(f"Position {idx}: description darf nicht leer sein.")
        row = {"position_number": idx, "line_type": lt, "description": desc}
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
        prepared.append(row)

    # Kopf-Summen: Steuer je Steuergruppe (tax_code, tax_rate_percent) gerundet.
    net_total = Decimal("0.00")
    group_net = defaultdict(lambda: Decimal("0.00"))
    for row in prepared:
        if row["line_type"] in TEXT_TYPES:
            continue
        net_total += row["net_amount"]
        group_net[(row["tax_code_id"], row["tax_rate_percent"])] += row["net_amount"]
    tax_total = Decimal("0.00")
    for (_code, rate), net in group_net.items():
        tax_total += _round2(net * rate / Decimal(100))
    return prepared, net_total, tax_total, net_total + tax_total


def create_quote(
    actor_app_user_id,
    *,
    property_id,
    title,
    project_id=None,
    quote_date=None,
    valid_until_date=None,
    lines=None,
):
    """Legt ein Angebot (Status ENTWURF) mit Positionen an."""
    if not title or not title.strip():
        raise ValueError("title darf nicht leer sein.")
    prepared, net_total, tax_total, gross_total = _prepare_lines(lines)

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
        for row in prepared:
            QuoteLine.objects.create(id=uuid.uuid4(), quote_id=quote.id, **row)
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
    if invoice_type in _CREDIT_TYPES and reference_invoice_id is None:
        raise ValueError(
            f"{invoice_type} erfordert eine reference_invoice_id (Ursprungsbeleg)."
        )
    prepared, net_total, tax_total, gross_total = _prepare_lines(lines)

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
        for row in prepared:
            InvoiceLine.objects.create(id=uuid.uuid4(), invoice_id=invoice.id, **row)
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


def _line_snapshot(line):
    def s(v):
        return None if v is None else str(v)

    return {
        "position_number": line.position_number,
        "line_type": line.line_type,
        "description": line.description,
        "quantity": s(line.quantity),
        "unit": line.unit,
        "unit_price": s(line.unit_price),
        "discount_percent": s(line.discount_percent),
        "tax_code": line.tax_code_id,
        "tax_rate_percent": s(line.tax_rate_percent),
        "net_amount": s(line.net_amount),
    }


def _snapshot_and_hash(header, lines, parties=None):
    """Baut einen unveränderlichen Beleg-Snapshot (dict) und dessen SHA-256-Hash.

    Der Hash läuft über die kanonische JSON-Serialisierung (sortierte Schlüssel,
    keine Zeitstempel) — er identifiziert den Inhalt reproduzierbar (B-21/B-30).
    """
    snapshot = {
        "header": header,
        "lines": [_line_snapshot(l) for l in lines],
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
    snapshot, digest = _snapshot_and_hash(header, lines, parties)

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
    snapshot, digest = _snapshot_and_hash(header, lines)

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
        if row["line_type"] in TEXT_TYPES:
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
    if not any(r["line_type"] not in TEXT_TYPES for r in prepared):
        raise ValueError("Der Korrekturbeleg enthält keine Betragsposition.")
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
