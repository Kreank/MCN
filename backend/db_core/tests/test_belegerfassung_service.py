"""Service-Tests der Belegerfassung (Schema accounting) gegen die echte Test-DB.

Deckt ab: Buchungskonten/Kostenstellen (CRUD, Eindeutigkeit, Deaktivieren),
Eingangsbeleg-Anlage mit serverseitiger Summenberechnung (Decimal, ROUND_HALF_UP,
Rundung je Steuergruppe — Muster beleg.py::_prepare_lines), Statusautomat
(ERFASST→GEPRUEFT→FREIGEGEBEN→GEBUCHT/ABGELEHNT), das Freigabe-Tor (Kontierung),
den Freeze-Trigger ab FREIGEGEBEN, den Positionsschutz und den Schutzstandard
(kein DELETE). Fachliche Tor-Verstöße werfen ValueError (→422); harte DB-Regeln
(CHECK/forbid_mutation) werfen django.db.Error.

Die Statusautomat-/Freeze-Trigger dieses Schemas sind gewöhnliche BEFORE-Trigger
(nicht DEFERRED) — sie feuern also innerhalb der pytest-Transaktion sofort; ein
SET CONSTRAINTS ALL IMMEDIATE ist hier nicht nötig.
"""
import re
from decimal import Decimal

import pytest
from django.db import Error, transaction

from db_core.db_context import business_transaction
from db_core.models import Receipt, ReceiptLine
from db_core.services import belegerfassung as service
from db_core.services import identity as identity_service


# ---------------------------------------------------------------------------
# Helfer
# ---------------------------------------------------------------------------

def _supplier(app_user, first="Liefer", last="Ant"):
    return identity_service.create_person(app_user.id, first_name=first, last_name=last)


def _ledger(app_user, number="4400", label="Wareneingang", account_type="AUFWAND"):
    return service.create_ledger_account(
        app_user.id, account_number=number, label=label, account_type=account_type,
    )


def _cost_center(app_user, code="K100", label="Zentrale"):
    return service.create_cost_center(app_user.id, code=code, label=label)


def _line(desc="Position", quantity=1, unit_price=10, tax_code="DE_19",
          ledger_account_id=None, cost_center_id=None, unit=None):
    return {
        "description": desc, "quantity": quantity, "unit_price": unit_price,
        "tax_code": tax_code, "ledger_account_id": ledger_account_id,
        "cost_center_id": cost_center_id, "unit": unit,
    }


def _receipt(app_user, *, lines=None, kontiert=True, **kwargs):
    """Legt einen Eingangsbeleg an. Standardmäßig kontiert (Freigabe-tauglich)."""
    supplier = kwargs.pop("supplier", None) or _supplier(app_user)
    if lines is None:
        ledger_id = None
        if kontiert:
            ledger_id = _ledger(app_user).id
        lines = [_line(ledger_account_id=ledger_id)]
    return service.create_receipt(
        app_user.id, supplier_party_id=supplier.id,
        receipt_date=kwargs.pop("receipt_date", "2026-07-01"), lines=lines, **kwargs,
    )


# ---------------------------------------------------------------------------
# Buchungskonten (Stammdaten)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_create_ledger_account(app_user):
    acc = _ledger(app_user, number="4401", label="Fremdleistungen")
    assert acc.account_number == "4401"
    assert acc.account_type == "AUFWAND"
    assert acc.active is True
    assert acc.chart_of_accounts is None


@pytest.mark.django_db
def test_create_ledger_account_mit_kontenrahmen(app_user):
    acc = service.create_ledger_account(
        app_user.id, account_number="4402", label="Miete",
        account_type="AUFWAND", chart_of_accounts="SKR03",
    )
    assert acc.chart_of_accounts == "SKR03"


@pytest.mark.django_db
def test_create_ledger_account_ungueltige_kontoart(app_user):
    with pytest.raises(ValueError):
        service.create_ledger_account(
            app_user.id, account_number="4403", label="X", account_type="FALSCH",
        )


@pytest.mark.django_db
def test_create_ledger_account_ungueltiger_kontenrahmen(app_user):
    with pytest.raises(ValueError):
        service.create_ledger_account(
            app_user.id, account_number="4404", label="X",
            account_type="AKTIV", chart_of_accounts="SKR99",
        )


@pytest.mark.django_db
def test_ledger_account_nummer_eindeutig(app_user):
    _ledger(app_user, number="4405", label="Erst")
    with pytest.raises(ValueError):
        _ledger(app_user, number="4405", label="Zweit")


@pytest.mark.django_db
def test_ledger_account_leere_pflichtfelder(app_user):
    with pytest.raises(ValueError):
        service.create_ledger_account(
            app_user.id, account_number="  ", label="X", account_type="AKTIV",
        )
    with pytest.raises(ValueError):
        service.create_ledger_account(
            app_user.id, account_number="4406", label="  ", account_type="AKTIV",
        )


@pytest.mark.django_db
def test_update_ledger_account_label(app_user):
    acc = _ledger(app_user, number="4407")
    updated = service.update_ledger_account(
        app_user.id, ledger_account_id=acc.id, label="Neuer Name",
    )
    assert updated.label == "Neuer Name"


@pytest.mark.django_db
def test_update_ledger_account_deaktivieren(app_user):
    acc = _ledger(app_user, number="4408")
    updated = service.update_ledger_account(
        app_user.id, ledger_account_id=acc.id, active=False,
    )
    assert updated.active is False


@pytest.mark.django_db
def test_update_ledger_account_duplikat_nummer(app_user):
    _ledger(app_user, number="4409")
    other = _ledger(app_user, number="4410")
    with pytest.raises(ValueError):
        service.update_ledger_account(
            app_user.id, ledger_account_id=other.id, account_number="4409",
        )


@pytest.mark.django_db
def test_update_ledger_account_unbekannt(app_user):
    import uuid
    with pytest.raises(ValueError):
        service.update_ledger_account(
            app_user.id, ledger_account_id=uuid.uuid4(), label="X",
        )


@pytest.mark.django_db
def test_update_ledger_account_unbekanntes_feld(app_user):
    acc = _ledger(app_user, number="4411")
    with pytest.raises(ValueError):
        service.update_ledger_account(
            app_user.id, ledger_account_id=acc.id, quatsch="x",
        )


# ---------------------------------------------------------------------------
# Kostenstellen (Stammdaten)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_create_cost_center(app_user):
    cc = _cost_center(app_user, code="K200", label="Werkstatt")
    assert cc.code == "K200"
    assert cc.active is True


@pytest.mark.django_db
def test_cost_center_code_eindeutig(app_user):
    _cost_center(app_user, code="K201")
    with pytest.raises(ValueError):
        _cost_center(app_user, code="K201", label="Andere")


@pytest.mark.django_db
def test_update_cost_center_deaktivieren(app_user):
    cc = _cost_center(app_user, code="K202")
    updated = service.update_cost_center(
        app_user.id, cost_center_id=cc.id, active=False,
    )
    assert updated.active is False


@pytest.mark.django_db
def test_update_cost_center_unbekannt(app_user):
    import uuid
    with pytest.raises(ValueError):
        service.update_cost_center(
            app_user.id, cost_center_id=uuid.uuid4(), label="X",
        )


# ---------------------------------------------------------------------------
# Eingangsbeleg anlegen
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_create_receipt_grunddaten(app_user):
    supplier = _supplier(app_user)
    r = service.create_receipt(
        app_user.id, supplier_party_id=supplier.id, receipt_date="2026-07-01",
        supplier_invoice_number="LR-2026-42",
        lines=[_line(desc="Rohre", quantity=10, unit_price=3, unit="Stk")],
    )
    assert r.status == "ERFASST"
    assert r.supplier_party_id == supplier.id
    assert r.supplier_invoice_number == "LR-2026-42"
    # Netto 10 * 3 = 30, Steuer 19% = 5.70, Brutto 35.70
    assert r.net_total == Decimal("30.00")
    assert r.tax_total == Decimal("5.70")
    assert r.gross_total == Decimal("35.70")
    line = ReceiptLine.objects.get(receipt_id=r.id, position_number=1)
    assert line.net_amount == Decimal("30.00")
    assert line.tax_rate_percent == Decimal("19.00")
    assert line.tax_code_id == "DE_19"


@pytest.mark.django_db
def test_create_receipt_nummer_von_db_vergeben(app_user):
    """Die Belegnummer wird von der DB-Sequenz vergeben (EB-#####), nie vom Client.

    Der Service setzt receipt_number nie selbst; nach refresh_from_db() steht das
    DB-vergebene Muster. (Die exakte Zählnummer ist über die Suite hinweg nicht
    stabil — Sequenzen werden vom Test-Rollback nicht zurückgesetzt —, deshalb
    wird das Format geprüft, nicht exakt EB-00001.)
    """
    r = _receipt(app_user)
    assert re.match(r"^EB-\d{5,}$", r.receipt_number), r.receipt_number


@pytest.mark.django_db
def test_create_receipt_default_received_date(app_user):
    """received_date default = receipt_date, wenn nicht übergeben."""
    r = _receipt(app_user, receipt_date="2026-06-15")
    assert str(r.received_date) == "2026-06-15"


@pytest.mark.django_db
def test_create_receipt_praezision_quantisiert(app_user):
    """Menge mit >3 Nachkommastellen wird auf die Spaltenskala (3) gerundet;
    net_amount passt danach exakt zum DB-CHECK round(quantity*unit_price,2)."""
    r = _receipt(
        app_user,
        lines=[_line(desc="x", quantity="1.5555", unit_price=100)],
        kontiert=False,
    )
    line = ReceiptLine.objects.get(receipt_id=r.id, position_number=1)
    assert line.quantity == Decimal("1.556")
    assert line.net_amount == Decimal("155.60")  # round(1.556 * 100, 2)


@pytest.mark.django_db
def test_create_receipt_steuer_je_gruppe_gerundet(app_user):
    """Kopf-Steuer wird PRO Steuergruppe gerundet, nicht pro Zeile.

    Zwei Zeilen à netto 0.03 @19% → Gruppen-Netto 0.06, Steuer round(0.06*0.19,2)
    = 0.01. Naive Pro-Zeile-Rundung ergäbe 0.01+0.01 = 0.02 — hier verboten.
    """
    r = _receipt(
        app_user, kontiert=False,
        lines=[
            _line(desc="a", quantity="0.03", unit_price=1),
            _line(desc="b", quantity="0.03", unit_price=1),
        ],
    )
    assert r.net_total == Decimal("0.06")
    assert r.tax_total == Decimal("0.01")  # NICHT 0.02
    assert r.gross_total == Decimal("0.07")


@pytest.mark.django_db
def test_create_receipt_zwei_steuergruppen(app_user):
    """Verschiedene Steuersätze werden getrennt gruppiert und je Gruppe gerundet."""
    r = _receipt(
        app_user, kontiert=False,
        lines=[
            _line(desc="19er", quantity=1, unit_price=100, tax_code="DE_19"),
            _line(desc="7er", quantity=1, unit_price=100, tax_code="DE_7"),
        ],
    )
    assert r.net_total == Decimal("200.00")
    assert r.tax_total == Decimal("26.00")  # 19.00 + 7.00
    assert r.gross_total == Decimal("226.00")


@pytest.mark.django_db
def test_create_receipt_ohne_positionen(app_user):
    supplier = _supplier(app_user)
    with pytest.raises(ValueError):
        service.create_receipt(
            app_user.id, supplier_party_id=supplier.id,
            receipt_date="2026-07-01", lines=[],
        )


@pytest.mark.django_db
def test_create_receipt_unbekannter_lieferant(app_user):
    import uuid
    with pytest.raises(ValueError):
        service.create_receipt(
            app_user.id, supplier_party_id=uuid.uuid4(),
            receipt_date="2026-07-01", lines=[_line()],
        )


@pytest.mark.django_db
def test_create_receipt_unbekanntes_buchungskonto(app_user):
    """Unbekannte Kontierungs-Referenz → ValueError (422), nicht IntegrityError (500)."""
    import uuid
    supplier = _supplier(app_user)
    with pytest.raises(ValueError):
        service.create_receipt(
            app_user.id, supplier_party_id=supplier.id, receipt_date="2026-07-01",
            lines=[_line(ledger_account_id=uuid.uuid4())],
        )


@pytest.mark.django_db
def test_create_receipt_archiviertes_konto_abgelehnt(app_user):
    """Ein deaktiviertes (archiviertes) Konto ist als Kontierung unzulässig."""
    acc = _ledger(app_user, number="4499")
    service.update_ledger_account(app_user.id, ledger_account_id=acc.id, active=False)
    supplier = _supplier(app_user)
    with pytest.raises(ValueError):
        service.create_receipt(
            app_user.id, supplier_party_id=supplier.id, receipt_date="2026-07-01",
            lines=[_line(ledger_account_id=acc.id)],
        )


@pytest.mark.django_db
def test_create_receipt_ungueltiger_tax_code(app_user):
    supplier = _supplier(app_user)
    with pytest.raises(ValueError):
        service.create_receipt(
            app_user.id, supplier_party_id=supplier.id, receipt_date="2026-07-01",
            lines=[_line(tax_code="XX_99")],
        )


@pytest.mark.django_db
def test_create_receipt_menge_null(app_user):
    supplier = _supplier(app_user)
    with pytest.raises(ValueError):
        service.create_receipt(
            app_user.id, supplier_party_id=supplier.id, receipt_date="2026-07-01",
            lines=[_line(quantity=0)],
        )


@pytest.mark.django_db
def test_create_receipt_fehlendes_belegdatum(app_user):
    supplier = _supplier(app_user)
    with pytest.raises(ValueError):
        service.create_receipt(
            app_user.id, supplier_party_id=supplier.id, receipt_date=None,
            lines=[_line()],
        )


@pytest.mark.django_db
def test_create_receipt_faelligkeit_vor_belegdatum_422(app_user):
    """`due_date < receipt_date` fängt der Service vorab ab (ValueError → 422).

    Der DB-CHECK `receipt_due_after_receipt_date` ist ein harter CHECK
    (SQLSTATE 23514), kein fachliches Tor (P0001) — ohne Vorabprüfung käme er als
    IntegrityError durch und landete als 500 beim Aufrufer.
    """
    supplier = _supplier(app_user)
    with pytest.raises(ValueError, match="Fälligkeitsdatum"):
        service.create_receipt(
            app_user.id, supplier_party_id=supplier.id,
            receipt_date="2026-07-01", due_date="2026-06-01",
            lines=[_line()],
        )


@pytest.mark.django_db
def test_db_check_faelligkeit_bleibt_physisch_scharf(app_user):
    """Gegenprobe: Am Service vorbei weist die DB dieselbe Verletzung physisch ab.
    Die Vorabprüfung ersetzt den CHECK nicht, sie übersetzt ihn nur."""
    supplier = _supplier(app_user)
    beleg = service.create_receipt(
        app_user.id, supplier_party_id=supplier.id,
        receipt_date="2026-07-01", lines=[_line()],
    )
    with pytest.raises(Error):
        with transaction.atomic():
            with business_transaction(app_user.id):
                Receipt.objects.filter(id=beleg.id).update(due_date="2026-06-01")


@pytest.mark.django_db
def test_create_receipt_ungueltige_waehrung_422(app_user):
    """Auch der Währungs-CHECK (`^[A-Z]{3}$`) wird vorab als 422 abgefangen."""
    supplier = _supplier(app_user)
    with pytest.raises(ValueError, match="Währung"):
        service.create_receipt(
            app_user.id, supplier_party_id=supplier.id,
            receipt_date="2026-07-01", currency="Euro", lines=[_line()],
        )


@pytest.mark.django_db
def test_update_receipt_faelligkeit_gegen_bestandsdatum_geprueft(app_user):
    """Beim Ändern zählen die EFFEKTIVEN Werte: ein neues Fälligkeitsdatum muss
    auch zum unveränderten Belegdatum des Bestands passen."""
    supplier = _supplier(app_user)
    beleg = service.create_receipt(
        app_user.id, supplier_party_id=supplier.id,
        receipt_date="2026-07-01", lines=[_line()],
    )
    with pytest.raises(ValueError, match="Fälligkeitsdatum"):
        service.update_receipt(
            app_user.id, receipt_id=beleg.id, due_date="2026-06-01"
        )


# ---------------------------------------------------------------------------
# Statusautomat
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_status_voller_durchlauf(app_user):
    """ERFASST → GEPRUEFT → FREIGEGEBEN → GEBUCHT (kontiert, gültige Kette)."""
    r = _receipt(app_user)  # kontiert
    for to in ("GEPRUEFT", "FREIGEGEBEN", "GEBUCHT"):
        service.advance_status(app_user.id, receipt_id=r.id, to_status=to)
    r.refresh_from_db()
    assert r.status == "GEBUCHT"


@pytest.mark.django_db
def test_status_ungueltiger_uebergang(app_user):
    """ERFASST → FREIGEGEBEN direkt ist unzulässig (muss über GEPRUEFT) → ValueError."""
    r = _receipt(app_user)
    with pytest.raises(ValueError):
        service.advance_status(app_user.id, receipt_id=r.id, to_status="FREIGEGEBEN")


@pytest.mark.django_db
def test_status_erfasst_direkt_gebucht_ungueltig(app_user):
    r = _receipt(app_user)
    with pytest.raises(ValueError):
        service.advance_status(app_user.id, receipt_id=r.id, to_status="GEBUCHT")


@pytest.mark.django_db
def test_status_gebucht_ist_final(app_user):
    r = _receipt(app_user)
    for to in ("GEPRUEFT", "FREIGEGEBEN", "GEBUCHT"):
        service.advance_status(app_user.id, receipt_id=r.id, to_status=to)
    # GEBUCHT ist final — kein Wechsel mehr.
    with pytest.raises(ValueError):
        service.advance_status(app_user.id, receipt_id=r.id, to_status="ABGELEHNT")


@pytest.mark.django_db
def test_status_unbekannter_zielstatus(app_user):
    r = _receipt(app_user)
    with pytest.raises(ValueError):
        service.advance_status(app_user.id, receipt_id=r.id, to_status="QUATSCH")


@pytest.mark.django_db
def test_status_gleicher_status(app_user):
    r = _receipt(app_user)
    with pytest.raises(ValueError):
        service.advance_status(app_user.id, receipt_id=r.id, to_status="ERFASST")


@pytest.mark.django_db
def test_freigabe_ohne_kontierung_scheitert(app_user):
    """Freigabe-Tor: eine Position ohne Buchungskonto → 422 (DB-Trigger)."""
    r = _receipt(app_user, kontiert=False)
    service.advance_status(app_user.id, receipt_id=r.id, to_status="GEPRUEFT")
    with pytest.raises(ValueError):
        service.advance_status(app_user.id, receipt_id=r.id, to_status="FREIGEGEBEN")


@pytest.mark.django_db
def test_ablehnung_braucht_begruendung(app_user):
    r = _receipt(app_user)
    with pytest.raises(ValueError):
        service.advance_status(app_user.id, receipt_id=r.id, to_status="ABGELEHNT")


@pytest.mark.django_db
def test_ablehnung_mit_begruendung(app_user):
    r = _receipt(app_user)
    service.advance_status(
        app_user.id, receipt_id=r.id, to_status="ABGELEHNT", reason="Doppelt erfasst",
    )
    r.refresh_from_db()
    assert r.status == "ABGELEHNT"
    assert r.rejection_reason == "Doppelt erfasst"


@pytest.mark.django_db
def test_status_ruecksetzung_reaktiviert_bearbeitung(app_user):
    """FREIGEGEBEN → GEPRUEFT nimmt die Freigabe zurück; danach wieder bearbeitbar."""
    r = _receipt(app_user)
    for to in ("GEPRUEFT", "FREIGEGEBEN"):
        service.advance_status(app_user.id, receipt_id=r.id, to_status=to)
    service.advance_status(app_user.id, receipt_id=r.id, to_status="GEPRUEFT")
    r.refresh_from_db()
    assert r.status == "GEPRUEFT"
    # Wieder editierbar: Notiz ändern gelingt.
    updated = service.update_receipt(app_user.id, receipt_id=r.id, notes="Nachbesserung")
    assert updated.notes == "Nachbesserung"


# ---------------------------------------------------------------------------
# Bearbeiten & Einfrieren
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_update_receipt_positionen_ersetzen(app_user):
    r = _receipt(app_user, kontiert=False,
                 lines=[_line(desc="Alt", quantity=1, unit_price=10)])
    updated = service.update_receipt(
        app_user.id, receipt_id=r.id,
        lines=[
            _line(desc="Neu1", quantity=2, unit_price=5),
            _line(desc="Neu2", quantity=1, unit_price=20),
        ],
    )
    assert updated.net_total == Decimal("30.00")
    assert ReceiptLine.objects.filter(receipt_id=r.id).count() == 2


@pytest.mark.django_db
def test_update_receipt_nach_freigabe_gesperrt(app_user):
    """Der Service verweigert die Bearbeitung ab FREIGEGEBEN (nicht editierbar)."""
    r = _receipt(app_user)
    for to in ("GEPRUEFT", "FREIGEGEBEN"):
        service.advance_status(app_user.id, receipt_id=r.id, to_status=to)
    with pytest.raises(ValueError):
        service.update_receipt(app_user.id, receipt_id=r.id, notes="zu spät")


@pytest.mark.django_db
def test_freeze_trigger_sperrt_direkte_kopfaenderung(app_user):
    """GoBD-Freeze: ab FREIGEGEBEN sind Kopf-Beträge physisch unveränderlich.

    Umgeht bewusst den Service-Guard und schreibt direkt in die DB → der
    freeze_receipt-Trigger (P0001) verhindert die Betragsänderung.
    """
    r = _receipt(app_user)
    for to in ("GEPRUEFT", "FREIGEGEBEN"):
        service.advance_status(app_user.id, receipt_id=r.id, to_status=to)
    with pytest.raises(Error):
        with transaction.atomic():
            with business_transaction(app_user.id):
                Receipt.objects.filter(id=r.id).update(net_total=Decimal("999.00"))


@pytest.mark.django_db
def test_positionen_nach_freigabe_unveraenderlich(app_user):
    """GoBD-Positionsschutz: ab FREIGEGEBEN sind Positionen physisch gesperrt.

    Direktes Löschen einer Position umgeht den Service → protect_receipt_lines
    (P0001) verhindert es.
    """
    r = _receipt(app_user)
    for to in ("GEPRUEFT", "FREIGEGEBEN"):
        service.advance_status(app_user.id, receipt_id=r.id, to_status=to)
    with pytest.raises(Error):
        with transaction.atomic():
            with business_transaction(app_user.id):
                ReceiptLine.objects.filter(receipt_id=r.id).delete()


# ---------------------------------------------------------------------------
# Schutzstandard: kein DELETE
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_receipt_delete_verboten(app_user):
    """Schutzstandard: DELETE auf accounting.receipt scheitert per Trigger."""
    r = _receipt(app_user)
    with pytest.raises(Error):
        with transaction.atomic():
            Receipt.objects.filter(id=r.id).delete()


# ---------------------------------------------------------------------------
# Audit-Spur der Positionen (GoBD, Migration 0035)
# ---------------------------------------------------------------------------

def _audit_eintraege(target_type):
    from django.db import connection
    with connection.cursor() as cur:
        cur.execute(
            "SELECT action FROM audit.audit_entry WHERE target_type = %s",
            [target_type],
        )
        return [r[0] for r in cur.fetchall()]


@pytest.mark.django_db
def test_positionsaenderung_hinterlaesst_audit_spur(app_user):
    """Das Ersetzen der Positionen eines Entwurfs (DELETE + Neuanlage) muss eine
    Audit-Spur hinterlassen — wie bei invoicing.invoice_line/quote_line.

    Ohne die Trigger aus Migration 0035 verschwänden Positionsänderungen an einem
    GoBD-relevanten Beleg spurlos.
    """
    supplier = _supplier(app_user)
    beleg = service.create_receipt(
        app_user.id, supplier_party_id=supplier.id,
        receipt_date="2026-07-01", lines=[_line(desc="Alt", unit_price=10)],
    )
    vorher = len(_audit_eintraege("accounting.receipt_line"))

    service.update_receipt(
        app_user.id, receipt_id=beleg.id,
        lines=[_line(desc="Neu", unit_price=20)],
    )

    eintraege = _audit_eintraege("accounting.receipt_line")
    assert len(eintraege) > vorher, (
        "Positionsersetzung ohne Audit-Spur — Trigger aus 0035 fehlen."
    )
    assert "ROW_DELETE" in eintraege
