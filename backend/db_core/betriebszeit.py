"""Die Betriebszeitzone — der Kalendertag, nach dem der Betrieb arbeitet.

`settings.TIME_ZONE` ist bewusst **UTC** (Nummernkreis-Jahreszuordnung, DB-Trigger
`(now() AT TIME ZONE 'UTC')::date`). Damit ist `django.utils.timezone.localdate()`
**das UTC-Datum** — und eben NICHT der Tag, an dem der Betrieb arbeitet.

Zwischen 00:00 und 02:00 MESZ liegt das UTC-Datum einen Tag zurück. Wer eine
Zuordnung mit dem *lokalen* Datum erfasst (das tut jeder Mensch und jedes
Frontend), erzeugt in diesem Fenster eine Zeile, die aus UTC-Sicht erst
*morgen* gilt — sie fällt still aus jedem Gültigkeitsfenster, das gegen
`localdate()` prüft.

Genau das ist in `beleg.party_address` passiert: Der Docstring dort warnte vor
dieser Falle („eine am selben lokalen Tag erfasste Adresse fiele dann still aus
dem Gültigkeitsfenster") und benutzte `localdate()` als Schutz — der wegen
`TIME_ZONE = "UTC"` keiner war. Eine nachts erfasste Rechnungsadresse galt für
die Belegausgabe nicht, und der Beleg wäre ohne Empfängeranschrift rausgegangen.

Deshalb: Fachliche Stichtage, die ein Mensch setzt (Adressgültigkeit, Verträge,
Zuweisungen), laufen über `betriebs_datum()`. Technische Stichtage, die mit einem
DB-Trigger deckungsgleich bleiben müssen (Belegdatum, Nummernkreis), bleiben
bewusst bei UTC.

`workflow.local_day()` in der DB und `BETRIEBS_TZ` sind deckungsgleich (über die
Sommerzeit geprüft, Migration 0066/0068).
"""

from datetime import datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from django.utils import timezone as dj_timezone
from pydantic import AfterValidator

BETRIEBS_TZ = ZoneInfo("Europe/Berlin")


def betriebs_datum():
    """Der heutige Kalendertag in der Betriebszeitzone."""
    return dj_timezone.now().astimezone(BETRIEBS_TZ).date()


def _als_betriebszeit(wert: datetime | None) -> datetime | None:
    """Naive Zeitstempel als Betriebszeit deuten, aware unverändert lassen.

    Ein Zeitstempel ohne Offset kommt von einem Menschen (bzw. einem
    `<input type="datetime-local">`, das keinen Offset mitliefert). Gemeint ist
    dann immer die Uhr an der Wand in Deutschland — niemals UTC.

    Ohne diese Deutung greift Djangos Default: `settings.TIME_ZONE` ist bewusst
    UTC, also würde „08:00" als 08:00Z gespeichert und dem Nutzer als 10:00
    zurückgezeigt (im Winter 09:00). Genau dieser Versatz ist über die
    Detail-Masken in die Plantafel gelaufen.

    Zu den beiden Umstellungstagen: Bei der doppelt existierenden Herbststunde
    greift `fold=0`, also die erste (Sommerzeit-)Lesart. Die im Frühjahr nicht
    existierende Stunde (02:00–03:00) hat keine gültige Ortszeit; ZoneInfo
    verschiebt sie deterministisch **vorwärts** — aus „02:30" wird der Zeitpunkt,
    der als 03:30 Ortszeit auf der Uhr steht. Beides trifft nur Termine, die
    exakt in diesen Grenzstunden liegen.

    `replace(tzinfo=...)` ist hier korrekt, weil `ZoneInfo` den Offset aus dem
    Zeitpunkt selbst ableitet. Mit `pytz` wäre es der klassische Fehler: dessen
    Zonenobjekte tragen ohne `localize()` den historischen LMT-Offset (Berlin:
    +00:53).
    """
    if wert is not None and dj_timezone.is_naive(wert):
        return wert.replace(tzinfo=BETRIEBS_TZ)
    return wert


# Zeitstempel, den ein Mensch eingibt (Termine, Zeitbuchungen). Fehlt der Offset,
# gilt die Betriebszeitzone statt UTC. An Ausgabe-Schemas nicht nötig — was aus
# der DB kommt, ist immer aware (`timestamptz`).
Betriebszeitpunkt = Annotated[datetime, AfterValidator(_als_betriebszeit)]
