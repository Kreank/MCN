"""Der ICS-Serialisierer (RFC 5545) — Formatregeln und Sommerzeit.

Diese Tests brauchen keine Datenbank: `db_core.services.kalender` arbeitet auf
einer flachen `Termin`-Struktur. Genau deshalb gibt es sie — die drei Regeln,
an denen selbstgebaute ICS-Dateien in fremden Clients scheitern (Faltung nach
75 **Oktetten**, Maskierung, CRLF), lassen sich so einzeln festnageln.

Die Uhr wird eingefroren (`jetzt=`), nicht gegen `now()` gerechnet: Ein
Regressionstest, der nicht rot wird, wenn der Fehler drin ist, wäre wertlos
(docs/INVARIANTEN.md, Kap. 7).
"""
from datetime import datetime, timezone as dt_timezone
from uuid import UUID

import pytest

from db_core.betriebszeit import BETRIEBS_TZ
from db_core.services import kalender as k

JETZT = datetime(2026, 7, 31, 10, 0, tzinfo=dt_timezone.utc)
JOB_ID = UUID("11111111-2222-3333-4444-555555555555")


def _termin(**kwargs):
    basis = dict(
        id=JOB_ID,
        job_number="E-HZG-26-0142",
        titel="Therme warten",
        beginn=datetime(2026, 7, 13, 6, 0, tzinfo=dt_timezone.utc),
        ende=datetime(2026, 7, 13, 8, 0, tzinfo=dt_timezone.utc),
        status="GEPLANT",
        geaendert_am=datetime(2026, 7, 12, 9, 0, tzinfo=dt_timezone.utc),
    )
    basis.update(kwargs)
    return k.Termin(**basis)


def _zeilen(text):
    """Die ENTFALTETEN logischen Zeilen (Fortsetzungen wieder angehängt)."""
    roh = text.split(k.CRLF)
    logisch = []
    for z in roh:
        if z.startswith(" ") and logisch:
            logisch[-1] += z[1:]
        else:
            logisch.append(z)
    return [z for z in logisch if z != ""]


# --- Grundstruktur ---------------------------------------------------------

def test_grundstruktur_und_crlf():
    text = k.vcalendar([_termin()], jetzt=JETZT)
    assert text.startswith("BEGIN:VCALENDAR\r\n")
    assert text.endswith("END:VCALENDAR\r\n")
    # KEIN nacktes \n irgendwo — CRLF ist Pflicht, nicht Geschmack.
    assert "\n" not in text.replace("\r\n", "")
    zeilen = _zeilen(text)
    assert "VERSION:2.0" in zeilen
    assert "CALSCALE:GREGORIAN" in zeilen
    assert "METHOD:PUBLISH" in zeilen
    assert any(z.startswith("PRODID:") for z in zeilen)
    assert zeilen.count("BEGIN:VEVENT") == 1
    assert zeilen.count("END:VEVENT") == 1
    assert f"UID:{JOB_ID}@{k.UID_DOMAIN}" in zeilen
    assert "DTSTAMP:20260731T100000Z" in zeilen


def test_leerer_kalender_bleibt_gueltig():
    text = k.vcalendar([], jetzt=JETZT)
    assert "BEGIN:VEVENT" not in text
    assert text.startswith("BEGIN:VCALENDAR\r\n")
    assert text.endswith("END:VCALENDAR\r\n")


def test_uid_ist_stabil_ueber_zwei_exporte():
    """Ohne stabile UID legt jeder Re-Import eine Dublette an."""
    a = _zeilen(k.vcalendar([_termin()], jetzt=JETZT))
    b = _zeilen(k.vcalendar([_termin(titel="Therme warten (verschoben)")], jetzt=JETZT))
    uid_a = [z for z in a if z.startswith("UID:")][0]
    uid_b = [z for z in b if z.startswith("UID:")][0]
    assert uid_a == uid_b


def test_sequence_steigt_mit_updated_at():
    alt = _zeilen(k.vcalendar([_termin()], jetzt=JETZT))
    neu = _zeilen(
        k.vcalendar(
            [_termin(geaendert_am=datetime(2026, 7, 12, 9, 30, tzinfo=dt_timezone.utc))],
            jetzt=JETZT,
        )
    )
    s_alt = int([z for z in alt if z.startswith("SEQUENCE:")][0].split(":")[1])
    s_neu = int([z for z in neu if z.startswith("SEQUENCE:")][0].split(":")[1])
    assert s_neu > s_alt
    assert s_alt < 2**31, "SEQUENCE läuft in die 32-Bit-Grenze mancher Clients."


# --- Faltung (RFC 5545 §3.1) -----------------------------------------------

def test_faltung_ab_75_oktetten():
    text = k.vcalendar([_termin(titel="A" * 200)], jetzt=JETZT)
    for zeile in text.split(k.CRLF):
        assert len(zeile.encode("utf-8")) <= 75, f"Zeile zu lang: {zeile!r}"
    # Und der Inhalt überlebt das Entfalten unversehrt.
    assert f"SUMMARY:{'A' * 200}" in _zeilen(text)


def test_faltung_zaehlt_oktette_nicht_zeichen():
    """Umlaute sind in UTF-8 ZWEI Oktette. Wer Zeichen zählt, baut Zeilen, die
    in strengen Clients (Exchange) die Datei abbrechen lassen."""
    titel = "ä" * 60  # 60 Zeichen, 120 Oktette
    text = k.vcalendar([_termin(titel=titel)], jetzt=JETZT)
    roh = text.split(k.CRLF)
    assert any(len(z.encode("utf-8")) > 60 for z in roh), "Test prüft nichts"
    for zeile in roh:
        assert len(zeile.encode("utf-8")) <= 75
    assert f"SUMMARY:{titel}" in _zeilen(text)


def test_faltung_zerschneidet_kein_utf8_zeichen():
    """Ein an der Oktettgrenze zerschnittenes Zeichen ergäbe ungültiges UTF-8 —
    die Datei ließe sich dann gar nicht mehr dekodieren."""
    text = k.vcalendar([_termin(titel="ü" * 120)], jetzt=JETZT)
    for zeile in text.split(k.CRLF):
        zeile.encode("utf-8").decode("utf-8")  # wirft, wenn kaputt
    assert "ü" * 120 in "".join(_zeilen(text))


def test_kurze_zeile_wird_nicht_gefaltet():
    assert k.falte("SUMMARY:kurz") == "SUMMARY:kurz"


# --- Maskierung (RFC 5545 §3.3.11) -----------------------------------------

@pytest.mark.parametrize(
    "eingabe,erwartet",
    [
        ("a;b", "a\\;b"),
        ("a,b", "a\\,b"),
        ("a\\b", "a\\\\b"),
        ("a\nb", "a\\nb"),
        ("a\r\nb", "a\\nb"),
        ("Zeit: 8:00", "Zeit: 8:00"),  # Doppelpunkt bleibt unmaskiert
    ],
)
def test_maskierung(eingabe, erwartet):
    assert k.escape_text(eingabe) == erwartet


def test_backslash_wird_nur_einmal_maskiert():
    """Falsche Reihenfolge (erst ';' dann '\\') maskiert die eigenen Escapes ein
    zweites Mal — aus 'a;b' würde 'a\\\\;b'."""
    assert k.escape_text("a;b") == "a\\;b"
    assert k.escape_text("a\\;b") == "a\\\\\\;b"


def test_titel_mit_sonderzeichen_bricht_die_datei_nicht():
    text = k.vcalendar(
        [_termin(titel="Wartung; Etage 3, Haus A\\B\nzweite Zeile")], jetzt=JETZT
    )
    zeilen = _zeilen(text)
    assert (
        "SUMMARY:Wartung\\; Etage 3\\, Haus A\\\\B\\nzweite Zeile" in zeilen
    ), zeilen


# --- Sommerzeit ------------------------------------------------------------

@pytest.mark.parametrize(
    "wanduhr,erwartet_utc",
    [
        # 29.03.2026: MEZ→MESZ um 02:00. Vor der Umstellung gilt +01:00 …
        (datetime(2026, 3, 29, 1, 30, tzinfo=BETRIEBS_TZ), "20260329T003000Z"),
        # … danach +02:00. Beides derselbe Kalendertag.
        (datetime(2026, 3, 29, 9, 0, tzinfo=BETRIEBS_TZ), "20260329T070000Z"),
        (datetime(2026, 3, 30, 9, 0, tzinfo=BETRIEBS_TZ), "20260330T070000Z"),
        # 25.10.2026: MESZ→MEZ um 03:00.
        (datetime(2026, 10, 25, 9, 0, tzinfo=BETRIEBS_TZ), "20261025T080000Z"),
        (datetime(2026, 10, 24, 9, 0, tzinfo=BETRIEBS_TZ), "20261024T070000Z"),
        (datetime(2026, 10, 26, 9, 0, tzinfo=BETRIEBS_TZ), "20261026T080000Z"),
    ],
)
def test_sommerzeitwechsel_trifft_die_wanduhr(wanduhr, erwartet_utc):
    """Der Kunde hat 09:00 auf der Wanduhr vereinbart. Nach dem Import muss der
    Client 09:00 zeigen — also muss der UTC-Zeitpunkt VOR und NACH der
    Umstellung ein anderer sein (07:00Z bzw. 08:00Z). Ein Export, der stur
    'T090000Z' schriebe, wäre im Sommer eine Stunde daneben."""
    text = k.vcalendar([_termin(beginn=wanduhr, ende=None)], jetzt=JETZT)
    assert f"DTSTART:{erwartet_utc}" in _zeilen(text)
    # Gegenprobe: zurückgerechnet steht wieder dieselbe Wanduhrzeit da.
    zurueck = datetime.strptime(erwartet_utc, "%Y%m%dT%H%M%SZ").replace(
        tzinfo=dt_timezone.utc
    )
    assert zurueck.astimezone(BETRIEBS_TZ) == wanduhr


def test_termin_ueber_die_umstellung_behaelt_seine_wanduhrzeiten():
    """Ein Nachttermin 23:00 → 04:00 über den 25.10. dauert real SECHS Stunden
    (die Stunde 02:00–03:00 gibt es zweimal). Der Export darf daran nichts
    glattziehen."""
    beginn = datetime(2026, 10, 24, 23, 0, tzinfo=BETRIEBS_TZ)
    ende = datetime(2026, 10, 25, 4, 0, tzinfo=BETRIEBS_TZ)
    # Achtung: Python subtrahiert zwei aware Zeitstempel MIT DEMSELBEN tzinfo
    # naiv (die Zone wird ignoriert) — das ergäbe 5 h. Die tatsächlich
    # verstrichene Zeit sieht man erst nach der Umrechnung in UTC.
    verstrichen = ende.astimezone(dt_timezone.utc) - beginn.astimezone(dt_timezone.utc)
    assert verstrichen.total_seconds() == 6 * 3600
    zeilen = _zeilen(k.vcalendar([_termin(beginn=beginn, ende=ende)], jetzt=JETZT))
    assert "DTSTART:20261024T210000Z" in zeilen
    assert "DTEND:20261025T030000Z" in zeilen


# --- Status ----------------------------------------------------------------

@pytest.mark.parametrize(
    "status,erwartet",
    [
        ("UNGEPLANT", "TENTATIVE"),
        ("GEPLANT", "TENTATIVE"),
        ("BESTAETIGT", "CONFIRMED"),
        ("UNTERWEGS", "CONFIRMED"),
        ("VOR_ORT", "CONFIRMED"),
        ("PAUSIERT", "CONFIRMED"),
        ("ABGESCHLOSSEN", "CONFIRMED"),
        ("NACHARBEIT", "CONFIRMED"),
        ("AUSGEFALLEN", "CANCELLED"),
    ],
)
def test_status_abbildung(status, erwartet):
    zeilen = _zeilen(k.vcalendar([_termin(status=status)], jetzt=JETZT))
    assert f"STATUS:{erwartet}" in zeilen


def test_abgesagter_termin_wird_exportiert_nicht_weggelassen():
    """Ein weggelassener abgesagter Termin bleibt im Abonnenten-Kalender für
    immer stehen — der Monteur fährt hin."""
    zeilen = _zeilen(k.vcalendar([_termin(status="AUSGEFALLEN")], jetzt=JETZT))
    assert "BEGIN:VEVENT" in zeilen
    assert "STATUS:CANCELLED" in zeilen
    assert "SUMMARY:Abgesagt: Therme warten" in zeilen
    assert any("Status: Abgesagt" in z for z in zeilen)


# --- DTEND / Positivliste --------------------------------------------------

def test_ohne_ende_kein_dtend():
    """Keine erfundene Vorgabedauer — das wäre eine Zusage im Kundenkalender,
    die niemand gemacht hat."""
    zeilen = _zeilen(k.vcalendar([_termin(ende=None)], jetzt=JETZT))
    assert not any(z.startswith("DTEND") for z in zeilen)
    assert any(z.startswith("DTSTART:") for z in zeilen)


def test_beschreibung_ist_eine_positivliste():
    zeilen = _zeilen(
        k.vcalendar(
            [_termin(auftragsnummer="A-26-0007", kategorie="Wartung")], jetzt=JETZT
        )
    )
    beschreibung = [z for z in zeilen if z.startswith("DESCRIPTION:")][0]
    assert "Einsatz: E-HZG-26-0142" in beschreibung
    assert "Auftrag: A-26-0007" in beschreibung
    assert "Terminart: Wartung" in beschreibung
    assert "Status: Geplant" in beschreibung


def test_location_kommt_aus_dem_ort():
    zeilen = _zeilen(
        k.vcalendar([_termin(ort="Steglitzer Damm 12, 12169 Berlin")], jetzt=JETZT)
    )
    assert "LOCATION:Steglitzer Damm 12\\, 12169 Berlin" in zeilen


def test_dateinamen_sind_harmlos():
    assert k.dateiname_einzel("E-HZG-26-0142") == "einsatz-E-HZG-26-0142.ics"
    # Ein Semikolon/Anführungszeichen im Namen würde den Content-Disposition-
    # Header aufbrechen.
    assert '"' not in k.dateiname_einzel('E";x')
    assert ";" not in k.dateiname_einzel('E";x')
    assert k.dateiname_einzel("") == "einsatz-termin.ics"
