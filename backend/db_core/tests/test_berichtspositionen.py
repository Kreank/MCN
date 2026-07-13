"""Berichtspositionen und Soll-Ist-Abgleich (workflow.site_report_line, 0080).

Der Kern des Slices: Der Baustellenbericht führt Positionen aus dem Artikel-/
Leistungsstamm (Menge, Einheit — **nie Preise**), und daraus entsteht ein
beweisbarer Soll-Ist-Abgleich gegen das Angebot.

Was hier scharf geprüft wird:

* **Die Versiegelung ist ein DB-TRIGGER, nicht nur eine Service-Regel.** Die Tests
  `test_trigger_*` gehen bewusst am Service vorbei und schreiben direkt über das
  ORM. Ein Bericht, dessen Positionen sich nach der Kundenunterschrift noch
  ändern lassen, wäre kein Nachweis, sondern ein Vorschlag.
* **Der Bericht führt keine Preise** — auch nicht als Spalte „für später"
  (`test_positionstabelle_hat_keine_geldspalte` prüft das Schema selbst).
* **Soll ≠ planned_quantity.** Der Abgleich zieht das Soll aus den
  Angebotspositionen; käme es aus `planned_quantity`, fehlte der Fall ENTFALLEN
  (eine nie eingebaute Leistung hat keine Berichtsposition).
"""
import uuid
from decimal import Decimal

import pytest
from django.db import Error, connection

from db_core.db_context import business_transaction
from db_core.models import Article, Quote, QuoteLine, SiteReportLine
from db_core.services import artikel as artikel_service
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import property as property_service
from db_core.services import site_report as report_service
from db_core.services.site_report import SiteReportError

PNG_1x1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
    b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


# --- Hilfen ----------------------------------------------------------------

class FakeStorage:
    """Der Objektspeicher ist für diesen Slice Nebensache — nur die Unterschrift
    braucht ihn (sie besiegelt den Bericht)."""

    def ensure_bucket(self):
        pass

    def put_object(self, key, data, content_type="application/octet-stream"):
        return None

    def get_object(self, key):
        raise KeyError(key)

    def remove_object(self, key):
        pass


@pytest.fixture
def fake_storage(monkeypatch):
    from db_core import storage as storage_module

    monkeypatch.setattr(storage_module, "get_storage", lambda: FakeStorage())


def _property(actor, name="Baustelle"):
    return property_service.create_property(
        actor.id, name=name, property_type="WEG",
        street="Weg", house_number="1", postal_code="10115", city="Berlin",
    )


def _auftrag(actor, obj=None, titel="Bad sanieren"):
    obj = obj or _property(actor)
    return auftrag_service.create_work_order(
        actor.id, property_id=obj.id, title=titel
    )


def _bericht(actor, auftrag):
    return report_service.create_report(
        actor.id, work_order_id=auftrag.id, report_date="2026-07-13",
        activity_text="Fliesen verlegt.",
    )


def _artikel(actor, nummer, bezeichnung="Rohr DN20", einheit="m"):
    return artikel_service.create_article(
        actor.id, article_number=nummer, description=bezeichnung, unit=einheit,
        line_type="MATERIAL", list_price=Decimal("4.50"),
    )


def _angebot_am_auftrag(actor, auftrag, lines, *, status=None, versenden=True):
    """Ein Angebot, das dem Auftrag zugeordnet ist — **über den Produktweg**.

    Der Bezug entsteht ausschließlich über `create_quote(work_order_id=…)`; das ist
    derselbe Weg, den die API geht. (Ein Test, der den Bezug per Raw-ORM setzt,
    bewiese nur, dass die Spalte existiert — nicht, dass das Produkt sie füllen
    kann.)

    Versendet wird per Default: ein Angebot im ENTWURF ist keine Vereinbarung und
    bildet **kein** Soll (`SOLL_AUSGESCHLOSSENE_STATUS`).
    """
    quote = beleg_service.create_quote(
        actor.id, property_id=auftrag.property_id, title="Angebot Bad",
        work_order_id=auftrag.id, lines=lines,
    )
    if versenden or status:
        beleg_service.send_quote(actor.id, quote_id=quote.id)
    if status:
        # Der Statusautomat lässt ENTWURF → ABGELEHNT nicht zu — der Weg führt
        # über den Versand (B-15). Genau so entstünde der Fall auch im Betrieb.
        with business_transaction(actor.id, status_reason="Testfall"):
            Quote.objects.filter(id=quote.id).update(status=status)
    quote.refresh_from_db()
    return quote


def _mat(desc, qty, *, unit="m", price="10.00", kind="NORMAL", article_id=None):
    zeile = {
        "line_type": "MATERIAL", "description": desc, "quantity": qty,
        "unit": unit, "unit_price": price, "tax_code": "DE_19",
        "line_kind": kind,
    }
    if article_id:
        zeile["source_article_id"] = article_id
    return zeile


# --- Die Invariante: keine Preise ------------------------------------------

@pytest.mark.django_db
def test_positionstabelle_hat_keine_geldspalte(db):
    """INVARIANTE: Der Bericht führt keine Preise — auch nicht „für später".

    Ein vom Kunden unterschriebener Bericht mit Preisen wäre eine
    Preisvereinbarung. Deshalb darf die Tabelle gar keine Geldspalte haben.
    """
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'workflow' AND table_name = 'site_report_line'
            """
        )
        spalten = {r[0] for r in cur.fetchall()}
    verboten = {
        "unit_price", "net_amount", "gross_amount", "price", "amount",
        "discount_percent", "tax_code", "tax_rate_percent", "unit_cost",
        "markup_percent", "list_price",
    }
    assert not (spalten & verboten), f"Geldspalte am Bericht: {spalten & verboten}"
    # Und die Codeliste kennt keine ZWISCHENSUMME (der Bericht summiert nichts).
    assert "ZWISCHENSUMME" not in report_service.BERICHT_LINE_TYPES


# --- Positionen im ENTWURF --------------------------------------------------

@pytest.mark.django_db
def test_positionen_anlegen(app_user):
    auftrag = _auftrag(app_user)
    bericht = _bericht(app_user, auftrag)
    artikel = _artikel(app_user, "A-100")

    lines = report_service.set_report_lines(
        app_user.id, report_id=bericht.id,
        lines=[
            {"line_type": "MATERIAL", "source_article_id": str(artikel.id),
             "quantity": "12.5"},
            {"line_type": "ARBEITSZEIT", "description": "Montage",
             "quantity": "3", "unit": "h"},
            {"line_type": "TEXT", "description": "Hinweis: Altbestand feucht."},
        ],
    )
    assert [l.position_number for l in lines] == [1, 2, 3]
    # Bezeichnung und Einheit werden aus dem Stamm KOPIERT (kein Verweis).
    assert lines[0].description == "Rohr DN20"
    assert lines[0].unit == "m"
    assert lines[0].quantity == Decimal("12.500")
    assert lines[0].source_article_id == artikel.id
    # Die Textzeile trägt keine Menge (DB-CHECK).
    assert lines[2].quantity is None and lines[2].unit is None


@pytest.mark.django_db
def test_positionen_ersetzen_und_neu_nummerieren(app_user):
    auftrag = _auftrag(app_user)
    bericht = _bericht(app_user, auftrag)
    report_service.set_report_lines(
        app_user.id, report_id=bericht.id,
        lines=[
            {"line_type": "MATERIAL", "description": "Alt A", "quantity": "1",
             "unit": "Stk"},
            {"line_type": "MATERIAL", "description": "Alt B", "quantity": "2",
             "unit": "Stk"},
        ],
    )
    lines = report_service.set_report_lines(
        app_user.id, report_id=bericht.id,
        lines=[{"line_type": "MATERIAL", "description": "Neu", "quantity": "9",
                "unit": "Stk"}],
    )
    assert [l.description for l in lines] == ["Neu"]
    assert lines[0].position_number == 1
    assert SiteReportLine.objects.filter(site_report_id=bericht.id).count() == 1


@pytest.mark.django_db
def test_leere_liste_raeumt_die_positionen_ab(app_user):
    auftrag = _auftrag(app_user)
    bericht = _bericht(app_user, auftrag)
    report_service.set_report_lines(
        app_user.id, report_id=bericht.id,
        lines=[{"line_type": "MATERIAL", "description": "X", "quantity": "1",
                "unit": "Stk"}],
    )
    assert list(report_service.set_report_lines(
        app_user.id, report_id=bericht.id, lines=[]
    )) == []


@pytest.mark.django_db
def test_inaktiver_artikel_wird_abgelehnt(app_user):
    auftrag = _auftrag(app_user)
    bericht = _bericht(app_user, auftrag)
    artikel = _artikel(app_user, "A-INAKTIV")
    artikel_service.set_article_status(
        app_user.id, article_id=artikel.id, status="INAKTIV"
    )
    with pytest.raises(SiteReportError):
        report_service.set_report_lines(
            app_user.id, report_id=bericht.id,
            lines=[{"line_type": "MATERIAL", "source_article_id": str(artikel.id),
                    "quantity": "1"}],
        )


@pytest.mark.django_db
def test_unbekannter_artikel_wird_abgelehnt(app_user):
    auftrag = _auftrag(app_user)
    bericht = _bericht(app_user, auftrag)
    with pytest.raises(ValueError):
        report_service.set_report_lines(
            app_user.id, report_id=bericht.id,
            lines=[{"line_type": "MATERIAL", "source_article_id": str(uuid.uuid4()),
                    "quantity": "1"}],
        )


@pytest.mark.django_db
def test_menge_und_einheit_sind_pflicht(app_user):
    auftrag = _auftrag(app_user)
    bericht = _bericht(app_user, auftrag)
    with pytest.raises(SiteReportError):
        report_service.set_report_lines(
            app_user.id, report_id=bericht.id,
            lines=[{"line_type": "MATERIAL", "description": "Ohne Menge",
                    "unit": "Stk"}],
        )
    with pytest.raises(SiteReportError):
        report_service.set_report_lines(
            app_user.id, report_id=bericht.id,
            lines=[{"line_type": "MATERIAL", "description": "Ohne Einheit",
                    "quantity": "1"}],
        )


@pytest.mark.django_db
def test_textzeile_traegt_keine_menge(app_user):
    auftrag = _auftrag(app_user)
    bericht = _bericht(app_user, auftrag)
    with pytest.raises(SiteReportError):
        report_service.set_report_lines(
            app_user.id, report_id=bericht.id,
            lines=[{"line_type": "TEXT", "description": "Hinweis",
                    "quantity": "1", "unit": "Stk"}],
        )


@pytest.mark.django_db
def test_artikel_und_leistung_schliessen_sich_aus(app_user):
    auftrag = _auftrag(app_user)
    bericht = _bericht(app_user, auftrag)
    artikel = _artikel(app_user, "A-200")
    leistung = artikel_service.create_assembly(
        app_user.id, assembly_number="L-1", name="Bad komplett", unit="psch"
    )
    with pytest.raises(SiteReportError):
        report_service.set_report_lines(
            app_user.id, report_id=bericht.id,
            lines=[{"line_type": "PAUSCHALE", "quantity": "1",
                    "source_article_id": str(artikel.id),
                    "source_assembly_id": str(leistung.id)}],
        )


# --- Der unterzeichnete Bericht ist versiegelt (TRIGGER!) -------------------

@pytest.mark.django_db
def test_unterzeichneter_bericht_service_lehnt_positionen_ab(app_user, fake_storage):
    auftrag = _auftrag(app_user)
    bericht = _bericht(app_user, auftrag)
    report_service.sign_report(
        app_user.id, report_id=bericht.id, signed_by_name="Klara",
        signature_png=PNG_1x1,
    )
    with pytest.raises(SiteReportError):
        report_service.set_report_lines(
            app_user.id, report_id=bericht.id,
            lines=[{"line_type": "MATERIAL", "description": "Nachtrag",
                    "quantity": "1", "unit": "Stk"}],
        )
    with pytest.raises(SiteReportError):
        report_service.vorbelegen_aus_angebot(
            app_user.id, report_id=bericht.id, quote_id=uuid.uuid4()
        )


@pytest.mark.django_db
def test_trigger_insert_am_unterzeichneten_bericht(app_user, fake_storage):
    """Am Service vorbei: der DB-Trigger ist die letzte Instanz."""
    auftrag = _auftrag(app_user)
    bericht = _bericht(app_user, auftrag)
    report_service.sign_report(
        app_user.id, report_id=bericht.id, signed_by_name="Klara",
        signature_png=PNG_1x1,
    )
    with pytest.raises(Error):
        with business_transaction(app_user.id):
            SiteReportLine.objects.create(
                id=uuid.uuid4(), site_report_id=bericht.id, position_number=1,
                line_type="MATERIAL", description="Direkt eingeschleust",
                quantity=Decimal("1.000"), unit="Stk",
            )


@pytest.mark.django_db
def test_trigger_update_und_delete_am_unterzeichneten_bericht(app_user, fake_storage):
    auftrag = _auftrag(app_user)
    bericht = _bericht(app_user, auftrag)
    report_service.set_report_lines(
        app_user.id, report_id=bericht.id,
        lines=[{"line_type": "MATERIAL", "description": "Rohr", "quantity": "10",
                "unit": "m"}],
    )
    report_service.sign_report(
        app_user.id, report_id=bericht.id, signed_by_name="Klara",
        signature_png=PNG_1x1,
    )
    zeile = SiteReportLine.objects.get(site_report_id=bericht.id)

    with pytest.raises(Error):
        with business_transaction(app_user.id):
            SiteReportLine.objects.filter(id=zeile.id).update(
                quantity=Decimal("99.000")
            )
    with pytest.raises(Error):
        with business_transaction(app_user.id):
            SiteReportLine.objects.filter(id=zeile.id).delete()

    zeile.refresh_from_db()
    assert zeile.quantity == Decimal("10.000")


@pytest.mark.django_db
def test_trigger_prueft_auch_das_wegbewegen(app_user, fake_storage):
    """OLD und NEW getrennt: ein UPDATE, das eine Position von einem
    unterzeichneten Bericht WEGbewegt, wirkte sonst wie ein DELETE."""
    auftrag = _auftrag(app_user)
    signiert = _bericht(app_user, auftrag)
    entwurf = _bericht(app_user, auftrag)
    report_service.set_report_lines(
        app_user.id, report_id=signiert.id,
        lines=[{"line_type": "MATERIAL", "description": "Rohr", "quantity": "10",
                "unit": "m"}],
    )
    report_service.sign_report(
        app_user.id, report_id=signiert.id, signed_by_name="Klara",
        signature_png=PNG_1x1,
    )
    zeile = SiteReportLine.objects.get(site_report_id=signiert.id)
    with pytest.raises(Error):
        with business_transaction(app_user.id):
            SiteReportLine.objects.filter(id=zeile.id).update(
                site_report_id=entwurf.id
            )
    zeile.refresh_from_db()
    assert zeile.site_report_id == signiert.id


@pytest.mark.django_db
def test_positionen_im_entwurf_loeschbar(app_user):
    """Bewusste Ausnahme vom Schutzstandard: KEIN No-Delete-Trigger. Der Editor
    ersetzt den ganzen Positionssatz — ein Löschverbot machte das unmöglich."""
    auftrag = _auftrag(app_user)
    bericht = _bericht(app_user, auftrag)
    report_service.set_report_lines(
        app_user.id, report_id=bericht.id,
        lines=[{"line_type": "MATERIAL", "description": "Irrtum", "quantity": "1",
                "unit": "Stk"}],
    )
    with business_transaction(app_user.id):
        SiteReportLine.objects.filter(site_report_id=bericht.id).delete()
    assert not SiteReportLine.objects.filter(site_report_id=bericht.id).exists()


# --- Vorbelegung aus dem Angebot -------------------------------------------

@pytest.mark.django_db
def test_vorbelegen_uebernimmt_soll_als_ist(app_user):
    auftrag = _auftrag(app_user)
    bericht = _bericht(app_user, auftrag)
    artikel = _artikel(app_user, "A-300")
    angebot = _angebot_am_auftrag(app_user, auftrag, [
        _mat("Rohr DN20", "12", article_id=str(artikel.id)),
        _mat("Dichtung", "4", unit="Stk"),
    ])

    lines = report_service.vorbelegen_aus_angebot(
        app_user.id, report_id=bericht.id, quote_id=angebot.id
    )
    assert len(lines) == 2
    erste = lines[0]
    assert erste.quantity == Decimal("12.000")
    assert erste.planned_quantity == Decimal("12.000")  # Ist startet gleich dem Soll
    assert erste.source_quote_line_id is not None
    assert erste.source_article_id == artikel.id
    assert erste.unit == "m"
    assert erste.line_type == "MATERIAL"


@pytest.mark.django_db
def test_vorbelegen_ueberspringt_alternativ_bedarf_und_text(app_user):
    auftrag = _auftrag(app_user)
    bericht = _bericht(app_user, auftrag)
    angebot = _angebot_am_auftrag(app_user, auftrag, [
        _mat("Standardvariante", "5", unit="Stk"),
        _mat("Ausweichvariante", "5", unit="Stk", kind="ALTERNATIV"),
        _mat("Eventualposition", "3", unit="Stk", kind="BEDARF"),
        {"line_type": "TEXT", "description": "Vorbemerkung"},
    ])
    lines = report_service.vorbelegen_aus_angebot(
        app_user.id, report_id=bericht.id, quote_id=angebot.id
    )
    assert [l.description for l in lines] == ["Standardvariante"]
    assert lines[0].position_number == 1


@pytest.mark.django_db
def test_zweite_vorbelegung_auf_befuellten_bericht_scheitert(app_user):
    auftrag = _auftrag(app_user)
    bericht = _bericht(app_user, auftrag)
    angebot = _angebot_am_auftrag(app_user, auftrag, [_mat("Rohr", "12")])
    report_service.vorbelegen_aus_angebot(
        app_user.id, report_id=bericht.id, quote_id=angebot.id
    )
    with pytest.raises(SiteReportError):
        report_service.vorbelegen_aus_angebot(
            app_user.id, report_id=bericht.id, quote_id=angebot.id
        )
    # Und die Arbeit des Monteurs steht noch.
    assert SiteReportLine.objects.filter(site_report_id=bericht.id).count() == 1


@pytest.mark.django_db
def test_fremdes_angebot_wird_abgelehnt(app_user):
    auftrag = _auftrag(app_user, titel="Unsere Baustelle")
    fremder = _auftrag(app_user, titel="Fremde Baustelle")
    bericht = _bericht(app_user, auftrag)
    fremdes_angebot = _angebot_am_auftrag(app_user, fremder, [_mat("Rohr", "12")])
    with pytest.raises(SiteReportError):
        report_service.vorbelegen_aus_angebot(
            app_user.id, report_id=bericht.id, quote_id=fremdes_angebot.id
        )


@pytest.mark.django_db
def test_fremde_angebotsposition_als_soll_wird_abgelehnt(app_user):
    """Auch von Hand lässt sich kein fremdes Soll unterschieben."""
    auftrag = _auftrag(app_user, titel="Unsere Baustelle")
    fremder = _auftrag(app_user, titel="Fremde Baustelle")
    bericht = _bericht(app_user, auftrag)
    fremdes_angebot = _angebot_am_auftrag(app_user, fremder, [_mat("Rohr", "12")])
    fremde_zeile = fremdes_angebot.lines.first()
    with pytest.raises(SiteReportError):
        report_service.set_report_lines(
            app_user.id, report_id=bericht.id,
            lines=[{"line_type": "MATERIAL", "description": "Rohr",
                    "quantity": "12", "unit": "m",
                    "source_quote_line_id": str(fremde_zeile.id)}],
        )


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["ALTERNATIV", "BEDARF"])
def test_nicht_beauftragte_angebotsposition_ist_kein_soll(app_user, kind):
    """ALTERNATIV (Ausweichvariante) und BEDARF (Eventualposition) wurden gerade
    NICHT vereinbart. Als Herkunft zugelassen, stünde ihre Menge als „Soll" auf
    einem vom Kunden unterschriebenen Nachweis — eine Vereinbarung, die es nie gab.
    Der Vorbeleger überspringt sie ohnehin; von Hand muss dieselbe Grenze gelten."""
    auftrag = _auftrag(app_user)
    bericht = _bericht(app_user, auftrag)
    angebot = _angebot_am_auftrag(app_user, auftrag, [
        _mat("Standardvariante", "5", unit="Stk"),
        _mat("Ausweichvariante", "5", unit="Stk", kind=kind),
    ])
    zeile = angebot.lines.get(line_kind=kind)
    with pytest.raises(SiteReportError):
        report_service.set_report_lines(
            app_user.id, report_id=bericht.id,
            lines=[{"line_type": "MATERIAL", "description": "Ausweichvariante",
                    "quantity": "5", "unit": "Stk",
                    "source_quote_line_id": str(zeile.id)}],
        )
    assert SiteReportLine.objects.filter(site_report_id=bericht.id).count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize("beleg_typ", ["TEXT", "ZWISCHENSUMME"])
def test_textzeile_des_angebots_ist_keine_herkunft(app_user, beleg_typ):
    """Eine TEXT-/ZWISCHENSUMME-Zeile trägt gar keine Menge (DB-CHECK). Als
    Herkunft ergäbe sie eine Berichtsposition mit Herkunft, aber ohne Soll — eine
    Zuordnung, die nichts aussagt."""
    auftrag = _auftrag(app_user)
    bericht = _bericht(app_user, auftrag)
    angebot = _angebot_am_auftrag(app_user, auftrag, [
        _mat("Rohr", "12"),
        {"line_type": beleg_typ, "description": "Vorbemerkung"},
    ])
    zeile = angebot.lines.get(line_type=beleg_typ)
    with pytest.raises(SiteReportError):
        report_service.set_report_lines(
            app_user.id, report_id=bericht.id,
            lines=[{"line_type": "MATERIAL", "description": "Rohr", "quantity": "12",
                    "unit": "m", "source_quote_line_id": str(zeile.id)}],
        )
    assert SiteReportLine.objects.filter(site_report_id=bericht.id).count() == 0


@pytest.mark.django_db
def test_soll_ueberlebt_das_zurueckschreiben_des_positionssatzes(app_user):
    """Der Editor schickt den Satz komplett zurück. Fehlt planned_quantity, holt
    der Service es aus der Herkunft — sonst ginge das Soll beim ersten Speichern
    verloren."""
    auftrag = _auftrag(app_user)
    bericht = _bericht(app_user, auftrag)
    angebot = _angebot_am_auftrag(app_user, auftrag, [_mat("Rohr", "12")])
    vorbelegt = report_service.vorbelegen_aus_angebot(
        app_user.id, report_id=bericht.id, quote_id=angebot.id
    )
    ql_id = vorbelegt[0].source_quote_line_id

    lines = report_service.set_report_lines(
        app_user.id, report_id=bericht.id,
        lines=[{"line_type": "MATERIAL", "description": "Rohr", "quantity": "14",
                "unit": "m", "source_quote_line_id": str(ql_id)}],
    )
    assert lines[0].quantity == Decimal("14.000")
    assert lines[0].planned_quantity == Decimal("12.000")


# --- Soll-Ist-Abgleich ------------------------------------------------------

def _art(ergebnis, bezeichnung):
    for p in ergebnis["positionen"]:
        if p["bezeichnung"] == bezeichnung:
            return p
    raise AssertionError(f"{bezeichnung} fehlt im Abgleich")


@pytest.mark.django_db
def test_soll_ist_alle_fuenf_faelle(app_user, fake_storage):
    auftrag = _auftrag(app_user)
    _angebot_am_auftrag(app_user, auftrag, [
        _mat("Rohr DN20", "12"),          # Ist 15 → MEHRVERBRAUCH
        _mat("Dichtung", "10", unit="Stk"),   # Ist 6  → MINDERVERBRAUCH
        _mat("Wandhalter", "4", unit="Stk"),  # Ist 4  → UNVERAENDERT
        _mat("Absperrhahn", "2", unit="Stk"),  # kein Ist → ENTFALLEN
    ])
    bericht = _bericht(app_user, auftrag)
    report_service.set_report_lines(
        app_user.id, report_id=bericht.id,
        lines=[
            {"line_type": "MATERIAL", "description": "Rohr DN20", "quantity": "15",
             "unit": "m"},
            {"line_type": "MATERIAL", "description": "Dichtung", "quantity": "6",
             "unit": "Stk"},
            {"line_type": "MATERIAL", "description": "Wandhalter", "quantity": "4",
             "unit": "Stk"},
            {"line_type": "MATERIAL", "description": "Notdichtung", "quantity": "1",
             "unit": "Stk"},  # kein Soll → ZUSATZ
            {"line_type": "TEXT", "description": "Kunde war anwesend."},
        ],
    )
    report_service.sign_report(
        app_user.id, report_id=bericht.id, signed_by_name="Klara",
        signature_png=PNG_1x1,
    )

    ergebnis = report_service.soll_ist(auftrag.id)
    assert ergebnis["enthaelt_entwuerfe"] is False

    mehr = _art(ergebnis, "Rohr DN20")
    assert (mehr["soll"], mehr["ist"], mehr["differenz"], mehr["art"]) == (
        Decimal("12.000"), Decimal("15.000"), Decimal("3.000"), "MEHRVERBRAUCH"
    )
    minder = _art(ergebnis, "Dichtung")
    assert minder["differenz"] == Decimal("-4.000")
    assert minder["art"] == "MINDERVERBRAUCH"
    assert _art(ergebnis, "Wandhalter")["art"] == "UNVERAENDERT"

    entfallen = _art(ergebnis, "Absperrhahn")
    assert entfallen["art"] == "ENTFALLEN"
    assert entfallen["ist"] == Decimal("0.000")

    zusatz = _art(ergebnis, "Notdichtung")
    assert zusatz["art"] == "ZUSATZ"
    assert zusatz["soll"] == Decimal("0.000")

    # Die Textzeile taucht im Abgleich nicht auf.
    assert all(p["bezeichnung"] != "Kunde war anwesend."
               for p in ergebnis["positionen"])
    # Und nirgends steht ein Geldbetrag.
    assert all(
        not (set(p) & {"preis", "betrag", "netto", "unit_price", "net_amount"})
        for p in ergebnis["positionen"]
    )


@pytest.mark.django_db
def test_soll_ist_aggregiert_ueber_zwei_berichte(app_user):
    auftrag = _auftrag(app_user)
    artikel = _artikel(app_user, "A-400")
    _angebot_am_auftrag(app_user, auftrag, [
        _mat("Rohr DN20", "20", article_id=str(artikel.id)),
    ])
    for menge in ("8", "15"):
        b = _bericht(app_user, auftrag)
        report_service.set_report_lines(
            app_user.id, report_id=b.id,
            lines=[{"line_type": "MATERIAL", "source_article_id": str(artikel.id),
                    "quantity": menge}],
        )

    ergebnis = report_service.soll_ist(auftrag.id)
    pos = _art(ergebnis, "Rohr DN20")
    assert pos["soll"] == Decimal("20.000")
    assert pos["ist"] == Decimal("23.000")  # 8 + 15
    assert pos["art"] == "MEHRVERBRAUCH"
    # Beide Berichte sind ENTWURF → das Ergebnis ist vorläufig. Nicht verschweigen.
    assert ergebnis["enthaelt_entwuerfe"] is True


@pytest.mark.django_db
def test_soll_ist_trifft_ueber_den_artikel_nicht_ueber_den_text(app_user):
    """Der Monteur präzisiert den Text („Rohr DN20, Steigstrang"). Über den
    Artikelbezug findet die Position ihr Soll trotzdem."""
    auftrag = _auftrag(app_user)
    artikel = _artikel(app_user, "A-500")
    _angebot_am_auftrag(app_user, auftrag, [
        _mat("Rohr DN20", "12", article_id=str(artikel.id)),
    ])
    bericht = _bericht(app_user, auftrag)
    report_service.set_report_lines(
        app_user.id, report_id=bericht.id,
        lines=[{"line_type": "MATERIAL", "source_article_id": str(artikel.id),
                "description": "Rohr DN20, Steigstrang", "quantity": "12"}],
    )
    ergebnis = report_service.soll_ist(auftrag.id)
    assert len(ergebnis["positionen"]) == 1
    assert ergebnis["positionen"][0]["art"] == "UNVERAENDERT"


@pytest.mark.django_db
def test_soll_ist_ignoriert_alternativ_und_bedarf(app_user):
    auftrag = _auftrag(app_user)
    _angebot_am_auftrag(app_user, auftrag, [
        _mat("Standard", "5", unit="Stk"),
        _mat("Ausweichvariante", "5", unit="Stk", kind="ALTERNATIV"),
    ])
    bericht = _bericht(app_user, auftrag)
    report_service.set_report_lines(
        app_user.id, report_id=bericht.id,
        lines=[{"line_type": "MATERIAL", "description": "Standard", "quantity": "5",
                "unit": "Stk"}],
    )
    ergebnis = report_service.soll_ist(auftrag.id)
    assert [p["bezeichnung"] for p in ergebnis["positionen"]] == ["Standard"]


@pytest.mark.django_db
def test_soll_ist_ignoriert_ersetzte_und_abgelehnte_angebote(app_user):
    """Ein ersetztes Angebot trüge sein Soll doppelt (der Nachfolger trägt es
    bereits), ein abgelehntes wurde nie vereinbart."""
    auftrag = _auftrag(app_user)
    _angebot_am_auftrag(app_user, auftrag, [_mat("Rohr", "12")], status="ABGELEHNT")
    _angebot_am_auftrag(app_user, auftrag, [_mat("Rohr", "12")])
    bericht = _bericht(app_user, auftrag)
    report_service.set_report_lines(
        app_user.id, report_id=bericht.id,
        lines=[{"line_type": "MATERIAL", "description": "Rohr", "quantity": "12",
                "unit": "m"}],
    )
    ergebnis = report_service.soll_ist(auftrag.id)
    pos = _art(ergebnis, "Rohr")
    assert pos["soll"] == Decimal("12.000")   # nicht 24
    assert pos["art"] == "UNVERAENDERT"


@pytest.mark.django_db
def test_soll_ist_ohne_angebot_ist_alles_zusatz(app_user):
    auftrag = _auftrag(app_user)
    bericht = _bericht(app_user, auftrag)
    report_service.set_report_lines(
        app_user.id, report_id=bericht.id,
        lines=[{"line_type": "MATERIAL", "description": "Spontanmaterial",
                "quantity": "3", "unit": "Stk"}],
    )
    ergebnis = report_service.soll_ist(auftrag.id)
    assert ergebnis["positionen"][0]["art"] == "ZUSATZ"


@pytest.mark.django_db
def test_soll_ist_unbekannter_auftrag(db):
    with pytest.raises(SiteReportError):
        report_service.soll_ist(uuid.uuid4())


# --- Der Auftragsbezug des Angebots (das Fundament des Solls) ----------------

@pytest.mark.django_db
def test_auftragsbezug_entsteht_ueber_den_produktweg(app_user):
    """Ohne Produktweg wäre das Feature tot: `create_quote(work_order_id=…)`."""
    auftrag = _auftrag(app_user)
    angebot = beleg_service.create_quote(
        app_user.id, property_id=auftrag.property_id, title="Angebot",
        work_order_id=auftrag.id, lines=[_mat("Rohr", "12")],
    )
    assert angebot.work_order_id == auftrag.id


@pytest.mark.django_db
def test_auftragsbezug_nachtraeglich_setzen_und_loesen(app_user):
    auftrag = _auftrag(app_user)
    angebot = beleg_service.create_quote(
        app_user.id, property_id=auftrag.property_id, title="Angebot",
        lines=[_mat("Rohr", "12")],
    )
    assert angebot.work_order_id is None

    beleg_service.update_quote(
        app_user.id, quote_id=angebot.id, work_order_id=auftrag.id
    )
    angebot.refresh_from_db()
    assert angebot.work_order_id == auftrag.id

    # Und wieder lösen (None ist ein bewusstes Leeren, kein „nicht ändern").
    beleg_service.update_quote(app_user.id, quote_id=angebot.id, work_order_id=None)
    angebot.refresh_from_db()
    assert angebot.work_order_id is None


@pytest.mark.django_db
def test_fremder_auftrag_laesst_sich_nicht_anhaengen(app_user):
    """Ein Auftrag einer anderen Liegenschaft ist kein zulässiger Bezug (422)."""
    auftrag = _auftrag(app_user, titel="Unsere Baustelle")
    fremder = _auftrag(app_user, titel="Fremde Baustelle")
    with pytest.raises(ValueError):
        beleg_service.create_quote(
            app_user.id, property_id=auftrag.property_id, title="Angebot",
            work_order_id=fremder.id, lines=[_mat("Rohr", "12")],
        )


@pytest.mark.django_db
@pytest.mark.parametrize("status", ["VERSENDET", "ANGENOMMEN"])
def test_zuordnung_auch_nach_versand_und_annahme(app_user, status):
    """Der reale Ablauf: versenden → Kunde nimmt an → **dann** Auftrag anlegen.

    Wäre die Zuordnung ab VERSENDET gesperrt (so war es bis Migration 0082), wäre
    sie genau dann unmöglich, wenn man sie braucht — und das Soll bliebe leer.
    """
    auftrag = _auftrag(app_user)
    angebot = beleg_service.create_quote(
        app_user.id, property_id=auftrag.property_id, title="Angebot",
        lines=[_mat("Rohr", "12")],
    )
    beleg_service.send_quote(app_user.id, quote_id=angebot.id)
    if status != "VERSENDET":
        with business_transaction(app_user.id, status_reason="Kunde nimmt an"):
            Quote.objects.filter(id=angebot.id).update(status=status)
    angebot.refresh_from_db()
    assert angebot.status == status

    beleg_service.update_quote(
        app_user.id, quote_id=angebot.id, work_order_id=auftrag.id
    )
    angebot.refresh_from_db()
    assert angebot.work_order_id == auftrag.id
    # Und das Angebot bildet jetzt tatsächlich das Soll des Auftrags.
    assert report_service.soll_ist(auftrag.id)["positionen"][0]["soll"] == Decimal("12.000")

    # Lösen geht ebenso — die Zuordnung ist keine Einbahnstraße.
    beleg_service.update_quote(app_user.id, quote_id=angebot.id, work_order_id=None)
    angebot.refresh_from_db()
    assert angebot.work_order_id is None


@pytest.mark.django_db
def test_b30_bleibt_intakt_am_versendeten_angebot(app_user):
    """Der Beweis, dass 0082 nur EIN Feld freigibt: Titel, Datum, Positionen und
    Beträge eines versendeten Angebots sind weiterhin unveränderlich (B-30) —
    Service (422) UND Trigger."""
    auftrag = _auftrag(app_user)
    angebot = beleg_service.create_quote(
        app_user.id, property_id=auftrag.property_id, title="Angebot",
        lines=[_mat("Rohr", "12")],
    )
    beleg_service.send_quote(app_user.id, quote_id=angebot.id)

    # Der Service weist Inhaltsänderungen ab — auch zusammen mit der Zuordnung.
    with pytest.raises(ValueError):
        beleg_service.update_quote(
            app_user.id, quote_id=angebot.id, title="Anderer Titel",
        )
    with pytest.raises(ValueError):
        beleg_service.update_quote(
            app_user.id, quote_id=angebot.id, work_order_id=auftrag.id,
            lines=[_mat("Rohr", "99")],
        )
    angebot.refresh_from_db()
    assert angebot.title == "Angebot"
    assert angebot.work_order_id is None  # nichts durchgerutscht

    # Und der Trigger greift auch am Service vorbei: Betrag und Position.
    with pytest.raises(Error):
        with business_transaction(app_user.id):
            Quote.objects.filter(id=angebot.id).update(net_total=Decimal("1.00"))
    with pytest.raises(Error):
        with business_transaction(app_user.id):
            QuoteLine.objects.filter(quote_id=angebot.id).update(
                quantity=Decimal("99.000")
            )


@pytest.mark.django_db
def test_fremder_auftrag_auch_nach_versand_abgelehnt(app_user):
    """Die Ausnahme in `freeze_sent_quote` weicht den zusammengesetzten FK NICHT
    auf: ein Auftrag einer fremden Liegenschaft bleibt unzulässig (422)."""
    auftrag = _auftrag(app_user, titel="Unsere Baustelle")
    fremder = _auftrag(app_user, titel="Fremde Baustelle")
    angebot = beleg_service.create_quote(
        app_user.id, property_id=auftrag.property_id, title="Angebot",
        lines=[_mat("Rohr", "12")],
    )
    beleg_service.send_quote(app_user.id, quote_id=angebot.id)
    with pytest.raises(ValueError):
        beleg_service.update_quote(
            app_user.id, quote_id=angebot.id, work_order_id=fremder.id
        )
    angebot.refresh_from_db()
    assert angebot.work_order_id is None


@pytest.mark.django_db
def test_kein_projekt_fallback_im_soll(app_user):
    """Ein Projektangebot ohne Auftragsbezug ist NICHT das Soll des Auftrags.

    Sonst wäre dasselbe Angebot das Soll jedes Auftrags des Projekts, während das
    Ist nur aus dessen eigenen Berichten käme — jeder Auftrag zeigte einen frei
    erfundenen MINDERVERBRAUCH.
    """
    from db_core.services import projekt as projekt_service

    obj = _property(app_user)
    projekt = projekt_service.create_project(
        app_user.id, name="Bad-Sanierung", property_ids=[obj.id]
    )
    auftrag = auftrag_service.create_work_order(
        app_user.id, property_id=obj.id, title="Auftrag im Projekt",
        project_id=projekt.id,
    )
    projektangebot = beleg_service.create_quote(
        app_user.id, property_id=obj.id, title="Projektangebot",
        project_id=projekt.id, lines=[_mat("Rohr", "12")],
    )
    beleg_service.send_quote(app_user.id, quote_id=projektangebot.id)

    bericht = _bericht(app_user, auftrag)
    report_service.set_report_lines(
        app_user.id, report_id=bericht.id,
        lines=[{"line_type": "MATERIAL", "description": "Rohr", "quantity": "12",
                "unit": "m"}],
    )
    ergebnis = report_service.soll_ist(auftrag.id)
    assert ergebnis["angebote"] == []
    assert _art(ergebnis, "Rohr")["art"] == "ZUSATZ"   # kein MINDERVERBRAUCH


# --- Das Soll ist nicht fälschbar (planned_quantity) -------------------------

@pytest.mark.django_db
def test_sollmenge_ohne_herkunft_wird_abgelehnt(app_user):
    """Ein frei gesetztes Soll landete sonst auf dem unterschriebenen Dokument."""
    auftrag = _auftrag(app_user)
    bericht = _bericht(app_user, auftrag)
    with pytest.raises(SiteReportError):
        report_service.set_report_lines(
            app_user.id, report_id=bericht.id,
            lines=[{"line_type": "MATERIAL", "description": "Frei erfunden",
                    "quantity": "1", "unit": "Stk", "planned_quantity": "99"}],
        )


@pytest.mark.django_db
def test_gefaelschte_sollmenge_wird_verworfen(app_user):
    """Mit Herkunft gewinnt IMMER die Angebotszeile — der Client-Wert fliegt raus."""
    auftrag = _auftrag(app_user)
    bericht = _bericht(app_user, auftrag)
    angebot = _angebot_am_auftrag(app_user, auftrag, [_mat("Rohr", "12")])
    ql = angebot.lines.first()

    lines = report_service.set_report_lines(
        app_user.id, report_id=bericht.id,
        lines=[{"line_type": "MATERIAL", "description": "Rohr", "quantity": "12",
                "unit": "m", "source_quote_line_id": str(ql.id),
                "planned_quantity": "99"}],
    )
    assert lines[0].planned_quantity == Decimal("12.000")   # nicht 99


@pytest.mark.django_db
def test_db_check_verbietet_soll_ohne_herkunft(app_user):
    """Am Service vorbei: die Regel steht in der DATENBANK (CHECK)."""
    auftrag = _auftrag(app_user)
    bericht = _bericht(app_user, auftrag)
    with pytest.raises(Error):
        with business_transaction(app_user.id):
            SiteReportLine.objects.create(
                id=uuid.uuid4(), site_report_id=bericht.id, position_number=1,
                line_type="MATERIAL", description="Frei erfunden",
                quantity=Decimal("1.000"), unit="Stk",
                planned_quantity=Decimal("99.000"), source_quote_line_id=None,
            )


@pytest.mark.django_db
def test_db_check_verlangt_die_einheit(app_user):
    """Der Einheiten-CHECK hielt wegen dreiwertiger Logik nicht (NULL ≠ FALSE).

    Eine Mengenzeile ohne Einheit wäre eine Sackgasse: der Service verlangt die
    Einheit beim Speichern, der Bericht ließe sich nie mehr sichern.
    """
    auftrag = _auftrag(app_user)
    bericht = _bericht(app_user, auftrag)
    with pytest.raises(Error):
        with business_transaction(app_user.id):
            SiteReportLine.objects.create(
                id=uuid.uuid4(), site_report_id=bericht.id, position_number=1,
                line_type="MATERIAL", description="Ohne Einheit",
                quantity=Decimal("1.000"), unit=None,
            )


# --- Weitere Löcher der ersten Runde ----------------------------------------

@pytest.mark.django_db
def test_entwurfsangebot_verdoppelt_das_soll_nicht(app_user):
    """Ein nie hinausgegangenes Angebot ist keine Vereinbarung."""
    auftrag = _auftrag(app_user)
    versendet = _angebot_am_auftrag(app_user, auftrag, [_mat("Rohr", "12")])
    _angebot_am_auftrag(app_user, auftrag, [_mat("Rohr", "12")], versenden=False)

    bericht = _bericht(app_user, auftrag)
    report_service.set_report_lines(
        app_user.id, report_id=bericht.id,
        lines=[{"line_type": "MATERIAL", "description": "Rohr", "quantity": "12",
                "unit": "m"}],
    )
    ergebnis = report_service.soll_ist(auftrag.id)
    pos = _art(ergebnis, "Rohr")
    assert pos["soll"] == Decimal("12.000")   # nicht 24
    assert pos["art"] == "UNVERAENDERT"
    # Und der Nutzer sieht, worauf sich das Soll stützt.
    assert [a["id"] for a in ergebnis["angebote"]] == [versendet.id]
    assert ergebnis["angebote"][0]["status"] == "VERSENDET"


@pytest.mark.django_db
def test_entwurfsangebot_ist_auch_nicht_vorbelegbar(app_user):
    """Eine Wahrheit für vorbelegen UND soll_ist — sonst stünde eine vorbelegte
    Position hinterher als ZUSATZ da."""
    auftrag = _auftrag(app_user)
    bericht = _bericht(app_user, auftrag)
    entwurf = _angebot_am_auftrag(
        app_user, auftrag, [_mat("Rohr", "12")], versenden=False
    )
    assert report_service.angebote_zur_vorbelegung(bericht.id) == []
    with pytest.raises(SiteReportError):
        report_service.vorbelegen_aus_angebot(
            app_user.id, report_id=bericht.id, quote_id=entwurf.id
        )


@pytest.mark.django_db
def test_einheitenkonflikt_wird_getrennt_ausgewiesen(app_user):
    """„Montage 3 h" und „Montage 1 psch" sind nicht dieselbe Größe — zwei ehrliche
    Zeilen statt einer falschen Differenz von -2."""
    auftrag = _auftrag(app_user)
    _angebot_am_auftrag(app_user, auftrag, [
        {"line_type": "ARBEITSZEIT", "description": "Montage", "quantity": "3",
         "unit": "h", "unit_price": "60.00", "tax_code": "DE_19"},
    ])
    bericht = _bericht(app_user, auftrag)
    report_service.set_report_lines(
        app_user.id, report_id=bericht.id,
        lines=[{"line_type": "PAUSCHALE", "description": "Montage", "quantity": "1",
                "unit": "psch"}],
    )
    ergebnis = report_service.soll_ist(auftrag.id)
    zeilen = {(p["bezeichnung"], p["einheit"]): p for p in ergebnis["positionen"]}
    assert len(zeilen) == 2
    assert zeilen[("Montage", "h")]["art"] == "ENTFALLEN"
    assert zeilen[("Montage", "h")]["soll"] == Decimal("3.000")
    assert zeilen[("Montage", "psch")]["art"] == "ZUSATZ"
    assert zeilen[("Montage", "psch")]["ist"] == Decimal("1.000")


@pytest.mark.django_db
def test_vorbelegen_ohne_einheit_wird_klar_abgelehnt(app_user):
    """Keine Einheit erfinden — sonst entstünde eine Position, die die DB nicht
    nimmt und der Monteur nie mehr speichern kann."""
    auftrag = _auftrag(app_user)
    bericht = _bericht(app_user, auftrag)
    angebot = _angebot_am_auftrag(app_user, auftrag, [
        {"line_type": "MATERIAL", "description": "Rohr ohne Einheit",
         "quantity": "5", "unit_price": "10.00", "tax_code": "DE_19"},
    ])
    with pytest.raises(SiteReportError) as exc:
        report_service.vorbelegen_aus_angebot(
            app_user.id, report_id=bericht.id, quote_id=angebot.id
        )
    assert "Rohr ohne Einheit" in str(exc.value)
    assert not SiteReportLine.objects.filter(site_report_id=bericht.id).exists()


@pytest.mark.django_db
def test_ausgemusterter_artikel_blockiert_die_bestandszeile_nicht(app_user):
    """Wird ein Artikel nach der Erfassung INAKTIV gesetzt, muss der Monteur seinen
    Bericht weiter speichern können — die Position ist eine Kopie, kein Verweis.
    Geprüft wird der Aktivstatus nur bei NEU hinzukommenden Herkunftsverweisen."""
    auftrag = _auftrag(app_user)
    bericht = _bericht(app_user, auftrag)
    artikel = _artikel(app_user, "A-AUSGEMUSTERT")
    report_service.set_report_lines(
        app_user.id, report_id=bericht.id,
        lines=[{"line_type": "MATERIAL", "source_article_id": str(artikel.id),
                "quantity": "5"}],
    )
    artikel_service.set_article_status(
        app_user.id, article_id=artikel.id, status="INAKTIV"
    )

    # Weiterarbeiten am selben Bericht: geht (Bestandszeile + neue Freitextzeile).
    lines = report_service.set_report_lines(
        app_user.id, report_id=bericht.id,
        lines=[
            {"line_type": "MATERIAL", "source_article_id": str(artikel.id),
             "description": "Rohr DN20", "quantity": "7", "unit": "m"},
            {"line_type": "MATERIAL", "description": "Dichtung", "quantity": "2",
             "unit": "Stk"},
        ],
    )
    assert lines[0].quantity == Decimal("7.000")

    # Ein ANDERER Bericht darf den ausgemusterten Artikel aber nicht neu aufnehmen.
    neu = _bericht(app_user, auftrag)
    with pytest.raises(SiteReportError):
        report_service.set_report_lines(
            app_user.id, report_id=neu.id,
            lines=[{"line_type": "MATERIAL", "source_article_id": str(artikel.id),
                    "quantity": "1"}],
        )


# --- Herkunftstreue: die Herkunft muss der Zeile auch ENTSPRECHEN ------------
#
# Die Berichtszeile trägt ihre Herkunft (`source_quote_line_id`) — und ihre
# IDENTITÄT kommt vollständig von dort: Artikel, Leistung, Einheit, Sollmenge UND
# Bezeichnung. Was der Monteur präzisieren will, gehört in die **Notiz**; die Menge
# bleibt frei. So bleibt eine korrigierte Zeile im Soll (MEHRVERBRAUCH), statt in
# ENTFALLEN + ZUSATZ auseinanderzubrechen — das Büro fakturierte sonst die ganze
# Menge als Zusatzleistung statt nur die Mehrmenge.

@pytest.mark.django_db
def test_praezisierung_in_der_notiz_bleibt_im_soll(app_user):
    """Der Monteur präzisiert (in der NOTIZ) und korrigiert die Menge.

    Erwartet: EINE Position, MEHRVERBRAUCH +2 — kein ENTFALLEN/ZUSATZ-Paar. Genau
    das sieht ein Test nicht, der den Positionssatz unverändert zurückschreibt.
    """
    auftrag = _auftrag(app_user)
    bericht = _bericht(app_user, auftrag)
    angebot = _angebot_am_auftrag(app_user, auftrag, [_mat("Rohr", "12")])
    vorbelegt = report_service.vorbelegen_aus_angebot(
        app_user.id, report_id=bericht.id, quote_id=angebot.id
    )
    ql_id = vorbelegt[0].source_quote_line_id

    lines = report_service.set_report_lines(
        app_user.id, report_id=bericht.id,
        lines=[{"line_type": "MATERIAL",
                "description": "Rohr",                      # fest — aus dem Angebot
                "note": "DN20, Steigstrang 2. OG",          # hier wird präzisiert
                "quantity": "14", "unit": "m",
                "source_quote_line_id": str(ql_id)}],
    )
    assert lines[0].note == "DN20, Steigstrang 2. OG"

    ergebnis = report_service.soll_ist(auftrag.id)
    assert len(ergebnis["positionen"]) == 1, ergebnis["positionen"]
    pos = ergebnis["positionen"][0]
    assert pos["art"] == report_service.MEHRVERBRAUCH
    assert pos["soll"] == Decimal("12.000")
    assert pos["ist"] == Decimal("14.000")
    assert pos["differenz"] == Decimal("2.000")


@pytest.mark.django_db
def test_weggelassene_felder_werden_abgeleitet_nicht_geraten(app_user):
    """Der Client schickt NUR Menge + Herkunft. Alles andere — Artikel, Einheit,
    Sollmenge, Bezeichnung — leitet der Service aus der Angebotsposition ab."""
    auftrag = _auftrag(app_user)
    bericht = _bericht(app_user, auftrag)
    artikel = _artikel(app_user, "A-ROHR")
    angebot = _angebot_am_auftrag(
        app_user, auftrag, [_mat("Rohr", "12", article_id=str(artikel.id))]
    )
    ql = angebot.lines.first()

    lines = report_service.set_report_lines(
        app_user.id, report_id=bericht.id,
        lines=[{"line_type": "MATERIAL", "quantity": "14",
                "note": "DN20, Steigstrang",
                "source_quote_line_id": str(ql.id)}],
    )
    assert lines[0].source_article_id == artikel.id
    assert lines[0].unit == "m"
    assert lines[0].planned_quantity == Decimal("12.000")
    assert lines[0].description == "Rohr"      # NICHT vom Client — aus der Quelle
    assert lines[0].note == "DN20, Steigstrang"

    ergebnis = report_service.soll_ist(auftrag.id)
    assert [p["art"] for p in ergebnis["positionen"]] == [
        report_service.MEHRVERBRAUCH
    ]
    assert ergebnis["positionen"][0]["differenz"] == Decimal("2.000")


@pytest.mark.django_db
def test_abweichende_bezeichnung_bei_herkunft_wird_abgelehnt(app_user):
    """Die Bezeichnung einer Position MIT Herkunft ist fest.

    Sie ist Identität, nicht Anzeige: sonst stünde die Sollmenge einer ganz anderen
    Angebotsposition neben frei getipptem Text — auf einem unterschriebenen,
    versiegelten Kundendokument.
    """
    auftrag = _auftrag(app_user)
    bericht = _bericht(app_user, auftrag)
    angebot = _angebot_am_auftrag(app_user, auftrag, [_mat("Rohr", "12")])
    ql = angebot.lines.first()
    with pytest.raises(SiteReportError, match="Bezeichnung"):
        report_service.set_report_lines(
            app_user.id, report_id=bericht.id,
            lines=[{"line_type": "MATERIAL",
                    "description": "Rohr DN20, Steigstrang",   # abweichend!
                    "quantity": "14", "unit": "m",
                    "source_quote_line_id": str(ql.id)}],
        )
    assert SiteReportLine.objects.filter(site_report_id=bericht.id).count() == 0


@pytest.mark.django_db
def test_fremde_herkunft_greift_auch_ohne_mitgeschickte_felder(app_user):
    """**Der Befund aus der Abnahme.** Lässt der Client `source_article_id` und
    `unit` einfach WEG, kann er sie nicht „abweichend" schicken — die alte Prüfung
    lief ins Leere und leitete alles aus der Kessel-Zeile ab. Ergebnis: „Rohr DN20 ·
    5 Stk · angeboten 500". Die Bezeichnung ist das Feld, das übrig blieb — und
    genau deshalb gehört sie in die Prüfung."""
    auftrag = _auftrag(app_user)
    bericht = _bericht(app_user, auftrag)
    rohr = _artikel(app_user, "A-ROHR", "Rohr DN20", "m")
    kessel = _artikel(app_user, "A-KESSEL", "Kessel", "Stk")
    angebot = _angebot_am_auftrag(app_user, auftrag, [
        _mat("Rohr DN20", "12", article_id=str(rohr.id)),
        _mat("Kessel", "500", unit="Stk", article_id=str(kessel.id)),
    ])
    kessel_zeile = angebot.lines.get(description="Kessel")

    with pytest.raises(SiteReportError, match="Bezeichnung"):
        report_service.set_report_lines(
            app_user.id, report_id=bericht.id,
            lines=[{"line_type": "MATERIAL", "description": "Rohr DN20",
                    "quantity": "5",
                    # source_article_id und unit bewusst WEGGELASSEN
                    "source_quote_line_id": str(kessel_zeile.id)}],
        )
    assert SiteReportLine.objects.filter(site_report_id=bericht.id).count() == 0


@pytest.mark.django_db
def test_fremde_herkunft_wird_abgelehnt(app_user):
    """Die Herkunft der Kessel-Position an eine Rohr-Zeile gehängt: „angeboten: 500"
    stünde neben *Rohr DN20* auf einem Dokument, das der Kunde unterschreibt und
    das danach versiegelt wird. Der FK garantiert Herkunft — nicht Treue."""
    auftrag = _auftrag(app_user)
    bericht = _bericht(app_user, auftrag)
    rohr = _artikel(app_user, "A-ROHR", "Rohr DN20", "m")
    kessel = _artikel(app_user, "A-KESSEL", "Kessel", "Stk")
    angebot = _angebot_am_auftrag(app_user, auftrag, [
        _mat("Rohr DN20", "12", article_id=str(rohr.id)),
        _mat("Kessel", "500", unit="Stk", article_id=str(kessel.id)),
    ])
    kessel_zeile = angebot.lines.get(description="Kessel")

    with pytest.raises(SiteReportError, match="Artikelbezug"):
        report_service.set_report_lines(
            app_user.id, report_id=bericht.id,
            lines=[{"line_type": "MATERIAL", "description": "Rohr DN20",
                    "quantity": "5", "unit": "m",
                    "source_article_id": str(rohr.id),
                    "source_quote_line_id": str(kessel_zeile.id)}],
        )
    assert SiteReportLine.objects.filter(site_report_id=bericht.id).count() == 0


@pytest.mark.django_db
def test_abweichende_einheit_bei_herkunft_wird_abgelehnt(app_user):
    auftrag = _auftrag(app_user)
    bericht = _bericht(app_user, auftrag)
    angebot = _angebot_am_auftrag(app_user, auftrag, [_mat("Rohr", "12")])
    ql = angebot.lines.first()
    with pytest.raises(SiteReportError, match="Einheit"):
        report_service.set_report_lines(
            app_user.id, report_id=bericht.id,
            lines=[{"line_type": "MATERIAL", "description": "Rohr",
                    "quantity": "14", "unit": "Stk",   # das Angebot sagt „m"
                    "source_quote_line_id": str(ql.id)}],
        )
    assert SiteReportLine.objects.filter(site_report_id=bericht.id).count() == 0


@pytest.mark.django_db
def test_abweichender_artikel_bei_herkunft_wird_abgelehnt(app_user):
    """Die Angebotsposition ist Freitext (ohne Artikel) — der Client hängt einen
    Artikel an die Berichtszeile. Auch das ist eine erfundene Identität: die Zeile
    fiele im Abgleich unter den Artikel-Schlüssel statt unter den ihres Solls."""
    auftrag = _auftrag(app_user)
    bericht = _bericht(app_user, auftrag)
    artikel = _artikel(app_user, "A-ROHR")
    angebot = _angebot_am_auftrag(app_user, auftrag, [_mat("Rohr", "12")])
    ql = angebot.lines.first()
    with pytest.raises(SiteReportError, match="Artikelbezug"):
        report_service.set_report_lines(
            app_user.id, report_id=bericht.id,
            lines=[{"line_type": "MATERIAL", "description": "Rohr",
                    "quantity": "14", "unit": "m",
                    "source_article_id": str(artikel.id),
                    "source_quote_line_id": str(ql.id)}],
        )


# --- Dieselbe Regel physisch: der Trigger (Migration 0083) -------------------

def _herkunftsfall(app_user):
    """Auftrag + Bericht + Angebot mit Rohr- (12 m) und Kessel-Position (500 Stk)."""
    auftrag = _auftrag(app_user)
    bericht = _bericht(app_user, auftrag)
    rohr = _artikel(app_user, "A-ROHR", "Rohr DN20", "m")
    kessel = _artikel(app_user, "A-KESSEL", "Kessel", "Stk")
    angebot = _angebot_am_auftrag(app_user, auftrag, [
        _mat("Rohr DN20", "12", article_id=str(rohr.id)),
        _mat("Kessel", "500", unit="Stk", article_id=str(kessel.id)),
    ])
    return (
        bericht,
        rohr,
        angebot.lines.get(description="Rohr DN20"),
        angebot.lines.get(description="Kessel"),
    )


@pytest.mark.django_db
def test_trigger_gefaelschtes_soll(app_user):
    """Am Service vorbei: die DATENBANK erzwingt planned_quantity = Angebotsmenge."""
    bericht, rohr, rohr_ql, _kessel_ql = _herkunftsfall(app_user)
    with pytest.raises(Error, match="Sollmenge"):
        with business_transaction(app_user.id):
            SiteReportLine.objects.create(
                id=uuid.uuid4(), site_report_id=bericht.id, position_number=1,
                line_type="MATERIAL", description="Rohr DN20",
                quantity=Decimal("14.000"), unit="m",
                source_article_id=rohr.id,
                planned_quantity=Decimal("99.000"),   # das Angebot sagt 12
                source_quote_line_id=rohr_ql.id,
            )


@pytest.mark.django_db
def test_trigger_fremde_herkunft(app_user):
    """Am Service vorbei: Kessel-Herkunft auf einer Rohr-Zeile — mit dem SOLL des
    Kessels, damit die Mengenprüfung des Triggers gerade nicht greift. Der
    Artikelbezug muss sie trotzdem abweisen."""
    bericht, rohr, _rohr_ql, kessel_ql = _herkunftsfall(app_user)
    with pytest.raises(Error, match="Artikel"):
        with business_transaction(app_user.id):
            SiteReportLine.objects.create(
                id=uuid.uuid4(), site_report_id=bericht.id, position_number=1,
                line_type="MATERIAL", description="Rohr DN20",
                quantity=Decimal("5.000"), unit="Stk",
                source_article_id=rohr.id,             # Rohr …
                planned_quantity=Decimal("500.000"),   # … mit dem Soll des Kessels
                source_quote_line_id=kessel_ql.id,
            )


@pytest.mark.django_db
def test_trigger_abweichende_einheit(app_user):
    bericht, rohr, rohr_ql, _kessel_ql = _herkunftsfall(app_user)
    with pytest.raises(Error, match="Einheit"):
        with business_transaction(app_user.id):
            SiteReportLine.objects.create(
                id=uuid.uuid4(), site_report_id=bericht.id, position_number=1,
                line_type="MATERIAL", description="Rohr DN20",
                quantity=Decimal("14.000"), unit="Stk",   # das Angebot sagt „m"
                source_article_id=rohr.id,
                planned_quantity=Decimal("12.000"),
                source_quote_line_id=rohr_ql.id,
            )


@pytest.mark.django_db
def test_trigger_abweichende_bezeichnung(app_user):
    """**Die fünfte Gleichung.** Am Service vorbei: Kessel-Herkunft auf einer
    Rohr-Zeile — diesmal mit ALLEN übrigen Feldern des Kessels (Artikel, Einheit,
    Soll), so wie der Service sie ableiten WÜRDE, wenn der Client sie weglässt. Nur
    die Bezeichnung sagt „Rohr DN20". Ohne die Bezeichnungsprüfung ginge diese Zeile
    durch — und stünde als „Rohr DN20 · 5 Stk · angeboten 500" auf dem
    unterschriebenen Dokument."""
    bericht, _rohr, _rohr_ql, kessel_ql = _herkunftsfall(app_user)
    kessel = Article.objects.get(article_number="A-KESSEL")
    with pytest.raises(Error, match="Bezeichnung"):
        with business_transaction(app_user.id):
            SiteReportLine.objects.create(
                id=uuid.uuid4(), site_report_id=bericht.id, position_number=1,
                line_type="MATERIAL", description="Rohr DN20",   # das Angebot: „Kessel"
                quantity=Decimal("5.000"), unit="Stk",
                source_article_id=kessel.id,
                planned_quantity=Decimal("500.000"),
                source_quote_line_id=kessel_ql.id,
            )


@pytest.mark.django_db
def test_trigger_laesst_die_treue_zeile_durch(app_user):
    """Gegenprobe: die wortgleiche Zeile geht durch — und die NOTIZ bleibt frei.
    Dort präzisiert der Monteur; ein Trigger, der auch sie festnagelte, machte den
    Bericht unbrauchbar."""
    bericht, rohr, rohr_ql, _kessel_ql = _herkunftsfall(app_user)
    with business_transaction(app_user.id):
        SiteReportLine.objects.create(
            id=uuid.uuid4(), site_report_id=bericht.id, position_number=1,
            line_type="MATERIAL", description="Rohr DN20",
            note="Steigstrang, 2. OG",
            quantity=Decimal("14.000"), unit="m",
            source_article_id=rohr.id,
            planned_quantity=Decimal("12.000"),
            source_quote_line_id=rohr_ql.id,
        )
    assert SiteReportLine.objects.filter(site_report_id=bericht.id).count() == 1


@pytest.mark.django_db
def test_trigger_greift_auch_beim_update(app_user):
    """Der Trigger ist BEFORE INSERT **OR UPDATE**. Eine treue Zeile nachträglich
    untreu zu machen, muss genauso scheitern — sonst wäre der Insert-Weg nur eine
    Schikane und der Update-Weg das offene Tor. Die Notiz dagegen bleibt änderbar."""
    bericht, rohr, rohr_ql, _kessel_ql = _herkunftsfall(app_user)
    with business_transaction(app_user.id):
        zeile = SiteReportLine.objects.create(
            id=uuid.uuid4(), site_report_id=bericht.id, position_number=1,
            line_type="MATERIAL", description="Rohr DN20",
            quantity=Decimal("14.000"), unit="m",
            source_article_id=rohr.id,
            planned_quantity=Decimal("12.000"),
            source_quote_line_id=rohr_ql.id,
        )

    for feld, wert, muster in [
        ("description", "Kessel", "Bezeichnung"),
        ("unit", "Stk", "Einheit"),
        ("planned_quantity", Decimal("99.000"), "Sollmenge"),
        ("source_article_id", None, "Artikel"),
    ]:
        with pytest.raises(Error, match=muster):
            with business_transaction(app_user.id):
                SiteReportLine.objects.filter(id=zeile.id).update(**{feld: wert})

    # Die Menge (Ist) und die Notiz sind frei — sonst könnte der Monteur nichts mehr
    # korrigieren.
    with business_transaction(app_user.id):
        SiteReportLine.objects.filter(id=zeile.id).update(
            quantity=Decimal("15.000"), note="Steigstrang, 2. OG"
        )
    zeile.refresh_from_db()
    assert zeile.quantity == Decimal("15.000")
    assert zeile.note == "Steigstrang, 2. OG"


# --- Das Angebot darf sein Soll nicht unter dem Bericht wegziehen ------------

@pytest.mark.django_db
def test_angebot_mit_referenziertem_soll_ist_nicht_mehr_ersetzbar(app_user):
    """`update_quote` ersetzt den Positionssatz (Delete+Insert). Ist eine Position
    bereits als Herkunft in einem Bericht referenziert, liefe das DELETE in eine
    Fremdschlüsselverletzung (23503) und schlüge als 500 durch. Fachfehler statt
    Absturz — und das Soll des Nachweises bleibt stehen."""
    auftrag = _auftrag(app_user)
    bericht = _bericht(app_user, auftrag)
    # FREIGEGEBEN: inhaltlich noch editierbar (QUOTE_EDITIERBAR) UND bereits ein
    # Soll (nicht in SOLL_AUSGESCHLOSSENE_STATUS) — genau das Zeitfenster, in dem
    # sich beide Regeln begegnen.
    angebot = _angebot_am_auftrag(
        app_user, auftrag, [_mat("Rohr", "12")], versenden=False
    )
    with business_transaction(app_user.id, status_reason="Freigabe"):
        Quote.objects.filter(id=angebot.id).update(status="INTERN_GEPRUEFT")
        Quote.objects.filter(id=angebot.id).update(status="FREIGEGEBEN")

    report_service.vorbelegen_aus_angebot(
        app_user.id, report_id=bericht.id, quote_id=angebot.id
    )

    with pytest.raises(ValueError, match="Baustellenbericht"):
        beleg_service.update_quote(
            app_user.id, quote_id=angebot.id,
            lines=[_mat("Ganz was anderes", "1")],
        )
    # Der Kopf allein bleibt änderbar — die Sperre gilt dem Positionssatz.
    beleg_service.update_quote(app_user.id, quote_id=angebot.id, title="Neuer Titel")
    assert QuoteLine.objects.filter(quote_id=angebot.id).count() == 1


@pytest.mark.django_db
def test_zuordnung_laesst_sich_nicht_unter_dem_nachweis_wegziehen(app_user):
    """**Der zweite Befund aus der Abnahme.** Die Soll-Ist-Oberfläche hat einen
    Ein-Klick-Button „Zuordnung lösen". Wird `quote.work_order_id` gelöst, ist die
    Angebotsposition kein zulässiges Soll dieses Berichts mehr: das Soll fällt auf 0,
    die Position wird ZUSATZ — und der Monteur sitzt in der **Sackgasse**, weil sein
    Entwurfsbericht nicht mehr speicherbar ist („… gehört nicht zu einem Angebot
    dieses Auftrags") und der Editor keinen Weg bietet, die Herkunft zu lösen."""
    auftrag = _auftrag(app_user)
    bericht = _bericht(app_user, auftrag)
    angebot = _angebot_am_auftrag(app_user, auftrag, [_mat("Rohr", "12")])
    report_service.vorbelegen_aus_angebot(
        app_user.id, report_id=bericht.id, quote_id=angebot.id
    )

    with pytest.raises(ValueError, match="Zuordnung"):
        beleg_service.update_quote(
            app_user.id, quote_id=angebot.id, work_order_id=None
        )

    # Auch das UMHÄNGEN auf einen anderen Auftrag derselben Liegenschaft zieht das
    # Soll weg — dieselbe Sackgasse, nur mit einem Zwischenschritt.
    zweiter = auftrag_service.create_work_order(
        app_user.id, property_id=auftrag.property_id, title="Zweiter Auftrag"
    )
    with pytest.raises(ValueError, match="Zuordnung"):
        beleg_service.update_quote(
            app_user.id, quote_id=angebot.id, work_order_id=str(zweiter.id)
        )

    angebot.refresh_from_db()
    assert angebot.work_order_id == auftrag.id
    # Und der Bericht bleibt speicherbar — keine Sackgasse.
    zeile = report_service.list_report_lines(bericht.id)[0]
    report_service.set_report_lines(
        app_user.id, report_id=bericht.id,
        lines=[{"line_type": "MATERIAL", "quantity": "14",
                "source_quote_line_id": str(zeile.source_quote_line_id)}],
    )
    ergebnis = report_service.soll_ist(auftrag.id)
    assert [p["art"] for p in ergebnis["positionen"]] == [
        report_service.MEHRVERBRAUCH
    ]


@pytest.mark.django_db
def test_zuordnung_ohne_referenz_bleibt_loesbar(app_user):
    """Gegenprobe: Ohne Berichtsbezug bleibt die Zuordnung frei setz- und lösbar —
    die Sperre gilt genau dem referenzierten Soll, nicht der Zuordnung an sich."""
    auftrag = _auftrag(app_user)
    angebot = _angebot_am_auftrag(app_user, auftrag, [_mat("Rohr", "12")])
    beleg_service.update_quote(app_user.id, quote_id=angebot.id, work_order_id=None)
    angebot.refresh_from_db()
    assert angebot.work_order_id is None
