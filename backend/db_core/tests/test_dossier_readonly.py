"""Das Dossier ist READ-ONLY — statisch nachgewiesen, nicht bloß behauptet.

Ein Dossier ist eine Auskunft. Es darf **nichts verändern**: kein
`business_transaction`, kein `save()`, kein `create()`, kein `delete()`. Diese
Datei prüft das am Quelltext (Muster: `test_abrechnung_service.py::
test_abrechnung_schreibt_niemals_in_den_artikelstamm`) und zusätzlich am
Verhalten: Ein Dossier-Aufruf hinterlässt keine einzige neue Zeile in der
Datenbank.

Warum statisch UND verhaltensbasiert? Der statische Scan fängt den Fall, den ein
Verhaltenstest nie sieht: einen Schreibpfad in einem selten betretenen Zweig (ein
„leg das Dokument doch gleich an"-Komfort, ein Cache-Feld, ein Stempel „zuletzt
angesehen"). Genau so etwas rutscht später hinein — und dann ist eine
GoBD-relevante Änderung an einen GET-Aufruf gekoppelt, den jede KI-Anfrage
auslöst.
"""
import inspect
import re
from datetime import date

import pytest
from django.db import connection

from db_core.models import (
    Invoice,
    Party,
    Project,
    Property,
    StatusChange,
    Task,
    WorkOrder,
)
from db_core.services import dossier
from db_core.services import identity as identity_service
from db_core.services import projekt as projekt_service
from db_core.services import property as property_service

# Alles, was schreibt. `business_transaction` steht bewusst dabei: Es ist die
# EINE Klammer, durch die jeder fachliche Write laufen muss — taucht sie im
# Dossier auf, ist der Slice per Definition kein Read-Service mehr.
VERBOTENE_SCHREIBPFADE = (
    "business_transaction",
    "run_business_transaction",
    ".objects.create(",
    ".objects.update(",
    ".objects.delete(",
    ".objects.bulk_create(",
    ".objects.get_or_create(",
    ".objects.update_or_create(",
    ".save(",
    ".delete()",
)


def test_dossier_service_enthaelt_keinen_schreibpfad():
    """Statisch: `db_core/services/dossier.py` schreibt nirgends."""
    quelle = inspect.getsource(dossier)
    # Kommentare/Docstrings ausblenden — sie dürfen die Wörter erklären.
    ohne_docstrings = re.sub(r'"""(?:.|\n)*?"""', "", quelle)
    code = "\n".join(
        zeile for zeile in ohne_docstrings.splitlines()
        if not zeile.lstrip().startswith("#")
    )
    for verboten in VERBOTENE_SCHREIBPFADE:
        assert verboten not in code, (
            f"dossier.py enthält einen Schreibpfad ('{verboten}'). Ein Dossier ist "
            "eine Auskunft — es darf nichts verändern."
        )


def test_dossier_api_enthaelt_keinen_schreibpfad():
    """Statisch: der Router hat nur GETs und keinen Write-Import."""
    from api import dossier as dossier_api

    quelle = inspect.getsource(dossier_api)
    assert "business_transaction" not in quelle
    for methode in ("@router.post", "@router.put", "@router.patch", "@router.delete"):
        assert methode not in quelle, (
            f"api/dossier.py registriert {methode} — ein Dossier ist rein lesend."
        )


@pytest.mark.django_db
def test_dossier_aufruf_veraendert_die_datenbank_nicht(app_user):
    """Verhalten: vier Dossier-Aufrufe, keine einzige neue Zeile — auch nicht im
    Audit oder im Statusprotokoll."""
    actor = app_user.id
    obj = property_service.create_property(
        actor, name="Read-only", property_type="WEG", street="A", house_number="1",
        postal_code="10115", city="Berlin",
    )
    kunde = identity_service.create_person(actor, first_name="Rita", last_name="Read")
    projekt = projekt_service.create_project(
        actor, name="Nur lesen", property_ids=[obj.id]
    )
    from db_core.services import auftrag as auftrag_service

    order = auftrag_service.create_work_order(
        actor, property_id=obj.id, title="Nichts ändern", project_id=projekt.id
    )

    def _audit_zeilen():
        # audit.audit_entry hat kein Model (es ist reine Trigger-Senke) — deshalb
        # roh gezählt. Jeder fachliche Write landet hier; bleibt der Zähler stehen,
        # hat das Dossier nachweislich nichts geschrieben.
        with connection.cursor() as cur:
            cur.execute("SELECT count(*) FROM audit.audit_entry")
            return cur.fetchone()[0]

    def _zaehler():
        return {
            "audit": _audit_zeilen(),
            "status": StatusChange.objects.count(),
            "invoice": Invoice.objects.count(),
            "task": Task.objects.count(),
            "party": Party.objects.count(),
            "property": Property.objects.count(),
            "project": Project.objects.count(),
            "work_order": WorkOrder.objects.count(),
        }

    vorher = _zaehler()
    alles = dossier.Sicht(
        identity=True, property=True, workflow=True, invoicing=True,
        pricing=True, content=True, maintenance=True,
    )
    dossier.kontakt_dossier(kunde.id, alles)
    dossier.liegenschaft_dossier(obj.id, alles)
    dossier.projekt_dossier(projekt.id, alles)
    dossier.auftrag_dossier(order.id, alles)
    assert _zaehler() == vorher, "Ein Dossier-Aufruf hat die Datenbank verändert."


@pytest.mark.django_db
def test_unbekannte_entitaet_wirft_nicht_gefunden(app_user):
    """`DossierNichtGefunden` (→ 404) statt eines nichtssagenden 500."""
    import uuid

    leer = dossier.Sicht()
    for fn in (
        dossier.kontakt_dossier,
        dossier.liegenschaft_dossier,
        dossier.projekt_dossier,
        dossier.auftrag_dossier,
    ):
        with pytest.raises(dossier.DossierNichtGefunden):
            fn(uuid.uuid4(), leer)


@pytest.mark.django_db
def test_sicht_ohne_rechte_liefert_nur_den_kern(app_user):
    """Die Sicht ist fail-closed: ohne Flags kommt kein einziger Zusatzbaustein."""
    actor = app_user.id
    obj = property_service.create_property(
        actor, name="Kernsicht", property_type="WEG", street="A", house_number="1",
        postal_code="10115", city="Berlin",
    )
    kunde = identity_service.create_person(actor, first_name="Kern", last_name="Sicht")
    d = dossier.kontakt_dossier(kunde.id, dossier.Sicht())
    assert d["kontakt"]["display_name"] == "Kern Sicht"
    for baustein in (
        "liegenschaften", "vorgaenge", "auftraege", "aufgaben", "offene_posten",
        "zahlungsverhalten", "kommunikation", "dokumente",
    ):
        assert d[baustein] is None, f"{baustein} kam ohne Recht mit."

    liegenschaft = dossier.liegenschaft_dossier(obj.id, dossier.Sicht())
    assert liegenschaft["liegenschaft"]["name"] == "Kernsicht"
    for baustein in (
        "vorgaenge", "auftraege", "einsaetze", "zutrittshinweise", "faelligkeiten",
        "wartungsvertraege", "offene_posten", "dokumente",
    ):
        assert liegenschaft[baustein] is None, f"{baustein} kam ohne Recht mit."
    assert date.today() is not None  # (Marker: der Test läuft mit echter Zeit)
