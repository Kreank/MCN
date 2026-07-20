"""KI-Assistent — Fragesätze, Gesprächs-Fokus und der Offen-Überblick.

Diese Datei deckt den Ausbau vom 2026-07-20 ab. Drei Dinge, die vorher fehlten und
jeweils dieselbe Nicht-Antwort erzeugten („Im übergebenen Kontext stehen keine
Informationen"):

* **Fragesatz statt Suchbegriff** — die globale Suche verknüpft Tokens mit UND.
  Ein ganzer Fragesatz macht jedes Füllwort zur Pflicht und findet nichts, obwohl
  das Objekt existiert. Geprüft wird der Originalfall des Users.
* **Gesprächs-Fokus** — eine Nachfrage nennt das Objekt nicht mehr. Ohne Fokus
  sucht der Assistent nach „die Rechnungen" und verliert das Thema.
* **Rückfrage statt Detailflut** — „Was ist alles offen?" wird mit einem Menü aus
  Zählungen beantwortet, sobald mehrere Kategorien belegt sind; darunter antwortet
  er direkt. Und: eine Kategorie, die das Konto nicht lesen darf, taucht im Menü
  **gar nicht** auf (sonst verriete schon die Zeile, dass es sie gibt).

Das Modell ist wie nebenan ein `FakeBackend` — deterministisch, ohne Netz.
"""
import pytest

from db_core.ai import assistent
from db_core.ai.llm import FakeBackend
from db_core.services import auftrag as auftrag_service
from db_core.services import identity as identity_service
from db_core.services import projekt as projekt_service
from db_core.services import property as property_service
from db_core.services.dossier import Sicht as DossierSicht
from db_core.services.suche import Sicht as SucheSicht


def _sicht(actor_id, *, invoicing=True):
    such = SucheSicht(
        identity=True, property=True, workflow=True, invoicing=invoicing,
        pricing=True, hr=True, actor_id=actor_id,
    )
    doss = DossierSicht(
        identity=True, property=True, workflow=True, invoicing=invoicing,
        pricing=True, content=True, maintenance=True, actor_id=actor_id,
    )
    return assistent.AssistentSicht(
        such_sicht=such, dossier_sicht=doss,
        workflow_alle=True, invoicing_alle=invoicing, darf_anlegen=True,
    )


def _fake(*responses):
    return FakeBackend(responses=list(responses))


@pytest.fixture
def weg(app_user):
    """Eine WEG mit dem Namensschnitt aus dem Originalfall (Straße + Hausnummer)."""
    return property_service.create_property(
        app_user.id, name="WEG Albrechtstr 30", property_type="WEG",
        street="Albrechtstr", house_number="30",
        postal_code="12167", city="Berlin",
    )


@pytest.fixture
def weg_mit_offenen_punkten(app_user, weg):
    """Genug offene Punkte in zwei Kategorien, dass die Rückfrage greift."""
    u = app_user.id
    melder = identity_service.create_person(u, first_name="Erika", last_name="Petersen")
    projekt = projekt_service.create_project(
        u, name="Sanierung Nord", property_ids=[weg.id])
    for betreff in ("Defektes WC", "Heizkörper kalt", "Tiefgaragentor klemmt"):
        projekt_service.create_service_case(
            u, property_id=weg.id, subject=betreff,
            reported_by_party_id=melder.id, project_id=projekt.id)
    vorgang = projekt_service.create_service_case(
        u, property_id=weg.id, subject="Rohrbruch Keller",
        reported_by_party_id=melder.id, project_id=projekt.id)
    auftrag_service.create_work_order(
        u, property_id=weg.id, title="Rohrsanierung Kellergeschoss",
        service_case_id=vorgang.id, project_id=projekt.id)
    return weg


# --- Der Originalfall: ein ganzer Fragesatz --------------------------------

def test_fragesatz_findet_das_objekt(app_user, weg):
    """„WEG Albrechtstr. 30: Was ist alles offen?" — genau die Frage, die nichts fand.

    Vorher: die Tokens `was`/`ist`/`alles` wurden Pflicht, die Suche lieferte 0
    Treffer und der Assistent hatte einen leeren Kontext.
    """
    treffer = assistent._suchtreffer(
        "WEG Albrechtstr. 30: Was ist alles offen?", _sicht(app_user.id))
    assert [t["id"] for t in treffer].count(str(weg.id)) == 1


def test_floskeln_allein_suchen_nicht(app_user, weg):
    """Eine Frage ganz ohne Suchbegriff darf nicht das halbe Haus zurückgeben."""
    assert assistent._suchbegriffe("Was ist das?") == []
    assert assistent._suchtreffer("Was ist das?", _sicht(app_user.id)) == []


def test_hausnummer_ueberlebt_die_letzte_stufe(app_user):
    """Die weiteste Stufe behält Ziffern — sonst trifft sie jedes Haus der Straße."""
    stufen = assistent._suchbegriffe("Was ist alles offen in der Albrechtstr. 30?")
    assert "30" in stufen[-1]


# --- Gesprächs-Fokus -------------------------------------------------------

def test_nachfrage_behaelt_das_objekt(app_user, weg_mit_offenen_punkten):
    """Zweiter Turn nennt das Objekt nicht mehr — der Fokus trägt es weiter."""
    conv = assistent.starte_gespraech(app_user.id)
    erst = assistent.antworte(
        app_user.id, conversation=conv,
        frage="WEG Albrechtstr. 30: Was ist alles offen?",
        sicht=_sicht(app_user.id),
        backend=_fake({"intent": "AUSKUNFT", "entitaeten": [0], "kategorie": "ALLE"}),
    )
    assert erst.antwort_turn.sources, "erste Antwort muss eine Quelle setzen"

    # Reine Nachfrage: kein Objektname, keine Adresse — ohne Fokus fände die Suche nichts.
    zweit = assistent.antworte(
        app_user.id, conversation=conv, frage="Und die Vorgänge?",
        sicht=_sicht(app_user.id),
        backend=_fake(
            {"intent": "AUSKUNFT", "entitaeten": [0], "kategorie": "VORGANG"},
            {"antwort": "Vier Vorgänge sind offen.", "quellen": [0]},
        ),
    )
    assert zweit.antwort_turn.sources[0]["id"] == str(weg_mit_offenen_punkten.id)


def test_fokus_ist_keine_rechte_abkuerzung(app_user, weg_mit_offenen_punkten):
    """Wer das Objekt nicht mehr finden darf, bekommt es auch nicht über den Fokus."""
    conv = assistent.starte_gespraech(app_user.id)
    assistent.antworte(
        app_user.id, conversation=conv, frage="WEG Albrechtstr 30",
        sicht=_sicht(app_user.id),
        backend=_fake({"intent": "AUSKUNFT", "entitaeten": [0], "kategorie": "KEINE"},
                      {"antwort": "Das Objekt.", "quellen": [0]}),
    )
    # Konto ohne jedes Objektrecht: der Fokus darf die Objektsicht nicht aushebeln.
    blind = assistent.AssistentSicht(
        such_sicht=SucheSicht(actor_id=app_user.id),
        dossier_sicht=DossierSicht(actor_id=app_user.id),
    )
    assert assistent._fokus_treffer(conv, blind) == []


# --- Rückfrage statt Detailflut --------------------------------------------

def test_rueckfrage_bei_offener_frage(app_user, weg_mit_offenen_punkten):
    """Mehrere Kategorien belegt → Menü mit Zählungen, Intent RUECKFRAGE.

    Der Menütext entsteht ohne zweiten Modellaufruf: Das FakeBackend liefert NUR
    die Plan-Antwort. Bräuchte der Pfad einen Antwort-Call, liefe das Skript leer
    und der Test fiele in den Fallback.
    """
    conv = assistent.starte_gespraech(app_user.id)
    res = assistent.antworte(
        app_user.id, conversation=conv,
        frage="WEG Albrechtstr. 30: Was ist alles offen?",
        sicht=_sicht(app_user.id),
        backend=_fake({"intent": "AUSKUNFT", "entitaeten": [0], "kategorie": "ALLE"}),
    )
    assert res.antwort_turn.intent == "RUECKFRAGE"
    text = res.antwort_turn.content
    assert "4 Vorgänge" in text
    assert "1 Auftrag" in text and "1 Aufträge" not in text   # Singular korrekt
    assert text.rstrip().endswith("?")                        # es ist eine Frage
    # Das Objekt ist zitiert — damit trägt der Fokus es in den nächsten Turn.
    assert res.antwort_turn.sources[0]["id"] == str(weg_mit_offenen_punkten.id)


def test_keine_rueckfrage_wenn_wenig_offen(app_user, weg):
    """Ein einzelner offener Punkt → direkt antworten, nicht zurückfragen."""
    u = app_user.id
    melder = identity_service.create_person(u, first_name="Kurt", last_name="Einzel")
    projekt_service.create_service_case(
        u, property_id=weg.id, subject="Einzelner Vorgang",
        reported_by_party_id=melder.id)
    conv = assistent.starte_gespraech(u)
    res = assistent.antworte(
        u, conversation=conv, frage="WEG Albrechtstr 30: was ist offen?",
        sicht=_sicht(u),
        backend=_fake({"intent": "AUSKUNFT", "entitaeten": [0], "kategorie": "ALLE"},
                      {"antwort": "Ein Vorgang ist offen.", "quellen": [0]}),
    )
    assert res.antwort_turn.intent == "AUSKUNFT"


def test_kategorie_wird_tief_geladen(app_user, weg_mit_offenen_punkten):
    """Nennt der Nutzer eine Kategorie, kommen die ZEILEN in den Kontext — nicht nur
    die Anzahl. Sonst könnte der Assistent nicht sagen, WELCHE Vorgänge offen sind."""
    plan = {"intent": "AUSKUNFT", "entitaeten": [0], "kategorie": "VORGANG"}
    treffer = assistent._suchtreffer("WEG Albrechtstr 30", _sicht(app_user.id))
    offen = assistent._offen_pfad(plan, treffer, _sicht(app_user.id))
    assert offen is not None and offen["rueckfrage"] is False
    betreffs = {z["subject"] for z in offen["detail"]}
    assert "Defektes WC" in betreffs


# --- Rechtegrenze im Menü --------------------------------------------------

def test_menue_verschweigt_gesperrte_kategorie(app_user, weg_mit_offenen_punkten):
    """Ohne `invoicing` darf im Überblick keine Geld-Kategorie auftauchen.

    Nicht mit Anzahl 0, sondern **gar nicht**: Schon die Zeile „0 offene Rechnungen"
    wäre eine Auskunft über Daten, die dieses Konto nicht lesen darf.
    """
    ohne_geld = _sicht(app_user.id, invoicing=False)
    from db_core.services import dossier as dossier_service
    ueberblick = dossier_service.offen_ueberblick(
        weg_mit_offenen_punkten.id, ohne_geld.dossier_sicht)
    assert "POSTEN" not in ueberblick
    assert dossier_service.offen_detail(
        weg_mit_offenen_punkten.id, ohne_geld.dossier_sicht, "POSTEN") is None
