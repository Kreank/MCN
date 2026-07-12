"""Fachliche Transaktionen mit Benutzerkontext und Retry.

db/README.md verlangt je Transaktion:
    SET LOCAL app.current_user_id = '<uuid>'
    SET LOCAL app.status_reason  = '<Text>'   (nur bei begründungspflichtigen Übergängen)
und dass Sperrenkonflikte (40P01 Deadlock, 40001 Serialisierung) als
wiederholbarer Fehler behandelt werden.

Verwendung — jede fachliche Schreiboperation läuft durch business_transaction:

    from db_core.db_context import business_transaction

    with business_transaction(request.user.app_user_id):
        auftrag.save()

    with business_transaction(user_id, status_reason="Kunde hat storniert"):
        service_case_zuruecksetzen(...)

Für automatischen Retry die Funktionsform verwenden:

    result = run_business_transaction(user_id, lambda: do_work(...))

Hinweise:
- SET LOCAL erfolgt über SELECT set_config(..., true) — Utility-Kommandos
  akzeptieren unter psycopg3 keine Bind-Parameter, set_config schon.
- Kein Middleware-Ansatz: ATOMIC_REQUESTS wickelt nur die View ein, eine
  Middleware liefe außerhalb der Transaktion und SET LOCAL verpuffte.
- Retry nur, wenn wir die äußerste Transaktion sind; innerhalb einer
  bestehenden Transaktion wird der Fehler weitergereicht.
"""
import time
from contextlib import contextmanager

from django.db import OperationalError, connection, transaction

RETRYABLE_SQLSTATES = {"40001", "40P01"}  # serialization_failure, deadlock_detected


def _sqlstate(exc):
    return getattr(exc.__cause__, "sqlstate", None)


def is_retryable(exc):
    return isinstance(exc, OperationalError) and _sqlstate(exc) in RETRYABLE_SQLSTATES


@contextmanager
def business_transaction(app_user_id, *, status_reason=None, correction_reason=None):
    """Eine fachliche Transaktion: atomic + SET LOCAL Benutzerkontext.

    `correction_reason` (`app.correction_reason`) ist die Begründung für eine
    Korrektur AUSSERHALB eines Statuswechsels — Beschluss B-28 (Zeit-/
    Materialänderung nach Einsatzabschluss) und das Arbeitstag-Schloss
    (Migration 0067). Anders als `status_reason` wird sie von den Triggern nicht
    verbraucht; sie gilt für die ganze Transaktion.
    """
    if app_user_id is None:
        raise ValueError(
            "Fachliche Schreiboperation ohne app_user_id: dem Login-Konto ist "
            "kein security.app_user zugeordnet (accounts.User.app_user_id)."
        )
    with transaction.atomic():
        with connection.cursor() as cur:
            cur.execute(
                "SELECT set_config('app.current_user_id', %s, true)",
                [str(app_user_id)],
            )
            if status_reason is not None:
                cur.execute(
                    "SELECT set_config('app.status_reason', %s, true)",
                    [status_reason],
                )
            if correction_reason is not None:
                cur.execute(
                    "SELECT set_config('app.correction_reason', %s, true)",
                    [correction_reason],
                )
        yield


def run_business_transaction(
    app_user_id, fn, *, status_reason=None, retries=3, backoff_seconds=0.1
):
    """Führt fn in einer business_transaction aus; Retry bei 40001/40P01."""
    can_retry = not connection.in_atomic_block
    attempt = 0
    while True:
        try:
            with business_transaction(app_user_id, status_reason=status_reason):
                return fn()
        except OperationalError as exc:
            attempt += 1
            if not (can_retry and is_retryable(exc) and attempt <= retries):
                raise
            time.sleep(backoff_seconds * attempt)
