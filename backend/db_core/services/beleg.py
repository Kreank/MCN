"""Beleg-Service: Angebote (invoicing.quote) anlegen.

Dieser Slice deckt die Anlage bis Status ENTWURF ab (Liste/Detail lesend).
Der Versand-Workflow (Nummernvergabe, Snapshot/Hash, PDF, Summen-Gate) ist
bewusst nicht Teil dieses Slices — er hat viele Vorbedingungen (B-30/GoBD).

Alle Writes über business_transaction. Positionsbeträge (net_amount) werden
kaufmännisch gerundet vorberechnet, exakt wie der DB-CHECK sie erzwingt
(round(quantity*unit_price*(1-discount/100), 2)). Kopf-Summen werden aus den
Positionen abgeleitet (im ENTWURF nur zur Anzeige; die Send-Gate-Prüfung greift
erst beim Versand).
"""
import uuid
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from db_core.db_context import business_transaction
from db_core.models import Quote, QuoteLine, TaxCode

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
# DB-Spaltenskalen (Migration 0018): quantity numeric(15,3), unit_price
# numeric(15,2), discount_percent numeric(7,4), net_amount numeric(15,2).
_Q_QTY = Decimal("0.001")
_Q_PRICE = Decimal("0.01")
_Q_DISCOUNT = Decimal("0.0001")
_CENT = Decimal("0.01")


def _dec(value):
    return Decimal(str(value))


def _round2(value):
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


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
    """Legt ein Angebot (Status ENTWURF) mit Positionen an.

    lines: Liste von dicts. Betragszeilen (line_type != TEXT/ZWISCHENSUMME)
    brauchen quantity, unit_price, tax_code (unit/discount_percent optional);
    tax_rate_percent und net_amount berechnet der Service. TEXT/ZWISCHENSUMME
    tragen nur description.
    """
    if not title or not title.strip():
        raise ValueError("title darf nicht leer sein.")
    lines = lines or []

    # Vorab validieren + Betragszeilen berechnen (vor der Transaktion, damit
    # Eingabefehler als klare ValueError statt als DB-Fehler enden).
    prepared = []
    for idx, line in enumerate(lines, start=1):
        lt = line.get("line_type")
        desc = (line.get("description") or "").strip()
        if lt not in LINE_TYPES:
            raise ValueError(f"Ungültiger line_type '{lt}'.")
        if not desc:
            raise ValueError(f"Position {idx}: description darf nicht leer sein.")
        row = {
            "position_number": idx,
            "line_type": lt,
            "description": desc,
        }
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
            # Auf die DB-Spaltenskalen quantisieren, BEVOR net berechnet wird —
            # sonst rundet Django die gespeicherten Werte anders und der
            # DB-CHECK (net = round(quantity*unit_price*(1-discount/100),2))
            # scheitert als IntegrityError (500) statt als klarer 422.
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
            # Wertebereiche vorab prüfen (DB-CHECKs sonst als 500).
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

    with business_transaction(actor_app_user_id):
        quote = Quote.objects.create(
            id=uuid.uuid4(),
            property_id=property_id,
            project_id=project_id,
            title=title.strip(),
            status="ENTWURF",
            quote_date=quote_date,
            valid_until_date=valid_until_date,
            version=1,
        )
        for row in prepared:
            QuoteLine.objects.create(id=uuid.uuid4(), quote_id=quote.id, **row)

        # Kopf-Summen aus den Positionen ableiten. Die Steuer wird je
        # Steuergruppe (tax_code, tax_rate_percent) gerundet — exakt wie die
        # DB-Kanonik invoicing.assert_quote_totals (0018), damit die Werte beim
        # späteren Versand nicht abgelehnt werden.
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
        quote.net_total = net_total
        quote.tax_total = tax_total
        quote.gross_total = net_total + tax_total
        quote.save(update_fields=["net_total", "tax_total", "gross_total"])
        quote.refresh_from_db()
    return quote
