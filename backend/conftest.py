"""Gemeinsame pytest-Fixtures für die Backend-Tests.

Die Test-DB wird von pytest-django über die Migrationskette (inkl.
SQL-Baseline) aufgebaut — also mit allen echten Triggern und Statusautomaten.
"""
import uuid

import pytest
from django.db.backends.postgresql.operations import DatabaseOperations

from db_core.models import AppUser

# ===========================================================================
# Test-Teardown und Schutzstandard vertragen sich wieder
# ===========================================================================
# Tests mit `django_db(transaction=True)` räumen am Ende über Djangos `flush`
# auf: ein TRUNCATE über alle Tabellen, die Django selbst verwaltet — darunter
# `public.accounts_user`.
#
# Unsere Fachtabellen sind `managed = False`; Django kennt sie nicht und nimmt
# sie deshalb nicht mit ins TRUNCATE. Sobald aber EINE von ihnen einen
# Fremdschlüssel auf eine Django-Tabelle hält, verweigert Postgres den Dienst:
#
#     cannot truncate a table referenced in a foreign key constraint
#     DETAIL: Table "device_token" references "accounts_user".
#
# Das ist keine Postgres-Marotte, sondern die Regel: Eine referenzierte Tabelle
# darf nur zusammen mit ihren Referenzierern geleert werden. Heute betrifft das
# `security.device_token` (Migration 0114 — ein Gerät hängt an einem
# Login-Konto). Ohne diese Vorkehrung sterben ~19 Tests im Teardown, und zwar
# ausgerechnet die, die unsere schärfsten Regeln absichern: Mahnungs-
# Schreibpfad, Abrechnung unter Nebenläufigkeit, Löschschutz.
#
# Beide naheliegenden Abkürzungen laufen in unseren eigenen Schutzstandard:
# TRUNCATE ... CASCADE trifft `trg_device_token_no_truncate`, ein DELETE trifft
# `trg_device_token_no_delete`. Beide Trigger sind richtig und bleiben.
#
# Deshalb: Die betroffenen Fachtabellen werden für den Teardown ausdrücklich
# MIT geleert, und nur für die Dauer dieses einen TRUNCATE schweigen ihre
# Trigger. Bewusst so und nicht anders:
#
#   * Die Tabellenliste kommt aus dem Katalog, nicht aus einer gepflegten
#     Konstante. Bekommt irgendwann eine zweite Fachtabelle einen
#     Fremdschlüssel auf eine Django-Tabelle, wächst das hier automatisch mit,
#     statt dieselbe Fehlersuche ein zweites Mal auszulösen.
#   * Kein CASCADE. Geleert wird genau, was der Fremdschlüssel erzwingt —
#     CASCADE würde über Fremdschlüsselketten unabsehbar weit greifen.
#   * Die Trigger sind nur während des TRUNCATE aus, nie während eines Tests.
#     Tests, die den Schutzstandard prüfen, sehen ihn unverändert scharf.
#   * Der Eingriff greift ausschließlich in Test-Datenbanken (Präfix `test_`,
#     von Django vergeben). Trifft er etwas anderes an, hält er sich komplett
#     heraus und lässt das Original laufen.
#
# Das Fachschema bleibt unangetastet: keine Migration, kein gelockerter
# Trigger, kein entfernter Fremdschlüssel.

_SQL_GESCHUETZTE_REFERENZEN = """
    SELECT DISTINCT ns.nspname, cl.relname
    FROM pg_constraint con
    JOIN pg_class cl ON cl.oid = con.conrelid
    JOIN pg_namespace ns ON ns.oid = cl.relnamespace
    JOIN pg_class ziel ON ziel.oid = con.confrelid
    JOIN pg_namespace ziel_ns ON ziel_ns.oid = ziel.relnamespace
    WHERE con.contype = 'f'
      AND ziel_ns.nspname = 'public'
      AND ns.nspname <> 'public'
    ORDER BY 1, 2
"""

# Angesetzt wird am Postgres-Backend, nicht an `BaseDatabaseOperations`: Der
# Backend überschreibt `sql_flush` selbst, ein Patch an der Basisklasse liefe
# wirkungslos ins Leere.
_original_sql_flush = DatabaseOperations.sql_flush


def _sql_flush_mit_geschuetzten_fachtabellen(
    self, style, tables, *, reset_sequences=False, allow_cascade=False
):
    """Nimmt geschützte Fachtabellen mit ins Teardown-TRUNCATE.

    Ersetzt `BaseDatabaseOperations.sql_flush` für Testläufe. Die zusätzlichen
    ALTER-TABLE-Anweisungen laufen in derselben Transaktion wie das TRUNCATE
    (siehe `execute_sql_flush`) — bricht etwas ab, sind auch die Trigger wieder
    scharf.
    """
    if not tables or not self.connection.settings_dict["NAME"].startswith("test_"):
        return _original_sql_flush(
            self,
            style,
            tables,
            reset_sequences=reset_sequences,
            allow_cascade=allow_cascade,
        )

    with self.connection.cursor() as cursor:
        cursor.execute(_SQL_GESCHUETZTE_REFERENZEN)
        referenzen = cursor.fetchall()

    if not referenzen:
        return _original_sql_flush(
            self,
            style,
            tables,
            reset_sequences=reset_sequences,
            allow_cascade=allow_cascade,
        )

    # Schema-qualifiziert durch `quote_name` — derselbe Kunstgriff wie bei
    # `db_table` in den Models (z. B. 'security"."public_link').
    tables = list(tables) + [f'{schema}"."{tabelle}' for schema, tabelle in referenzen]

    anweisungen = _original_sql_flush(
        self,
        style,
        tables,
        reset_sequences=reset_sequences,
        allow_cascade=allow_cascade,
    )

    stumm = [
        f"ALTER TABLE {schema}.{tabelle} DISABLE TRIGGER USER;"
        for schema, tabelle in referenzen
    ]
    scharf = [
        f"ALTER TABLE {schema}.{tabelle} ENABLE TRIGGER USER;"
        for schema, tabelle in referenzen
    ]
    return stumm + anweisungen + scharf


DatabaseOperations.sql_flush = _sql_flush_mit_geschuetzten_fachtabellen


@pytest.fixture
def app_user(db):
    """Ein fachlicher security.app_user als Akteur für Schreibvorgänge."""
    return AppUser.objects.create(
        id=uuid.uuid4(),
        display_name="Test Sachbearbeiter",
        status="ACTIVE",
        version=1,
    )
