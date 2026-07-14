"""Nebenläufigkeit: die Doppelabrechnung durch **Timing** statt durch Reihenfolge.

Der Review-Befund über die zwei sequenziellen Doppelabrechnungen hinaus: Die
quellenunabhängige Mengengrenze (`_billed_je_schluessel`) rechnet über den Auftrag,
aber Nachtrag und Angebotskopie sperren **verschiedene** Quellzeilen
(`site_report_line` vs. `quote_line`). Ohne eine gemeinsame Serialisierung liefen
zwei gleichzeitige Läufe aneinander vorbei — beide läsen den leeren Bindungsstand,
beide buchten die volle Menge.

Dieser Test **fährt die Race wirklich**: zwei Threads, zwei offene Transaktionen,
echte Commits (`transaction=True`). Er ist parametrisiert:

* **ohne Sperre** (`_auftrag_sperren` ausgehebelt) → **zwei** Rechnungen über
  dieselben 19 Stück, zusammen 912 € statt 456 €. Der Beweis, dass die Race
  existiert und Geld kostet.
* **mit Sperre** (Produktivzustand) → **eine** Rechnung, der zweite Lauf wird mit
  422 abgewiesen. Der Beweis, dass die `work_order`-Sperre sie schließt.

Ein Test, der nur den zweiten Fall führt, wäre wertlos — er liefe auch grün, wenn
die Sperre gar nichts serialisierte. Deshalb muss der erste Fall ohne die Sperre
**umfallen**.

Die erzwungene Verschränkung: Der Nachtrag liest den (leeren) Stand und **wartet**
kurz vor dem Schreiben; in diesem Fenster ordnet der Angebots-Thread sein Angebot
dem Auftrag zu und fakturiert es. Ohne Sperre schreiben danach beide; mit Sperre
hält der Nachtrag die `work_order`-Zeile, der Angebots-Thread blockiert an ihr, und
nach dem Commit des Nachtrags sieht er dessen Bindung — die Mengengrenze weist ihn
ab.
"""
import threading
import uuid
from datetime import date
from decimal import Decimal

import pytest
from django.db import connections

from db_core.models import Invoice
from db_core.services import abrechnung as abrechnung_service
from db_core.services import artikel as artikel_service
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service
from db_core.services import site_report as report_service

PNG_1x1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
    b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


class FakeStorage:
    def ensure_bucket(self):
        pass

    def put_object(self, key, data, content_type="application/octet-stream"):
        return None

    def get_object(self, key):
        raise KeyError(key)

    def remove_object(self, key):
        pass


def _auftrag(actor, obj, kunde):
    order = auftrag_service.create_work_order(
        actor, property_id=obj.id, title="Thermostatventile"
    )
    auftrag_service.set_order_evidence(
        actor, work_order_id=order.id, reference="Mail"
    )
    auftrag_service.confirm_responsibility(
        actor, work_order_id=order.id, scope="COMMON_PROPERTY"
    )
    for role in ("PRINCIPAL", "INVOICE_DEBTOR"):
        auftrag_service.add_work_order_party(
            actor, work_order_id=order.id, party_id=kunde.id, role=role,
            is_primary=True,
        )
    for to in ("FREIGEGEBEN", "IN_PLANUNG", "IN_AUSFUEHRUNG"):
        auftrag_service.advance_status(actor, work_order_id=order.id, to_status=to)
    order.refresh_from_db()
    return order


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("mit_sperre", [False, True])
def test_nebenlaeufig_nachtrag_und_angebotsrechnung(mit_sperre, monkeypatch):
    """Zwei gleichzeitige Läufe über dieselben 19 Stück — ohne Sperre doppelt.

    Ohne `_auftrag_sperren`: beide sehen den leeren Bindungsstand, beide buchen 19
    → 912 €. Mit Sperre: der zweite blockiert, sieht danach die Bindung des ersten
    und wird abgewiesen (422) → 456 €.
    """
    from db_core import storage as storage_module

    monkeypatch.setattr(storage_module, "get_storage", lambda: FakeStorage())

    # --- Aufbau (committet, für beide Threads sichtbar) ----------------------
    from db_core.models import AppUser

    admin = AppUser.objects.create(
        id=uuid.uuid4(), display_name="Backoffice", status="ACTIVE", version=1
    )
    obj = property_service.create_property(
        admin.id, name="Neben-Objekt", property_type="WEG",
        street="Weg", house_number="1", postal_code="10115", city="Berlin",
    )
    kunde = identity_service.create_person(
        admin.id, first_name="Karla", last_name="Kundin"
    )
    order = _auftrag(admin.id, obj, kunde)
    ventil = artikel_service.create_article(
        admin.id, article_number=f"NB-{uuid.uuid4().hex[:8]}",
        description="Thermostatventil", unit="stk", line_type="MATERIAL",
    )
    artikel_service.set_article_sale_price(
        admin.id, article_id=ventil.id, fixed_price=Decimal("24.00"), is_standard=True,
    )
    # Signierter Bericht: 19 Stück, OHNE Herkunft (Soll=0 → ZUSATZ), Einheit stk.
    report = report_service.create_report(
        admin.id, work_order_id=order.id, report_date=date(2026, 7, 6),
        activity_text="Ventile getauscht.",
    )
    report_service.set_report_lines(admin.id, report_id=report.id, lines=[
        {"line_type": "MATERIAL", "quantity": "19", "unit": "stk",
         "source_article_id": str(ventil.id)},
    ])
    report_service.sign_report(
        admin.id, report_id=report.id, signed_by_name="Karla", signature_png=PNG_1x1,
    )
    # Angebot über dieselben 19 Stück — NOCH NICHT zugeordnet (Soll bleibt 0, bis
    # der Angebots-Thread es im Race-Fenster zuordnet).
    quote = beleg_service.create_quote(
        admin.id, property_id=obj.id, title="Angebot",
        lines=[{
            "line_type": "MATERIAL", "description": "Thermostatventil",
            "quantity": "19", "unit": "stk", "unit_price": "24.00",
            "tax_code": "DE_19", "source_article_id": str(ventil.id),
        }],
    )
    beleg_service.send_quote(admin.id, quote_id=quote.id)

    if not mit_sperre:
        # Die Sperre aushebeln — jetzt MUSS die Race umfallen.
        monkeypatch.setattr(abrechnung_service, "_auftrag_sperren", lambda wid: None)

    ev_n_read = threading.Event()   # Nachtrag hat den leeren Stand gelesen
    ev_a_done = threading.Event()   # Angebots-Thread ist fertig (oder aufgegeben)

    real_kandidaten = abrechnung_service._nachtrag_kandidaten

    def kandidaten_hook(o):
        """Der Nachtrag liest — und wartet dann kurz vor dem Schreiben.

        Das Fenster, in dem der Angebots-Thread zuordnet und fakturiert. Der
        Timeout hält den Test auch dann heil, wenn (mit Sperre) der Angebots-Thread
        blockiert und `ev_a_done` nie setzt: Der Nachtrag schreibt dann nach dem
        Timeout, gibt die `work_order`-Sperre frei, und der Angebots-Thread läuft
        in die Mengengrenze.
        """
        result = real_kandidaten(o)
        ev_n_read.set()
        ev_a_done.wait(timeout=5)
        return result

    monkeypatch.setattr(abrechnung_service, "_nachtrag_kandidaten", kandidaten_hook)

    ergebnis = {}

    def lauf_nachtrag():
        try:
            inv = abrechnung_service.rechnung_aus_nachtrag(
                admin.id, work_order_id=order.id, tax_code="DE_19",
            )
            ergebnis["nachtrag"] = ("ok", inv.net_total)
        except Exception as exc:  # noqa: BLE001 — im Test bewusst breit
            ergebnis["nachtrag"] = ("fehler", str(exc))
        finally:
            connections.close_all()

    def lauf_angebot():
        try:
            ev_n_read.wait(timeout=5)
            # Im Race-Fenster: Angebot dem Auftrag zuordnen und fakturieren.
            beleg_service.update_quote(
                admin.id, quote_id=quote.id, work_order_id=order.id
            )
            inv = abrechnung_service.rechnung_aus_angebot(admin.id, quote_id=quote.id)
            ergebnis["angebot"] = ("ok", inv.net_total)
        except Exception as exc:  # noqa: BLE001
            ergebnis["angebot"] = ("fehler", str(exc))
        finally:
            ev_a_done.set()
            connections.close_all()

    t_n = threading.Thread(target=lauf_nachtrag)
    t_a = threading.Thread(target=lauf_angebot)
    t_n.start()
    t_a.start()
    t_n.join(timeout=30)
    t_a.join(timeout=30)
    assert not t_n.is_alive() and not t_a.is_alive(), "Threads hängen (Deadlock?)"

    # --- Das eigentliche Urteil: die Summe der geltenden Rechnungen ----------
    rechnungen = list(
        Invoice.objects.filter(work_order_id=order.id, invoice_type="RECHNUNG")
    )
    summe = sum((i.net_total for i in rechnungen), Decimal("0.00"))

    if mit_sperre:
        # Genau EINE Rechnung über die 19 Stück (456 €); der zweite Lauf wurde
        # abgewiesen. WELCHER von beiden gewinnt, hängt am Timing — entscheidend
        # ist: die Menge steht genau einmal auf einer Rechnung.
        assert summe == Decimal("456.00"), ergebnis
        fehler = [v for v in ergebnis.values() if v[0] == "fehler"]
        assert len(fehler) == 1, ergebnis
        assert len(rechnungen) == 1, ergebnis
    else:
        # Ohne Sperre fällt die Race um: BEIDE buchen die vollen 19 → 912 €.
        assert summe == Decimal("912.00"), ergebnis
        assert ergebnis["nachtrag"][0] == "ok" and ergebnis["angebot"][0] == "ok"
        assert len(rechnungen) == 2, ergebnis
