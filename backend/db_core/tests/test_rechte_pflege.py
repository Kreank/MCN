"""Service-Tests für die Rechtematrix-PFLEGE (db_core.services.rechte_pflege).

Sicherheitskritischer Code: hier entscheidet sich, ob jemand seine eigenen Rechte
ausweiten (Privilege Escalation) oder das System aussperren kann. Geprüft werden
die drei Härtungen aus dem Modul-Docstring:

  * **Keine Selbst-Erweiterung.** Wer eine Rolle selbst trägt, darf ihre Rechte
    nicht ausweiten (allowed false→true oder Sichtfeld EIGENE→ALLE). Reduzieren
    bleibt erlaubt. Und niemand kann sich selbst eine Rolle zuweisen.
  * **Kein Verlust der letzten ADMINISTRATION.** Die letzte aktive
    ADMINISTRATION-Zuordnung lässt sich nicht beenden.

Zwei Ebenen wie in test_vier_augen.py:
  * der Service-Guard (klarer ValueError → später 422 in der API), und
  * die physische DB-Durchsetzung (CHECK role_permission_module_check), bewusst
    am Service vorbei per ORM geprüft.

Für die Härtungstests braucht es ZWEI verschiedene Akteure (der Handelnde ≠ das
Ziel); dafür der `_actor`-Helfer und `grant_role`.
"""
import re
import uuid
from datetime import date, timedelta

import pytest
from django.db.utils import IntegrityError

from api.tests.conftest import grant_role
from db_core.db_context import business_transaction
from db_core.models import AppUser, RolePermission, UserRole
from db_core.services import rechte as rechte_service
from db_core.services import rechte_pflege

KERNROLLEN = {
    "ADMINISTRATION", "GESCHAEFTSFUEHRUNG", "DISPOSITION", "TECHNISCHE_LEITUNG",
    "BUCHHALTUNG", "MONTEUR", "NUR_LESEN",
}


def _actor(name="Akteur"):
    """Ein fachlicher security.app_user als Handelnder bzw. Ziel."""
    return AppUser.objects.create(
        id=uuid.uuid4(), display_name=name, status="ACTIVE", version=1,
    )


def _cell(role_code, module, action):
    """Aktuelle Matrix-Zelle (oder None) frisch aus der DB."""
    return RolePermission.objects.filter(
        role_id=role_code, module=module, action=action
    ).first()


# --- Rollen & Matrix lesen -------------------------------------------------

@pytest.mark.django_db
def test_list_roles_enthaelt_kernrollen():
    """Alle Startrollen (Migration 0026) erscheinen als Matrix-Spalten."""
    codes = {r.code for r in rechte_pflege.list_roles()}
    assert KERNROLLEN <= codes


@pytest.mark.django_db
def test_permission_rows_enthaelt_accounting_zellen():
    """Die Matrix führt das Modul `accounting` (Migration 0032): BUCHHALTUNG darf
    lesen, MONTEUR nicht — die Zeilen sind vorhanden und korrekt vorbelegt."""
    module = {(p.role_id, p.action): p for p in rechte_pflege.permission_rows()
              if p.module == "accounting"}
    assert module, "Es gibt keine accounting-Zeilen in der Rechtematrix."
    assert module[("BUCHHALTUNG", "LESEN")].allowed is True
    assert module[("BUCHHALTUNG", "LOESCHEN")].allowed is False   # GoBD
    assert module[("MONTEUR", "LESEN")].allowed is False          # kein Zugriff
    assert module[("ADMINISTRATION", "FREIGEBEN")].allowed is True


# --- set_permission: Selbst-Erweiterung (die scharfe Grenze) ---------------

@pytest.mark.django_db
def test_selbst_erweiterung_false_true_verboten():
    """Eigene Rolle, Zelle allowed false→true: das ist eine Erweiterung → verboten.
    NUR_LESEN hat identity/ANLEGEN=false; der Träger darf sie nicht auf true setzen."""
    au = _actor()
    grant_role(au.id, "NUR_LESEN")
    assert _cell("NUR_LESEN", "identity", "ANLEGEN").allowed is False
    with pytest.raises(ValueError, match="erweitern"):
        rechte_pflege.set_permission(
            au.id, role_code="NUR_LESEN", module="identity", action="ANLEGEN",
            allowed=True,
        )
    # Die Zelle blieb unverändert (keine stille Ausweitung).
    assert _cell("NUR_LESEN", "identity", "ANLEGEN").allowed is False


@pytest.mark.django_db
def test_selbst_reduktion_erlaubt():
    """Die andere Seite der Grenze: die EIGENEN Rechte REDUZIEREN (true→false) ist
    erlaubt — man kann sich nur nicht mehr geben, wohl aber weniger."""
    au = _actor()
    grant_role(au.id, "NUR_LESEN")
    assert _cell("NUR_LESEN", "identity", "LESEN").allowed is True
    row = rechte_pflege.set_permission(
        au.id, role_code="NUR_LESEN", module="identity", action="LESEN",
        allowed=False,
    )
    assert row.allowed is False
    assert _cell("NUR_LESEN", "identity", "LESEN").allowed is False


@pytest.mark.django_db
def test_selbst_sichtfeld_eigene_zu_alle_verboten():
    """Sichtfeld verbreitern (EIGENE→ALLE) zählt ebenfalls als Erweiterung.
    MONTEUR sieht workflow/LESEN nur EIGENE; der Träger darf das nicht auf ALLE
    aufweiten."""
    au = _actor()
    grant_role(au.id, "MONTEUR")
    zelle = _cell("MONTEUR", "workflow", "LESEN")
    assert zelle.allowed is True and zelle.row_scope == "EIGENE"
    with pytest.raises(ValueError, match="erweitern"):
        rechte_pflege.set_permission(
            au.id, role_code="MONTEUR", module="workflow", action="LESEN",
            allowed=True, row_scope="ALLE",
        )
    assert _cell("MONTEUR", "workflow", "LESEN").row_scope == "EIGENE"


@pytest.mark.django_db
def test_selbst_sichtfeld_eigene_bleibt_eigene_erlaubt():
    """Grenze scharf: dieselbe Zelle auf EIGENE (unverändert) zu setzen ist KEINE
    Erweiterung und bleibt erlaubt."""
    au = _actor()
    grant_role(au.id, "MONTEUR")
    row = rechte_pflege.set_permission(
        au.id, role_code="MONTEUR", module="workflow", action="LESEN",
        allowed=True, row_scope="EIGENE",
    )
    assert row.allowed is True and row.row_scope == "EIGENE"


@pytest.mark.django_db
def test_fremde_rolle_erweitern_erlaubt():
    """Die Härtung greift NUR für selbst getragene Rollen. Ein NUR_LESEN-Träger darf
    die Rechte einer Rolle, die er NICHT hat (MONTEUR), sehr wohl ausweiten."""
    au = _actor()
    grant_role(au.id, "NUR_LESEN")
    row = rechte_pflege.set_permission(
        au.id, role_code="MONTEUR", module="identity", action="ANLEGEN",
        allowed=True, row_scope="ALLE",
    )
    assert row.allowed is True


@pytest.mark.django_db
def test_row_scope_pflege_greift_pro_rollenzeile_nicht_effektiv():
    """row_scope-Aggregation ('weiteste Sicht gewinnt') ist Sache der Auswertung;
    die Pflege-Härtung dagegen prüft die konkrete Rollenzeile. Ein Akteur mit
    MONTEUR (workflow/LESEN=EIGENE) UND DISPOSITION (workflow/LESEN=ALLE) hat
    effektiv ALLE — darf aber die MONTEUR-Zeile trotzdem nicht von EIGENE auf ALLE
    heben, weil das genau diese Rolle ausweitet."""
    au = _actor()
    grant_role(au.id, "MONTEUR")
    grant_role(au.id, "DISPOSITION")
    # Effektiv ist die Sicht bereits ALLE (weiteste gewinnt) …
    eff = rechte_service.effective_permissions(au.id)
    assert eff[("workflow", "LESEN")] == "ALLE"
    # … die MONTEUR-Zeile selbst zu erweitern bleibt dennoch verboten.
    with pytest.raises(ValueError, match="erweitern"):
        rechte_pflege.set_permission(
            au.id, role_code="MONTEUR", module="workflow", action="LESEN",
            allowed=True, row_scope="ALLE",
        )
    # Die DISPOSITION-Zeile (nicht die eigene Beschränkung) zu reduzieren ist ok.
    row = rechte_pflege.set_permission(
        au.id, role_code="DISPOSITION", module="workflow", action="LESEN",
        allowed=False,
    )
    assert row.allowed is False


# --- set_permission: ungültige Eingaben → ValueError, nicht 500 ------------

@pytest.mark.django_db
def test_set_permission_unbekanntes_modul():
    au = _actor()
    with pytest.raises(ValueError, match="Unbekanntes Modul"):
        rechte_pflege.set_permission(
            au.id, role_code="NUR_LESEN", module="quatsch", action="LESEN",
            allowed=True,
        )


@pytest.mark.django_db
def test_set_permission_unbekannte_aktion():
    au = _actor()
    with pytest.raises(ValueError, match="Unbekannte Aktion"):
        rechte_pflege.set_permission(
            au.id, role_code="NUR_LESEN", module="identity", action="TANZEN",
            allowed=True,
        )


@pytest.mark.django_db
def test_set_permission_unbekannte_rolle():
    au = _actor()
    with pytest.raises(ValueError, match="Unbekannte Rolle"):
        rechte_pflege.set_permission(
            au.id, role_code="GIBTESNICHT", module="identity", action="LESEN",
            allowed=True,
        )


@pytest.mark.django_db
def test_set_permission_unbekannter_row_scope():
    au = _actor()
    with pytest.raises(ValueError, match="row_scope"):
        rechte_pflege.set_permission(
            au.id, role_code="NUR_LESEN", module="identity", action="LESEN",
            allowed=True, row_scope="TEILWEISE",
        )


@pytest.mark.django_db
def test_modules_deckungsgleich_mit_db_check():
    """`rechte.MODULES` und der DB-CHECK auf `role_permission.module` müssen
    exakt dieselbe Menge führen.

    Die Liste driftete zweimal ab: Migration 0024 (`company`) und 0032
    (`accounting`) erweiterten den CHECK, nicht aber die Python-Konstante. Dieser
    Test schlägt an, sobald eine künftige Migration ein Modul ergänzt, ohne
    `MODULES` nachzuziehen.
    """
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT pg_get_constraintdef(oid) FROM pg_constraint
            WHERE conrelid = 'security.role_permission'::regclass
              AND contype = 'c' AND conname LIKE '%%module%%'
            """
        )
        row = cur.fetchone()
    assert row, "Kein CHECK auf role_permission.module gefunden."
    db_module = set(re.findall(r"'([a-z_]+)'::text", row[0]))
    assert db_module == set(rechte_service.MODULES)


@pytest.mark.django_db
def test_db_check_weist_ungueltiges_modul_physisch_ab():
    """Scharfe Prüfung des CHECK `role_permission_module_check`: am Service-Guard
    vorbei direkt per ORM ein unbekanntes Modul einfügen — die DB muss das physisch
    ablehnen (IntegrityError), nicht erst die App-Schicht."""
    au = _actor()
    with pytest.raises(IntegrityError):
        with business_transaction(au.id):
            RolePermission.objects.create(
                id=uuid.uuid4(), role_id="NUR_LESEN", module="quatsch",
                action="LESEN", allowed=True, row_scope="ALLE",
            )


@pytest.mark.django_db
@pytest.mark.parametrize("module", ["accounting", "company", "hr"])
def test_set_permission_nachgezogene_module_pflegbar(module):
    """Regression: `rechte.MODULES` muss jedes Modul führen, das der DB-CHECK auf
    `role_permission.module` erlaubt (hr=0021, company=0024, accounting=0032).

    Fehlte ein Modul, ließ sich seine Matrix-Zelle nicht pflegen ("Unbekanntes
    Modul"), obwohl die Matrix sie führt und `effective_permissions` sie
    durchsetzt — und `GET /security/permissions` lieferte eine unvollständige
    Spaltenliste.
    """
    au = _actor()   # trägt keine Rolle → keine Selbst-Erweiterung im Weg
    row = rechte_pflege.set_permission(
        au.id, role_code="NUR_LESEN", module=module, action="LESEN",
        allowed=True, row_scope="ALLE",
    )
    assert row.allowed is True
    assert module in rechte_service.MODULES


# --- assign_role -----------------------------------------------------------

@pytest.mark.django_db
def test_assign_role_selbstzuweisung_verboten():
    """Niemand weist sich selbst eine Rolle zu (Selbst-Erweiterung)."""
    au = _actor()
    with pytest.raises(ValueError, match="selbst"):
        rechte_pflege.assign_role(au.id, user_id=au.id, role_code="ADMINISTRATION")
    assert not UserRole.objects.filter(user_id=au.id).exists()


@pytest.mark.django_db
def test_assign_role_an_anderen_funktioniert():
    """Einem ANDEREN Benutzer eine Rolle zuweisen: legt eine gültige Zuordnung an
    (valid_from heute, granted_by = Akteur)."""
    actor = _actor("Handelnder")
    ziel = _actor("Ziel")
    ur = rechte_pflege.assign_role(actor.id, user_id=ziel.id, role_code="MONTEUR")
    assert ur.user_id == ziel.id
    assert ur.role_id == "MONTEUR"
    assert ur.valid_from == date.today()
    assert ur.granted_by_id == actor.id
    assert "MONTEUR" in rechte_service.active_role_codes(ziel.id)


@pytest.mark.django_db
def test_assign_role_mit_gueltigkeitsbeginn():
    """Ein zukünftiger valid_from wird übernommen; die Rolle ist heute noch nicht
    aktiv."""
    actor = _actor("Handelnder")
    ziel = _actor("Ziel")
    morgen = date.today() + timedelta(days=1)
    ur = rechte_pflege.assign_role(
        actor.id, user_id=ziel.id, role_code="MONTEUR", valid_from=morgen,
    )
    assert ur.valid_from == morgen
    assert "MONTEUR" not in rechte_service.active_role_codes(ziel.id)


@pytest.mark.django_db
def test_assign_role_unbekannte_rolle():
    actor = _actor("Handelnder")
    ziel = _actor("Ziel")
    with pytest.raises(ValueError, match="Unbekannte Rolle"):
        rechte_pflege.assign_role(actor.id, user_id=ziel.id, role_code="GIBTESNICHT")


@pytest.mark.django_db
def test_assign_role_unbekannter_benutzer():
    actor = _actor("Handelnder")
    with pytest.raises(ValueError, match="existiert nicht"):
        rechte_pflege.assign_role(
            actor.id, user_id=uuid.uuid4(), role_code="MONTEUR",
        )


@pytest.mark.django_db
def test_assign_role_doppelzuordnung_verboten():
    """Dieselbe Rolle zeitgleich doppelt zuweisen → klarer ValueError statt
    IntegrityError aus dem EXCLUDE."""
    actor = _actor("Handelnder")
    ziel = _actor("Ziel")
    rechte_pflege.assign_role(actor.id, user_id=ziel.id, role_code="MONTEUR")
    with pytest.raises(ValueError, match="bereits zugewiesen"):
        rechte_pflege.assign_role(actor.id, user_id=ziel.id, role_code="MONTEUR")


# --- end_user_role ---------------------------------------------------------

@pytest.mark.django_db
def test_end_user_role_beendet_zuordnung():
    """Beenden setzt valid_until (kein Löschen). Bei einer in der Vergangenheit
    begonnenen Zuordnung ist der Endtag heute."""
    actor = _actor("Handelnder")
    ziel = _actor("Ziel")
    ur = grant_role(ziel.id, "MONTEUR", valid_from=date.today() - timedelta(days=10))
    ended = rechte_pflege.end_user_role(actor.id, user_role_id=ur.id)
    assert ended.valid_until == date.today()
    assert "MONTEUR" not in rechte_service.active_role_codes(ziel.id)


@pytest.mark.django_db
def test_end_user_role_nicht_gefunden():
    actor = _actor("Handelnder")
    with pytest.raises(ValueError, match="nicht gefunden"):
        rechte_pflege.end_user_role(actor.id, user_role_id=uuid.uuid4())


@pytest.mark.django_db
def test_end_user_role_bereits_beendet():
    """Eine bereits (in der Vergangenheit) beendete Zuordnung lässt sich nicht
    erneut beenden."""
    actor = _actor("Handelnder")
    ziel = _actor("Ziel")
    ur = grant_role(
        ziel.id, "MONTEUR",
        valid_from=date.today() - timedelta(days=30),
        valid_until=date.today() - timedelta(days=1),
    )
    with pytest.raises(ValueError, match="bereits beendet"):
        rechte_pflege.end_user_role(actor.id, user_role_id=ur.id)


@pytest.mark.django_db
def test_letzte_admin_zuordnung_geschuetzt():
    """Ist es die EINZIGE aktive ADMINISTRATION-Zuordnung, lässt sie sich nicht
    beenden — sonst wäre das System nicht mehr administrierbar."""
    actor = _actor("Handelnder")
    admin = _actor("Der einzige Admin")
    ur = grant_role(admin.id, "ADMINISTRATION",
                    valid_from=date.today() - timedelta(days=10))
    with pytest.raises(ValueError, match="ADMINISTRATION"):
        rechte_pflege.end_user_role(actor.id, user_role_id=ur.id)
    # Die Zuordnung blieb aktiv.
    assert "ADMINISTRATION" in rechte_service.active_role_codes(admin.id)


@pytest.mark.django_db
def test_vorletzte_admin_darf_letzte_nicht():
    """Beide Seiten der Grenze: bei ZWEI aktiven Admins darf einer beendet werden;
    der dann verbleibende letzte nicht mehr."""
    actor = _actor("Handelnder")
    a = _actor("Admin A")
    b = _actor("Admin B")
    vergangen = date.today() - timedelta(days=10)
    ur_a = grant_role(a.id, "ADMINISTRATION", valid_from=vergangen)
    ur_b = grant_role(b.id, "ADMINISTRATION", valid_from=vergangen)

    # Vorletzte darf: A beenden geht, weil B noch aktiv ist.
    rechte_pflege.end_user_role(actor.id, user_role_id=ur_a.id)
    assert "ADMINISTRATION" not in rechte_service.active_role_codes(a.id)

    # Letzte nicht: B ist jetzt der einzige aktive Admin.
    with pytest.raises(ValueError, match="ADMINISTRATION"):
        rechte_pflege.end_user_role(actor.id, user_role_id=ur_b.id)
    assert "ADMINISTRATION" in rechte_service.active_role_codes(b.id)


@pytest.mark.django_db
def test_letzte_admins_koennen_sich_nicht_in_die_zukunft_aussperren():
    """Zwei Administratoren dürfen sich nicht beide auf denselben künftigen Tag
    beenden — ab dann hätte das System keinen Administrator mehr.

    Der Schutz prüft den ZUSTAND ZUM ENDZEITPUNKT. Prüfte er nur „heute", ginge
    jeder der beiden Aufrufe durch, weil der jeweils andere heute noch aktiv ist.
    """
    handelnder = _actor("Handelnder")
    admin_a, admin_b = _actor("Admin A"), _actor("Admin B")
    ur_a = rechte_pflege.assign_role(handelnder.id, user_id=admin_a.id, role_code="ADMINISTRATION")
    ur_b = rechte_pflege.assign_role(handelnder.id, user_id=admin_b.id, role_code="ADMINISTRATION")
    morgen = date.today() + timedelta(days=1)

    # Der erste darf beendet werden — B bleibt über `morgen` hinaus aktiv.
    rechte_pflege.end_user_role(handelnder.id, user_role_id=ur_a.id, valid_until=morgen)

    # Der zweite nicht: ab `morgen` gäbe es sonst keinen Administrator mehr.
    with pytest.raises(ValueError, match="letzte ADMINISTRATION"):
        rechte_pflege.end_user_role(handelnder.id, user_role_id=ur_b.id, valid_until=morgen)

    ur_b.refresh_from_db()
    assert ur_b.valid_until is None


@pytest.mark.django_db
def test_admin_beenden_bleibt_moeglich_wenn_nachfolger_laenger_aktiv():
    """Gegenprobe: Endet A vor B, ist das zulässig — B deckt den Zeitraum ab."""
    handelnder = _actor("Handelnder")
    admin_a, admin_b = _actor("Admin A"), _actor("Admin B")
    ur_a = rechte_pflege.assign_role(handelnder.id, user_id=admin_a.id, role_code="ADMINISTRATION")
    rechte_pflege.assign_role(handelnder.id, user_id=admin_b.id, role_code="ADMINISTRATION")

    beendet = rechte_pflege.end_user_role(
        handelnder.id, user_role_id=ur_a.id, valid_until=date.today() + timedelta(days=1)
    )
    assert beendet.valid_until == date.today() + timedelta(days=1)
