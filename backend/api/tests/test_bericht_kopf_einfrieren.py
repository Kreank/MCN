"""Der Briefkopf eines unterschriebenen Berichts ist eingefroren (Befund B9).

Der Mieter unterschreibt ein Dokument mit seinem Namen darauf. Zieht er drei
Monate später aus und wird der Nachmieter erfasst, stünde ohne Snapshot
**dessen** Name auf dem Papier, das der Vormieter unterschrieben hat — und
niemand könnte im Nachhinein sagen, was zum Zeitpunkt der Unterschrift dastand.

Migration 0132 friert den Kopf beim Unterzeichnen ein; `protect_site_report`
macht ihn danach unveränderlich. Diese Tests halten beide Seiten fest: dass
eingefroren wird, und dass sich das Eingefrorene nicht mehr anfassen lässt.
"""
from datetime import date
from hashlib import sha256

import pytest
from django.db import transaction
from django.db.utils import ProgrammingError

from db_core import storage as storage_module
from db_core.db_context import business_transaction
from db_core.models import SiteReport
from db_core.services import auftrag as auftrag_service
from db_core.services import belegung as belegung_service
from db_core.services import identity as identity_service
from db_core.services import property as property_service
from db_core.services import site_report as report_service

# Ein winziges gültiges PNG (1×1, transparent) — der Dienst prüft die Signatur.
PNG_1x1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


class FakeStorage:
    """MinIO läuft in der Testumgebung nicht — die Unterschrift landet im RAM."""

    def __init__(self):
        self.objects = {}

    def ensure_bucket(self):
        pass

    def put_object(self, key, data, content_type="application/octet-stream"):
        payload = bytes(data)
        self.objects[key] = payload
        return storage_module.ObjectInfo(
            storage_key=key, sha256=sha256(payload).hexdigest(), size_bytes=len(payload)
        )

    def get_object(self, key):
        if key not in self.objects:
            raise storage_module.StorageError(key)
        return self.objects[key]

    def remove_object(self, key):
        self.objects.pop(key, None)


@pytest.fixture
def fake_storage(monkeypatch):
    fake = FakeStorage()
    monkeypatch.setattr(storage_module, "get_storage", lambda: fake)
    return fake


@pytest.fixture
def bericht_mit_mieterin(app_user):
    """Wohnung mit Mieterin, Auftrag und Bericht — bereit zur Unterschrift."""
    a = app_user.id
    kunde = identity_service.create_organization(
        a, legal_name="Hausverwaltung Nord GmbH", organization_type="PROPERTY_MANAGEMENT"
    )
    prop = property_service.create_property(
        a, name="Wohnanlage Ahornweg", property_type="WEG",
        street="Ahornweg", house_number="7", postal_code="10115", city="Berlin",
    )
    haus = property_service.add_building(
        a, property_id=prop.id, building_number="1", name="Vorderhaus"
    )
    wohnung = property_service.add_unit(
        a, building_id=haus.id, property_id=prop.id,
        unit_type="APARTMENT", unit_number="WE 12", storey="3. OG",
    )
    vormieterin = identity_service.create_person(
        a, first_name="Erika", last_name="Vormieterin"
    )
    belegung = belegung_service.create_belegung(
        a, unit_id=wohnung.id, occupancy_type="RENTED", valid_from=date(2024, 1, 1),
        mieter=[{"party_id": vormieterin.id, "role": "CONTRACTUAL_TENANT"}],
    )
    auftrag = auftrag_service.create_work_order(
        a, property_id=prop.id, title="Heizung tropft",
        building_id=haus.id, unit_id=wohnung.id,
    )
    auftrag_service.add_work_order_party(
        a, work_order_id=auftrag.id, party_id=kunde.id, role="PRINCIPAL",
        is_primary=True,
    )
    bericht = report_service.create_report(
        a, work_order_id=auftrag.id, service_job_id=None,
        report_date=date(2026, 7, 21), activity_text="Thermostat getauscht.",
    )
    return {
        "actor": a, "bericht": bericht, "belegung": belegung,
        "wohnung": wohnung, "vormieterin": vormieterin,
    }


def _unterschreiben(daten, fake_storage):
    return report_service.sign_report(
        daten["actor"],
        report_id=daten["bericht"].id,
        signed_by_name="Erika Vormieterin",
        signature_png=PNG_1x1,
    )


@pytest.mark.django_db
def test_unterschreiben_friert_den_kopf_ein(bericht_mit_mieterin, fake_storage):
    signiert = _unterschreiben(bericht_mit_mieterin, fake_storage)

    assert signiert.status == "UNTERZEICHNET"
    assert signiert.header_snapshot is not None, (
        "Der Briefkopf muss mit der Unterschrift eingefroren werden"
    )
    assert signiert.header_snapshot["mieter"] == ["Erika Vormieterin"]
    assert signiert.header_snapshot["einheit"] == "WE 12"
    assert signiert.header_snapshot["auftraggeber"] == "Hausverwaltung Nord GmbH"


@pytest.mark.django_db
def test_mieterwechsel_aendert_den_unterschriebenen_bericht_nicht(
    bericht_mit_mieterin, fake_storage
):
    """Der eigentliche Befund B9.

    Vor Migration 0132 hätte dieser Test den Nachmieter auf dem unterschriebenen
    Bericht gefunden — auf einem Dokument, das die Vormieterin unterschrieben
    hat und das sie nie zu Gesicht bekam.
    """
    a = bericht_mit_mieterin["actor"]
    signiert = _unterschreiben(bericht_mit_mieterin, fake_storage)
    assert report_service.kopfdaten(signiert)["mieter"] == ["Erika Vormieterin"]

    # Der Wechsel muss VOLLZOGEN sein, sonst prüft der Test nichts: Läge er in
    # der Zukunft, wäre die Vormieterin am Stichtag „heute" ohnehin noch die
    # aktive Mieterin und der Test bestünde auch ohne Snapshot.
    # `valid_until = heute` beendet die Belegung sofort (halboffenes Intervall
    # `[von, bis)`), die neue beginnt am selben Tag.
    heute = date.today()
    belegung_service.update_belegung(
        a, bericht_mit_mieterin["belegung"].id, {"valid_until": heute}
    )
    nachmieter = identity_service.create_person(
        a, first_name="Norbert", last_name="Nachmieter"
    )
    neue = belegung_service.create_belegung(
        a, unit_id=bericht_mit_mieterin["wohnung"].id, occupancy_type="RENTED",
        valid_from=heute,
        mieter=[{"party_id": nachmieter.id, "role": "CONTRACTUAL_TENANT"}],
    )
    # Kontrollprobe: Der Nachmieter ist jetzt WIRKLICH der aktive Mieter —
    # sonst prüfte der eigentliche Assert weiter unten ins Leere.
    assert [b.party.display_name for b in belegung_service.aktive_mieter(neue)] == [
        "Norbert Nachmieter"
    ]

    frisch = SiteReport.objects.get(id=signiert.id)
    assert report_service.kopfdaten(frisch)["mieter"] == ["Erika Vormieterin"], (
        "Auf dem unterschriebenen Bericht muss stehen bleiben, wer damals dort wohnte"
    )


@pytest.mark.django_db
def test_entwurf_loest_weiterhin_live_auf(bericht_mit_mieterin):
    """Solange nicht unterschrieben ist, soll der Bericht Änderungen zeigen."""
    bericht = bericht_mit_mieterin["bericht"]
    assert bericht.header_snapshot is None
    assert report_service.kopfdaten(bericht)["mieter"] == ["Erika Vormieterin"]


@pytest.mark.django_db
def test_datenbank_verbietet_das_nachtraegliche_aendern(
    bericht_mit_mieterin, fake_storage
):
    """Nicht der Dienst hält das dicht, sondern der Trigger.

    Der Savepoint ist Absicht: Die Ausnahme kommt aus plpgsql, und ohne
    `atomic()` risse sie die ganze Testtransaktion auf.
    """
    a = bericht_mit_mieterin["actor"]
    signiert = _unterschreiben(bericht_mit_mieterin, fake_storage)

    with pytest.raises(ProgrammingError, match="unveränderlich"):
        with transaction.atomic():
            with business_transaction(a):
                SiteReport.objects.filter(id=signiert.id).update(
                    header_snapshot={"mieter": ["Wer ganz anderes"]}
                )


@pytest.mark.django_db
def test_altbestand_ohne_snapshot_faellt_auf_live_zurueck(bericht_mit_mieterin):
    """Berichte, die vor 0132 unterschrieben wurden, tragen NULL.

    Sie nachträglich zu befüllen hieße, eine Aussage über einen Zeitpunkt zu
    erfinden, an dem niemand hingesehen hat — also bleibt es bei der
    Live-Auflösung. Der Test stellt genau diesen Zustand her (UNTERZEICHNET
    ohne Snapshot) und prüft, dass `kopfdaten` nicht daran zerbricht.
    """
    bericht = bericht_mit_mieterin["bericht"]
    bericht.status = "UNTERZEICHNET"
    bericht.header_snapshot = None

    kopf = report_service.kopfdaten(bericht)
    assert kopf["mieter"] == ["Erika Vormieterin"]
    assert kopf["einheit"] == "WE 12"
