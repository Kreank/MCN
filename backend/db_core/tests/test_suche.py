"""Die Abnahmeliste des Nutzers, wörtlich als Test.

Der Nutzer kommt von einem CRM, dessen Suche ihn täglich aufhält. Seine
Beschwerden sind hier keine Anekdoten, sondern Testfälle — jeder Test unten trägt
den Satz, den er gesagt hat:

1. „Wir finden Projekte nicht, obwohl wir den genauen Straßennamen angeben."
2. „Eine Mail, die darin vorkommt, findet er nicht."
3. „Alles über 3 Ziffern wird quasi ignoriert."
4. „Selbst wenn ich die genaue Angebotsnummer angebe, findet er sie nicht oder
   listet eine elend lange Liste, irgendwo darin steht sie."

Dazu die Fälle, die eine Suche kaputtmachen, wenn man sie vergisst: der Entwurf
ohne Belegnummer (quote_number IS NULL), die stabile Reihenfolge bei Gleichstand
— und der Monteur, für den die Suche kein Datenleck sein darf.

Das Szenario ist EIN Fixture (`welt`), damit alle Tests gegen dieselbe, echte
Datenlage laufen: eine Liegenschaft in der Badenschen Straße 53 mit WEG, Projekt,
Vorgang, Auftrag, Einsätzen, Angebot und Rechnung — plus eine zweite Liegenschaft
in der Kantstraße 42, damit „42" mehrdeutig ist (Hausnummer UND Belegnummer).
"""
import ast
import inspect
import uuid
from datetime import date

import pytest
from django.db import connection

from db_core.db_context import business_transaction
from db_core.models import AppUser, Article, Party, Quote
from db_core.services import artikel as artikel_service
from db_core.services import auftrag as auftrag_service
from db_core.services import beleg as beleg_service
from db_core.services import einsatz as einsatz_service
from db_core.services import identity as identity_service
from db_core.services import mitarbeiter as mitarbeiter_service
from db_core.services import projekt as projekt_service
from db_core.services import property as property_service
from db_core.services import suche as suche_service
from db_core.services.suche import Sicht

ALLES = Sicht(
    identity=True, property=True, workflow=True, invoicing=True,
    pricing=True, hr=True,
)


def _typen(ergebnis):
    return {t.typ for t in ergebnis.treffer}


def _ids(ergebnis, typ):
    return {t.id for t in ergebnis.treffer if t.typ == typ}


def _vorziehen_der_angebotsnummer(anzahl):
    """Belegzähler AN vorspulen, damit das nächste Angebot AN-JJJJ-000042 wird.

    workflow.next_number ist die einzige Stelle, die Belegnummern vergibt (die DB
    lässt keine gesetzte Nummer zu). Statt sie zu umgehen, wird sie hier
    schlicht mehrfach gezogen — das ist genau das, was 41 andere Angebote auch
    getan hätten.
    """
    with connection.cursor() as cur:
        cur.execute(
            "SELECT workflow.next_number('AN') FROM generate_series(1, %s)",
            [anzahl],
        )


@pytest.fixture
def welt(app_user):
    """Das Abnahme-Szenario. Gibt ein dict mit allen Beteiligten zurück."""
    u = app_user.id

    # --- Liegenschaft Badensche Straße 53 (der Kern aller Beschwerden) -------
    obj = property_service.create_property(
        u, name="Wohnanlage Wilmersdorf", property_type="WEG",
        street="Badensche Straße", house_number="53",
        postal_code="10825", city="Berlin",
    )
    gebaeude = property_service.add_building(
        u, property_id=obj.id, building_number="1", name="Vorderhaus",
    )
    for nr in range(1, 7):
        property_service.add_unit(
            u, building_id=gebaeude.id, property_id=obj.id,
            unit_type="APARTMENT", unit_number=f"WE{nr}",
        )

    # WEG als Beteiligte der Liegenschaft — der Kontakt, den „Badensche" finden muss.
    weg = identity_service.create_organization(
        u, legal_name="WEG Badensche Straße 53", organization_type="WEG",
    )
    property_service.add_party_role(
        u, property_id=obj.id, party_id=weg.id, role="COMMUNITY_OF_OWNERS",
        valid_from=date(2020, 1, 1),
    )

    # Melderin mit E-Mail und Telefonnummer (Beschwerde 2 und 6).
    melderin = identity_service.create_person(
        u, first_name="Erika", last_name="Meier",
    )
    identity_service.add_contact_point(
        u, melderin.id, contact_type="EMAIL",
        value="erika.meier@hausverwaltung.test", is_primary=True,
    )
    identity_service.add_contact_point(
        u, melderin.id, contact_type="PHONE", value="030 79085327", is_primary=True,
    )

    # --- Zweite Liegenschaft: Hausnummer 42 (damit „42" mehrdeutig ist) ------
    obj42 = property_service.create_property(
        u, name="Ladenlokal Charlottenburg", property_type="COMMERCIAL",
        street="Kantstraße", house_number="42", postal_code="10625", city="Berlin",
    )

    # --- Projekt: heißt bewusst ANDERS als die Straße ------------------------
    projekt = projekt_service.create_project(
        u, name="Dachsanierung Etappe 2", property_ids=[obj.id],
    )

    # --- Vorgang mit Melderin ------------------------------------------------
    vorgang = projekt_service.create_service_case(
        u, property_id=obj.id, subject="Wasserschaden Dachgeschoss",
        reported_by_party_id=melderin.id, project_id=projekt.id,
    )

    # --- Auftrag mit Beteiligten (WEG + Melderin) ----------------------------
    auftrag = auftrag_service.create_work_order(
        u, property_id=obj.id, title="Dachrinne erneuern",
        service_case_id=vorgang.id, project_id=projekt.id,
    )
    auftrag_service.add_work_order_party(
        u, work_order_id=auftrag.id, party_id=weg.id, role="PRINCIPAL",
        is_primary=True,
    )
    auftrag_service.add_work_order_party(
        u, work_order_id=auftrag.id, party_id=melderin.id, role="REPORTER",
    )

    # --- Einsätze: einer dem Monteur zugewiesen, einer nicht -----------------
    monteur = AppUser.objects.create(
        id=uuid.uuid4(), display_name="Mario Monteur",
        status="ACTIVE", version=1,
    )
    einsatz_eigen = einsatz_service.create_service_job(
        u, work_order_id=auftrag.id, title="Gerüst stellen",
    )
    einsatz_service.assign_user(
        u, service_job_id=einsatz_eigen.id, assignee_user_id=monteur.id,
    )
    einsatz_fremd = einsatz_service.create_service_job(
        u, work_order_id=auftrag.id, title="Rinne montieren",
    )

    # --- Angebot mit der Nummer AN-JJJJ-000042 (Beschwerde 4 und 3) ----------
    _vorziehen_der_angebotsnummer(41)
    angebot = beleg_service.create_quote(
        u, property_id=obj.id, title="Dachrinne Material und Montage",
        work_order_id=auftrag.id,
        lines=[{
            "line_type": "MATERIAL", "description": "Zinkrinne 6-teilig",
            "quantity": "12", "unit": "m", "unit_price": "24.00",
            "tax_code": "DE_19",
        }],
    )
    angebot = beleg_service.send_quote(u, quote_id=angebot.id)
    assert angebot.quote_number.endswith("-000042"), angebot.quote_number

    # --- Angebot im ENTWURF: quote_number IS NULL (Testfall 9) ---------------
    entwurf = beleg_service.create_quote(
        u, property_id=obj.id, title="Fassade Badensche — Vorabschätzung",
    )
    assert entwurf.quote_number is None

    # --- Rechnung (ENTWURF) mit der WEG als Schuldnerin ----------------------
    rechnung = beleg_service.create_invoice(
        u, property_id=obj.id, work_order_id=auftrag.id,
        lines=[{
            "line_type": "MATERIAL", "description": "Zinkrinne 6-teilig",
            "quantity": "12", "unit": "m", "unit_price": "24.00",
            "tax_code": "DE_19",
        }],
    )
    beleg_service.add_invoice_party(
        u, invoice_id=rechnung.id, party_id=weg.id, role="INVOICE_DEBTOR",
        is_primary=True,
    )

    # --- Stammdaten: Artikel (mit GTIN), Leistung, Mitarbeiter ---------------
    artikel = artikel_service.create_article(
        u, article_number="ZR-6000", description="Zinkrinne 6-teilig",
        unit="m", manufacturer_name="Rheinzink", matchcode="ZINKRINNE",
    )
    with business_transaction(u):
        Article.objects.filter(id=artikel.id).update(gtin="4012345678901")
    leistung = artikel_service.create_assembly(
        u, assembly_number="L-DACH-01", name="Dachrinne montieren", unit="m",
    )
    person_ma = identity_service.create_person(
        u, first_name="Mario", last_name="Monteur",
    )
    mitarbeiter = mitarbeiter_service.create_employee(
        u, app_user_id=monteur.id, party_id=person_ma.id, hired_on=date(2024, 1, 1),
    )

    return {
        "user": app_user, "monteur": monteur, "obj": obj, "obj42": obj42,
        "weg": weg, "melderin": melderin, "projekt": projekt, "vorgang": vorgang,
        "auftrag": auftrag, "einsatz_eigen": einsatz_eigen,
        "einsatz_fremd": einsatz_fremd, "angebot": angebot, "entwurf": entwurf,
        "rechnung": rechnung, "artikel": artikel, "leistung": leistung,
        "mitarbeiter": mitarbeiter,
    }


# ---------------------------------------------------------------------------
# Normalisierung (die Grundlage von allem)
# ---------------------------------------------------------------------------

def test_normalisierung_entfaltet_umlaute_und_wirft_zeichen_weg():
    assert suche_service.normalisieren("Badensche Straße 53") == "badenschestrasse53"
    assert suche_service.normalisieren("Müller-Lüdenscheidt") == "muellerluedenscheidt"
    assert suche_service.normalisieren("AN-2026-000042") == "an2026000042"
    assert suche_service.nur_ziffern("+49 (30) 790-853") == "4930790853"


def test_kennung_wird_tolerant_erkannt():
    assert suche_service.kennung_parsen("AN-2026-000042") == ("ANGEBOT", "AN-2026-000042")
    assert suche_service.kennung_parsen("an 2026 000042") == ("ANGEBOT", "AN-2026-000042")
    assert suche_service.kennung_parsen("an-2026-42") == ("ANGEBOT", "AN-2026-000042")
    assert suche_service.kennung_parsen("obj 1") == ("LIEGENSCHAFT", "OBJ-00001")
    assert suche_service.kennung_parsen("MA-00007") == ("MITARBEITER", "MA-00007")
    # Kein Muster → kein Direkttreffer-Pfad (die Volltextsuche greift trotzdem).
    assert suche_service.kennung_parsen("Badensche") is None
    assert suche_service.kennung_parsen("AN-2026") is None  # ohne Zähler


# ---------------------------------------------------------------------------
# 1. „Wir finden Projekte nicht, obwohl wir den genauen Straßennamen angeben."
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_badensche_findet_liegenschaft_kontakt_auftrag_und_rechnung(welt):
    e = suche_service.suche("Badensche", sicht=ALLES)
    typen = _typen(e)
    assert "LIEGENSCHAFT" in typen and welt["obj"].id in _ids(e, "LIEGENSCHAFT")
    assert "KONTAKT" in typen and welt["weg"].id in _ids(e, "KONTAKT")
    assert "AUFTRAG" in typen and welt["auftrag"].id in _ids(e, "AUFTRAG")
    assert "RECHNUNG" in typen and welt["rechnung"].id in _ids(e, "RECHNUNG")
    # Und der Auftrag heißt „Dachrinne erneuern" — er wurde ÜBER DIE ADRESSE
    # gefunden. Genau das kann das Altsystem nicht.
    auftrag = next(t for t in e.treffer if t.typ == "AUFTRAG")
    assert auftrag.rang == 3
    assert "Adresse" in auftrag.grund


@pytest.mark.django_db
def test_badensche_strasse_53_findet_das_projekt_ueber_die_liegenschaft(welt):
    """Das Projekt heißt „Dachsanierung Etappe 2" — kein Token steht in seinen
    eigenen Feldern. Gefunden wird es über die Adresse der Liegenschaft."""
    e = suche_service.suche("Badensche Straße 53", sicht=ALLES)
    assert welt["projekt"].id in _ids(e, "PROJEKT")
    projekt = next(t for t in e.treffer if t.typ == "PROJEKT")
    assert projekt.rang == 3
    assert projekt.grund == "Adresse der Liegenschaft"
    # Der Untertitel trägt den Kontext, der die Trefferliste erst brauchbar macht.
    assert "Badensche Straße 53" in projekt.untertitel


@pytest.mark.django_db
def test_liegenschaft_untertitel_traegt_typ_adresse_und_einheiten(welt):
    e = suche_service.suche("Badensche", sicht=ALLES)
    liegenschaft = next(t for t in e.treffer if t.typ == "LIEGENSCHAFT")
    assert "WEG" in liegenschaft.untertitel
    assert "Badensche Straße 53, 10825 Berlin" in liegenschaft.untertitel
    assert "6 Einheiten" in liegenschaft.untertitel


# ---------------------------------------------------------------------------
# 2. „Eine Mail, die darin vorkommt, findet er nicht."
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_mailadresse_der_melderin_findet_vorgang_auftrag_und_kontakt(welt):
    e = suche_service.suche("erika.meier@hausverwaltung.test", sicht=ALLES)
    assert welt["vorgang"].id in _ids(e, "VORGANG")
    assert welt["auftrag"].id in _ids(e, "AUFTRAG")
    assert welt["melderin"].id in _ids(e, "KONTAKT")
    vorgang = next(t for t in e.treffer if t.typ == "VORGANG")
    assert vorgang.rang == 3
    assert "Kontaktweg" in vorgang.grund


@pytest.mark.django_db
def test_mail_der_weg_findet_rechnung_und_liegenschaft(welt, app_user):
    """Die Rechnung hat KEIN Titelfeld — über die Mail ihrer Schuldnerin muss sie
    trotzdem auffindbar sein. Dasselbe gilt für die Liegenschaft, an der dieselbe
    WEG als Eigentümergemeinschaft hängt."""
    identity_service.add_contact_point(
        app_user.id, welt["weg"].id, contact_type="EMAIL",
        value="buchhaltung@zahlstelle.test", is_primary=True,
    )
    e = suche_service.suche("buchhaltung@zahlstelle.test", sicht=ALLES)
    assert welt["rechnung"].id in _ids(e, "RECHNUNG")
    assert welt["obj"].id in _ids(e, "LIEGENSCHAFT")
    assert welt["auftrag"].id in _ids(e, "AUFTRAG")
    assert welt["weg"].id in _ids(e, "KONTAKT")
    rechnung = next(t for t in e.treffer if t.typ == "RECHNUNG")
    assert "Kontaktweg" in rechnung.grund


@pytest.mark.django_db
def test_telefonnummer_findet_den_kontakt_mit_und_ohne_leerzeichen(welt):
    for begriff in ("030 79085327", "03079085327", "79085327"):
        e = suche_service.suche(begriff, sicht=ALLES)
        assert welt["melderin"].id in _ids(e, "KONTAKT"), begriff


# ---------------------------------------------------------------------------
# 3. „Alles über 3 Ziffern wird quasi ignoriert."
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_nackte_ziffern_treffen_belegnummer_und_hausnummer(welt):
    e = suche_service.suche("42", sicht=ALLES)
    # Beleg mit Nummer …000042
    assert welt["angebot"].id in _ids(e, "ANGEBOT")
    # UND die Hausnummer 42 der zweiten Liegenschaft
    assert welt["obj42"].id in _ids(e, "LIEGENSCHAFT")
    # Kein Direkttreffer — „42" ist keine Kennung, sondern eine Ähnlichkeit.
    assert e.direkttreffer is None


@pytest.mark.django_db
def test_lange_ziffernfolge_wird_nicht_ignoriert(welt):
    """Sechsstellige Zahl aus der Belegnummer — im Altsystem verschwunden."""
    e = suche_service.suche("000042", sicht=ALLES)
    assert welt["angebot"].id in _ids(e, "ANGEBOT")


# ---------------------------------------------------------------------------
# 4. „Selbst wenn ich die genaue Angebotsnummer angebe …"
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_exakte_angebotsnummer_ist_treffer_eins_und_direkttreffer(welt):
    nummer = welt["angebot"].quote_number
    e = suche_service.suche(nummer, sicht=ALLES)
    assert e.treffer[0].typ == "ANGEBOT"
    assert e.treffer[0].id == welt["angebot"].id
    assert e.treffer[0].rang == 0
    assert e.treffer[0].ist_direkttreffer is True
    assert e.direkttreffer is not None and e.direkttreffer.id == welt["angebot"].id
    # Das Angebot steht GENAU EINMAL in der Liste (kein Doppel aus Volltextpfad).
    assert [t.id for t in e.treffer].count(welt["angebot"].id) == 1


@pytest.mark.django_db
def test_kennung_auch_ohne_nullen_und_ohne_bindestriche(welt):
    jahr = welt["angebot"].quote_number.split("-")[1]
    for begriff in (f"an-{jahr}-42", f"AN {jahr} 000042", f"an {jahr} 42"):
        e = suche_service.suche(begriff, sicht=ALLES)
        assert e.direkttreffer is not None, begriff
        assert e.direkttreffer.id == welt["angebot"].id, begriff


@pytest.mark.django_db
def test_objektnummer_und_personalnummer_sind_direkttreffer(welt):
    e = suche_service.suche(welt["obj"].property_number.lower(), sicht=ALLES)
    assert e.direkttreffer is not None
    assert e.direkttreffer.typ == "LIEGENSCHAFT"
    assert e.direkttreffer.id == welt["obj"].id

    e = suche_service.suche(welt["mitarbeiter"].employee_number, sicht=ALLES)
    assert e.direkttreffer is not None
    assert e.direkttreffer.typ == "MITARBEITER"


@pytest.mark.django_db
def test_gtin_und_artikelnummer_sind_direkttreffer(welt):
    e = suche_service.suche("4012345678901", sicht=ALLES)
    assert e.direkttreffer is not None
    assert e.direkttreffer.typ == "ARTIKEL"
    assert e.direkttreffer.id == welt["artikel"].id

    e = suche_service.suche("ZR-6000", sicht=ALLES)
    assert e.direkttreffer is not None and e.direkttreffer.id == welt["artikel"].id

    e = suche_service.suche("L-DACH-01", sicht=ALLES)
    assert e.direkttreffer is not None and e.direkttreffer.typ == "LEISTUNG"


# ---------------------------------------------------------------------------
# Abgekürzt/vertippt, UND-Semantik, Entwurf ohne Nummer
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_badenschestr_findet_die_badensche_strasse(welt):
    """Nach der Normalisierung ist „badenschestr" ein Teilstring von
    „badenschestrasse" — abgekürzte Eingabe trifft trotzdem."""
    e = suche_service.suche("badenschestr", sicht=ALLES)
    assert welt["obj"].id in _ids(e, "LIEGENSCHAFT")
    assert welt["projekt"].id in _ids(e, "PROJEKT")


@pytest.mark.django_db
def test_jedes_token_muss_treffen(welt):
    """UND über Tokens: ein Token, das nirgends vorkommt, killt den Treffer."""
    assert welt["obj"].id in _ids(
        suche_service.suche("Badensche Berlin", sicht=ALLES), "LIEGENSCHAFT")
    assert welt["obj"].id not in _ids(
        suche_service.suche("Badensche Hamburg", sicht=ALLES), "LIEGENSCHAFT")


@pytest.mark.django_db
def test_angebot_im_entwurf_ohne_nummer_kracht_nicht(welt):
    """quote_number IS NULL — die Suche muss den Entwurf trotzdem finden."""
    e = suche_service.suche("Fassade", sicht=ALLES)
    assert welt["entwurf"].id in _ids(e, "ANGEBOT")
    entwurf = next(t for t in e.treffer if t.id == welt["entwurf"].id)
    assert "ohne Nummer" in entwurf.untertitel
    # Auch über die Adresse (nur Beziehung, keine Nummer) findet die Suche ihn.
    assert welt["entwurf"].id in _ids(
        suche_service.suche("Badensche", sicht=ALLES), "ANGEBOT")


@pytest.mark.django_db
def test_rechnung_ohne_titelfeld_wird_ueber_beteiligte_gefunden(welt):
    e = suche_service.suche("WEG Badensche", sicht=ALLES)
    assert welt["rechnung"].id in _ids(e, "RECHNUNG")


@pytest.mark.django_db
def test_leerer_und_zu_kurzer_begriff_liefern_leere_liste(welt):
    # „a b": beide Tokens einstellig → kein Token trägt → keine Suche, kein Scan.
    for begriff in ("", "   ", None, "#", "a", "a b"):
        e = suche_service.suche(begriff, sicht=ALLES)
        assert e.treffer == []
        assert e.direkttreffer is None


@pytest.mark.django_db
def test_absurd_langer_begriff_wird_gekappt_statt_die_db_zu_quaelen(welt):
    """Ein hineinkopierter Absatz ist kein 422 — er wird gekürzt (Begriff und
    Tokenzahl), damit ein einziger GET keinen Worker bindet."""
    begriff = "Badensche " + " ".join(f"wort{i}" for i in range(200))
    e = suche_service.suche(begriff, sicht=ALLES)
    assert len(suche_service.tokenisieren(begriff)) == suche_service.MAX_TOKENS
    # Die überzähligen Tokens sind weg — der erste (Badensche) trägt weiter, die
    # UND-Semantik der verbliebenen Fantasiewörter lässt trotzdem nichts übrig.
    assert e.treffer == []


# ---------------------------------------------------------------------------
# Rang und Reihenfolge
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_rang_trennt_primaerfeld_von_beziehung(welt):
    """Wortanfang im Namen (1) < Teilstring im Namen (2) < nur Beziehung (3)."""
    e = suche_service.suche("Wilmersdorf", sicht=ALLES)
    liegenschaft = next(t for t in e.treffer if t.typ == "LIEGENSCHAFT")
    assert liegenschaft.rang == 1  # „Wohnanlage Wilmersdorf" — Wortanfang

    e = suche_service.suche("ilmersdorf", sicht=ALLES)
    liegenschaft = next(t for t in e.treffer if t.typ == "LIEGENSCHAFT")
    assert liegenschaft.rang == 2  # Teilstring, kein Wortanfang


@pytest.mark.django_db
def test_gleicher_rang_ergibt_stabile_reihenfolge(app_user):
    """Zwei Treffer gleichen Rangs — die Reihenfolge darf nicht flackern."""
    for name in ("Ahorn Hof", "Ahorn Park", "Ahorn Allee"):
        property_service.create_property(
            app_user.id, name=name, property_type="WEG", street="Teststraße",
            house_number="1", postal_code="10115", city="Berlin",
        )
    laeufe = [
        [(t.typ, t.id) for t in suche_service.suche("Ahorn", sicht=ALLES).treffer]
        for _ in range(5)
    ]
    assert all(lauf == laeufe[0] for lauf in laeufe)
    assert len(laeufe[0]) == 3


@pytest.mark.django_db
def test_begrenzung_je_kategorie_und_mehr_vorhanden(app_user):
    for i in range(7):
        property_service.create_property(
            app_user.id, name=f"Ahorn {i}", property_type="WEG",
            street="Teststraße", house_number="1", postal_code="10115",
            city="Berlin",
        )
    e = suche_service.suche("Ahorn", sicht=ALLES)
    assert len(_ids(e, "LIEGENSCHAFT")) == 5
    kategorie = next(k for k in e.kategorien if k.typ == "LIEGENSCHAFT")
    assert kategorie.anzahl == 5
    assert kategorie.mehr_vorhanden is True


# ---------------------------------------------------------------------------
# Rechte — der Bruchfall
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ohne_rechte_gibt_es_keine_treffer_aber_auch_keinen_fehler(welt):
    e = suche_service.suche("Badensche", sicht=Sicht())
    assert e.treffer == []
    assert e.kategorien == []


@pytest.mark.django_db
def test_fehlendes_modul_laesst_nur_seine_kategorie_weg(welt):
    """Kein invoicing → keine Belege. Alles andere bleibt findbar."""
    sicht = Sicht(identity=True, property=True, workflow=True)
    e = suche_service.suche("Badensche", sicht=sicht)
    assert "ANGEBOT" not in _typen(e) and "RECHNUNG" not in _typen(e)
    assert "LIEGENSCHAFT" in _typen(e) and "AUFTRAG" in _typen(e)


@pytest.mark.django_db
def test_ohne_property_recht_keine_strasse_im_untertitel(welt):
    """Die Suche zieht keine Grenze neu.

    `api/auftrag.py` und `api/planung.py` geben zu einem Auftrag Objektnummer,
    -name und ORT heraus — nie Straße und Hausnummer. Die volle Adresse steht
    ausschließlich hinter den `property`-getorten Schemata. Ein Untertitel, der
    sie trotzdem zeigte, wäre der bequemste Weg, an genau diesem Tor vorbeizulesen.
    """
    nur_workflow = Sicht(workflow=True)
    e = suche_service.suche("Dachrinne", sicht=nur_workflow)
    auftrag = next(t for t in e.treffer if t.typ == "AUFTRAG")
    assert "Badensche" not in auftrag.untertitel
    assert "53" not in auftrag.untertitel
    assert "Berlin" in auftrag.untertitel  # Ort bleibt — wie in den Listen

    # Mit property-Recht ist die Straße wieder da.
    e = suche_service.suche("Dachrinne", sicht=Sicht(workflow=True, property=True))
    auftrag = next(t for t in e.treffer if t.typ == "AUFTRAG")
    assert "Badensche Straße 53, 10825 Berlin" in auftrag.untertitel


@pytest.mark.django_db
def test_ohne_identity_recht_keine_suche_ueber_kontaktwege(welt):
    """Sonst wäre die Suche ein Auskunftsdienst: E-Mail rein, Name raus.

    Ein Konto mit `workflow` (aber ohne `identity`) darf Vorgänge sehen — aber
    nicht die Kontaktdaten dahinter. Dann darf es auch nicht über eine
    Mailadresse auf den Vorgang schließen und im Untertitel den Namen der
    Melderin geliefert bekommen.
    """
    mail = "erika.meier@hausverwaltung.test"
    e = suche_service.suche(mail, sicht=Sicht(workflow=True))
    assert e.treffer == []

    # Der Vorgang selbst bleibt findbar — nur eben nicht über die Mail.
    e = suche_service.suche("Wasserschaden", sicht=Sicht(workflow=True))
    vorgang = next(t for t in e.treffer if t.typ == "VORGANG")
    assert "Erika" not in vorgang.untertitel
    assert "Kontaktweg" not in vorgang.grund

    # Mit identity-Recht funktioniert beides wieder.
    sicht = Sicht(workflow=True, identity=True)
    e = suche_service.suche(mail, sicht=sicht)
    assert welt["vorgang"].id in _ids(e, "VORGANG")
    e = suche_service.suche("Wasserschaden", sicht=sicht)
    vorgang = next(t for t in e.treffer if t.typ == "VORGANG")
    assert "Erika Meier" in vorgang.untertitel


@pytest.mark.django_db
def test_zweistelliger_begriff_durchsucht_den_artikelstamm_nicht(welt):
    """Ein Trigramm braucht drei Zeichen — zwei wären auf 800.000 Artikeln ein
    Vollscan (gemessen 1,1 s je Tastendruck). Belege und Adressen (kleine
    Tabellen) finden „42" unverändert."""
    e = suche_service.suche("zr", sicht=ALLES)
    assert "ARTIKEL" not in _typen(e)
    # Drei Zeichen: der Artikelstamm ist wieder dabei.
    e = suche_service.suche("zin", sicht=ALLES)
    assert welt["artikel"].id in _ids(e, "ARTIKEL")


@pytest.mark.django_db
def test_monteur_findet_ausschliesslich_eigene_einsaetze(welt):
    """DER Bruchfall: row_scope EIGENE auf workflow.

    Der Monteur darf seine Einsätze finden — und sonst NICHTS. Kein Auftrag, kein
    Vorgang, kein Kontakt, keine Liegenschaft, keine Rechnung. Jede andere
    Kategorie hat für ihn keine definierte Zeilenbegrenzung; sie fällt deshalb
    ganz weg (fail-closed), statt ungefilterte Zeilen preiszugeben.
    """
    sicht = Sicht(workflow_eigene=True, actor_id=welt["monteur"].id)
    e = suche_service.suche("Badensche", sicht=sicht)

    assert _typen(e) <= {"EINSATZ"}
    assert _ids(e, "EINSATZ") == {welt["einsatz_eigen"].id}
    assert welt["einsatz_fremd"].id not in _ids(e, "EINSATZ")

    # Auch die Kennung des Auftrags öffnet ihm keine Tür (Direkttreffer-Pfad
    # respektiert dieselben Rechte).
    e = suche_service.suche(welt["auftrag"].order_number, sicht=sicht)
    assert e.direkttreffer is None
    assert _typen(e) <= {"EINSATZ"}

    # Und ohne Akteur (kein app_user) gibt es auch keine „eigenen" Zeilen.
    e = suche_service.suche("Badensche", sicht=Sicht(workflow_eigene=True))
    assert e.treffer == []


@pytest.mark.django_db
def test_monteur_findet_eigenen_einsatz_ueber_die_adresse(welt):
    """Die Beziehungssuche gilt auch für ihn — nur eben auf seinen Zeilen."""
    sicht = Sicht(workflow_eigene=True, actor_id=welt["monteur"].id)
    e = suche_service.suche("Badensche Straße 53", sicht=sicht)
    assert _ids(e, "EINSATZ") == {welt["einsatz_eigen"].id}


@pytest.mark.django_db
def test_merged_kontakte_tauchen_nie_auf(welt, app_user):
    """MERGED = fachlich verschwundene Dublette — nie ein Suchtreffer."""
    dublette = identity_service.create_organization(
        app_user.id, legal_name="WEG Badensche Str. 53 (alt)",
        organization_type="WEG",
    )
    with business_transaction(app_user.id):
        Party.objects.filter(id=dublette.id).update(
            status="MERGED", merged_into_party_id=welt["weg"].id,
        )
    e = suche_service.suche("Badensche", sicht=ALLES)
    assert dublette.id not in _ids(e, "KONTAKT")
    assert welt["weg"].id in _ids(e, "KONTAKT")


# ---------------------------------------------------------------------------
# Artikel: die Hero-Operatoren bleiben erhalten
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_artikel_hero_operatoren_funktionieren_weiter(welt):
    assert welt["artikel"].id in _ids(
        suche_service.suche("Zinkrinne+6", sicht=ALLES), "ARTIKEL")
    assert welt["artikel"].id in _ids(
        suche_service.suche("Zink*rinne", sicht=ALLES), "ARTIKEL")
    # Und die gewöhnliche Mehrwortsuche (ohne Operatoren) trifft ebenfalls.
    assert welt["artikel"].id in _ids(
        suche_service.suche("Zinkrinne Rheinzink", sicht=ALLES), "ARTIKEL")


@pytest.mark.django_db
def test_suche_schreibt_nichts(welt):
    """Rein lesend — verhaltensbasiert UND statisch am Syntaxbaum.

    Der statische Teil (AST, nicht Textsuche: Docstrings und Kommentare dürfen
    das Wort nennen) fängt den Fall, den kein Verhaltenstest sieht — einen
    Schreibpfad in einem selten betretenen Zweig. Eine Suche, die schreibt, ist
    keine Suche; und jede KI-Anfrage löst sie aus.
    """
    vorher = Quote.objects.count()
    suche_service.suche("Badensche Straße 53", sicht=ALLES)
    suche_service.suche(welt["angebot"].quote_number, sicht=ALLES)
    assert Quote.objects.count() == vorher

    baum = ast.parse(inspect.getsource(suche_service))
    verboten = {"business_transaction", "save", "delete", "update", "create",
                "bulk_create", "get_or_create", "raw"}
    aufrufe = {
        knoten.func.id if isinstance(knoten.func, ast.Name) else knoten.func.attr
        for knoten in ast.walk(baum)
        if isinstance(knoten, ast.Call)
        and isinstance(knoten.func, (ast.Name, ast.Attribute))
    }
    assert not (aufrufe & verboten), f"Schreibpfad im Suchservice: {aufrufe & verboten}"
