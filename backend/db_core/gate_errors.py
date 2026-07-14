"""Übersetzung fachlicher DB-Tor-Fehler in ValueError (→ HTTP 422).

Die Statusautomat-/Freigabe-Tore der Datenbank werfen über PL/pgSQL
`RAISE EXCEPTION` (SQLSTATE P0001, „raise_exception") mit einer klaren
deutschen Fachmeldung — z. B. „Auftrag …: Freigabe ohne Beauftragungsnachweis
in Textform ist unzulässig (A-26)". Solche Verstöße sind erwartbare Fachfehler,
keine Programmierfehler: sie sollen als 422 mit der Meldung beim Aufrufer landen,
nicht als generischer 500.

Alle übrigen DB-Fehler (Unique-/FK-/Check-Verletzungen, Verbindungsfehler …)
bleiben unangetastet und propagieren wie bisher — mit **einer** benannten
Ausnahme: den Constraints der Zeiterfassung (Migration 0066). Eine Überlappung
zweier Zeitbuchungen und eine zweite laufende Stempeluhr sind Bedienfehler des
Monteurs, keine Programmierfehler; sie gehören als 422 mit lesbarer Meldung ins
UI und nicht als 500 ins Log. Ein Browser-Durchlauf hat genau das aufgedeckt.
Die Zuordnung erfolgt über den **Constraint-Namen**, nicht über den SQLSTATE
allein — sonst würde jede beliebige Unique-Verletzung im System stillschweigend
zu einem 422 umgedeutet.
"""
import re
from contextlib import contextmanager

from django.db import Error

_BUSINESS_SQLSTATE = "P0001"  # PL/pgSQL RAISE EXCEPTION ohne eigenen SQLSTATE

# Manche DB-Meldungen tragen den technischen Weg gleich mit („… erfordert eine
# Begruendung (SET LOCAL app.correction_reason)"). Das ist ein Hinweis an die
# Anwendungsschicht, keiner an den Monteur — im UI hat er nichts zu suchen.
# Die Fachaussage bleibt stehen, nur der Klammerzusatz faellt weg.
_TECHNIK = re.compile(r"\s*\(SET LOCAL [^)]*\)")

# 23P01 = exclusion_violation, 23505 = unique_violation.
_CONSTRAINT_SQLSTATES = {"23P01", "23505"}

# Constraint-Name → Fachmeldung. Nur diese sind Bedienfehler.
_CONSTRAINT_MESSAGES = {
    "excl_time_entry_overlap": (
        "Die Zeitbuchung überschneidet sich mit einer bereits erfassten Zeit "
        "desselben Mitarbeiters. Zeiten dürfen sich nicht überlappen."
    ),
    "uq_time_entry_running": (
        "Es läuft bereits eine Zeitbuchung. Bitte zuerst stoppen."
    ),
    "uq_time_category_name_active": (
        "Eine aktive Zeitkategorie mit diesem Namen existiert bereits."
    ),
    "work_day_unique": (
        "Für diesen Mitarbeiter existiert bereits ein Arbeitstag mit diesem Datum."
    ),
    # Zwei Bearbeiter schreiben gleichzeitig Positionen desselben Entwurfs: Die
    # Positionsnummer wird aus dem Bestand abgeleitet. Die Schreiber (`beleg.
    # add_invoice_line`, `beleg.set_invoice_advances`) sperren die Rechnung
    # `FOR UPDATE` und lesen den Bestand innerhalb der Transaktion — das
    # serialisiert sie gegeneinander. Bleibt die UNIQUE als letzte Instanz (Gürtel
    # und Hosenträger) — sie darf nie als 500 enden.
    "invoice_line_invoice_id_position_number_key": (
        "Die Position wurde gleichzeitig von jemand anderem geändert. Bitte den "
        "Beleg neu laden und den Vorgang wiederholen."
    ),
}


def _business_message(exc):
    """Fachmeldung eines DB-Tor-/Constraint-Fehlers, sonst None."""
    cause = getattr(exc, "__cause__", None)
    sqlstate = getattr(cause, "sqlstate", None)

    if sqlstate == _BUSINESS_SQLSTATE:
        text = str(cause) if cause is not None else str(exc)
        if not text:
            return None
        return _TECHNIK.sub("", text.splitlines()[0]).strip()

    if sqlstate in _CONSTRAINT_SQLSTATES:
        name = getattr(getattr(cause, "diag", None), "constraint_name", None)
        return _CONSTRAINT_MESSAGES.get(name)

    return None


@contextmanager
def as_business_error():
    """Kontextmanager: fängt einen fachlichen DB-Tor-Fehler und wirft ihn als
    ValueError (→ 422). Andere DB-Fehler bleiben unverändert.

    Um den Commit-Zeitpunkt der DEFERRED Constraint-Trigger einzuschließen, muss
    die zu prüfende business_transaction INNERHALB dieses Kontexts liegen.
    """
    try:
        yield
    except Error as exc:
        message = _business_message(exc)
        if message is not None:
            raise ValueError(message) from exc
        raise
