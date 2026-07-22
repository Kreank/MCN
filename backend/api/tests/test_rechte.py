"""Tests des Rechte-Service und seiner Durchsetzung in der API.

Zwei Ebenen:
  * db_core.services.rechte — Auswertung der Rechtematrix (Rollen addieren
    Rechte, abgelaufene Rollen zählen nicht, row_scope 'ALLE'/'EIGENE').
  * api.permissions.require — 403, wenn das Recht fehlt bzw. dem Login-Konto kein
    app_user zugeordnet ist. Zusätzlich über die echte HR-API geprüft.
"""
from datetime import date, timedelta

import pytest
from django.test import Client

from db_core.services import rechte as rechte_service

from .conftest import grant_role, make_app_user, make_role_user


@pytest.mark.django_db
def test_nur_lesen_darf_lesen_nicht_anlegen():
    au = make_app_user()
    grant_role(au.id, "NUR_LESEN")
    perms = rechte_service.effective_permissions(au.id)
    assert ("identity", "LESEN") in perms
    assert ("identity", "ANLEGEN") not in perms


@pytest.mark.django_db
def test_abgelaufene_rolle_zaehlt_nicht():
    au = make_app_user()
    # valid_until in der Vergangenheit → am Stichtag heute nicht mehr gültig.
    grant_role(
        au.id, "ADMINISTRATION",
        valid_from=date.today() - timedelta(days=30),
        valid_until=date.today() - timedelta(days=1),
    )
    assert rechte_service.active_role_codes(au.id) == set()
    assert rechte_service.effective_permissions(au.id) == {}


@pytest.mark.django_db
def test_zwei_rollen_addieren_rechte():
    au = make_app_user()
    grant_role(au.id, "MONTEUR")       # workflow (u. a. ANLEGEN)
    grant_role(au.id, "BUCHHALTUNG")   # invoicing (u. a. STORNIEREN)
    perms = rechte_service.effective_permissions(au.id)
    assert ("workflow", "ANLEGEN") in perms      # aus MONTEUR
    assert ("invoicing", "STORNIEREN") in perms  # aus BUCHHALTUNG


@pytest.mark.django_db
def test_row_scope_eigene_vs_alle():
    monteur = make_app_user()
    grant_role(monteur.id, "MONTEUR")
    admin = make_app_user()
    grant_role(admin.id, "ADMINISTRATION")

    monteur_perms = rechte_service.effective_permissions(monteur.id)
    admin_perms = rechte_service.effective_permissions(admin.id)
    assert monteur_perms[("workflow", "LESEN")] == "EIGENE"
    assert admin_perms[("workflow", "LESEN")] == "ALLE"


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["DISPOSITION", "BUCHHALTUNG", "NUR_LESEN"])
def test_hr_modul_fuer_nicht_admin_gesperrt(role):
    """Personaldaten (Modul hr) sehen/pflegen nur ADMINISTRATION/GESCHAEFTSFUEHRUNG
    (DSGVO Art. 9). Alle übrigen Rollen haben auf hr KEIN Recht — auch NUR_LESEN
    nicht (kein LESEN).

    Ausnahme MONTEUR seit Migration 0068 — siehe den nächsten Test."""
    au = make_app_user()
    grant_role(au.id, role)
    perms = rechte_service.effective_permissions(au.id)
    assert ("hr", "LESEN") not in perms
    assert not any(module == "hr" for (module, _action) in perms)


@pytest.mark.django_db
def test_monteur_hr_nur_eigene(  # Migrationen 0068 (Zeiterfassung), 0130 (Anträge)
):
    """Der MONTEUR braucht `hr` für SICH SELBST — aber ausschließlich EIGENE.

    Ohne diese Rechte könnte er weder seine eigene Arbeitszeit erfassen (die
    Aufzeichnungspflicht nach § 17 MiLoG liefe ins Leere) noch seinen Urlaub
    beantragen — das musste bis Migration 0130 das Büro für ihn tun.

    `EIGENE` ist fail-closed: alle `require`-gesicherten hr-Endpunkte
    (Personalliste, Abwesenheiten aller, Verträge) antworten für ihn weiter mit
    403. Nur die Endpunkte, die den Scope ausdrücklich auswerten und die
    Objektgrenze selbst ziehen, lassen ihn durch — für ANLEGEN ist das allein
    der Abwesenheitsantrag am eigenen Personalsatz.

    Zwei Rechte fehlen ihm bewusst und dauerhaft:

    * **FREIGEBEN** — Arbeitstag bestätigen und Abwesenheit genehmigen sind
      Führungsaufgaben. Wer beantragt, entscheidet nicht über sich selbst.
    * **EXPORTIEREN** — Personalauswertungen über den ganzen Betrieb.
    """
    au = make_app_user()
    grant_role(au.id, "MONTEUR")
    perms = rechte_service.effective_permissions(au.id)
    assert perms[("hr", "LESEN")] == "EIGENE"
    assert perms[("hr", "AENDERN")] == "EIGENE"
    assert perms[("hr", "ANLEGEN")] == "EIGENE"
    assert ("hr", "FREIGEBEN") not in perms
    assert ("hr", "EXPORTIEREN") not in perms


@pytest.mark.django_db
def test_monteur_hr_verwaltungsendpunkte_403(client_with_role):
    """fail-closed: `require` wirft bei EIGENE — die Personalliste bleibt zu."""
    c = client_with_role("MONTEUR")
    assert c.get("/api/hr/employees").status_code == 403
    assert c.get("/api/hr/absences").status_code == 403
    assert c.get("/api/zeiterfassung").status_code == 403


@pytest.mark.django_db
def test_hr_modul_fuer_admin_erlaubt():
    au = make_app_user()
    grant_role(au.id, "ADMINISTRATION")
    perms = rechte_service.effective_permissions(au.id)
    assert ("hr", "LESEN") in perms


@pytest.mark.django_db
def test_hr_api_disposition_403_admin_200(client_with_role):
    # DISPOSITION hat kein hr-Recht → 403; ADMINISTRATION → 200 (leere Liste ok).
    dispo = client_with_role("DISPOSITION")
    assert dispo.get("/api/hr/employees").status_code == 403
    admin = client_with_role("ADMINISTRATION")
    assert admin.get("/api/hr/employees").status_code == 200


@pytest.mark.django_db
def test_konto_ohne_app_user_403(client_with_role):
    # Login ohne zugeordneten app_user → require.actor_id lehnt mit klarer Meldung ab.
    c = client_with_role("ADMINISTRATION", with_app_user=False)
    r = c.get("/api/identity/parties")
    assert r.status_code == 403
    assert "app_user" in r.json()["detail"]


@pytest.mark.django_db
def test_csrf_pflicht_bei_post():
    """POST ohne X-CSRFToken-Header wird von der SessionAuth abgelehnt (403),
    bevor das Recht überhaupt geprüft wird."""
    user, _ = make_role_user("ADMINISTRATION")
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(user)
    r = csrf_client.post(
        "/api/identity/parties/person",
        data={"first_name": "Ohne", "last_name": "Csrf"},
        content_type="application/json",
    )
    assert r.status_code == 403
