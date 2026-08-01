"""ICS-Export (iCalendar, RFC 5545) für Einsätze/Termine.

Reine Funktionen ohne HTTP und ohne Rechteprüfung — die Tore sitzen in
`api/kalender.py`. Hier wird nur serialisiert.

Warum kein `icalendar`-Paket: Das Format ist Zeilen-Text mit drei Regeln
(Faltung, Escaping, CRLF). Der Serialisierer unten ist kürzer als die
Abhängigkeit zu pflegen wäre, und er lässt sich ohne Datenbank testen.

## Warum UTC und KEINE VTIMEZONE

`DTSTART`/`DTEND` werden als **UTC-Zeitpunkte** (`…Z`) geschrieben, nicht als
`TZID=Europe/Berlin` mit mitgelieferter `VTIMEZONE`-Komponente.

Begründung: Was wir exportieren, sind **absolute Zeitpunkte**. Der Termin steht
in der Datenbank als `timestamptz`; „Dienstag 09:00 Berlin" ist damit genau ein
Punkt auf der Zeitachse. Ein Client, der `20260329T070000Z` in seiner Anzeige
nach Europe/Berlin umrechnet, zeigt 09:00 — vor wie nach der Sommerzeit-
umstellung, weil die Umrechnung aus der aktuellen Zonendatenbank des Clients
kommt und nicht aus einer VTIMEZONE, die wir pflegen und mit jeder EU-Reform
nachziehen müssten. Die Invariante „Ein Handwerkstermin ist eine Uhrzeit auf der
WANDUHR" (docs/INVARIANTEN.md, Kap. 7) bleibt damit gewahrt: Sie zielt auf
Rechenvorschriften, die aus einer Uhrzeit eine andere ableiten (Serientakte,
Tagesgrenzen) — dort ist UTC falsch. Ein einzelner, bereits festgelegter
Zeitpunkt ist dagegen zonenunabhängig, und UTC ist seine eindeutigste
Schreibweise.

Wo UTC NICHT reichen würde: Ganztagstermine (`VALUE=DATE`) und in die Datei
geschriebene Serienregeln (`RRULE`) — beide beziehen sich auf Ortszeit und
bräuchten eine VTIMEZONE. Wir exportieren weder das eine noch das andere: Eine
Serie ist im MCN eine Reihe echter, eigenständiger Einsätze (Invariante Kap. 11),
also fallen n Termine als n VEVENTs an, jeder mit absolutem Zeitpunkt.

## Was NICHT in die Datei geht

Die `DESCRIPTION` wird aus einer **Positivliste** gebaut (Einsatznummer,
Auftragsnummer, Terminart, Status) — nicht „alles außer". Diese Datei landet
erfahrungsgemäß in fremden Kalendern (Handy, Exchange, Google). Ausdrücklich
nicht enthalten: `access_instructions` (Zutrittshinweise/Schlüsselverstecke),
`completion_notes`, Preise und die Namen zugewiesener Mitarbeiter (keine
ATTENDEE-/ORGANIZER-Zeilen).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone as dt_timezone
from uuid import UUID

from db_core.betriebszeit import BETRIEBS_TZ

CRLF = "\r\n"
PRODID = "-//MCN//Leitstand ICS-Export//DE"
#: Fester Domänen-Suffix der UID. Zusammen mit der Einsatz-UUID global eindeutig
#: und über alle Exporte hinweg **stabil** — nur so erkennt ein Kalender beim
#: zweiten Import denselben Termin und legt keine Dublette an.
UID_DOMAIN = "einsatz.mcn"
#: RFC 5545 §3.1: eine Zeile darf 75 OKTETTE nicht überschreiten (ohne CRLF).
MAX_OKTETTE = 75
#: Bezugspunkt der abgeleiteten SEQUENCE (siehe `_sequence`).
SEQUENCE_EPOCHE = datetime(2020, 1, 1, tzinfo=dt_timezone.utc)

#: Einsatzstatus → RFC-5545-`STATUS`.
#:
#: AUSGEFALLEN wird als CANCELLED exportiert und **nicht weggelassen**: Ein
#: abgesagter Termin, der aus der Datei verschwindet, bleibt im Kalender des
#: Abonnenten für immer stehen — der Monteur fährt hin.
STATUS_ICS = {
    "UNGEPLANT": "TENTATIVE",
    "GEPLANT": "TENTATIVE",
    "BESTAETIGT": "CONFIRMED",
    "UNTERWEGS": "CONFIRMED",
    "VOR_ORT": "CONFIRMED",
    "PAUSIERT": "CONFIRMED",
    "ABGESCHLOSSEN": "CONFIRMED",
    "NACHARBEIT": "CONFIRMED",
    "AUSGEFALLEN": "CANCELLED",
}

#: Klartext des Status für die DESCRIPTION — der Status darf nie nur an einem
#: Feld hängen, das ein Client womöglich ignoriert (WCAG-Grundhaltung: nie nur
#: über Farbe/Darstellung).
STATUS_TEXT = {
    "UNGEPLANT": "Ungeplant",
    "GEPLANT": "Geplant",
    "BESTAETIGT": "Bestätigt",
    "UNTERWEGS": "Unterwegs",
    "VOR_ORT": "Vor Ort",
    "PAUSIERT": "Pausiert",
    "ABGESCHLOSSEN": "Abgeschlossen",
    "NACHARBEIT": "Nacharbeit",
    "AUSGEFALLEN": "Abgesagt",
}

# Steuerzeichen haben in einem TEXT-Wert nichts verloren (RFC 5545 §3.1: erlaubt
# sind WSP und %x21-7E plus NON-US-ASCII). TAB (\x09) bleibt, Zeilenumbrüche
# werden vorher zu "\n" umgeschrieben.
_STEUERZEICHEN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# Dateinamen: nur Unverfängliches, damit der Content-Disposition-Header nicht
# aufbricht (Anführungszeichen/Semikolon/Pfadtrenner).
_DATEINAME_UNERWUENSCHT = re.compile(r"[^A-Za-z0-9._-]+")


# --- Bausteine des Formats -------------------------------------------------

def escape_text(wert) -> str:
    """TEXT-Wert nach RFC 5545 §3.3.11 maskieren.

    Reihenfolge ist wesentlich: der Backslash zuerst, sonst maskiert der zweite
    Durchgang die soeben erzeugten Escapes ein zweites Mal. Der Doppelpunkt wird
    ausdrücklich NICHT maskiert (er ist in TEXT-Werten erlaubt).
    """
    if wert is None:
        return ""
    text = str(wert).replace("\r\n", "\n").replace("\r", "\n")
    text = _STEUERZEICHEN.sub("", text)
    text = text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    return text.replace("\n", "\\n")


def falte(logische_zeile: str) -> str:
    """Zeilenfaltung nach RFC 5545 §3.1: max. 75 **Oktette** je Zeile.

    Fortsetzungszeilen beginnen mit genau einem Leerzeichen, das zu den 75
    Oktetten zählt. Geschnitten wird an Zeichengrenzen — ein mitten
    durchtrenntes UTF-8-Zeichen wäre kein gültiges UTF-8 mehr (deutsche Umlaute
    sind zwei Oktette, das trifft in der Praxis jede zweite Adresse).
    """
    roh = logische_zeile.encode("utf-8")
    if len(roh) <= MAX_OKTETTE:
        return logische_zeile
    stuecke: list[bytes] = []
    rest = roh
    grenze = MAX_OKTETTE
    while len(rest) > grenze:
        schnitt = grenze
        # 0b10xxxxxx = Folgeoktett eines mehrteiligen Zeichens → weiter zurück.
        while schnitt > 0 and (rest[schnitt] & 0xC0) == 0x80:
            schnitt -= 1
        if schnitt == 0:  # theoretisch unerreichbar (Zeichen ≤ 4 Oktette)
            schnitt = grenze
        stuecke.append(rest[:schnitt])
        rest = rest[schnitt:]
        grenze = MAX_OKTETTE - 1  # das führende Leerzeichen belegt ein Oktett
    stuecke.append(rest)
    kopf = stuecke[0].decode("utf-8")
    fortsetzung = [" " + s.decode("utf-8") for s in stuecke[1:]]
    return CRLF.join([kopf, *fortsetzung])


def zeile(name: str, wert: str, *, maskieren: bool = True) -> str:
    """Eine gefaltete Property-Zeile. `maskieren=False` für Nicht-TEXT-Werte
    (Zeitstempel, Zahlen, UID) — dort gilt die TEXT-Maskierung nicht."""
    return falte(f"{name}:{escape_text(wert) if maskieren else wert}")


def als_utc(wert: datetime) -> str:
    """Zeitstempel als UTC-Form `20260329T070000Z`.

    Ein (in der Praxis nicht vorkommender) naiver Wert wird als Betriebszeit
    gedeutet — dieselbe Regel wie überall sonst im Projekt, nie als UTC.
    """
    if wert.tzinfo is None:
        wert = wert.replace(tzinfo=BETRIEBS_TZ)
    return wert.astimezone(dt_timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sequence(geaendert_am: datetime | None) -> int:
    """Monoton steigende SEQUENCE, abgeleitet aus `updated_at`.

    RFC 5545 §3.8.7.4 verlangt eine Zahl, die bei jeder inhaltlichen Änderung
    steigt — sonst verwerfen strenge Clients (Outlook, Exchange) einen erneuten
    Import als „schon bekannt" und der verschobene Termin bleibt auf der alten
    Uhrzeit stehen. Sekunden seit 2020 halten die Zahl klein genug, um nicht in
    die 32-Bit-Grenze mancher Clients zu laufen (2020 + 68 Jahre).
    """
    if geaendert_am is None:
        return 0
    if geaendert_am.tzinfo is None:
        geaendert_am = geaendert_am.replace(tzinfo=BETRIEBS_TZ)
    return max(0, int((geaendert_am - SEQUENCE_EPOCHE).total_seconds()))


# --- Termin als transportfähige Zwischenform -------------------------------

@dataclass(frozen=True)
class Termin:
    """Was von einem Einsatz in die ICS-Datei darf — und sonst nichts.

    Bewusst eine eigene, flache Struktur statt des ORM-Objekts: Der
    Serialisierer kann so gar nicht erst auf `access_instructions` zugreifen,
    und er ist ohne Datenbank testbar.
    """

    id: UUID
    job_number: str
    titel: str
    beginn: datetime
    ende: datetime | None
    status: str
    kategorie: str | None = None
    auftragsnummer: str | None = None
    ort: str = ""
    geaendert_am: datetime | None = None

    @property
    def uid(self) -> str:
        return f"{self.id}@{UID_DOMAIN}"


def _gebaeude_label(gebaeude) -> str | None:
    if gebaeude is None:
        return None
    return gebaeude.name or f"Gebäude {gebaeude.building_number}"


def _ort_text(job) -> str:
    """Anschrift des Einsatzortes als eine Zeile für `LOCATION`.

    Dieselbe Auflösung wie in der Einsatzanzeige: Liegenschaft vom Einsatz,
    sonst vom Auftrag; Gebäude/Einheit vom Einsatz, sonst vom Auftrag; und die
    Anschrift des Gebäudes, falls es eine eigene trägt, sonst die der
    Liegenschaft. Bewusst NICHT über `api.planung` importiert — ein Service
    hängt nicht an der API-Schicht.
    """
    liegenschaft = job.property if job.property_id is not None else None
    auftrag = job.work_order if job.work_order_id is not None else None
    if liegenschaft is None and auftrag is not None:
        liegenschaft = auftrag.property

    if job.building_id is not None:
        gebaeude, einheit = job.building, job.unit
    elif auftrag is not None and auftrag.building_id is not None:
        gebaeude, einheit = auftrag.building, auftrag.unit
    else:
        gebaeude = einheit = None

    if gebaeude is not None and gebaeude.address_id is not None:
        adresse = gebaeude.address
    else:
        adresse = liegenschaft.address if liegenschaft is not None else None

    teile: list[str] = []
    if adresse is not None:
        strasse = " ".join(t for t in (adresse.street, adresse.house_number) if t)
        stadt = " ".join(t for t in (adresse.postal_code, adresse.city) if t)
        teile = [t for t in (strasse, stadt) if t]
    text = ", ".join(teile)

    genau = " · ".join(
        t
        for t in (
            _gebaeude_label(gebaeude),
            einheit.unit_number if einheit is not None else None,
        )
        if t
    )
    if genau:
        text = f"{text} ({genau})" if text else genau
    return text


def termin_aus_job(job) -> Termin | None:
    """Einen `workflow.service_job` in die Exportform übersetzen.

    Gibt `None` zurück, wenn der Einsatz keinen Planbeginn hat (Status
    UNGEPLANT/Rückstand): Ein VEVENT ohne DTSTART ist nach RFC 5545 ungültig,
    und ein erfundener Beginn wäre eine Falschaussage im Kalender des Kunden.
    """
    if job.scheduled_start is None:
        return None
    auftrag = job.work_order if job.work_order_id is not None else None
    titel = job.title or (auftrag.title if auftrag is not None else "") or job.job_number
    kategorie = job.appointment_category if job.appointment_category_id else None
    ende = job.scheduled_end
    # Ein Ende, das nicht nach dem Beginn liegt, ist keine Dauer — dann bleibt
    # DTEND weg (RFC 5545 §3.6.1: der Termin endet dann auf seinem Beginn).
    # Eine Vorgabedauer zu erfinden hieße, dem Kunden eine Zusage in den
    # Kalender zu schreiben, die niemand gemacht hat.
    if ende is not None and ende <= job.scheduled_start:
        ende = None
    return Termin(
        id=job.id,
        job_number=job.job_number or "",
        titel=titel,
        beginn=job.scheduled_start,
        ende=ende,
        status=job.status,
        kategorie=kategorie.name if kategorie is not None else None,
        auftragsnummer=auftrag.order_number if auftrag is not None else None,
        ort=_ort_text(job),
        geaendert_am=job.updated_at,
    )


# --- Serialisierung --------------------------------------------------------

def _beschreibung(termin: Termin) -> str:
    """DESCRIPTION als **Positivliste** — nie „alles außer"."""
    zeilen = []
    if termin.job_number:
        zeilen.append(f"Einsatz: {termin.job_number}")
    if termin.auftragsnummer:
        zeilen.append(f"Auftrag: {termin.auftragsnummer}")
    if termin.kategorie:
        zeilen.append(f"Terminart: {termin.kategorie}")
    zeilen.append(f"Status: {STATUS_TEXT.get(termin.status, termin.status)}")
    return "\n".join(zeilen)


def vevent(termin: Termin, *, jetzt: datetime) -> list[str]:
    """Die Zeilen eines VEVENT (bereits gefaltet, ohne CRLF am Ende)."""
    ics_status = STATUS_ICS.get(termin.status, "TENTATIVE")
    # Abgesagt steht zusätzlich im Titel: STATUS:CANCELLED wertet nicht jeder
    # Client aus, und ein still normal aussehender abgesagter Termin ist genau
    # die Fahrt, die sich niemand erklären kann.
    titel = termin.titel
    if ics_status == "CANCELLED":
        titel = f"Abgesagt: {titel}"

    zeilen = [
        "BEGIN:VEVENT",
        zeile("UID", termin.uid, maskieren=False),
        zeile("DTSTAMP", als_utc(jetzt), maskieren=False),
        zeile("DTSTART", als_utc(termin.beginn), maskieren=False),
    ]
    if termin.ende is not None:
        zeilen.append(zeile("DTEND", als_utc(termin.ende), maskieren=False))
    zeilen.append(zeile("SUMMARY", titel))
    if termin.ort:
        zeilen.append(zeile("LOCATION", termin.ort))
    zeilen.append(zeile("DESCRIPTION", _beschreibung(termin)))
    zeilen.append(zeile("STATUS", ics_status, maskieren=False))
    zeilen.append(zeile("SEQUENCE", str(_sequence(termin.geaendert_am)), maskieren=False))
    if termin.geaendert_am is not None:
        zeilen.append(
            zeile("LAST-MODIFIED", als_utc(termin.geaendert_am), maskieren=False)
        )
    zeilen.append("END:VEVENT")
    return zeilen


def vcalendar(termine, *, name: str | None = None, jetzt: datetime | None = None) -> str:
    """Vollständige ICS-Datei als Text (CRLF, abgeschlossen mit CRLF).

    `jetzt` ist einspeisbar, damit Tests die Uhr einfrieren können — ein Test
    gegen `now()` prüft nichts Wiederholbares.
    """
    jetzt = jetzt or datetime.now(dt_timezone.utc)
    zeilen = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        zeile("PRODID", PRODID),
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    if name:
        # X-WR-CALNAME ist kein RFC-Bestandteil, wird aber von Google/Apple/
        # Outlook als Kalendername gelesen. Unbekannte X-Properties MÜSSEN
        # Clients ignorieren (§3.8.8.2) — schadlos, wo sie unbekannt ist.
        zeilen.append(zeile("X-WR-CALNAME", name))
    for termin in termine:
        zeilen.extend(vevent(termin, jetzt=jetzt))
    zeilen.append("END:VCALENDAR")
    return CRLF.join(zeilen) + CRLF


def baue_ics(jobs, *, name: str | None = None, jetzt: datetime | None = None) -> str:
    """ICS-Datei aus `workflow.service_job`-Instanzen. Ungeplante fallen raus."""
    termine = [t for t in (termin_aus_job(j) for j in jobs) if t is not None]
    return vcalendar(termine, name=name, jetzt=jetzt)


# --- Dateinamen ------------------------------------------------------------

def _sauber(text: str, ersatz: str) -> str:
    gesaeubert = _DATEINAME_UNERWUENSCHT.sub("-", text).strip("-")
    return gesaeubert or ersatz


def dateiname_einzel(job_number: str) -> str:
    return f"einsatz-{_sauber(job_number, 'termin')}.ics"


def dateiname_zeitraum(von: date, bis: date) -> str:
    return f"einsaetze-{von.isoformat()}_{bis.isoformat()}.ics"
