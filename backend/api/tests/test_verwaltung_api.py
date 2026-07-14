"""Verwaltungsmandat (`management.*`) — Service- und API-Tests.

Die Bruchfälle namentlich (Lehre aus Welle 5):

* Mandat mit `scope_type='ENTIRE_PROPERTY'` **und** Einheitenliste → muss
  scheitern (deferred Constraint-Trigger `assert_mandate_valid`).
* Mandat `SELECTED_UNITS` **ohne** Einheiten → muss scheitern (derselbe Trigger).
* Mandat **ohne Standardkontakt** → muss scheitern (A-10, NOT NULL).
* Zwei überlappende **Vollmandate desselben Typs** → 422, nicht 500
  (`excl_mandate_entire` ist ein EXCLUDE).
* Verwalter == Auftraggeber → muss scheitern (CHECK).
* **Kein Löschen** — die DATENBANK verbietet es (0009), nicht der fehlende Pfad.
* Der **Umfang ist unveränderlich** (`trg_mandate_unit_immutable`, A-11).
* **MONTEUR:** sieht die Verwaltung **seines** Objekts inkl. Telefonnummer,
  **nicht** die eines fremden. Er kann **nichts** ändern.
* Der Demo-Fall: WEG mit Mandat WEG_MANAGEMENT bei Stegos.
"""
import uuid
from datetime import date, timedelta

import pytest
from django.db import connection, transaction

from db_core.models import ManagementMandate, ManagementMandateUnit
from db_core.services import identity as identity_service
from db_core.services import property as property_service
from db_core.services import verwaltung as verwaltung_service

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


@pytest.fixture
def stegos(app_user):
    """Die Verwaltung — eine Organisation vom Typ PROPERTY_MANAGEMENT."""
    party = identity_service.create_organization(
        app_user.id, legal_name="Stegos Immobilien GmbH",
        organization_type="PROPERTY_MANAGEMENT",
    )
    identity_service.add_contact_point(
        app_user.id, party.id, contact_type="PHONE", value="030 79085327",
        is_primary=True, valid_from=GESTERN,
    )
    identity_service.add_contact_point(
        app_user.id, party.id, contact_type="EMAIL", value="info@stegos.net",
        is_primary=True, valid_from=GESTERN,
    )
    return party


@pytest.fixture
def weg(app_user):
    """Der Auftraggeber — die Eigentümergemeinschaft. **Nicht** der Verwalter."""
    return identity_service.create_organization(
        app_user.id, legal_name="WEG Badensche Straße 53", organization_type="WEG",
    )


@pytest.fixture
def sachbearbeiter(app_user):
    """Der Standardkontakt beim Verwalter (A-10: Pflicht)."""
    party = identity_service.create_person(
        app_user.id, first_name="Karin", last_name="Stegemann"
    )
    identity_service.add_contact_point(
        app_user.id, party.id, contact_type="MOBILE", value="0170 1234567",
        is_primary=True, valid_from=GESTERN,
    )
    return party


def _einheiten(app_user, prop, anzahl=2):
    g = property_service.add_building(
        app_user.id, property_id=prop.id, building_number="1", name="Vorderhaus"
    )
    return [
        property_service.add_unit(
            app_user.id, building_id=g.id, property_id=prop.id,
            unit_type="APARTMENT", unit_number=f"W{i + 1}",
        )
        for i in range(anzahl)
    ]


def _payload(stegos, weg, kontakt, **kwargs):
    daten = {
        "management_party_id": str(stegos.id),
        "principal_party_id": str(weg.id),
        "default_contact_party_id": str(kontakt.id),
        "mandate_type": "WEG_MANAGEMENT",
        "scope_type": "ENTIRE_PROPERTY",
        "valid_from": HEUTE.isoformat(),
    }
    daten.update(kwargs)
    return daten


def _post(client, prop, stegos, weg, kontakt, **kwargs):
    return client.post(
        f"/api/management/properties/{prop.id}/mandate",
        data=_payload(stegos, weg, kontakt, **kwargs),
        content_type="application/json",
    )


def _deferred_pruefen():
    """DEFERRED Constraint-Trigger **jetzt** feuern lassen.

    Sie prüfen normalerweise beim COMMIT. Unter `pytest-django` läuft jeder Test
    in einer äußeren Transaktion, die nie committet — der Verstoß fiele erst im
    Teardown auf und wäre dort kein Testergebnis, sondern ein Rätsel.
    `SET CONSTRAINTS ALL IMMEDIATE` zieht die Prüfung an die Stelle vor, an der
    sie im Betrieb stattfindet.
    """
    with connection.cursor() as cur:
        cur.execute("SET CONSTRAINTS ALL IMMEDIATE")


# --- Der Demo-Fall ----------------------------------------------------------

@pytest.mark.django_db
def test_demo_fall_weg_mit_mandat_bei_stegos(
    admin_client, objekt, stegos, weg, sachbearbeiter
):
    """`docs/demo-szenario.md`: Die WEG beauftragt, Stegos verwaltet.

    **Der Unterschied ist der Punkt:** Auftraggeber und Verwaltung sind zwei
    verschiedene Parteien, und die Rechnung geht später an die WEG — nicht an
    Stegos, obwohl Stegos anruft.
    """
    r = _post(admin_client, objekt, stegos, weg, sachbearbeiter,
              contract_reference="VV-2019-11")
    assert r.status_code == 201, r.content
    m = r.json()
    assert m["mandate_type"] == "WEG_MANAGEMENT"
    assert m["scope_type"] == "ENTIRE_PROPERTY"
    assert m["status"] == "ACTIVE"
    assert m["is_current"] is True
    assert m["verwaltung"]["display_name"] == "Stegos Immobilien GmbH"
    assert m["verwaltung"]["telefon"] == "030 79085327"
    assert m["verwaltung"]["email"] == "info@stegos.net"
    assert m["auftraggeber"]["display_name"] == "WEG Badensche Straße 53"
    # A-10: Wen ruft man an?
    assert m["standardkontakt"]["display_name"] == "Karin Stegemann"
    assert m["standardkontakt"]["telefon"] == "0170 1234567"
    assert m["einheiten"] == []


@pytest.mark.django_db
def test_verwaltung_ist_keine_beteiligtenrolle(objekt):
    """Die Codeliste von `property_party_role` kennt keine Verwaltung.

    Ein Test, der die **Modellierung** festhält, nicht den Code: Wer das später
    „vereinfachen" will, fällt hier auf.
    """
    assert "PROPERTY_MANAGEMENT" not in property_service.PARTY_ROLES
    assert "VERWALTUNG" not in property_service.PARTY_ROLES
    assert set(property_service.PARTY_ROLES) == {
        "COMMUNITY_OF_OWNERS", "PROPERTY_OWNER", "OPERATOR", "CARETAKER",
    }


# --- BRUCHFALL: Scope-Regeln (deferred Constraint-Trigger) ------------------

@pytest.mark.django_db
def test_entire_property_mit_einheitenliste_scheitert(
    admin_client, app_user, objekt, stegos, weg, sachbearbeiter
):
    """ENTIRE_PROPERTY **und** Einheiten → 422. Nicht beides."""
    units = _einheiten(app_user, objekt)
    r = _post(admin_client, objekt, stegos, weg, sachbearbeiter,
              scope_type="ENTIRE_PROPERTY", unit_ids=[str(units[0].id)])
    assert r.status_code == 422, r.content
    assert not ManagementMandate.objects.exists()


@pytest.mark.django_db
def test_entire_property_mit_einheiten_der_trigger_haelt_auch_ohne_service(
    app_user, objekt, stegos, weg, sachbearbeiter
):
    """Der Service ist umgehbar — der DEFERRED Trigger nicht.

    Hier wird das Mandat **an der Vorprüfung vorbei** angelegt und die Einheit
    direkt eingefügt. Der Constraint-Trigger feuert beim COMMIT.
    """
    from db_core.db_context import business_transaction

    units = _einheiten(app_user, objekt)
    with pytest.raises(Exception) as exc:
        with transaction.atomic():
            with business_transaction(app_user.id):
                m = ManagementMandate.objects.create(
                    id=uuid.uuid4(), property_id=objekt.id,
                    management_party_id=stegos.id, principal_party_id=weg.id,
                    default_contact_party_id=sachbearbeiter.id,
                    mandate_type="WEG_MANAGEMENT", scope_type="ENTIRE_PROPERTY",
                    valid_from=HEUTE, status="ACTIVE", version=1,
                )
                ManagementMandateUnit.objects.create(
                    mandate_id=m.id, property_id=objekt.id, unit_id=units[0].id
                )
            _deferred_pruefen()
    assert "ENTIRE_PROPERTY" in str(exc.value)


@pytest.mark.django_db
def test_selected_units_ohne_einheiten_scheitert(
    admin_client, objekt, stegos, weg, sachbearbeiter
):
    r = _post(admin_client, objekt, stegos, weg, sachbearbeiter,
              scope_type="SELECTED_UNITS", unit_ids=[])
    assert r.status_code == 422, r.content
    assert not ManagementMandate.objects.exists()


@pytest.mark.django_db
def test_selected_units_ohne_einheiten_der_trigger_haelt(
    app_user, objekt, stegos, weg, sachbearbeiter
):
    from db_core.db_context import business_transaction

    with pytest.raises(Exception) as exc:
        with transaction.atomic():
            with business_transaction(app_user.id):
                ManagementMandate.objects.create(
                    id=uuid.uuid4(), property_id=objekt.id,
                    management_party_id=stegos.id, principal_party_id=weg.id,
                    default_contact_party_id=sachbearbeiter.id,
                    mandate_type="WEG_MANAGEMENT", scope_type="SELECTED_UNITS",
                    valid_from=HEUTE, status="ACTIVE", version=1,
                )
            _deferred_pruefen()
    assert "SELECTED_UNITS" in str(exc.value)


@pytest.mark.django_db
def test_teilmandat_mit_einheiten_geht(
    admin_client, app_user, objekt, stegos, weg, sachbearbeiter
):
    units = _einheiten(app_user, objekt, 3)
    r = _post(
        admin_client, objekt, stegos, weg, sachbearbeiter,
        mandate_type="SPECIAL_PROPERTY_MANAGEMENT", scope_type="SELECTED_UNITS",
        unit_ids=[str(units[0].id), str(units[1].id)],
    )
    assert r.status_code == 201, r.content
    assert {e["unit_number"] for e in r.json()["einheiten"]} == {"W1", "W2"}


@pytest.mark.django_db
def test_einheit_eines_fremden_objekts_scheitert(
    admin_client, app_user, objekt, fremd_objekt, stegos, weg, sachbearbeiter
):
    """Der zusammengesetzte FK verlangt dieselbe Liegenschaft — 422 statt 500."""
    fremd = _einheiten(app_user, fremd_objekt)[0]
    r = _post(admin_client, objekt, stegos, weg, sachbearbeiter,
              scope_type="SELECTED_UNITS", unit_ids=[str(fremd.id)])
    assert r.status_code == 422, r.content
    assert "nicht zu dieser Liegenschaft" in r.json()["detail"]


# --- BRUCHFALL: Standardkontakt ist Pflicht (A-10) --------------------------

@pytest.mark.django_db
def test_mandat_ohne_standardkontakt_scheitert(
    admin_client, objekt, stegos, weg
):
    """Ohne `default_contact_party_id` → 422 (das Schema verlangt NOT NULL)."""
    daten = {
        "management_party_id": str(stegos.id),
        "principal_party_id": str(weg.id),
        "mandate_type": "WEG_MANAGEMENT",
        "scope_type": "ENTIRE_PROPERTY",
        "valid_from": HEUTE.isoformat(),
    }
    r = admin_client.post(
        f"/api/management/properties/{objekt.id}/mandate",
        data=daten, content_type="application/json",
    )
    # Pydantic weist das Pflichtfeld ab (422) — ein Mandat ohne Ansprechpartner
    # entsteht gar nicht erst.
    assert r.status_code == 422, r.content
    assert not ManagementMandate.objects.exists()


@pytest.mark.django_db
def test_service_verlangt_standardkontakt(app_user, objekt, stegos, weg):
    """Auch am Service vorbei am Schema: NOT NULL. Der Service sagt es lesbar."""
    with pytest.raises(ValueError, match="Standardkontakt"):
        verwaltung_service.create_mandat(
            app_user.id, property_id=objekt.id,
            management_party_id=stegos.id, principal_party_id=weg.id,
            default_contact_party_id=None, mandate_type="WEG_MANAGEMENT",
            scope_type="ENTIRE_PROPERTY", valid_from=HEUTE,
        )


# --- BRUCHFALL: überlappende Vollmandate desselben Typs → 422 --------------

@pytest.mark.django_db
def test_zwei_ueberlappende_vollmandate_desselben_typs_sind_422(
    admin_client, app_user, objekt, stegos, weg, sachbearbeiter
):
    """`excl_mandate_entire` (EXCLUDE) → 422, nicht 500."""
    erste = _post(admin_client, objekt, stegos, weg, sachbearbeiter)
    assert erste.status_code == 201, erste.content

    andere = identity_service.create_organization(
        app_user.id, legal_name="Andere Verwaltung GmbH",
        organization_type="PROPERTY_MANAGEMENT",
    )
    zweite = _post(admin_client, objekt, andere, weg, sachbearbeiter)
    assert zweite.status_code == 422, zweite.content
    assert ManagementMandate.objects.count() == 1


@pytest.mark.django_db
def test_anderer_mandatstyp_darf_parallel_laufen(
    admin_client, objekt, stegos, weg, sachbearbeiter
):
    """Der EXCLUDE greift **je Mandatstyp** — WEG- und Mietverwaltung parallel."""
    assert _post(admin_client, objekt, stegos, weg, sachbearbeiter).status_code == 201
    zweite = _post(admin_client, objekt, stegos, weg, sachbearbeiter,
                   mandate_type="RENTAL_MANAGEMENT")
    assert zweite.status_code == 201, zweite.content


@pytest.mark.django_db
def test_nachfolgemandat_nach_beendigung_geht(
    admin_client, app_user, objekt, stegos, weg, sachbearbeiter
):
    """Der vorgesehene Weg für den Verwalterwechsel (A-12: Zeitraumsemantik)."""
    alt = _post(admin_client, objekt, stegos, weg, sachbearbeiter,
                valid_from=GESTERN.isoformat()).json()
    beendet = admin_client.post(
        f"/api/management/mandate/{alt['id']}/beenden",
        data={"valid_until": MORGEN.isoformat()}, content_type="application/json",
    )
    assert beendet.status_code == 200, beendet.content
    assert beendet.json()["status"] == "ENDED"

    neu = identity_service.create_organization(
        app_user.id, legal_name="Neue Hausverwaltung GmbH",
        organization_type="PROPERTY_MANAGEMENT",
    )
    nachfolger = _post(admin_client, objekt, neu, weg, sachbearbeiter,
                       valid_from=MORGEN.isoformat())
    assert nachfolger.status_code == 201, nachfolger.content

    # Heute gilt noch das alte, ab morgen das neue.
    aktuell = admin_client.get(
        f"/api/management/properties/{objekt.id}/mandate"
    ).json()
    assert [m["id"] for m in aktuell] == []  # beendet: status ENDED
    mit_historie = admin_client.get(
        f"/api/management/properties/{objekt.id}/mandate?historie=true"
    ).json()
    assert len(mit_historie) == 2


# --- BRUCHFALL: Verwalter == Auftraggeber -----------------------------------

@pytest.mark.django_db
def test_verwalter_ist_nicht_auftraggeber(
    admin_client, objekt, stegos, sachbearbeiter
):
    r = _post(admin_client, objekt, stegos, stegos, sachbearbeiter)
    assert r.status_code == 422, r.content
    assert not ManagementMandate.objects.exists()


# --- Kein Löschen; Umfang unveränderlich -----------------------------------

@pytest.mark.django_db
def test_db_verbietet_delete_des_mandats(app_user, objekt, stegos, weg, sachbearbeiter):
    """Trigger `trg_mandate_no_delete` (0009). Die Rechnungen von damals bleiben."""
    m = verwaltung_service.create_mandat(
        app_user.id, property_id=objekt.id, management_party_id=stegos.id,
        principal_party_id=weg.id, default_contact_party_id=sachbearbeiter.id,
        mandate_type="WEG_MANAGEMENT", scope_type="ENTIRE_PROPERTY",
        valid_from=HEUTE,
    )
    with pytest.raises(Exception) as exc:
        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute(
                    "DELETE FROM management.management_mandate WHERE id = %s",
                    [str(m.id)],
                )
    assert "append-only" in str(exc.value)


@pytest.mark.django_db
def test_mandatseinheiten_sind_unveraenderlich(
    app_user, objekt, stegos, weg, sachbearbeiter
):
    """A-11: `trg_mandate_unit_immutable` — UPDATE und DELETE verboten.

    Deshalb bietet der Service auch keinen Weg dorthin: Der Umfang eines
    laufenden Mandats ändert sich über ein **Nachfolgemandat**.
    """
    units = _einheiten(app_user, objekt, 2)
    m = verwaltung_service.create_mandat(
        app_user.id, property_id=objekt.id, management_party_id=stegos.id,
        principal_party_id=weg.id, default_contact_party_id=sachbearbeiter.id,
        mandate_type="SPECIAL_MANDATE", scope_type="SELECTED_UNITS",
        valid_from=HEUTE, unit_ids=[units[0].id],
    )
    with pytest.raises(Exception) as exc:
        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute(
                    "DELETE FROM management.management_mandate_unit "
                    "WHERE mandate_id = %s",
                    [str(m.id)],
                )
    assert "append-only" in str(exc.value)


@pytest.mark.django_db
def test_umfang_ist_nicht_per_patch_aenderbar(
    admin_client, app_user, objekt, stegos, weg, sachbearbeiter
):
    m = _post(admin_client, objekt, stegos, weg, sachbearbeiter).json()
    r = admin_client.patch(
        f"/api/management/mandate/{m['id']}",
        data={"scope_type": "SELECTED_UNITS"}, content_type="application/json",
    )
    # Das Feld gibt es im Patch-Schema gar nicht — ninja verwirft es, der Service
    # sähe es nie. Der Umfang bleibt.
    assert r.status_code in (200, 422)
    assert ManagementMandate.objects.get(pk=m["id"]).scope_type == "ENTIRE_PROPERTY"


@pytest.mark.django_db
def test_kein_delete_endpunkt(admin_client, objekt, stegos, weg, sachbearbeiter):
    m = _post(admin_client, objekt, stegos, weg, sachbearbeiter).json()
    r = admin_client.delete(f"/api/management/mandate/{m['id']}")
    assert r.status_code in (404, 405)


# --- Standardkontakt korrigieren + Zuständigkeiten --------------------------

@pytest.mark.django_db
def test_standardkontakt_wechseln(
    admin_client, app_user, objekt, stegos, weg, sachbearbeiter
):
    m = _post(admin_client, objekt, stegos, weg, sachbearbeiter).json()
    neu = identity_service.create_person(
        app_user.id, first_name="Bernd", last_name="Neu"
    )
    r = admin_client.patch(
        f"/api/management/mandate/{m['id']}",
        data={"default_contact_party_id": str(neu.id)},
        content_type="application/json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["standardkontakt"]["display_name"] == "Bernd Neu"


@pytest.mark.django_db
def test_notfallkontakt_am_mandat(
    admin_client, app_user, objekt, stegos, weg, sachbearbeiter
):
    m = _post(admin_client, objekt, stegos, weg, sachbearbeiter).json()
    notfall = identity_service.create_person(
        app_user.id, first_name="Nacht", last_name="Dienst"
    )
    identity_service.add_contact_point(
        app_user.id, notfall.id, contact_type="MOBILE", value="0800 112",
        is_primary=True, valid_from=GESTERN,
    )
    r = admin_client.post(
        f"/api/management/mandate/{m['id']}/zustaendigkeiten",
        data={
            "responsibility_type": "EMERGENCY_CONTACT",
            "responsible_party_id": str(notfall.id),
            "valid_from": HEUTE.isoformat(),
            "priority": 1,
        },
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    z = r.json()["zustaendigkeiten"][0]
    assert z["responsibility_type"] == "EMERGENCY_CONTACT"
    assert z["telefon"] == "0800 112"
    assert z["is_current"] is True

    beendet = admin_client.post(
        f"/api/management/zustaendigkeiten/{z['id']}/beenden",
        data={"valid_until": MORGEN.isoformat()}, content_type="application/json",
    )
    assert beendet.status_code == 200, beendet.content


# --- row_scope 'EIGENE': die Objektsicht des Monteurs -----------------------

def _monteur_mit_einsatz(app_user, prop):
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
def test_monteur_sieht_verwaltung_seines_objekts(
    admin_client, app_user, objekt, stegos, weg, sachbearbeiter
):
    """Wenn niemand aufmacht, ruft er den Verwalter an — er muss ihn sehen."""
    _post(admin_client, objekt, stegos, weg, sachbearbeiter)
    c = _monteur_mit_einsatz(app_user, objekt)

    r = c.get(f"/api/management/properties/{objekt.id}/mandate")
    assert r.status_code == 200, r.content
    m = r.json()[0]
    assert m["verwaltung"]["display_name"] == "Stegos Immobilien GmbH"
    assert m["verwaltung"]["telefon"] == "030 79085327"
    assert m["standardkontakt"]["telefon"] == "0170 1234567"

    # Und er findet den Verwalter als Kontakt (Objektsicht `eigene_party_q`).
    assert c.get(f"/api/identity/parties/{stegos.id}").status_code == 200
    assert c.get(f"/api/identity/parties/{sachbearbeiter.id}").status_code == 200


@pytest.mark.django_db
def test_monteur_sieht_fremde_verwaltung_nicht_404_statt_403(
    admin_client, app_user, objekt, fremd_objekt, stegos, weg, sachbearbeiter
):
    fremde_verwaltung = identity_service.create_organization(
        app_user.id, legal_name="Fremde Verwaltung GmbH",
        organization_type="PROPERTY_MANAGEMENT",
    )
    fremdes_mandat = _post(
        admin_client, fremd_objekt, fremde_verwaltung, weg, sachbearbeiter
    ).json()
    c = _monteur_mit_einsatz(app_user, objekt)

    # LESEN hat er — deshalb greift hier die OBJEKTGRENZE: 404, keine
    # Existenzaussage über das fremde Objekt.
    assert c.get(
        f"/api/management/properties/{fremd_objekt.id}/mandate"
    ).status_code == 404
    # Die Schreibwege dagegen weist schon das **fehlende Recht** ab: 403 — und
    # zwar für jede ID gleich, auch für eine erfundene. Der Unterschied ist
    # gewollt: 403 verrät nichts (er darf es nirgends), 404 verriete sonst, dass
    # es diese Zeile gibt.
    assert c.patch(
        f"/api/management/mandate/{fremdes_mandat['id']}",
        data={"contract_reference": "gehackt"}, content_type="application/json",
    ).status_code == 403
    assert c.post(
        f"/api/management/mandate/{fremdes_mandat['id']}/beenden",
        data={"valid_until": MORGEN.isoformat()}, content_type="application/json",
    ).status_code == 403
    # Der fremde Verwalter ist auch als Kontakt unsichtbar.
    assert c.get(f"/api/identity/parties/{fremde_verwaltung.id}").status_code == 404
    # Nichts hat sich geändert.
    assert ManagementMandate.objects.get(pk=fremdes_mandat["id"]).status == "ACTIVE"


@pytest.mark.django_db
def test_monteur_darf_verwaltung_nicht_aendern(
    admin_client, app_user, objekt, stegos, weg, sachbearbeiter
):
    """LESEN ja, sonst nichts. Die Sperre ist die **Abwesenheit des Rechts** (403)."""
    m = _post(admin_client, objekt, stegos, weg, sachbearbeiter).json()
    c = _monteur_mit_einsatz(app_user, objekt)

    assert _post(c, objekt, stegos, weg, sachbearbeiter,
                 mandate_type="RENTAL_MANAGEMENT").status_code == 403
    assert c.patch(
        f"/api/management/mandate/{m['id']}",
        data={"contract_reference": "X"}, content_type="application/json",
    ).status_code == 403
    assert c.post(
        f"/api/management/mandate/{m['id']}/beenden",
        data={"valid_until": MORGEN.isoformat()}, content_type="application/json",
    ).status_code == 403
    assert ManagementMandate.objects.get(pk=m["id"]).status == "ACTIVE"


@pytest.mark.django_db
def test_anonym_ist_gesperrt(anonymous_client, objekt):
    r = anonymous_client.get(f"/api/management/properties/{objekt.id}/mandate")
    assert r.status_code == 401


@pytest.mark.django_db
def test_ohne_management_recht_403(client_with_role, objekt):
    """BUCHHALTUNG hat kein `management` (0026)."""
    c = client_with_role("BUCHHALTUNG")
    r = c.get(f"/api/management/properties/{objekt.id}/mandate")
    assert r.status_code == 403


@pytest.mark.django_db
def test_unbekanntes_objekt_ist_404(admin_client):
    r = admin_client.get(f"/api/management/properties/{uuid.uuid4()}/mandate")
    assert r.status_code == 404
