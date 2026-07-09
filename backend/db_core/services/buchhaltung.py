"""Buchhaltungs-Service: Zahlungen erfassen/stornieren und Mahnstufen erzeugen.

Baut auf den veröffentlichten Rechnungen aus dem Beleg-Slice auf. Wie die übrigen
Services laufen alle Writes über business_transaction; die fachlichen DB-Tore
(Zahlung nur auf veröffentlichte Rechnung B-23; Mahnung nur auf veröffentlichte,
fällige Rechnung mit lückenlos aufsteigender Stufe B-22) prüft die DB als Trigger
und wird über as_business_error in 422 übersetzt.

**Zahlungs-Vorzeichenkonvention (App-seitig, die DB erzwingt kein Vorzeichen):**
Beträge werden stets als positiver Betrag erfasst; das Vorzeichen für den offenen
Posten ergibt sich aus dem payment_type — Geldeingänge reduzieren den offenen
Betrag, Rückerstattungen/Storno-Buchungen erhöhen ihn wieder. `PAYMENT_SIGN`
ist die eine Quelle dieser Konvention (auch die API-Ableitung nutzt sie).

**Storno einer Zahlung:** `invoicing.payment` ist append-only — eine Zahlung wird
nie gelöscht, sondern durch eine Gegenbuchung (payment_type='STORNO_BUCHUNG')
neutralisiert. Ein FK auf die Ursprungszahlung fehlt im Schema; die Verknüpfung
wird über external_reference ('STORNO:<id>') hergestellt.
"""
import uuid
from datetime import date

from db_core.db_context import business_transaction
from db_core.gate_errors import as_business_error
from db_core.models import DunningNotice, Invoice, Payment
from db_core.services._validation import ensure_exists

# Beitrag je payment_type zum bezahlten Betrag (+1 = Geldeingang reduziert den
# offenen Posten, -1 = Rückfluss/Storno erhöht ihn wieder). Einzige Quelle der
# Vorzeichenkonvention — die API-Ableitung importiert diese Tabelle.
PAYMENT_SIGN = {
    "ZAHLUNG": 1,
    "TEILZAHLUNG": 1,
    "UEBERZAHLUNG": 1,
    "RUECKERSTATTUNG": -1,
    "STORNO_BUCHUNG": -1,
}
PAYMENT_TYPES = tuple(PAYMENT_SIGN)


def record_payment(
    actor_app_user_id,
    *,
    invoice_id,
    amount,
    paid_at,
    payment_type="ZAHLUNG",
    import_source="MANUAL",
    external_reference=None,
    currency="EUR",
):
    """Erfasst eine (Teil-)Zahlung zu einer veröffentlichten Rechnung.

    amount ist immer ein positiver Betrag (das Vorzeichen ergibt sich aus dem
    payment_type, siehe PAYMENT_SIGN). Die DB verlangt eine veröffentlichte
    Rechnung (B-23) und Idempotenz über UNIQUE(import_source, external_reference);
    fehlt external_reference, wird bei manueller Erfassung eine synthetische
    Referenz vergeben.
    """
    if payment_type not in PAYMENT_TYPES:
        raise ValueError(
            f"Ungültiger payment_type '{payment_type}'. "
            f"Erlaubt: {', '.join(PAYMENT_TYPES)}."
        )
    if amount is None or amount <= 0:
        raise ValueError("amount muss ein positiver Betrag sein.")
    ensure_exists(Invoice, invoice_id, "Rechnung")
    ref = external_reference or f"{import_source}:{uuid.uuid4()}"
    with as_business_error():
        with business_transaction(actor_app_user_id):
            payment = Payment.objects.create(
                id=uuid.uuid4(),
                invoice_id=invoice_id,
                payment_type=payment_type,
                amount=amount,
                currency=currency,
                paid_at=paid_at,
                import_source=import_source,
                external_reference=ref,
            )
    return payment


def reverse_payment(actor_app_user_id, *, payment_id, paid_at=None):
    """Storniert eine Zahlung durch eine Gegenbuchung (STORNO_BUCHUNG).

    Keine physische Löschung (append-only). Eine bereits stornierende Buchung
    (STORNO_BUCHUNG) kann nicht storniert werden, und eine Zahlung wird nicht
    doppelt storniert (Prüfung über die external_reference 'STORNO:<id>').
    """
    original = Payment.objects.filter(id=payment_id).first()
    if original is None:
        raise ValueError("Zahlung nicht gefunden.")
    # Die Gegenbuchung ist stets eine negative STORNO_BUCHUNG; sie neutralisiert
    # nur eingehende (positiv gewertete) Zahlungen. Eine bereits negative Buchung
    # (RUECKERSTATTUNG/STORNO_BUCHUNG) würde dadurch doppelt statt aufgehoben.
    if PAYMENT_SIGN[original.payment_type] < 0:
        raise ValueError(
            "Nur eingehende Zahlungen können storniert werden "
            "(Rückerstattungen/Storno-Buchungen nicht)."
        )
    storno_ref = f"STORNO:{original.id}"
    if Payment.objects.filter(
        invoice_id=original.invoice_id, external_reference=storno_ref
    ).exists():
        raise ValueError("Diese Zahlung wurde bereits storniert.")
    with as_business_error():
        with business_transaction(actor_app_user_id):
            storno = Payment.objects.create(
                id=uuid.uuid4(),
                invoice_id=original.invoice_id,
                payment_type="STORNO_BUCHUNG",
                amount=original.amount,
                currency=original.currency,
                paid_at=paid_at or date.today(),
                import_source="MANUAL",
                external_reference=storno_ref,
            )
    return storno


def issue_dunning_notice(
    actor_app_user_id, *, invoice_id, level, issued_at, note=None, document_id=None
):
    """Erzeugt eine Mahnstufe (Zahlungserinnerung/Mahnung) zu einer Rechnung.

    Die DB erzwingt: veröffentlichte, zum issued_at bereits fällige Rechnung, und
    die nächste lückenlose Stufe (max+1). Verstöße werden als 422 übersetzt. Das
    Mahndokument ist optional (die Ausfertigung ist reine Ausgabe, keine
    Vorbedingung).
    """
    if level is None or level <= 0:
        raise ValueError("level muss eine positive Stufennummer sein.")
    ensure_exists(Invoice, invoice_id, "Rechnung")
    with as_business_error():
        with business_transaction(actor_app_user_id):
            notice = DunningNotice.objects.create(
                id=uuid.uuid4(),
                invoice_id=invoice_id,
                level_id=level,
                issued_at=issued_at,
                document_id=document_id,
                note=note,
                created_by_id=actor_app_user_id,
            )
    return notice
