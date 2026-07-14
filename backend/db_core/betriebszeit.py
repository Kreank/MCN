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

from zoneinfo import ZoneInfo

from django.utils import timezone as dj_timezone

BETRIEBS_TZ = ZoneInfo("Europe/Berlin")


def betriebs_datum():
    """Der heutige Kalendertag in der Betriebszeitzone."""
    return dj_timezone.now().astimezone(BETRIEBS_TZ).date()
