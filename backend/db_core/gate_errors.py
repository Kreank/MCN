"""Übersetzung fachlicher DB-Tor-Fehler in ValueError (→ HTTP 422).

Die Statusautomat-/Freigabe-Tore der Datenbank werfen über PL/pgSQL
`RAISE EXCEPTION` (SQLSTATE P0001, „raise_exception") mit einer klaren
deutschen Fachmeldung — z. B. „Auftrag …: Freigabe ohne Beauftragungsnachweis
in Textform ist unzulässig (A-26)". Solche Verstöße sind erwartbare Fachfehler,
keine Programmierfehler: sie sollen als 422 mit der Meldung beim Aufrufer landen,
nicht als generischer 500.

Alle übrigen DB-Fehler (Unique-/FK-/Check-Verletzungen, Verbindungsfehler …)
bleiben unangetastet und propagieren wie bisher.
"""
from contextlib import contextmanager

from django.db import Error

_BUSINESS_SQLSTATE = "P0001"  # PL/pgSQL RAISE EXCEPTION ohne eigenen SQLSTATE


def _business_message(exc):
    """Extrahiert bei einem P0001-Fehler die erste (Fach-)Meldungszeile, sonst None."""
    cause = getattr(exc, "__cause__", None)
    if getattr(cause, "sqlstate", None) != _BUSINESS_SQLSTATE:
        return None
    text = str(cause) if cause is not None else str(exc)
    return text.splitlines()[0].strip() if text else None


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
