"""Eigentum an einer Einheit (Arbeitspaket AP5).

Die Tabellen `tenure.ownership_period`/`ownership_interest` liegen seit
Migration 0005 in der Datenbank — mit Bruchanteilen, exakter
Vollständigkeitsprüfung, Quellenpflicht und Bestätigung. Benutzt wurden sie von
**null** Backend-Zeilen; der Reiter „Eigentum" zeigte einen Platzhalter.

Der Kern dieser Tests ist der Anteil als **Bruch**: Drei Erben zu je 1/3 müssen
durchgehen, „33,33 % dreimal" darf es nicht. Und: Die Datenbank prüft das erst
beim COMMIT — der Service muss vorher eine Meldung liefern, mit der jemand
etwas anfangen kann.
"""
from datetime import date

import pytest

from db_core.services import eigentum as eigentum_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service


@pytest.fixture
def wohnung(app_user):
    """Eine Liegenschaft mit einer Wohnung und drei möglichen Eigentümern."""
    a = app_user.id
    prop = property_service.create_property(
        a, name="Wohnanlage Ahornweg", property_type="WEG",
        street="Ahornweg", house_number="7", postal_code="10115", city="Berlin",
    )
    haus = property_service.add_building(
        a, property_id=prop.id, building_number="1", name="Vorderhaus"
    )
    einheit = property_service.add_unit(
        a, building_id=haus.id, property_id=prop.id,
        unit_type="APARTMENT", unit_number="WE 12", storey="3. OG",
    )
    erben = [
        identity_service.create_person(a, first_name=vor, last_name="Erbe")
        for vor in ("Anna", "Bernd", "Clara")
    ]
    return {"actor": a, "prop": prop, "haus": haus, "einheit": einheit, "erben": erben}


def _stand(daten, *, status="UNRESOLVED", eigentuemer=None, valid_from=None):
    return eigentum_service.create_stand(
        daten["actor"],
        unit_id=daten["einheit"].id,
        valid_from=valid_from or date(2024, 1, 1),
        source_type="OWNER_LIST",
        source_reference="Eigentümerliste der Verwaltung vom 12.03.2026",
        distribution_status=status,
        eigentuemer=eigentuemer or [],
    )


# --- Der Anteil ist ein Bruch ----------------------------------------------

@pytest.mark.django_db
def test_drei_erben_zu_je_einem_drittel_gehen_durch(wohnung):
    """Der Fall, an dem eine Dezimalrechnung scheitern würde.

    1/3 + 1/3 + 1/3 ist exakt 1. „33,33 % dreimal" wäre 99,99 % — ein
    vollständiger Eigentumsstand wäre mit Dezimalzahlen nie erreichbar.
    """
    stand = _stand(
        wohnung,
        status="COMPLETE",
        eigentuemer=[
            {
                "party_id": e.id,
                "share_numerator": 1,
                "share_denominator": 3,
                "confirmation_status": "CONFIRMED",
            }
            for e in wohnung["erben"]
        ],
    )
    assert stand.distribution_status == "COMPLETE"
    assert stand.interests.count() == 3


@pytest.mark.django_db
def test_gerundete_anteile_werden_abgelehnt(wohnung):
    """333/1000 dreimal ergibt 999/1000 — knapp daneben ist auch daneben."""
    with pytest.raises(eigentum_service.EigentumError, match="nicht genau 1"):
        _stand(
            wohnung,
            status="COMPLETE",
            eigentuemer=[
                {
                    "party_id": e.id,
                    "share_numerator": 333,
                    "share_denominator": 1000,
                    "confirmation_status": "CONFIRMED",
                }
                for e in wohnung["erben"]
            ],
        )


@pytest.mark.django_db
def test_ungleiche_anteile_gehen_auf(wohnung):
    """1/2 + 1/4 + 1/4 — verschiedene Nenner, exakt 1."""
    anteile = [(1, 2), (1, 4), (1, 4)]
    stand = _stand(
        wohnung,
        status="COMPLETE",
        eigentuemer=[
            {
                "party_id": e.id,
                "share_numerator": z,
                "share_denominator": n,
                "confirmation_status": "CONFIRMED",
            }
            for e, (z, n) in zip(wohnung["erben"], anteile)
        ],
    )
    assert stand.interests.count() == 3


@pytest.mark.django_db
def test_zu_viel_wird_abgelehnt(wohnung):
    with pytest.raises(eigentum_service.EigentumError, match="nicht genau 1"):
        _stand(
            wohnung,
            status="COMPLETE",
            eigentuemer=[
                {
                    "party_id": wohnung["erben"][0].id,
                    "share_numerator": 3,
                    "share_denominator": 4,
                    "confirmation_status": "CONFIRMED",
                },
                {
                    "party_id": wohnung["erben"][1].id,
                    "share_numerator": 1,
                    "share_denominator": 2,
                    "confirmation_status": "CONFIRMED",
                },
            ],
        )


# --- Die drei Vollständigkeitsgrade ----------------------------------------

@pytest.mark.django_db
def test_teilweise_geklaert_darf_lueckenhaft_sein(wohnung):
    """Der Alltag: Man kennt einen von vier Eigentümern.

    Genau dafür gibt es PARTIAL — das Modell zwingt niemanden, etwas zu
    behaupten, was er nicht weiß.
    """
    stand = _stand(
        wohnung,
        status="PARTIAL",
        eigentuemer=[{"party_id": wohnung["erben"][0].id}],
    )
    assert stand.distribution_status == "PARTIAL"
    assert stand.interests.first().share_numerator is None


@pytest.mark.django_db
def test_ungeklaert_ohne_jeden_eigentuemer(wohnung):
    """„Wir wissen, dass es die Wohnung gibt, mehr nicht."" """
    stand = _stand(wohnung, status="UNRESOLVED")
    assert stand.interests.count() == 0


@pytest.mark.django_db
def test_vollstaendig_ohne_eigentuemer_wird_abgelehnt(wohnung):
    with pytest.raises(eigentum_service.EigentumError, match="mindestens einen"):
        _stand(wohnung, status="COMPLETE")


@pytest.mark.django_db
def test_vollstaendig_mit_unbestaetigtem_wird_abgelehnt(wohnung):
    with pytest.raises(eigentum_service.EigentumError, match="nicht bestätigt"):
        _stand(
            wohnung,
            status="COMPLETE",
            eigentuemer=[
                {
                    "party_id": wohnung["erben"][0].id,
                    "share_numerator": 1,
                    "share_denominator": 1,
                    "confirmation_status": "UNCONFIRMED",
                }
            ],
        )


@pytest.mark.django_db
def test_alleineigentum_vertraegt_keinen_zweiten(wohnung):
    with pytest.raises(eigentum_service.EigentumError, match="Alleineigentum"):
        _stand(
            wohnung,
            status="COMPLETE",
            eigentuemer=[
                {
                    "party_id": wohnung["erben"][0].id,
                    "share_numerator": 1,
                    "share_denominator": 2,
                    "ownership_type": "SOLE",
                    "confirmation_status": "CONFIRMED",
                },
                {
                    "party_id": wohnung["erben"][1].id,
                    "share_numerator": 1,
                    "share_denominator": 2,
                    "confirmation_status": "CONFIRMED",
                },
            ],
        )


# --- Zeitraum und Kette ----------------------------------------------------

@pytest.mark.django_db
def test_zwei_staende_duerfen_sich_nicht_ueberlappen(wohnung):
    _stand(wohnung, valid_from=date(2024, 1, 1))
    with pytest.raises(eigentum_service.EigentumError, match="bereits einen"):
        _stand(wohnung, valid_from=date(2025, 1, 1))


@pytest.mark.django_db
def test_eigentuemerwechsel_ueber_beenden_und_neu(wohnung):
    """Der einzige Weg: Beteiligungen lassen sich nicht austauschen.

    Es gibt kein Löschen (0009) und keinen eigenen Zeitraum an der Beteiligung —
    ein Verkauf ist deshalb immer ein neuer Stand.
    """
    a = wohnung["actor"]
    alt = _stand(
        wohnung,
        status="COMPLETE",
        valid_from=date(2024, 1, 1),
        eigentuemer=[
            {
                "party_id": wohnung["erben"][0].id,
                "share_numerator": 1,
                "share_denominator": 1,
                "ownership_type": "SOLE",
                "confirmation_status": "CONFIRMED",
            }
        ],
    )
    eigentum_service.beenden(a, alt.id, valid_until=date(2026, 3, 1))
    neu = _stand(
        wohnung,
        status="COMPLETE",
        valid_from=date(2026, 3, 1),
        eigentuemer=[
            {
                "party_id": wohnung["erben"][1].id,
                "share_numerator": 1,
                "share_denominator": 1,
                "ownership_type": "SOLE",
                "confirmation_status": "CONFIRMED",
            }
        ],
    )
    kette = list(eigentum_service.staende_der_einheit(wohnung["einheit"].id))
    assert [s.id for s in kette] == [neu.id, alt.id], "jüngster Stand zuerst"


@pytest.mark.django_db
def test_beteiligung_laesst_sich_nicht_umhaengen(wohnung):
    stand = _stand(
        wohnung, status="PARTIAL",
        eigentuemer=[{"party_id": wohnung["erben"][0].id}],
    )
    beteiligung = stand.interests.first()
    with pytest.raises(eigentum_service.EigentumError, match="nicht austauschen"):
        eigentum_service.update_eigentuemer(
            wohnung["actor"],
            beteiligung.id,
            {"owner_party_id": wohnung["erben"][1].id},
        )


# --- Gemeinschaftsflächen (A-08) -------------------------------------------

@pytest.mark.django_db
def test_gemeinschaftsflaeche_traegt_keinen_eigentumsstand(wohnung):
    """Das Eigentum am Treppenhaus folgt der Gemeinschaft."""
    a = wohnung["actor"]
    flur = property_service.add_unit(
        a, building_id=wohnung["haus"].id, property_id=wohnung["prop"].id,
        unit_type="COMMON_AREA", unit_number="Treppenhaus",
    )
    with pytest.raises(eigentum_service.EigentumError, match="Gemeinschaft"):
        eigentum_service.create_stand(
            a, unit_id=flur.id, valid_from=date(2024, 1, 1),
            source_type="OWNER_LIST", source_reference="Liste",
        )


# --- Quellenpflicht (A-14) -------------------------------------------------

@pytest.mark.django_db
def test_ohne_quelle_kein_eigentumsstand(wohnung):
    """Wer behauptet, wem etwas gehört, muss sagen, woher er das hat."""
    with pytest.raises(eigentum_service.EigentumError, match="Quelle ist Pflicht"):
        eigentum_service.create_stand(
            wohnung["actor"],
            unit_id=wohnung["einheit"].id,
            valid_from=date(2024, 1, 1),
            source_type="OWNER_LIST",
            source_reference="   ",
        )


# --- Der Weg zu den Rechnungsadressen --------------------------------------

@pytest.mark.django_db
def test_eigentuemer_der_liegenschaft_fuer_die_empfaengerauswahl(wohnung):
    """Saschas „20 Rechnungsadressen, die ich immer angeben muss".

    Auch ein bloß vermuteter Eigentümer in einem PARTIAL-Stand ist jemand, an
    den man eine Rechnung schreiben kann — deshalb über alle
    Vollständigkeitsgrade.
    """
    a = wohnung["actor"]
    zweite = property_service.add_unit(
        a, building_id=wohnung["haus"].id, property_id=wohnung["prop"].id,
        unit_type="APARTMENT", unit_number="WE 13",
    )
    _stand(
        wohnung, status="PARTIAL",
        eigentuemer=[{"party_id": wohnung["erben"][0].id}],
    )
    eigentum_service.create_stand(
        a, unit_id=zweite.id, valid_from=date(2024, 1, 1),
        source_type="OWNER_LIST", source_reference="Liste",
        distribution_status="PARTIAL",
        eigentuemer=[
            {"party_id": wohnung["erben"][1].id},
            # Derselbe Eigentümer wie in der ersten Wohnung — er darf in der
            # Empfängerliste nur EINMAL erscheinen.
            {"party_id": wohnung["erben"][0].id},
        ],
    )
    namen = [
        p.display_name
        for p in eigentum_service.eigentuemer_der_liegenschaft(wohnung["prop"].id)
    ]
    assert namen == ["Anna Erbe", "Bernd Erbe"]


@pytest.mark.django_db
def test_beendeter_stand_zaehlt_nicht_mehr_zu_den_eigentuemern(wohnung):
    a = wohnung["actor"]
    alt = _stand(
        wohnung, status="PARTIAL", valid_from=date(2020, 1, 1),
        eigentuemer=[{"party_id": wohnung["erben"][0].id}],
    )
    eigentum_service.beenden(a, alt.id, valid_until=date(2021, 1, 1))
    assert eigentum_service.eigentuemer_der_liegenschaft(wohnung["prop"].id) == []


# --- API -------------------------------------------------------------------

@pytest.mark.django_db
def test_api_liste_zeigt_einheiten_ohne_stand_als_nicht_erfasst(
    admin_client, wohnung
):
    r = admin_client.get(f"/api/tenure/properties/{wohnung['prop'].id}/eigentum")
    assert r.status_code == 200, r.content
    zeilen = r.json()
    assert len(zeilen) == 1
    assert zeilen[0]["eigentum"] is None, "nicht erfasst, nicht „gehört niemandem“"
    assert zeilen[0]["eigentumsfaehig"] is True


@pytest.mark.django_db
def test_api_legt_stand_an_und_zeigt_den_anteil_lesbar(admin_client, wohnung):
    r = admin_client.post(
        f"/api/tenure/properties/{wohnung['prop'].id}/eigentum",
        data={
            "unit_id": str(wohnung["einheit"].id),
            "valid_from": "2024-01-01",
            "source_type": "OWNER_LIST",
            "source_reference": "Eigentümerliste vom 12.03.2026",
            "distribution_status": "COMPLETE",
            "eigentuemer": [
                {
                    "party_id": str(wohnung["erben"][0].id),
                    "share_numerator": 1,
                    "share_denominator": 2,
                    "confirmation_status": "CONFIRMED",
                },
                {
                    "party_id": str(wohnung["erben"][1].id),
                    "share_numerator": 1,
                    "share_denominator": 2,
                    "confirmation_status": "CONFIRMED",
                },
            ],
        },
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    anteile = sorted(e["anteil_text"] for e in r.json()["eigentuemer"])
    assert anteile == ["50 %", "50 %"], "glatte Anteile liest man als Prozent"


@pytest.mark.django_db
def test_api_zeigt_krumme_anteile_als_bruch(admin_client, wohnung):
    """„1/3" ist die Wahrheit, „33,33 %" wäre gerundet."""
    r = admin_client.post(
        f"/api/tenure/properties/{wohnung['prop'].id}/eigentum",
        data={
            "unit_id": str(wohnung["einheit"].id),
            "valid_from": "2024-01-01",
            "source_type": "OWNER_LIST",
            "source_reference": "Liste",
            "distribution_status": "COMPLETE",
            "eigentuemer": [
                {
                    "party_id": str(e.id),
                    "share_numerator": 1,
                    "share_denominator": 3,
                    "confirmation_status": "CONFIRMED",
                }
                for e in wohnung["erben"]
            ],
        },
        content_type="application/json",
    )
    assert r.status_code == 201, r.content
    assert {e["anteil_text"] for e in r.json()["eigentuemer"]} == {"1/3"}


@pytest.mark.django_db
def test_api_meldet_den_anteilsfehler_lesbar_statt_500(admin_client, wohnung):
    """Der DEFERRED-Trigger meldet erst beim COMMIT und ohne Feldbezug.

    Der Service prüft deshalb vorher — hier zählt, dass ein 422 mit einer
    verständlichen Meldung herauskommt, nicht ein 500 aus der Datenbank.
    """
    r = admin_client.post(
        f"/api/tenure/properties/{wohnung['prop'].id}/eigentum",
        data={
            "unit_id": str(wohnung["einheit"].id),
            "valid_from": "2024-01-01",
            "source_type": "OWNER_LIST",
            "source_reference": "Liste",
            "distribution_status": "COMPLETE",
            "eigentuemer": [
                {
                    "party_id": str(wohnung["erben"][0].id),
                    "share_numerator": 1,
                    "share_denominator": 3,
                    "confirmation_status": "CONFIRMED",
                }
            ],
        },
        content_type="application/json",
    )
    assert r.status_code == 422, r.content
    assert "1/3" in r.json()["detail"]


@pytest.mark.django_db
def test_api_verweigert_fremde_einheit_an_meiner_liegenschaft(
    admin_client, wohnung, app_user
):
    """Die Einheit muss zur Liegenschaft der Route gehören — sonst 404.

    Ohne diese Prüfung ließe sich über die eigene Liegenschaft ein Stand an
    einer fremden Einheit anlegen; die Objektgrenze wäre dekorativ.
    """
    fremd = property_service.create_property(
        app_user.id, name="Fremdes Objekt", property_type="WEG",
        street="Anderswo", postal_code="20095", city="Hamburg",
    )
    fremdes_haus = property_service.add_building(
        app_user.id, property_id=fremd.id, building_number="1"
    )
    fremde_einheit = property_service.add_unit(
        app_user.id, building_id=fremdes_haus.id, property_id=fremd.id,
        unit_type="APARTMENT", unit_number="WE 1",
    )
    r = admin_client.post(
        f"/api/tenure/properties/{wohnung['prop'].id}/eigentum",
        data={
            "unit_id": str(fremde_einheit.id),
            "valid_from": "2024-01-01",
            "source_type": "OWNER_LIST",
            "source_reference": "Liste",
        },
        content_type="application/json",
    )
    assert r.status_code == 404, r.content


@pytest.mark.django_db
def test_api_eigentuemerliste_fuer_die_empfaengerauswahl(admin_client, wohnung):
    _stand(
        wohnung, status="PARTIAL",
        eigentuemer=[{"party_id": wohnung["erben"][0].id}],
    )
    r = admin_client.get(
        f"/api/tenure/properties/{wohnung['prop'].id}/eigentuemer"
    )
    assert r.status_code == 200, r.content
    assert [e["display_name"] for e in r.json()] == ["Anna Erbe"]


# --- Die Datenbank hinter dem Service --------------------------------------

@pytest.mark.django_db
def test_die_datenbank_haelt_auch_ohne_die_vorpruefung(wohnung):
    """Am Service vorbei: Die DB lässt einen unvollständigen COMPLETE nicht zu.

    **Warum `SET CONSTRAINTS ALL IMMEDIATE`?** `trg_ownership_interest_totals`
    ist DEFERRABLE INITIALLY DEFERRED — er feuert erst beim COMMIT. Unter
    pytest ist jede Transaktion aber nur ein Savepoint, es gibt gar keinen
    Commit; der Trigger wäre in der gesamten Suite **inert**, und ein Test
    ohne diese Zeile prüfte nur die Python-Vorprüfung. Genau das war der Fall,
    bis der Review es aufdeckte.

    `SET CONSTRAINTS ALL IMMEDIATE` zieht die aufgeschobene Prüfung an dieser
    Stelle vor — ohne `transaction=True`, das den bekannten Teardown-Konflikt
    mit den No-Truncate-Triggern auslöst.

    Der Weg führt bewusst **nicht** über den Service: Geprüft wird ja gerade,
    dass die Sperre auch dann greift, wenn die Vorprüfung umgangen wird (zweiter
    Sachbearbeiter, künftiger Codepfad, direkter SQL-Zugriff).
    """
    import uuid as _uuid

    from django.db import connection, transaction as dj_transaction
    from django.db.utils import InternalError, ProgrammingError

    period_id = _uuid.uuid4()
    with pytest.raises((ProgrammingError, InternalError), match="100 Prozent"):
        with dj_transaction.atomic():
            with connection.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO tenure.ownership_period
                        (id, unit_id, distribution_status, valid_from,
                         source_type, source_reference)
                    VALUES (%s, %s, 'COMPLETE', DATE '2024-01-01',
                            'OWNER_LIST', 'Direkt am Service vorbei')
                    """,
                    [period_id, wohnung["einheit"].id],
                )
                # Ein einziger Drittel-Anteil in einem VOLLSTÄNDIGEN Stand.
                cur.execute(
                    """
                    INSERT INTO tenure.ownership_interest
                        (id, ownership_period_id, owner_party_id,
                         share_numerator, share_denominator,
                         ownership_type, confirmation_status)
                    VALUES (%s, %s, %s, 1, 3, 'CO_OWNER', 'CONFIRMED')
                    """,
                    [_uuid.uuid4(), period_id, wohnung["erben"][0].id],
                )
                # Hier schlägt der Trigger zu — nicht beim INSERT.
                cur.execute("SET CONSTRAINTS ALL IMMEDIATE")


@pytest.mark.django_db
def test_die_datenbank_laesst_den_gueltigen_stand_durch(wohnung):
    """Gegenprobe: Derselbe Weg mit drei Dritteln geht durch.

    Ohne diese Gegenprobe bewiese der Test oben nur, dass IRGENDETWAS
    scheitert — nicht, dass die Prüfung die richtige Grenze zieht.
    """
    import uuid as _uuid

    from django.db import connection, transaction as dj_transaction

    period_id = _uuid.uuid4()
    with dj_transaction.atomic():
        with connection.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tenure.ownership_period
                    (id, unit_id, distribution_status, valid_from,
                     source_type, source_reference)
                VALUES (%s, %s, 'COMPLETE', DATE '2024-01-01',
                        'OWNER_LIST', 'Direkt am Service vorbei')
                """,
                [period_id, wohnung["einheit"].id],
            )
            for erbe in wohnung["erben"]:
                cur.execute(
                    """
                    INSERT INTO tenure.ownership_interest
                        (id, ownership_period_id, owner_party_id,
                         share_numerator, share_denominator,
                         ownership_type, confirmation_status)
                    VALUES (%s, %s, %s, 1, 3, 'CO_OWNER', 'CONFIRMED')
                    """,
                    [_uuid.uuid4(), period_id, erbe.id],
                )
            cur.execute("SET CONSTRAINTS ALL IMMEDIATE")


# --- Die bisher ungetestete Schreibfläche ----------------------------------

@pytest.mark.django_db
def test_eigentuemer_nachtragen_und_stand_hochstufen(wohnung):
    """Der Weg, den der Alltag nimmt — und der ohne UI eine Sackgasse wäre.

    Erst „teilweise geklärt" mit einem unbestätigten Eigentümer, dann kommt die
    Eigentümerliste: zweiter Eigentümer dazu, beide Anteile beziffert und
    bestätigt, Stand auf „vollständig geklärt".
    """
    a = wohnung["actor"]
    stand = _stand(
        wohnung,
        status="PARTIAL",
        eigentuemer=[{"party_id": wohnung["erben"][0].id}],
    )
    stand = eigentum_service.add_eigentuemer(
        a,
        period_id=stand.id,
        party_id=wohnung["erben"][1].id,
        share_numerator=1,
        share_denominator=2,
        confirmation_status="CONFIRMED",
    )
    assert stand.interests.count() == 2

    # Hochstufen scheitert, solange der erste weder Anteil noch Bestätigung hat.
    with pytest.raises(eigentum_service.EigentumError):
        eigentum_service.update_stand(a, stand.id, {"distribution_status": "COMPLETE"})

    offen = next(i for i in stand.interests.all() if i.share_numerator is None)
    eigentum_service.update_eigentuemer(
        a,
        offen.id,
        {
            "share_numerator": 1,
            "share_denominator": 2,
            "confirmation_status": "CONFIRMED",
        },
    )
    fertig = eigentum_service.update_stand(
        a, stand.id, {"distribution_status": "COMPLETE"}
    )
    assert fertig.distribution_status == "COMPLETE"


@pytest.mark.django_db
def test_derselbe_eigentuemer_nicht_zweimal_am_stand(wohnung):
    a = wohnung["actor"]
    stand = _stand(
        wohnung, status="PARTIAL",
        eigentuemer=[{"party_id": wohnung["erben"][0].id}],
    )
    with pytest.raises(eigentum_service.EigentumError, match="bereits beteiligt"):
        eigentum_service.add_eigentuemer(
            a, period_id=stand.id, party_id=wohnung["erben"][0].id
        )


@pytest.mark.django_db
def test_bestaetigen_setzt_zeitpunkt_und_person(wohnung):
    a = wohnung["actor"]
    stand = _stand(wohnung, status="PARTIAL",
                   eigentuemer=[{"party_id": wohnung["erben"][0].id}])
    assert stand.confirmed_at is None

    bestaetigt = eigentum_service.bestaetigen(a, stand.id)
    assert bestaetigt.confirmed_at is not None
    assert bestaetigt.confirmed_by_user_id == a

    with pytest.raises(eigentum_service.EigentumError, match="bereits bestätigt"):
        eigentum_service.bestaetigen(a, stand.id)


@pytest.mark.django_db
def test_api_beenden_und_historie(admin_client, wohnung):
    """Nach dem Beenden verschwindet der Stand aus der Tagessicht — und bleibt
    in der Historie."""
    stand = _stand(wohnung, status="PARTIAL", valid_from=date(2020, 1, 1),
                   eigentuemer=[{"party_id": wohnung["erben"][0].id}])

    r = admin_client.post(
        f"/api/tenure/eigentum/{stand.id}/beenden?valid_until=2021-01-01"
    )
    assert r.status_code == 200, r.content
    assert r.json()["is_current"] is False

    aktuell = admin_client.get(
        f"/api/tenure/properties/{wohnung['prop'].id}/eigentum"
    ).json()
    assert all(z["eigentum"] is None for z in aktuell), "beendet = nicht mehr aktuell"

    historie = admin_client.get(
        f"/api/tenure/properties/{wohnung['prop'].id}/eigentum?historie=true"
    ).json()
    assert any(z["eigentum"] is not None for z in historie), "Historie bleibt"


@pytest.mark.django_db
def test_api_unbekannter_kontakt_ist_422_kein_500(admin_client, wohnung):
    """Ein unbekannter Kontakt kommt aus `ensure_party_usable` als blankes
    `ValueError` — wird das nicht gefangen, antwortet die API mit 500.

    Dasselbe gilt für JEDEN Torfehler der Datenbank: `as_business_error`
    übersetzt ihn ebenfalls in ein blankes `ValueError`. Ein zu enges `except`
    hätte die gesamte Fehlerübersetzung ausgehebelt.
    """
    import uuid as _uuid

    r = admin_client.post(
        f"/api/tenure/properties/{wohnung['prop'].id}/eigentum",
        data={
            "unit_id": str(wohnung["einheit"].id),
            "valid_from": "2024-01-01",
            "source_type": "OWNER_LIST",
            "source_reference": "Liste",
            "eigentuemer": [{"party_id": str(_uuid.uuid4())}],
        },
        content_type="application/json",
    )
    assert r.status_code == 422, r.content


@pytest.mark.django_db
def test_monteur_sieht_fremdes_eigentum_nicht(client_with_role, wohnung):
    """404, nicht 403 — die Existenz einer fremden Liegenschaft geht ihn nichts an.

    Der MONTEUR trägt seit Migration 0103 `tenure/LESEN` mit `row_scope='EIGENE'`
    (er braucht den Mieter, bei dem er klingelt). Das Eigentum hängt am selben
    Modul und erbt die Sichtbarkeit — begrenzt auf **seine** Objekte. Diese
    Liegenschaft gehört zu keinem seiner Einsätze.

    Die Unterscheidung 404/403 ist Absicht: Ein 403 verriete, dass es die
    Liegenschaft gibt.
    """
    c = client_with_role("MONTEUR")
    r = c.get(f"/api/tenure/properties/{wohnung['prop'].id}/eigentum")
    assert r.status_code == 404, r.content


@pytest.mark.django_db
def test_monteur_darf_kein_eigentum_anlegen(client_with_role, wohnung):
    """Schreiben bleibt zu: ANLEGEN ist für MONTEUR `false` (fail-closed)."""
    c = client_with_role("MONTEUR")
    r = c.post(
        f"/api/tenure/properties/{wohnung['prop'].id}/eigentum",
        data={
            "unit_id": str(wohnung["einheit"].id),
            "valid_from": "2024-01-01",
            "source_type": "OWNER_LIST",
            "source_reference": "Liste",
        },
        content_type="application/json",
    )
    assert r.status_code == 403, r.content
