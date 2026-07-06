"""Gemeinsame pytest-Fixtures für die Backend-Tests.

Die Test-DB wird von pytest-django über die Migrationskette (inkl.
SQL-Baseline) aufgebaut — also mit allen echten Triggern und Statusautomaten.
"""
import uuid

import pytest

from db_core.models import AppUser


@pytest.fixture
def app_user(db):
    """Ein fachlicher security.app_user als Akteur für Schreibvorgänge."""
    return AppUser.objects.create(
        id=uuid.uuid4(),
        display_name="Test Sachbearbeiter",
        status="ACTIVE",
        version=1,
    )
