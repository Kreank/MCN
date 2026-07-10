"""Belegerfassungs-Service: Buchungskonten, Kostenstellen und Eingangsbelege.

Fachschema `accounting` (Migrationen 0030/0031). Alle Writes laufen über
business_transaction; die fachlichen DB-Tore (Statusautomat, Freigabe-Kontierung,
Positions-/Kopf-Immutabilität, Lieferant nicht MERGED) prüft die DB als Trigger
und wird über as_business_error in 422 übersetzt.

Beträge werden SERVERSEITIG aus den Positionen gerechnet (der Client liefert keine
Summen) — Muster beleg.py::_prepare_lines: Decimal, ROUND_HALF_UP, auf die
DB-Spaltenskalen quantisiert BEVOR gerechnet wird, Steuer je Steuergruppe
gerundet. Eingangsbelege haben keine Rabatte und keine Text-/Zwischensummenzeilen;
jede Position ist eine Betragsposition mit Steuersatz aus der bestehenden
invoicing.tax_code-Codeliste.
"""
import re
import uuid
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from db_core.db_context import business_transaction
from db_core.gate_errors import as_business_error
from db_core.models import (
    CostCenter,
    LedgerAccount,
    Receipt,
    ReceiptLine,
    TaxCode,
)
from db_core.services._validation import ensure_exists, ensure_party_usable

# Codelisten (spiegeln die DB-CHECKs → klare 422 statt 500)
ACCOUNT_TYPES = ("AKTIV", "PASSIV", "AUFWAND", "ERTRAG")
CHART_OF_ACCOUNTS = ("SKR03", "SKR04")
RECEIPT_STATUSES = ("ERFASST", "GEPRUEFT", "FREIGEGEBEN", "GEBUCHT", "ABGELEHNT")
# Status, in denen der Beleg (Kopf + Positionen) noch bearbeitbar ist.
EDITABLE_STATUSES = ("ERFASST", "GEPRUEFT")

# DB-Spaltenskalen (Migration 0031): quantity numeric(15,3), unit_price/net
# numeric(15,2).
_Q_QTY = Decimal("0.001")
_Q_PRICE = Decimal("0.01")
_CENT = Decimal("0.01")


def _dec(value):
    return Decimal(str(value))


def _round2(value):
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


# ---------------------------------------------------------------------------
# Buchungskonten (Stammdaten)
# ---------------------------------------------------------------------------

def list_ledger_accounts(*, include_inactive=True):
    qs = LedgerAccount.objects.all()
    if not include_inactive:
        qs = qs.filter(active=True)
    return qs.order_by("account_number", "id")


def create_ledger_account(
    actor_app_user_id, *, account_number, label, account_type,
    chart_of_accounts=None, notes=None,
):
    account_number = _clean(account_number)
    label = _clean(label)
    if not account_number:
        raise ValueError("Kontonummer ist erforderlich.")
    if not label:
        raise ValueError("Bezeichnung ist erforderlich.")
    if account_type not in ACCOUNT_TYPES:
        raise ValueError(
            f"Ungültige Kontoart '{account_type}'. Erlaubt: {', '.join(ACCOUNT_TYPES)}."
        )
    chart = _clean(chart_of_accounts)
    if chart is not None and chart not in CHART_OF_ACCOUNTS:
        raise ValueError(
            f"Ungültiger Kontenrahmen '{chart}'. Erlaubt: {', '.join(CHART_OF_ACCOUNTS)}."
        )
    if LedgerAccount.objects.filter(account_number=account_number).exists():
        raise ValueError(f"Kontonummer '{account_number}' ist bereits vergeben.")
    with business_transaction(actor_app_user_id):
        account = LedgerAccount.objects.create(
            id=uuid.uuid4(),
            account_number=account_number,
            label=label,
            account_type=account_type,
            chart_of_accounts=chart,
            notes=_clean(notes),
            created_by_id=actor_app_user_id,
        )
    return account


def update_ledger_account(actor_app_user_id, *, ledger_account_id, **fields):
    account = LedgerAccount.objects.filter(id=ledger_account_id).first()
    if account is None:
        raise ValueError("Buchungskonto nicht gefunden.")
    allowed = ("account_number", "label", "account_type", "chart_of_accounts",
               "active", "notes")
    unknown = set(fields) - set(allowed)
    if unknown:
        raise ValueError(f"Unbekannte Felder: {', '.join(sorted(unknown))}")
    changed = []
    for key in allowed:
        if key not in fields:
            continue
        val = fields[key]
        if key == "active":
            val = bool(val)
        elif key == "account_type":
            if val not in ACCOUNT_TYPES:
                raise ValueError(
                    f"Ungültige Kontoart '{val}'. Erlaubt: {', '.join(ACCOUNT_TYPES)}."
                )
        elif key == "chart_of_accounts":
            val = _clean(val)
            if val is not None and val not in CHART_OF_ACCOUNTS:
                raise ValueError(
                    f"Ungültiger Kontenrahmen '{val}'. Erlaubt: {', '.join(CHART_OF_ACCOUNTS)}."
                )
        elif key == "account_number":
            val = _clean(val)
            if not val:
                raise ValueError("Kontonummer darf nicht leer sein.")
            if LedgerAccount.objects.filter(account_number=val).exclude(
                id=ledger_account_id
            ).exists():
                raise ValueError(f"Kontonummer '{val}' ist bereits vergeben.")
        elif key == "label":
            val = _clean(val)
            if not val:
                raise ValueError("Bezeichnung darf nicht leer sein.")
        else:  # notes
            val = _clean(val)
        setattr(account, key, val)
        changed.append(key)
    if changed:
        with business_transaction(actor_app_user_id):
            account.save(update_fields=changed + ["updated_at"])
        account.refresh_from_db()
    return account


# ---------------------------------------------------------------------------
# Kostenstellen (Stammdaten)
# ---------------------------------------------------------------------------

def list_cost_centers(*, include_inactive=True):
    qs = CostCenter.objects.all()
    if not include_inactive:
        qs = qs.filter(active=True)
    return qs.order_by("code", "id")


def create_cost_center(actor_app_user_id, *, code, label, notes=None):
    code = _clean(code)
    label = _clean(label)
    if not code:
        raise ValueError("Kostenstellen-Nummer ist erforderlich.")
    if not label:
        raise ValueError("Bezeichnung ist erforderlich.")
    if CostCenter.objects.filter(code=code).exists():
        raise ValueError(f"Kostenstelle '{code}' ist bereits vergeben.")
    with business_transaction(actor_app_user_id):
        cc = CostCenter.objects.create(
            id=uuid.uuid4(), code=code, label=label, notes=_clean(notes),
            created_by_id=actor_app_user_id,
        )
    return cc


def update_cost_center(actor_app_user_id, *, cost_center_id, **fields):
    cc = CostCenter.objects.filter(id=cost_center_id).first()
    if cc is None:
        raise ValueError("Kostenstelle nicht gefunden.")
    allowed = ("code", "label", "active", "notes")
    unknown = set(fields) - set(allowed)
    if unknown:
        raise ValueError(f"Unbekannte Felder: {', '.join(sorted(unknown))}")
    changed = []
    for key in allowed:
        if key not in fields:
            continue
        val = fields[key]
        if key == "active":
            val = bool(val)
        elif key == "code":
            val = _clean(val)
            if not val:
                raise ValueError("Kostenstellen-Nummer darf nicht leer sein.")
            if CostCenter.objects.filter(code=val).exclude(id=cost_center_id).exists():
                raise ValueError(f"Kostenstelle '{val}' ist bereits vergeben.")
        elif key == "label":
            val = _clean(val)
            if not val:
                raise ValueError("Bezeichnung darf nicht leer sein.")
        else:  # notes
            val = _clean(val)
        setattr(cc, key, val)
        changed.append(key)
    if changed:
        with business_transaction(actor_app_user_id):
            cc.save(update_fields=changed + ["updated_at"])
        cc.refresh_from_db()
    return cc


# ---------------------------------------------------------------------------
# Eingangsbeleg
# ---------------------------------------------------------------------------

def _prepare_lines(lines):
    """Validiert und berechnet Positionen; gibt (prepared, net, tax, gross).

    Vor der Transaktion aufgerufen, damit Eingabefehler klare ValueError (→422)
    statt DB-IntegrityError (→500) werden. Prüft tax_code sowie — falls angegeben
    — Buchungskonto/Kostenstelle (Existenz UND aktiv).
    """
    if not lines:
        raise ValueError("Ein Eingangsbeleg braucht mindestens eine Position.")
    # Konten/Kostenstellen in EINER Query je Menge prüfen (kein N+1).
    ledger_ids = {
        line.get("ledger_account_id") for line in lines
        if line.get("ledger_account_id")
    }
    cc_ids = {
        line.get("cost_center_id") for line in lines if line.get("cost_center_id")
    }
    active_ledgers = set(
        LedgerAccount.objects.filter(id__in=ledger_ids, active=True)
        .values_list("id", flat=True)
    )
    active_ccs = set(
        CostCenter.objects.filter(id__in=cc_ids, active=True)
        .values_list("id", flat=True)
    )

    prepared = []
    for idx, line in enumerate(lines, start=1):
        desc = (line.get("description") or "").strip()
        if not desc:
            raise ValueError(f"Position {idx}: Bezeichnung darf nicht leer sein.")
        if line.get("quantity") is None or line.get("unit_price") is None:
            raise ValueError(f"Position {idx}: Menge und Einzelpreis sind Pflicht.")
        tax_code = line.get("tax_code")
        tc = TaxCode.objects.filter(code=tax_code).first() if tax_code else None
        if tc is None:
            raise ValueError(
                f"Position {idx}: gültiger Steuersatz (tax_code) Pflicht (z. B. DE_19)."
            )
        quantity = _dec(line["quantity"]).quantize(_Q_QTY, rounding=ROUND_HALF_UP)
        unit_price = _dec(line["unit_price"]).quantize(_Q_PRICE, rounding=ROUND_HALF_UP)
        if quantity <= 0:
            raise ValueError(f"Position {idx}: Menge muss > 0 sein.")
        if unit_price < 0:
            raise ValueError(f"Position {idx}: Einzelpreis darf nicht negativ sein.")

        ledger_id = line.get("ledger_account_id")
        if ledger_id and ledger_id not in active_ledgers:
            raise ValueError(
                f"Position {idx}: Buchungskonto {ledger_id} existiert nicht oder ist archiviert."
            )
        cc_id = line.get("cost_center_id")
        if cc_id and cc_id not in active_ccs:
            raise ValueError(
                f"Position {idx}: Kostenstelle {cc_id} existiert nicht oder ist archiviert."
            )

        net = _round2(unit_price * quantity)
        prepared.append({
            "position_number": idx,
            "description": desc,
            "quantity": quantity,
            "unit": _clean(line.get("unit")),
            "unit_price": unit_price,
            "tax_code_id": tc.code,
            "tax_rate_percent": tc.rate_percent,
            "net_amount": net,
            "ledger_account_id": ledger_id,
            "cost_center_id": cc_id,
        })

    net_total = Decimal("0.00")
    group_net = defaultdict(lambda: Decimal("0.00"))
    for row in prepared:
        net_total += row["net_amount"]
        group_net[(row["tax_code_id"], row["tax_rate_percent"])] += row["net_amount"]
    tax_total = Decimal("0.00")
    for (_code, rate), net in group_net.items():
        tax_total += _round2(net * rate / Decimal(100))
    return prepared, net_total, tax_total, net_total + tax_total


def _as_date(value):
    """Datum aus `date` oder ISO-String; alles andere unverändert zurück."""
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise ValueError(f"Ungültiges Datum: '{value}' (erwartet JJJJ-MM-TT).")
    return value


def _validate_header(*, receipt_date, due_date, currency):
    """Prüft die Kopffelder gegen die harten DB-CHECKs, BEVOR die DB sie sieht.

    `receipt_due_after_receipt_date` und der Währungs-CHECK sind reine
    CHECK-Constraints (SQLSTATE 23514), keine fachlichen Tore (P0001) — sie
    kämen als IntegrityError durch und landeten als 500 statt als 422 beim
    Aufrufer.
    """
    beleg, faellig = _as_date(receipt_date), _as_date(due_date)
    if faellig is not None and beleg is not None and faellig < beleg:
        raise ValueError(
            "Das Fälligkeitsdatum darf nicht vor dem Belegdatum liegen."
        )
    if currency is not None and not re.fullmatch(r"[A-Z]{3}", currency or ""):
        raise ValueError(
            f"Ungültige Währung '{currency}': erwartet ein ISO-Kürzel aus drei "
            "Großbuchstaben (z. B. EUR)."
        )


def create_receipt(
    actor_app_user_id, *, supplier_party_id, receipt_date, lines,
    received_date=None, due_date=None, supplier_invoice_number=None,
    currency="EUR", notes=None,
):
    """Legt einen Eingangsbeleg (Status ERFASST) mit Positionen an.

    Der Lieferant ist Pflicht (identity.party, nicht MERGED). Beträge werden aus
    den Positionen gerechnet. Kontierung (Buchungskonto/Kostenstelle) ist beim
    Anlegen optional, wird aber zur Freigabe erzwungen (DB-Tor).
    """
    if not receipt_date:
        raise ValueError("Belegdatum ist erforderlich.")
    _validate_header(receipt_date=receipt_date, due_date=due_date, currency=currency)
    ensure_party_usable(supplier_party_id, "Lieferant")
    prepared, net_total, tax_total, gross_total = _prepare_lines(lines)

    with as_business_error():
        with business_transaction(actor_app_user_id):
            receipt = Receipt.objects.create(
                id=uuid.uuid4(),
                supplier_party_id=supplier_party_id,
                supplier_invoice_number=_clean(supplier_invoice_number),
                receipt_date=receipt_date,
                received_date=received_date or receipt_date,
                due_date=due_date,
                currency=currency,
                status="ERFASST",
                net_total=net_total,
                tax_total=tax_total,
                gross_total=gross_total,
                notes=_clean(notes),
                created_by_id=actor_app_user_id,
                version=1,
            )
            for row in prepared:
                ReceiptLine.objects.create(id=uuid.uuid4(), receipt_id=receipt.id, **row)
            receipt.refresh_from_db()
    return receipt


def update_receipt(
    actor_app_user_id, *, receipt_id, lines=None, supplier_party_id=None,
    receipt_date=None, received_date=None, due_date=..., supplier_invoice_number=...,
    currency=None, notes=...,
):
    """Aktualisiert einen Eingangsbeleg im Status ERFASST/GEPRUEFT.

    Positionen werden — wenn übergeben — vollständig ersetzt (im Entwurf zulässig,
    DB-Positionsschutz greift ab FREIGEGEBEN). `due_date`, `supplier_invoice_number`
    und `notes` nutzen den Sentinel `...`, damit ein bewusstes Leeren (None) von
    „nicht ändern" unterschieden wird.
    """
    receipt = Receipt.objects.filter(id=receipt_id).first()
    if receipt is None:
        raise ValueError("Eingangsbeleg nicht gefunden.")
    if receipt.status not in EDITABLE_STATUSES:
        raise ValueError(
            f"Eingangsbeleg im Status {receipt.status} ist nicht mehr bearbeitbar "
            f"(nur {', '.join(EDITABLE_STATUSES)})."
        )
    if supplier_party_id is not None:
        ensure_party_usable(supplier_party_id, "Lieferant")

    prepared = None
    net_total = tax_total = gross_total = None
    if lines is not None:
        prepared, net_total, tax_total, gross_total = _prepare_lines(lines)

    header = {}
    if supplier_party_id is not None:
        header["supplier_party_id"] = supplier_party_id
    if receipt_date is not None:
        header["receipt_date"] = receipt_date
    if received_date is not None:
        header["received_date"] = received_date
    if currency is not None:
        header["currency"] = currency
    if due_date is not ...:
        header["due_date"] = due_date
    if supplier_invoice_number is not ...:
        header["supplier_invoice_number"] = _clean(supplier_invoice_number)
    if notes is not ...:
        header["notes"] = _clean(notes)
    if prepared is not None:
        header.update(
            net_total=net_total, tax_total=tax_total, gross_total=gross_total
        )

    # Gegen die EFFEKTIVEN Werte prüfen: ein neues Fälligkeitsdatum muss auch zu
    # einem unveränderten Belegdatum passen (und umgekehrt).
    _validate_header(
        receipt_date=header.get("receipt_date", receipt.receipt_date),
        due_date=header["due_date"] if "due_date" in header else receipt.due_date,
        currency=header.get("currency", receipt.currency),
    )

    with as_business_error():
        with business_transaction(actor_app_user_id):
            if header:
                for key, val in header.items():
                    setattr(receipt, key, val)
                receipt.save(update_fields=list(header) + ["updated_at"])
            if prepared is not None:
                ReceiptLine.objects.filter(receipt_id=receipt.id).delete()
                for row in prepared:
                    ReceiptLine.objects.create(
                        id=uuid.uuid4(), receipt_id=receipt.id, **row
                    )
            receipt.refresh_from_db()
    return receipt


def advance_status(actor_app_user_id, *, receipt_id, to_status, reason=None):
    """Führt einen Statuswechsel des Eingangsbelegs aus (DB-Statusautomat).

    ERFASST→GEPRUEFT→FREIGEGEBEN→GEBUCHT (+ABGELEHNT, +Rücksetzungen). Die DB
    prüft die Zulässigkeit und das Freigabe-Tor (Kontierung); Verstöße → 422.
    ABGELEHNT ist begründungspflichtig (rejection_reason).
    """
    if to_status not in RECEIPT_STATUSES:
        raise ValueError(
            f"Ungültiger Zielstatus '{to_status}'. Erlaubt: {', '.join(RECEIPT_STATUSES)}."
        )
    receipt = Receipt.objects.filter(id=receipt_id).first()
    if receipt is None:
        raise ValueError("Eingangsbeleg nicht gefunden.")
    if receipt.status == to_status:
        raise ValueError(f"Eingangsbeleg ist bereits im Status {to_status}.")

    reason = _clean(reason)
    fields = {"status": to_status}
    if to_status == "ABGELEHNT":
        if not reason:
            raise ValueError("Eine Ablehnung ist begründungspflichtig.")
        fields["rejection_reason"] = reason

    with as_business_error():
        with business_transaction(actor_app_user_id, status_reason=reason):
            updated = Receipt.objects.filter(
                id=receipt_id, status=receipt.status
            ).update(**fields)
            if not updated:
                raise ValueError(
                    "Der Beleg wurde zwischenzeitlich geändert; bitte neu laden."
                )
    receipt.refresh_from_db()
    return receipt
