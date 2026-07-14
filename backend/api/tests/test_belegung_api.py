"""Belegung (`tenure.occupancy` + `occupancy_party`) — Service- und API-Tests.

Die Bruchfälle sind wichtiger als der Normalfall (Lehre aus Welle 5). Sie sind
hier namentlich abgebildet:

* **Überlappender Zeitraum** an derselben Einheit → **422, nicht 500**
  (`excl_occupancy` ist ein EXCLUDE-Constraint; ohne Mapping in `gate_errors`
  wäre er ein IntegrityError).
* **COMMON_AREA / TECHNICAL_ROOM** tragen keine Belegung (Trigger
  `forbid_common_area_occupancy`, Beschluss F-12) → 422.
* **Leerstand** (Belegung ohne Beteiligte) bleibt zulässig.
* **Kein Löschen** — und der Nachweis, dass es die **DATENBANK** verbietet
  (Trigger aus 0009), nicht nur der fehlende Servicepfad.
* **MONTEUR:** sieht Mieter **seines** Objekts inkl. Telefonnummer, **nicht** die
  eines fremden — auch nicht über eine ID. Er kann **nichts** ändern.
* Der Mieterzeitraum liegt **innerhalb** der Belegung (deferred Trigger).
"""
import uuid
from datetime import date, timedelta

import pytest
from django.db import IntegrityError, connection, transaction

from db_core.models import Occupancy, OccupancyParty
from db_core.services import belegung as belegung_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service

HEUTE = date.today()
GESTERN = HEUTE - timedelta(days=1)
MORGEN = HEUTE + timedelta(days=1)


# --- Fixtures ---------------------------------------------------------------

@pytest.fixture
def objekt(app_user):
    return property_service.create_property(
        app_user.id, name="WEG Badensche Straße 53", property_type="WEG",
        street="Badensche Straße", house_number="53", postal_code="10825",
        city="Berlin",
    )


@pytest.fixture
def fremd_objekt(app_user):
    return property_service.create_property(
        app_user.id, name="Fremdes Haus", property_type="WEG",
        street="Anderswo", house_number="9", postal_code="20095", city="Hamburg",
    )


def _gebaeude(app_user, prop):
    return property_service.add_building(
        app_user.id, property_id=prop.id, building_number="1", name="Vorderhaus"
    )


def _einheit(app_user, gebaeude, nummer="EG rechts", unit_type="APARTMENT"):
    return property_service.add_unit(
        app_user.id, building_id=gebaeude.id, property_id=gebaeude.property_id,
        unit_type=unit_type, unit_number=nummer,
    )


@pytest.fixture
def einheit(app_user, objekt):
    return _einheit(app_user, _gebaeude(app_user, objekt))


def _mieter(app_user, name="Robco", telefon="030 79085327", email=None):
    """Ein ganz normaler Kontakt — genau das ist der Punkt des Slices.

    Der Mieter ist eine `identity.party` (Person) mit Kommunikationswegen:
    auffindbar, verknüpfbar, anrufbar. Kein Freitext, kein Sonderweg.
    """
    party = identity_service.create_person(
        app_user.id, first_name="Familie", last_name=name
    )
    if telefon:
        identity_service.add_contact_point(
            app_user.id, party.id, contact_type="MOBILE", value=telefon,
            is_primary=True, valid_from=GESTERN,
        )
    if email:
        identity_service.add_contact_point(
            app_user.id, party.id, contact_type="EMAIL", value=email,
            is_primary=True, valid_from=GESTERN,
        )
    return party


def _post_belegung(client, prop, **kwargs):
    daten = {
        "occupancy_type": "RENTED",
        "valid_from": HEUTE.isoformat(),
    }
    daten.update(kwargs)
    return client.post(
        f"/api/tenure/properties/{prop.id}/belegung",
        data=daten, content_type="application/json",
    )


# --- Normalfall: der Mieter hängt namentlich an der Einheit -----------------

@pytest.mark.django_db
def test_mieter_an_einheit_setzen_mit_telefonnummer(
    admin_client, app_user, objekt, einheit
):
    """Der Kernfall: Robco hängt an EG rechts — mit Nummer, die man wählen kann."""
    robco = _mieter(app_user, "Robco", telefon="0176 62147248",
                    email="robco@example.test")
    r = _post_belegung(
        admin_client, objekt,
        unit_id=str(einheit.id),
        contract_reference="MV-2024-003",
        mieter=[{"party_id": str(robco.id), "role": "CONTRACTUAL_TENANT"}],
    )
    assert r.status_code == 201, r.content
    daten = r.json()
    assert daten["occupancy_type"] == "RENTED"
    assert daten["unit_number"] == "EG rechts"
    assert len(daten["mieter"]) == 1
    m = daten["mieter"][0]
    assert m["display_name"] == robco.display_name
    # Genau dafür gibt es diesen Slice: Der Monteur muss anrufen können.
    assert m["telefon"] == "0176 62147248"
    assert m["email"] == "robco@example.test"
    assert m["is_current"] is True
    # Der Mieter ist ein normaler Kontakt — verlinkbar in die Kontaktmappe.
    assert m["party_id"] == str(robco.id)

    # …und die Vertragsreferenz bleibt eine Referenz, kein Namensversteck.
    assert daten["contract_reference"] == "MV-2024-003"


@pytest.mark.django_db
def test_liste_zeigt_jede_einheit_auch_die_unbelegte(
    admin_client, app_user, objekt
):
    """„Nicht erfasst" ist nicht „leerstehend" — die Liste unterscheidet das."""
    g = _gebaeude(app_user, objekt)
    belegt = _einheit(app_user, g, "EG links")
    unbelegt = _einheit(app_user, g, "EG rechts")
    _post_belegung(
        admin_client, objekt, unit_id=str(belegt.id),
        mieter=[{"party_id": str(_mieter(app_user, "Picolino").id),
                 "role": "CONTRACTUAL_TENANT"}],
    )
    r = admin_client.get(f"/api/tenure/properties/{objekt.id}/belegung")
    assert r.status_code == 200, r.content
    je_einheit = {z["unit_id"]: z for z in r.json()}
    assert "Picolino" in je_einheit[str(belegt.id)]["belegung"]["mieter"][0][
        "display_name"]
    # Keine Belegungszeile: „nicht erfasst", nicht „leerstehend".
    assert je_einheit[str(unbelegt.id)]["belegung"] is None
    assert je_einheit[str(unbelegt.id)]["belegbar"] is True


@pytest.mark.django_db
def test_mehrere_mieter_sind_der_normalfall(admin_client, app_user, objekt, einheit):
    """Ein Ehepaar: zwei Vertragsmieter. Eine einzelne `party_id` könnte das nicht."""
    a = _mieter(app_user, "Kutzi", telefon="030 111")
    b = _mieter(app_user, "Kutzi (Ehefrau)", telefon="030 222")
    r = _post_belegung(
        admin_client, objekt, unit_id=str(einheit.id),
        mieter=[
            {"party_id": str(a.id), "role": "CONTRACTUAL_TENANT"},
            {"party_id": str(b.id), "role": "CONTRACTUAL_TENANT"},
        ],
    )
    assert r.status_code == 201, r.content
    assert len(r.json()["mieter"]) == 2


# --- BRUCHFALL: überlappender Zeitraum → 422, nicht 500 ---------------------

@pytest.mark.django_db
def test_ueberlappender_zeitraum_ist_422_nicht_500(
    admin_client, app_user, objekt, einheit
):
    """A-18: Belegungszeiträume derselben Einheit überlappen nie.

    Der Constraint ist ein EXCLUDE (`excl_occupancy`) — er schlägt als
    IntegrityError durch, wenn ihn niemand übersetzt. Hier wird beides geprüft:
    der Normalfall (Vorprüfung im Service) und die Rennbedingung (Mapping in
    `gate_errors`).
    """
    erste = _post_belegung(admin_client, objekt, unit_id=str(einheit.id))
    assert erste.status_code == 201, erste.content

    zweite = _post_belegung(
        admin_client, objekt, unit_id=str(einheit.id),
        occupancy_type="VACANT", valid_from=MORGEN.isoformat(),
    )
    assert zweite.status_code == 422, zweite.content
    assert "überschneid" in zweite.json()["detail"].lower()
    assert Occupancy.objects.filter(unit_id=einheit.id).count() == 1


@pytest.mark.django_db
def test_ueberlappung_aus_der_rennbedingung_wird_422(app_user, einheit):
    """Der EXCLUDE-Constraint selbst (Vorprüfung übersprungen) → ValueError, nie 500.

    Beweist, dass `gate_errors` den Constraint-Namen kennt: Ohne das Mapping wäre
    das ein IntegrityError und im HTTP-Weg ein **500**.
    """
    belegung_service.create_belegung(
        app_user.id, unit_id=einheit.id, occupancy_type="RENTED",
        valid_from=HEUTE,
    )
    # Die Vorprüfung des Service umgehen: direkt an die DB, wie es zwei
    # gleichzeitige Sachbearbeiter täten.
    from db_core.db_context import business_transaction
    from db_core.gate_errors import as_business_error

    with pytest.raises(ValueError, match="überschneiden"):
        with as_business_error():
            with business_transaction(app_user.id):
                Occupancy.objects.create(
                    id=uuid.uuid4(), unit_id=einheit.id,
                    occupancy_type="VACANT", valid_from=MORGEN,
                )


@pytest.mark.django_db
def test_anschliessende_belegung_ohne_ueberlappung_geht(
    admin_client, app_user, objekt, einheit
):
    """`daterange` ist halboffen: bis MORGEN und ab MORGEN überlappen NICHT."""
    erste = _post_belegung(
        admin_client, objekt, unit_id=str(einheit.id),
        valid_from=GESTERN.isoformat(), valid_until=MORGEN.isoformat(),
    )
    assert erste.status_code == 201, erste.content
    zweite = _post_belegung(
        admin_client, objekt, unit_id=str(einheit.id),
        valid_from=MORGEN.isoformat(),
    )
    assert zweite.status_code == 201, zweite.content


# --- BRUCHFALL: COMMON_AREA / TECHNICAL_ROOM tragen keine Belegung (F-12) ---

@pytest.mark.django_db
@pytest.mark.parametrize("unit_type", ["COMMON_AREA", "TECHNICAL_ROOM"])
def test_gemeinschaftsflaeche_traegt_keine_belegung(
    admin_client, app_user, objekt, unit_type
):
    """Beschluss F-12 → 422 (Trigger `forbid_common_area_occupancy`)."""
    u = _einheit(app_user, _gebaeude(app_user, objekt), "Heizraum", unit_type)
    r = _post_belegung(admin_client, objekt, unit_id=str(u.id))
    assert r.status_code == 422, r.content
    assert "F-12" in r.json()["detail"]
    assert not Occupancy.objects.filter(unit_id=u.id).exists()


@pytest.mark.django_db
def test_gemeinschaftsflaeche_der_trigger_haelt_auch_ohne_service(app_user, objekt):
    """Der Schutz sitzt im TRIGGER, nicht in der Vorprüfung des Service.

    *Was im Service sitzt, ist umgehbar; erst was im Trigger sitzt, hält.*
    """
    from db_core.db_context import business_transaction

    u = _einheit(app_user, _gebaeude(app_user, objekt), "Technik", "TECHNICAL_ROOM")
    with pytest.raises(Exception) as exc:
        with transaction.atomic():
            with business_transaction(app_user.id):
                Occupancy.objects.create(
                    id=uuid.uuid4(), unit_id=u.id, occupancy_type="RENTED",
                    valid_from=HEUTE,
                )
    assert "F-12" in str(exc.value)


# --- Leerstand --------------------------------------------------------------

@pytest.mark.django_db
def test_leerstand_ohne_mieter_bleibt_zulaessig(
    admin_client, app_user, objekt, einheit
):
    """Leerstand = Belegung `VACANT` **ohne** Beteiligte. Muss weiter gehen."""
    r = _post_belegung(
        admin_client, objekt, unit_id=str(einheit.id), occupancy_type="VACANT",
    )
    assert r.status_code == 201, r.content
    assert r.json()["mieter"] == []
    assert r.json()["occupancy_type"] == "VACANT"


@pytest.mark.django_db
def test_leerstand_markieren_nach_auszug(admin_client, app_user, objekt, einheit):
    """Der reale Ablauf: Mieter zieht aus → Belegung enden → Leerstand ab morgen.

    **Der Fall, den erst der Test gefunden hat.** Robcos Mietverhältnis ist offen
    (`valid_until = NULL`). Wird die Belegung beendet, passt ein *offener*
    Beteiligtenzeitraum nicht mehr in eine *geschlossene* Belegung — der deferred
    Containment-Trigger weist das ab. Der häufigste Vorgang der Domäne (der
    Auszug!) wäre schlicht nicht durchführbar gewesen.

    Die Auflösung: **Endet die Belegung, endet auch, wer in ihr wohnt** — zum
    selben Tag, in derselben Transaktion (`_mieter_mitziehen`).
    """
    robco = _mieter(app_user, "Robco")
    alt = _post_belegung(
        admin_client, objekt, unit_id=str(einheit.id),
        valid_from=GESTERN.isoformat(),
        mieter=[{"party_id": str(robco.id), "role": "CONTRACTUAL_TENANT"}],
    ).json()
    assert alt["mieter"][0]["valid_until"] is None  # offen

    beendet = admin_client.patch(
        f"/api/tenure/belegung/{alt['id']}",
        data={"valid_until": MORGEN.isoformat()}, content_type="application/json",
    )
    assert beendet.status_code == 200, beendet.content
    # Der Mieter ist mitgezogen — nicht stehen geblieben, nicht gelöscht.
    assert beendet.json()["mieter"][0]["valid_until"] == MORGEN.isoformat()

    leer = _post_belegung(
        admin_client, objekt, unit_id=str(einheit.id), occupancy_type="VACANT",
        valid_from=MORGEN.isoformat(),
    )
    assert leer.status_code == 201, leer.content
    assert leer.json()["mieter"] == []


@pytest.mark.django_db
def test_frueher_ausgezogener_mieter_behaelt_sein_datum(
    admin_client, app_user, objekt, einheit
):
    """Nur **offene und überstehende** Zeilen werden gekürzt — keine Umschreibung."""
    frueher = HEUTE - timedelta(days=100)
    uebermorgen = HEUTE + timedelta(days=2)
    bel = _post_belegung(
        admin_client, objekt, unit_id=str(einheit.id),
        valid_from=frueher.isoformat(),
        mieter=[
            {"party_id": str(_mieter(app_user, "Vormieter").id),
             "role": "CONTRACTUAL_TENANT",
             "valid_from": frueher.isoformat(), "valid_until": GESTERN.isoformat()},
            {"party_id": str(_mieter(app_user, "Robco").id),
             "role": "CONTRACTUAL_TENANT", "valid_from": GESTERN.isoformat()},
        ],
    ).json()

    r = admin_client.patch(
        f"/api/tenure/belegung/{bel['id']}",
        data={"valid_until": uebermorgen.isoformat()},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    nach = {m["display_name"]: m["valid_until"] for m in r.json()["mieter"]}
    assert nach["Familie Vormieter"] == GESTERN.isoformat()   # unangetastet
    assert nach["Familie Robco"] == uebermorgen.isoformat()   # mitgezogen


@pytest.mark.django_db
def test_belegung_kann_nicht_hinter_den_einzug_geschoben_werden(
    admin_client, app_user, objekt, einheit
):
    """Wann jemand eingezogen ist, ist eine Tatsache — kein stiller Verschiebebahnhof."""
    bel = _post_belegung(
        admin_client, objekt, unit_id=str(einheit.id),
        valid_from=GESTERN.isoformat(),
        mieter=[{"party_id": str(_mieter(app_user, "Robco").id),
                 "role": "CONTRACTUAL_TENANT"}],
    ).json()
    r = admin_client.patch(
        f"/api/tenure/belegung/{bel['id']}",
        data={"valid_from": MORGEN.isoformat()}, content_type="application/json",
    )
    assert r.status_code == 422, r.content
    assert "Mieter" in r.json()["detail"]


# --- Mieterzeitraum liegt innerhalb der Belegung ----------------------------

@pytest.mark.django_db
def test_mieter_kann_nicht_vor_der_belegung_einziehen(
    admin_client, app_user, objekt, einheit
):
    """Deferred Containment-Trigger: Beteiligtenzeitraum ⊆ Belegungszeitraum."""
    r = _post_belegung(
        admin_client, objekt, unit_id=str(einheit.id), valid_from=HEUTE.isoformat(),
        mieter=[{
            "party_id": str(_mieter(app_user, "Musili").id),
            "role": "CONTRACTUAL_TENANT",
            "valid_from": GESTERN.isoformat(),
        }],
    )
    assert r.status_code == 422, r.content
    assert not Occupancy.objects.filter(unit_id=einheit.id).exists()


@pytest.mark.django_db
def test_mieter_beenden_statt_loeschen(admin_client, app_user, objekt, einheit):
    lufnik = _mieter(app_user, "Lufnik")
    bel = _post_belegung(
        admin_client, objekt, unit_id=str(einheit.id),
        valid_from=GESTERN.isoformat(),
        mieter=[{"party_id": str(lufnik.id), "role": "CONTRACTUAL_TENANT"}],
    ).json()
    zeile_id = bel["mieter"][0]["id"]

    r = admin_client.post(
        f"/api/tenure/mieter/{zeile_id}/beenden",
        data={"valid_until": MORGEN.isoformat()}, content_type="application/json",
    )
    assert r.status_code == 200, r.content
    # Die Zeile bleibt — sie ist die Antwort auf „wer wohnte hier damals?".
    assert OccupancyParty.objects.filter(pk=zeile_id).exists()
    m = r.json()["mieter"][0]
    assert m["valid_until"] == MORGEN.isoformat()
    assert m["is_current"] is True  # gilt heute noch (daterange ist halboffen)


# --- Kein Löschen: die DATENBANK verbietet es ------------------------------

@pytest.mark.django_db
def test_db_verbietet_delete_der_belegung(app_user, einheit):
    """Trigger `trg_occupancy_no_delete` (0009) — nicht nur der fehlende Pfad."""
    occ = belegung_service.create_belegung(
        app_user.id, unit_id=einheit.id, occupancy_type="RENTED", valid_from=HEUTE,
    )
    with pytest.raises(Exception) as exc:
        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute("DELETE FROM tenure.occupancy WHERE id = %s", [str(occ.id)])
    assert "append-only" in str(exc.value)


@pytest.mark.django_db
def test_db_verbietet_delete_des_mieters(app_user, einheit):
    occ = belegung_service.create_belegung(
        app_user.id, unit_id=einheit.id, occupancy_type="RENTED", valid_from=HEUTE,
        mieter=[{"party_id": _mieter(app_user, "Ruboni").id,
                 "role": "CONTRACTUAL_TENANT"}],
    )
    zeile = occ.parties.all()[0]
    with pytest.raises(Exception) as exc:
        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute(
                    "DELETE FROM tenure.occupancy_party WHERE id = %s", [str(zeile.id)]
                )
    assert "append-only" in str(exc.value)


@pytest.mark.django_db
def test_kein_delete_endpunkt(admin_client, app_user, objekt, einheit):
    occ = belegung_service.create_belegung(
        app_user.id, unit_id=einheit.id, occupancy_type="RENTED", valid_from=HEUTE,
    )
    r = admin_client.delete(f"/api/tenure/belegung/{occ.id}")
    assert r.status_code in (404, 405)


# --- MERGED-Party -----------------------------------------------------------

@pytest.mark.django_db
def test_unbekannter_mieter_ist_422_kein_500(admin_client, objekt, einheit):
    r = _post_belegung(
        admin_client, objekt, unit_id=str(einheit.id),
        mieter=[{"party_id": str(uuid.uuid4()), "role": "CONTRACTUAL_TENANT"}],
    )
    assert r.status_code == 422, r.content


@pytest.mark.django_db
def test_ungueltige_rolle_ist_422(admin_client, app_user, objekt, einheit):
    r = _post_belegung(
        admin_client, objekt, unit_id=str(einheit.id),
        mieter=[{"party_id": str(_mieter(app_user, "X").id), "role": "HAUSMEISTER"}],
    )
    assert r.status_code == 422, r.content


@pytest.mark.django_db
def test_einheit_eines_fremden_objekts_im_payload_ist_404(
    admin_client, app_user, objekt, fremd_objekt
):
    """Die Liegenschaft steht in der ROUTE — ein gefälschter Payload läuft ins Leere."""
    fremde_einheit = _einheit(app_user, _gebaeude(app_user, fremd_objekt))
    r = _post_belegung(admin_client, objekt, unit_id=str(fremde_einheit.id))
    assert r.status_code == 404, r.content


# --- row_scope 'EIGENE': die Objektsicht des Monteurs -----------------------

def _monteur_mit_einsatz(app_user, prop):
    """Ein eingeloggter MONTEUR, der auf `prop` je einen Einsatz hatte."""
    from django.test import Client

    from db_core.services import einsatz as einsatz_service

    from .conftest import make_role_user

    user, monteur = make_role_user("MONTEUR")
    job = einsatz_service.create_service_job(
        app_user.id, title="Begehung", property_id=prop.id
    )
    einsatz_service.assign_user(
        app_user.id, service_job_id=job.id, assignee_user_id=monteur.id
    )
    client = Client()
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_monteur_sieht_mieter_seines_objekts_mit_telefonnummer(
    admin_client, app_user, objekt, einheit
):
    """**Der Zweck des Slices.** Er fährt hin und muss Robco anrufen können."""
    robco = _mieter(app_user, "Robco", telefon="0176 62147248")
    _post_belegung(
        admin_client, objekt, unit_id=str(einheit.id),
        mieter=[{"party_id": str(robco.id), "role": "CONTRACTUAL_TENANT"}],
    )
    c = _monteur_mit_einsatz(app_user, objekt)

    r = c.get(f"/api/tenure/properties/{objekt.id}/belegung")
    assert r.status_code == 200, r.content
    mieter = r.json()[0]["belegung"]["mieter"][0]
    assert mieter["display_name"] == robco.display_name
    assert mieter["telefon"] == "0176 62147248"

    # Und er findet den Mieter als Kontakt (Objektsicht `eigene_party_q`).
    kontakt = c.get(f"/api/identity/parties/{robco.id}")
    assert kontakt.status_code == 200, kontakt.content


@pytest.mark.django_db
def test_monteur_sieht_fremde_mieter_nicht_404_statt_403(
    admin_client, app_user, objekt, fremd_objekt
):
    """Ein fremder Mieter ist für ihn **nicht vorhanden** — auch nicht über eine ID."""
    fremde_einheit = _einheit(app_user, _gebaeude(app_user, fremd_objekt))
    fremder = _mieter(app_user, "Fremdmieter", telefon="030 999")
    fremde_bel = _post_belegung(
        admin_client, fremd_objekt, unit_id=str(fremde_einheit.id),
        mieter=[{"party_id": str(fremder.id), "role": "CONTRACTUAL_TENANT"}],
    ).json()
    c = _monteur_mit_einsatz(app_user, objekt)

    # LESEN hat er — hier greift die OBJEKTGRENZE: 404, keine Existenzaussage.
    assert c.get(
        f"/api/tenure/properties/{fremd_objekt.id}/belegung"
    ).status_code == 404
    # Die Schreibwege weist schon das **fehlende Recht** ab: 403 — für jede ID
    # gleich, auch für eine erfundene. Er darf Mietverhältnisse nirgends ändern,
    # also verrät der 403 nichts (anders als ein 404, der die Zeile bestätigte).
    assert c.patch(
        f"/api/tenure/belegung/{fremde_bel['id']}",
        data={"occupancy_type": "VACANT"}, content_type="application/json",
    ).status_code == 403
    assert c.post(
        f"/api/tenure/mieter/{fremde_bel['mieter'][0]['id']}/beenden",
        data={"valid_until": MORGEN.isoformat()}, content_type="application/json",
    ).status_code == 403
    # Und über den Kontakt selbst: der fremde Mieter ist unsichtbar.
    assert c.get(f"/api/identity/parties/{fremder.id}").status_code == 404
    # Nichts hat sich geändert.
    assert Occupancy.objects.get(pk=fremde_bel["id"]).occupancy_type == "RENTED"


@pytest.mark.django_db
def test_monteur_darf_am_eigenen_objekt_nichts_aendern(
    admin_client, app_user, objekt, einheit
):
    """LESEN ja, ANLEGEN/AENDERN nein — Mietverhältnisse sind Sache des Büros.

    Die Sperre ist die **Abwesenheit des Rechts** (403), nicht ein Filter, den
    jemand vergessen kann.
    """
    bel = _post_belegung(
        admin_client, objekt, unit_id=str(einheit.id),
        mieter=[{"party_id": str(_mieter(app_user, "Robco").id),
                 "role": "CONTRACTUAL_TENANT"}],
    ).json()
    c = _monteur_mit_einsatz(app_user, objekt)

    assert _post_belegung(c, objekt, unit_id=str(einheit.id)).status_code == 403
    assert c.patch(
        f"/api/tenure/belegung/{bel['id']}",
        data={"occupancy_type": "VACANT"}, content_type="application/json",
    ).status_code == 403
    assert c.post(
        f"/api/tenure/belegung/{bel['id']}/mieter",
        data={"party_id": str(_mieter(app_user, "Y").id),
              "role": "OCCUPANT"},
        content_type="application/json",
    ).status_code == 403
    assert c.post(
        f"/api/tenure/mieter/{bel['mieter'][0]['id']}/beenden",
        data={"valid_until": MORGEN.isoformat()}, content_type="application/json",
    ).status_code == 403
    # Unverändert.
    assert Occupancy.objects.get(pk=bel["id"]).occupancy_type == "RENTED"


@pytest.mark.django_db
def test_anonym_ist_gesperrt(anonymous_client, objekt):
    r = anonymous_client.get(f"/api/tenure/properties/{objekt.id}/belegung")
    assert r.status_code == 401


@pytest.mark.django_db
def test_ohne_tenure_recht_403(client_with_role, objekt):
    """BUCHHALTUNG hat kein `tenure` (0026) — Mieterdaten gehen sie nichts an."""
    c = client_with_role("BUCHHALTUNG")
    r = c.get(f"/api/tenure/properties/{objekt.id}/belegung")
    assert r.status_code == 403


# --- Historie ---------------------------------------------------------------

@pytest.mark.django_db
def test_historie_zeigt_wer_damals_wohnte(admin_client, app_user, objekt, einheit):
    """„Wer wohnte hier, als der Schaden entstand?" — die eigentliche Historienfrage."""
    frueher = HEUTE - timedelta(days=400)
    vorher = belegung_service.create_belegung(
        app_user.id, unit_id=einheit.id, occupancy_type="RENTED",
        valid_from=frueher, valid_until=GESTERN,
        mieter=[{"party_id": _mieter(app_user, "Vormieter").id,
                 "role": "CONTRACTUAL_TENANT"}],
    )
    belegung_service.create_belegung(
        app_user.id, unit_id=einheit.id, occupancy_type="RENTED", valid_from=GESTERN,
        mieter=[{"party_id": _mieter(app_user, "Robco").id,
                 "role": "CONTRACTUAL_TENANT"}],
    )

    aktuell = admin_client.get(
        f"/api/tenure/properties/{objekt.id}/belegung"
    ).json()
    namen = [
        m["display_name"]
        for z in aktuell if z["belegung"]
        for m in z["belegung"]["mieter"]
    ]
    assert namen == ["Familie Robco"]

    mit_historie = admin_client.get(
        f"/api/tenure/properties/{objekt.id}/belegung?historie=true"
    ).json()
    alle = [
        m["display_name"]
        for z in mit_historie if z["belegung"]
        for m in z["belegung"]["mieter"]
    ]
    assert set(alle) == {"Familie Robco", "Familie Vormieter"}
    beendet = [z for z in mit_historie
               if z["belegung"] and z["belegung"]["id"] == str(vorher.id)][0]
    assert beendet["belegung"]["is_current"] is False


# --- Service-Ebene ----------------------------------------------------------

@pytest.mark.django_db
def test_einheit_ist_nicht_aenderbar(app_user, objekt, einheit):
    """Eine Belegung, die die Wohnung wechselt, ist keine Korrektur."""
    occ = belegung_service.create_belegung(
        app_user.id, unit_id=einheit.id, occupancy_type="RENTED", valid_from=HEUTE,
    )
    with pytest.raises(ValueError, match="nicht ändern"):
        belegung_service.update_belegung(
            app_user.id, occ.id, {"unit_id": uuid.uuid4()}
        )


@pytest.mark.django_db
def test_mietername_gehoert_nicht_in_contract_reference(app_user, einheit):
    """Kein Test des Codes, sondern der Modellierung: Der Mieter ist eine Party.

    Wäre der Name in `contract_reference` geschmuggelt, gäbe es keine
    Telefonnummer — und die Vorführung wäre genau dort stumm, wo der Chef
    hinschaut.
    """
    occ = belegung_service.create_belegung(
        app_user.id, unit_id=einheit.id, occupancy_type="RENTED", valid_from=HEUTE,
        contract_reference="MV-2024-003",
        mieter=[{"party_id": _mieter(app_user, "Robco", telefon="030 1").id,
                 "role": "CONTRACTUAL_TENANT"}],
    )
    assert occ.contract_reference == "MV-2024-003"
    zeile = occ.parties.all()[0]
    assert zeile.party.display_name == "Familie Robco"
    wege = identity_service.list_contact_points(zeile.party_id)
    assert any(w.value == "030 1" for w in wege)
