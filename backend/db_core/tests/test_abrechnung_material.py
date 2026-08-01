"""Slice „Material am Einsatz wird abrechenbar" (Migration 0139).

**Der Befund.** Der Monteur hatte am Einsatz zwei „Material"-Wege vor sich — die
Berichtsposition und die Materialbuchung — und nur einer führte zu Geld. Die
Materialbuchung kam im Abrechnungsservice nirgends vor. Wer sein Material dort
erfasste, hatte es erfasst; er hatte es nur nicht berechnet.

Diese Suite prüft die vier Zusagen des Slices:

1. **Mit Artikel wird die Buchung zur Rechnungsposition** — zum Preis aus der
   einen Rechenstelle, gebunden mit `source_kind = 'MATERIALBUCHUNG'`.
2. **Zweimal geht nicht** — die vierte partielle UNIQUE-Sperre liegt in der
   Datenbank, nicht im Service (`test_db_sperrt_die_zweite_materialbindung`
   schreibt bewusst am Service vorbei). Und der **Storno** gibt die Buchung
   wieder frei — sonst wäre sie nach einer aufgehobenen Rechnung für immer
   verbrannt.
3. **Nie 0,00 €, nie stillschweigend weglassen** — eine Buchung ohne Artikel und
   ein Artikel ohne ermittelbaren VK landen mit benanntem Grund in der
   Klärungsliste.
4. **Dieselbe Schraube nicht doppelt** — steht sie im Bericht UND als
   Materialbuchung, weist der Lauf ab (der Fall, der reproduziert 178,50 € auf
   zwei Rechnungen brachte). Abweichende Einheiten („Stk"/„Stück") gehen
   fail-closed in die Klärung, statt als zwei Posten durchzurutschen.
"""
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError, connection

from db_core.db_context import business_transaction
from db_core.models import BillingLink, MaterialEntry
from db_core.services import abrechnung as abrechnung_service
from db_core.services import beleg as beleg_service
from db_core.services import einsatz as einsatz_service
from db_core.services.abrechnung import (
    AbrechnungError,
    EinheitUneindeutig,
    PreisUnbekannt,
)

# Szenario und Bausteine kommen aus der Schwester-Suite — ein zweiter Satz
# Hilfsfunktionen wäre ein zweiter Wahrheitsort für „so entsteht ein Auftrag".
from db_core.tests.test_abrechnung_service import (  # noqa: F401  (Fixtures)
    T0,
    _artikel,
    _artikel_mit_vk_gruppe,
    _auftrag,
    _bericht,
    _beteiligte,
    _job,
    _kg,
    _monteur,
    _zeit,
    fake_storage,
    szenario,
)


def _material(szenario, job, beschreibung, menge, einheit, *, artikel=None):
    return einsatz_service.log_material(
        szenario["user"].id,
        service_job_id=job.id,
        description=beschreibung,
        quantity=Decimal(menge),
        unit=einheit,
        recorded_by=szenario["user"].id,
        source_article_id=artikel.id if artikel is not None else None,
    )


# ---------------------------------------------------------------------------
# 1. Der Weg von der Buchung in die Rechnung
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_material_mit_artikel_wird_rechnungsposition(szenario):
    """Die Zusage des Slices: gebuchtes Material steht auf der Rechnung.

    Der Preis kommt aus `vk_vorschlag` — nicht aus der Buchung (die führt keinen)
    und nicht vom Client.
    """
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG", billing_mode="REGIE")
    artikel = _artikel(szenario, "MB-1", vk="17.50", beschreibung="Kupferrohr 18")
    job = _job(szenario, order)
    eintrag = _material(szenario, job, "Kupferrohr 18", "4", "m", artikel=artikel)
    _kg(szenario, order)

    invoice = abrechnung_service.rechnung_aus_auftrag(
        szenario["user"].id, work_order_id=order.id, tax_code="DE_19"
    )
    line = invoice.lines.get()
    assert line.line_type == "MATERIAL"
    assert line.description == "Kupferrohr 18"
    assert line.quantity == Decimal("4.000")
    assert line.unit == "m"
    assert line.unit_price == Decimal("17.50")
    assert line.net_amount == Decimal("70.00")
    assert line.source_article_id == artikel.id

    link = BillingLink.objects.get(invoice_id=invoice.id)
    assert link.source_kind == "MATERIALBUCHUNG"
    assert link.material_entry_id == eintrag.id
    assert link.invoice_line_id == line.id
    assert link.released_at is None


@pytest.mark.django_db
def test_material_ohne_auftrag_bleibt_draussen(szenario):
    """Ein freier Termin ohne Auftrag gehört zu keiner Baustelle.

    Dieselbe Grenze wie bei der Zeitbuchung (`_zeitbuchungen`): Der Auftragsbezug
    läuft über den Einsatz; ohne ihn gibt es keine Rechnung, an die die Buchung
    gehörte.
    """
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG", billing_mode="REGIE")
    artikel = _artikel(szenario, "MB-2", vk="10.00")
    freier = einsatz_service.create_service_job(
        szenario["user"].id, property_id=szenario["obj"].id,
        title="Begehung ohne Auftrag",
    )
    _material(szenario, freier, "Dichtung", "2", "Stk", artikel=artikel)
    _kg(szenario, order)

    assert abrechnung_service._materialbuchungen(order.id) == []
    with pytest.raises(AbrechnungError, match="nichts abzurechnen"):
        abrechnung_service.rechnung_aus_auftrag(
            szenario["user"].id, work_order_id=order.id, tax_code="DE_19"
        )


@pytest.mark.django_db
def test_offene_abrechnung_weist_material_aus(szenario):
    """Der fehlende Preis ist SCHON VORHER sichtbar — nicht erst beim Lauf."""
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG", billing_mode="REGIE")
    artikel = _artikel(szenario, "MB-3", vk="9.00")
    job = _job(szenario, order)
    mit = _material(szenario, job, "Rohr", "3", "m", artikel=artikel)
    ohne = _material(szenario, job, "Dichtung aus dem Fahrzeug", "2", "Stk")

    offen = abrechnung_service.offene_abrechnung(order.id)
    je_id = {m["material_entry_id"]: m for m in offen["materialbuchungen"]}
    assert set(je_id) == {mit.id, ohne.id}
    assert je_id[mit.id]["preis_status"] == "BEKANNT"
    assert je_id[mit.id]["einzelpreis"] == Decimal("9.00")
    assert je_id[ohne.id]["preis_status"] == "UNBEKANNT"
    # **Null heißt unbekannt, nicht 0,00 €.**
    assert je_id[ohne.id]["einzelpreis"] is None
    assert je_id[ohne.id]["grund"] == "MATERIAL_OHNE_ARTIKEL"


# ---------------------------------------------------------------------------
# 2. Zweimal geht nicht — und der Storno gibt frei
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_zweiter_lauf_findet_die_buchung_nicht_mehr(szenario):
    """Was gebunden ist, kommt nicht noch einmal — der Normalfall."""
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG", billing_mode="REGIE")
    artikel = _artikel(szenario, "MB-4", vk="12.00")
    job = _job(szenario, order)
    _material(szenario, job, "Rohr", "5", "m", artikel=artikel)
    _kg(szenario, order)

    abrechnung_service.rechnung_aus_auftrag(
        szenario["user"].id, work_order_id=order.id, tax_code="DE_19"
    )
    with pytest.raises(AbrechnungError, match="nichts abzurechnen"):
        abrechnung_service.rechnung_aus_auftrag(
            szenario["user"].id, work_order_id=order.id, tax_code="DE_19"
        )


@pytest.mark.django_db
def test_db_sperrt_die_zweite_materialbindung(szenario):
    """Am Service vorbei, direkt über das ORM: die DATENBANK weist es ab.

    Der Beweis, dass die Sperre für die neue Quelle **physisch** ist. Wäre sie nur
    eine Service-Regel, genügte ein zweiter Schreibpfad (KI-Agent, Skript,
    künftiger Endpunkt), um sie zu umgehen.
    """
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG", billing_mode="REGIE")
    artikel = _artikel(szenario, "MB-5", vk="12.00")
    job = _job(szenario, order)
    eintrag = _material(szenario, job, "Rohr", "5", "m", artikel=artikel)
    _kg(szenario, order)

    invoice = abrechnung_service.rechnung_aus_auftrag(
        szenario["user"].id, work_order_id=order.id, tax_code="DE_19"
    )
    line = invoice.lines.get()
    with pytest.raises(IntegrityError):
        with business_transaction(szenario["user"].id):
            BillingLink.objects.create(
                id=uuid.uuid4(),
                invoice_id=invoice.id,
                invoice_line_id=line.id,
                source_kind="MATERIALBUCHUNG",
                material_entry_id=eintrag.id,
            )


@pytest.mark.django_db
def test_storno_gibt_die_materialbuchung_wieder_frei(szenario):
    """**Der ganze Grund für das Bindungs-Design** — jetzt auch für Material.

    Die veröffentlichte Rechnungsposition ist unveränderlich (B-21). Ohne die
    Freigabe durch den Storno wäre das gebuchte Material für immer verbrannt:
    nie wieder abrechenbar, obwohl der Beleg, der es abgerechnet hat, aufgehoben
    ist. Es wurde ja verbaut.
    """
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG", billing_mode="REGIE")
    artikel = _artikel(szenario, "MB-6", vk="20.00")
    job = _job(szenario, order)
    eintrag = _material(szenario, job, "Ventil", "3", "Stk", artikel=artikel)
    _kg(szenario, order)

    invoice = abrechnung_service.rechnung_aus_auftrag(
        szenario["user"].id, work_order_id=order.id, tax_code="DE_19"
    )
    _beteiligte(szenario, invoice)
    beleg_service.publish_invoice(szenario["user"].id, invoice_id=invoice.id)

    storno = beleg_service.create_cancellation(
        szenario["user"].id, invoice_id=invoice.id
    )
    link = BillingLink.objects.get(invoice_id=invoice.id)
    assert link.released_at is not None
    assert storno.invoice_number in link.released_reason

    neu = abrechnung_service.rechnung_aus_auftrag(
        szenario["user"].id, work_order_id=order.id, tax_code="DE_19"
    )
    assert neu.id != invoice.id
    assert neu.net_total == Decimal("60.00")
    assert BillingLink.objects.filter(
        invoice_id=neu.id, material_entry_id=eintrag.id, released_at__isnull=True
    ).count() == 1


# ---------------------------------------------------------------------------
# 3. Nie 0,00 €, nie stillschweigend weglassen
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_material_ohne_artikel_geht_in_die_klaerung(szenario):
    """Die Freitextbuchung ist der Bestand — und darf weder 0 € noch stumm sein.

    Bis Migration 0139 konnte die Buchung gar keinen Artikel tragen; jede alte
    Zeile ist Freitext. Sie verschwindet nicht aus der Rechnung und geht auch
    nicht mit 0,00 € durch, sondern bekommt einen **eigenen** Grund
    (`MATERIAL_OHNE_ARTIKEL`) — der Weg heraus ist ein anderer als bei einem
    fehlenden Einkaufspreis.
    """
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG", billing_mode="REGIE")
    job = _job(szenario, order)
    eintrag = _material(szenario, job, "Dichtung aus dem Fahrzeug", "2", "Stk")
    _kg(szenario, order)

    with pytest.raises(PreisUnbekannt) as exc:
        abrechnung_service.rechnung_aus_auftrag(
            szenario["user"].id, work_order_id=order.id, tax_code="DE_19"
        )
    (pos,) = exc.value.positionen
    assert pos["quelle_art"] == "MATERIALBUCHUNG"
    assert pos["quelle_id"] == eintrag.id
    assert pos["grund"] == "MATERIAL_OHNE_ARTIKEL"
    assert pos["menge"] == Decimal("2.000")
    assert pos["einheit"] == "Stk"
    assert "0,00" in pos["grund_text"]
    # Und: NICHTS wurde geschrieben — keine stille 0-€-Rechnung im Hintergrund.
    assert not BillingLink.objects.filter(material_entry_id=eintrag.id).exists()

    # Der Ausweg: Der Mensch nennt den Preis. Derselbe Aufruf, ein Feld mehr.
    invoice = abrechnung_service.rechnung_aus_auftrag(
        szenario["user"].id, work_order_id=order.id, tax_code="DE_19",
        preise={str(eintrag.id): Decimal("4.20")},
    )
    line = invoice.lines.get()
    assert line.unit_price == Decimal("4.20")
    assert line.net_amount == Decimal("8.40")


@pytest.mark.django_db
def test_artikel_ohne_ermittelbaren_vk_geht_in_die_klaerung(szenario):
    """Kein VK im Stamm → Klärung mit Vorschlägen, keine 0-€-Position."""
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG", billing_mode="REGIE")
    artikel = _artikel(szenario, "MB-7")          # ohne Festpreis, ohne EK
    job = _job(szenario, order)
    eintrag = _material(szenario, job, "Rohr", "6", "m", artikel=artikel)
    _kg(szenario, order)

    with pytest.raises(PreisUnbekannt) as exc:
        abrechnung_service.rechnung_aus_auftrag(
            szenario["user"].id, work_order_id=order.id, tax_code="DE_19"
        )
    (pos,) = exc.value.positionen
    assert pos["quelle_id"] == eintrag.id
    assert pos["grund"] in ("EK_FEHLT", "KEINE_VK_REGEL", "KEINE_HERKUNFT")
    assert pos["grund"] != "MATERIAL_OHNE_ARTIKEL"


@pytest.mark.django_db
def test_null_euro_aus_dem_import_ist_kein_preis(szenario):
    """0,00 € aus einem 0-EK ist eine Lücke, kein günstiger Preis.

    Dieselbe Grenze wie bei der Berichtsposition (`_ist_preis`): Die VK-Gruppe
    rechnet ihre Formel brav auf der 0-Basis durch und liefert eine Zahl, die wie
    ein Preis aussieht. Sie darf die Materialbuchung nicht auf die Rechnung lassen.
    """
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG", billing_mode="REGIE")
    artikel = _artikel_mit_vk_gruppe(szenario, "MB-8", "0.00")
    job = _job(szenario, order)
    eintrag = _material(szenario, job, "Importrohr", "10", "m", artikel=artikel)
    _kg(szenario, order)

    with pytest.raises(PreisUnbekannt) as exc:
        abrechnung_service.rechnung_aus_auftrag(
            szenario["user"].id, work_order_id=order.id, tax_code="DE_19"
        )
    (pos,) = exc.value.positionen
    assert pos["quelle_id"] == eintrag.id
    assert pos["grund"] == "VK_NULL"


# ---------------------------------------------------------------------------
# 4. Dieselbe Schraube nicht doppelt (der 178,50-€-Fall)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_dieselbe_schraube_im_bericht_UND_als_buchung_wird_abgewiesen(
    szenario, fake_storage
):
    """Der reproduzierte Doppelabrechnungsfall — jetzt für die neue Quelle.

    Der Monteur hat zwei „Material"-Formulare vor sich. Trägt er dieselbe Schraube
    in beide ein, sind das für die vier UNIQUE-Indizes **zwei verschiedene
    Quellen** — sie können per Konstruktion nicht sehen, dass es dieselbe Schraube
    ist. Die Klammer ist der Auftrag; dort greift die Sperre.
    """
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG", billing_mode="REGIE")
    artikel = _artikel(szenario, "MB-9", vk="17.85", beschreibung="Schraube M8")
    job = _job(szenario, order)
    _bericht(szenario, order, [{
        "line_type": "MATERIAL", "description": "Schraube M8",
        "quantity": "10", "unit": "Stk", "source_article_id": str(artikel.id),
    }])
    _material(szenario, job, "Schraube M8", "10", "Stk", artikel=artikel)
    _kg(szenario, order)

    with pytest.raises(AbrechnungError, match="ZWEIMAL erfasst"):
        abrechnung_service.rechnung_aus_auftrag(
            szenario["user"].id, work_order_id=order.id, tax_code="DE_19"
        )

    # Der Ausweg ist eine ENTSCHEIDUNG, keine Vermutung des Servers: Ein Mensch
    # sagt, welche der beiden Erfassungen die Wahrheit ist.
    invoice = abrechnung_service.rechnung_aus_auftrag(
        szenario["user"].id, work_order_id=order.id, tax_code="DE_19",
        mit_material=False,
    )
    assert invoice.net_total == Decimal("178.50")
    assert BillingLink.objects.filter(
        invoice_id=invoice.id, source_kind="BERICHTSPOSITION"
    ).count() == 1
    assert not BillingLink.objects.filter(
        invoice_id=invoice.id, source_kind="MATERIALBUCHUNG"
    ).exists()


@pytest.mark.django_db
def test_sperre_greift_auch_ueber_ZWEI_rechnungen(szenario, fake_storage):
    """Der teure Fall: Bericht in RE-1, Materialbuchung in RE-2.

    Jede Rechnung für sich sauber gebunden, für die UNIQUE-Indizes unsichtbar —
    exakt das Muster, das 178,50 € auf zwei Rechnungen brachte. Die Sperre fragt
    deshalb den **Auftrag**, nicht den Lauf.
    """
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG", billing_mode="REGIE")
    artikel = _artikel(szenario, "MB-10", vk="17.85", beschreibung="Schraube M8")
    job = _job(szenario, order)
    _bericht(szenario, order, [{
        "line_type": "MATERIAL", "description": "Schraube M8",
        "quantity": "10", "unit": "Stk", "source_article_id": str(artikel.id),
    }])
    _material(szenario, job, "Schraube M8", "10", "Stk", artikel=artikel)
    _kg(szenario, order)

    erste = abrechnung_service.rechnung_aus_auftrag(
        szenario["user"].id, work_order_id=order.id, tax_code="DE_19",
        mit_material=False,
    )
    assert erste.net_total == Decimal("178.50")

    # Zweiter Anlauf, jetzt nur das Material: Es ist dieselbe Schraube.
    with pytest.raises(AbrechnungError, match="ZWEIMAL erfasst"):
        abrechnung_service.rechnung_aus_auftrag(
            szenario["user"].id, work_order_id=order.id, tax_code="DE_19",
            mit_berichten=False,
        )


@pytest.mark.django_db
def test_abweichende_einheit_ist_fail_closed(szenario, fake_storage):
    """„Stk" im Bericht, „Stück" in der Buchung — derselbe Artikel.

    Die Identität enthält die Einheit **bewusst nicht** (`_identitaet`); sonst
    zerfiele der Posten in zwei Schlüssel und die Sperre sähe die Gegenseite nicht.
    Divergieren die Einheiten, sind die Mengen nicht vergleichbar — dann entscheidet
    ein Mensch (422 mit `einheit_uneindeutig`), statt still zu summieren oder still
    durchzulassen.
    """
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG", billing_mode="REGIE")
    artikel = _artikel(szenario, "MB-11", vk="17.85", beschreibung="Schraube M8")
    job = _job(szenario, order)
    _bericht(szenario, order, [{
        "line_type": "MATERIAL", "description": "Schraube M8",
        "quantity": "10", "unit": "Stk", "source_article_id": str(artikel.id),
    }])
    _material(szenario, job, "Schraube M8", "10", "Stück", artikel=artikel)
    _kg(szenario, order)

    with pytest.raises(EinheitUneindeutig) as exc:
        abrechnung_service.rechnung_aus_auftrag(
            szenario["user"].id, work_order_id=order.id, tax_code="DE_19"
        )
    (konflikt,) = exc.value.konflikte
    assert konflikt["identitaet"] == f"ARTIKEL:{artikel.id}"
    assert konflikt["einheiten"] == ["stk", "stück"]


@pytest.mark.django_db
def test_verschiedene_artikel_stoeren_sich_nicht(szenario, fake_storage):
    """Die Sperre ist scharf, aber nicht blind: zwei Artikel sind zwei Posten.

    Ein Wächter, der auch den Normalfall abweist, wird nach der dritten
    Fehlermeldung umgangen — deshalb der Gegenbeweis.
    """
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG", billing_mode="REGIE")
    rohr = _artikel(szenario, "MB-12", vk="15.00", beschreibung="Rohr DN20")
    ventil = _artikel(szenario, "MB-13", vk="80.00", beschreibung="Ventil")
    job = _job(szenario, order)
    _bericht(szenario, order, [{
        "line_type": "MATERIAL", "description": "Rohr DN20",
        "quantity": "10", "unit": "m", "source_article_id": str(rohr.id),
    }])
    _material(szenario, job, "Ventil", "2", "Stk", artikel=ventil)
    _kg(szenario, order)

    invoice = abrechnung_service.rechnung_aus_auftrag(
        szenario["user"].id, work_order_id=order.id, tax_code="DE_19"
    )
    assert invoice.net_total == Decimal("310.00")
    assert BillingLink.objects.filter(
        invoice_id=invoice.id, source_kind="MATERIALBUCHUNG"
    ).count() == 1
    assert BillingLink.objects.filter(
        invoice_id=invoice.id, source_kind="BERICHTSPOSITION"
    ).count() == 1


@pytest.mark.django_db
def test_material_und_zeiten_zusammen(szenario):
    """Material und Arbeitszeit sind verschiedene Posten — kein Konflikt."""
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG", billing_mode="REGIE")
    artikel = _artikel(szenario, "MB-14", vk="15.00")
    job = _job(szenario, order)
    monteur = _monteur(szenario, "Anton", stundensatz="65.00")
    _zeit(szenario, monteur, job, von=T0, bis=T0 + timedelta(hours=2))
    _material(szenario, job, "Rohr", "4", "m", artikel=artikel)
    _kg(szenario, order)

    invoice = abrechnung_service.rechnung_aus_auftrag(
        szenario["user"].id, work_order_id=order.id, tax_code="DE_19"
    )
    # 4 × 15,00 + 2 × 65,00
    assert invoice.net_total == Decimal("190.00")
    assert BillingLink.objects.filter(
        invoice_id=invoice.id, source_kind="MATERIALBUCHUNG"
    ).count() == 1
    assert BillingLink.objects.filter(
        invoice_id=invoice.id, source_kind="ZEITBUCHUNG"
    ).count() == 1


# ---------------------------------------------------------------------------
# Tore, die scharf bleiben
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_pauschal_fakturiert_kein_material(szenario):
    """Bei PAUSCHAL ist die Materialbuchung Nachweis, kein Rechnungsposten."""
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG")     # Default PAUSCHAL
    artikel = _artikel(szenario, "MB-15", vk="15.00")
    job = _job(szenario, order)
    _material(szenario, job, "Rohr", "4", "m", artikel=artikel)
    _kg(szenario, order)

    offen = abrechnung_service.offene_abrechnung(order.id)
    assert offen["abrechenbar"] is False
    assert len(offen["materialbuchungen"]) == 1
    with pytest.raises(AbrechnungError, match="PAUSCHAL"):
        abrechnung_service.rechnung_aus_auftrag(
            szenario["user"].id, work_order_id=order.id, tax_code="DE_19"
        )


@pytest.mark.django_db
def test_b28_sperrt_die_nachtraegliche_artikelzuordnung(szenario):
    """Eine Artikelzuordnung ist eine INHALTLICHE Änderung — sie entscheidet den Preis.

    Der Korrekturtrigger `workflow.guard_entry_correction` kennt keine Spaltenliste
    und deckt die neue Spalte deshalb automatisch ab. Genau das wird hier
    nachgewiesen — sonst ließe sich nach der kaufmännischen Prüfung noch der Artikel
    (und damit der Preis) tauschen.
    """
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG", billing_mode="REGIE")
    artikel = _artikel(szenario, "MB-16", vk="15.00")
    job = _job(szenario, order)
    eintrag = _material(szenario, job, "Rohr", "4", "m")

    # Vor der kaufmännischen Prüfung: erlaubt.
    with business_transaction(szenario["user"].id):
        MaterialEntry.objects.filter(id=eintrag.id).update(
            source_article_id=artikel.id
        )
    eintrag.refresh_from_db()
    assert eintrag.source_article_id == artikel.id

    _kg(szenario, order)
    teuer = _artikel(szenario, "MB-17", vk="9999.00")
    # Danach: das Tor ist zu. Der Trigger meldet ProgrammingError, nicht
    # InternalError — deshalb breit gefangen und am Text festgemacht.
    with pytest.raises(Exception) as exc:
        with business_transaction(szenario["user"].id):
            MaterialEntry.objects.filter(id=eintrag.id).update(
                source_article_id=teuer.id
            )
    assert "B-28" in str(exc.value)
    eintrag.refresh_from_db()
    assert eintrag.source_article_id == artikel.id


@pytest.mark.django_db
def test_billing_link_erzwingt_genau_eine_quelle(szenario):
    """Vier Quellspalten, aber immer nur EINE gesetzt (CHECK aus 0139).

    Ohne diesen CHECK könnte eine Zeile behaupten, sie binde eine Zeitbuchung, und
    dabei eine Materialbuchung referenzieren — die Sperre wäre eine Zierde.
    """
    order = _auftrag(szenario, bis="IN_AUSFUEHRUNG", billing_mode="REGIE")
    artikel = _artikel(szenario, "MB-18", vk="12.00")
    job = _job(szenario, order)
    eintrag = _material(szenario, job, "Rohr", "5", "m", artikel=artikel)
    _kg(szenario, order)
    invoice = abrechnung_service.rechnung_aus_auftrag(
        szenario["user"].id, work_order_id=order.id, tax_code="DE_19"
    )
    line = invoice.lines.get()

    # Falsche Art zur gesetzten Quelle.
    with pytest.raises(IntegrityError):
        with business_transaction(szenario["user"].id):
            BillingLink.objects.create(
                id=uuid.uuid4(), invoice_id=invoice.id, invoice_line_id=line.id,
                source_kind="ZEITBUCHUNG", material_entry_id=eintrag.id,
            )
    # Gar keine Quelle.
    with pytest.raises(IntegrityError):
        with business_transaction(szenario["user"].id):
            BillingLink.objects.create(
                id=uuid.uuid4(), invoice_id=invoice.id, invoice_line_id=line.id,
                source_kind="MATERIALBUCHUNG",
            )


@pytest.mark.django_db
def test_materialbuchung_traegt_keinen_preis(db):
    """Sicherheitsnetz gegen die naheliegende „Verbesserung".

    Der ausführliche Schema-Nachweis steht in `test_berichtspositionen.py`
    (`test_erfassung_fuehrt_keine_geldspalte`); hier steht er, weil er zum
    Verständnis dieses Slices gehört: Die Buchung liefert die MENGE, den PREIS
    macht das Belegwesen. Ein Preis, den der Monteur auf der Baustelle nennt, wäre
    eine Preisvereinbarung.
    """
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'workflow' AND table_name = 'material_entry'
            """
        )
        spalten = {r[0] for r in cur.fetchall()}
    assert "source_article_id" in spalten
    assert not (spalten & {"unit_price", "unit_cost", "net_amount", "list_price"})
