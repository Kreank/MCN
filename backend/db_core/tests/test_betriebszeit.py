"""Die Zeitzonen-Grenze — der Fehler, der nur nachts zuschlägt.

`settings.TIME_ZONE` ist UTC. `django.utils.timezone.localdate()` liefert damit
das UTC-Datum, nicht den Tag, an dem der Betrieb arbeitet. Zwischen 00:00 und
02:00 MESZ liegen die beiden einen Tag auseinander.

Wer in diesem Fenster eine Rechnungsadresse erfasst, setzt `valid_from` auf das
lokale Datum (das tut jeder Mensch und jedes Frontend). Prüfte die Belegausgabe
gegen `localdate()`, wäre diese Adresse **erst morgen gültig** — der Beleg ginge
ohne Empfängeranschrift raus, und niemandem fiele es auf, weil tagsüber alles
stimmt.

Der Test friert die Uhr auf 00:30 Berlin ein (= 22:30 UTC am Vortag). Ohne
`betriebs_datum()` ist er rot.
"""

from datetime import date, datetime, timezone as dt_timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from db_core.betriebszeit import BETRIEBS_TZ, betriebs_datum
from db_core.db_context import business_transaction
from db_core.services import beleg as beleg_service
from db_core.services import identity as identity_service

# 00:30 Uhr in Berlin am 14.07. — in UTC ist es da noch der 13.07., 22:30.
NACHTS_BERLIN = datetime(2026, 7, 14, 0, 30, tzinfo=BETRIEBS_TZ)


def test_betriebsdatum_ist_nachts_nicht_das_utc_datum():
    """Die Grundannahme des Tests — sonst prüfte er nichts."""
    assert NACHTS_BERLIN.astimezone(dt_timezone.utc).date() == date(2026, 7, 13)
    assert NACHTS_BERLIN.astimezone(BETRIEBS_TZ).date() == date(2026, 7, 14)


@pytest.mark.django_db
def test_nachts_erfasste_rechnungsadresse_gilt_sofort(app_user):
    """Eine um 00:30 erfasste Adresse muss für die Belegausgabe SOFORT gelten.

    Vor der Umstellung auf `betriebs_datum()` lieferte `party_address` hier
    `None` — die Adresse galt aus UTC-Sicht erst am nächsten Tag.
    """
    with patch("django.utils.timezone.now", return_value=NACHTS_BERLIN):
        heute = betriebs_datum()
        assert heute == date(2026, 7, 14)  # nicht der 13., wie localdate() sagen würde

        kunde = identity_service.create_person(
            app_user.id, first_name="Nacht", last_name="Kunde"
        )
        identity_service.add_address(
            app_user.id,
            kunde.id,
            address_type="BILLING",
            street="Nachtweg",
            house_number="1",
            postal_code="34117",
            city="Kassel",
            valid_from=heute,
        )

        adresse = beleg_service.party_address(kunde.id)

    assert adresse is not None, (
        "Die um 00:30 erfasste Rechnungsadresse wurde nicht gefunden — der Beleg "
        "ginge ohne Empfängeranschrift raus."
    )
    assert adresse.postal_code == "34117"
