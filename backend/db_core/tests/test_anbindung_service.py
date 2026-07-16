"""Service-Tests der Lieferanten-Anbindungs-Verwaltung (pricing.supplier_connection).

Prüft Anlegen/Ändern/Deaktivieren, die Namespace-Normalisierung/-Validierung, die
Unveränderlichkeit der Identität (System/Namespace/Lieferant) und die
UNIQUE-Regel — alles gespiegelt aus den DB-Constraints (422 statt 500).
"""
import pytest
from cryptography.fernet import Fernet
from django.test import override_settings

from db_core.services import anbindung as anbindung_service
from db_core.services import identity as identity_service

TEST_KEY = Fernet.generate_key().decode()


def _lieferant(app_user, last="Großhandel"):
    return identity_service.create_person(
        app_user.id, first_name="Liefer", last_name=last
    )


def _ids_conn(app_user, *, shop_url="https://gut.example/ids"):
    return anbindung_service.create_connection(
        app_user.id, supplier_party_id=_lieferant(app_user).id,
        source_namespace="gut", label="G.U.T.", source_system="IDS_CONNECT",
        shop_url=shop_url,
    )


@pytest.mark.django_db
def test_create_grundfall(app_user):
    p = _lieferant(app_user)
    c = anbindung_service.create_connection(
        app_user.id, supplier_party_id=p.id, source_namespace="gut",
        label="G.U.T. Großhandel",
    )
    assert c.source_system == "IDS_CONNECT"      # Default
    assert c.source_namespace == "gut"
    assert c.connection_kind == "GROSSHAENDLER"  # Default
    assert c.status == "ACTIVE"
    assert c.net_price_semantics == "EINHEIT"    # Default
    assert c.supplier_party_id == p.id


@pytest.mark.django_db
def test_create_mit_gesamt_semantik(app_user):
    """GC-Quirk: eine Anbindung kann NetPrice als Positionssumme deklarieren."""
    p = _lieferant(app_user)
    c = anbindung_service.create_connection(
        app_user.id, supplier_party_id=p.id, source_namespace="gut",
        label="G.U.T.", net_price_semantics="gesamt",  # klein → normalisiert
    )
    assert c.net_price_semantics == "GESAMT"


@pytest.mark.django_db
def test_create_ungueltige_semantik_scheitert(app_user):
    p = _lieferant(app_user)
    with pytest.raises(ValueError):
        anbindung_service.create_connection(
            app_user.id, supplier_party_id=p.id, source_namespace="gut",
            label="G.U.T.", net_price_semantics="BRUTTO",
        )


@pytest.mark.django_db
def test_update_net_price_semantics(app_user):
    p = _lieferant(app_user)
    c = anbindung_service.create_connection(
        app_user.id, supplier_party_id=p.id, source_namespace="gut", label="G.U.T.",
    )
    c = anbindung_service.update_connection(
        app_user.id, connection_id=c.id, net_price_semantics="GESAMT",
    )
    assert c.net_price_semantics == "GESAMT"
    with pytest.raises(ValueError):
        anbindung_service.update_connection(
            app_user.id, connection_id=c.id, net_price_semantics="X",
        )


@pytest.mark.django_db
def test_namespace_wird_kleingeschrieben(app_user):
    p = _lieferant(app_user)
    c = anbindung_service.create_connection(
        app_user.id, supplier_party_id=p.id, source_namespace="Vaillant",
        label="Vaillant", source_system="IDS_CONNECT", connection_kind="HERSTELLER",
    )
    assert c.source_namespace == "vaillant"
    assert c.connection_kind == "HERSTELLER"


@pytest.mark.django_db
def test_namespace_ungueltig_scheitert(app_user):
    p = _lieferant(app_user)
    with pytest.raises(ValueError):
        anbindung_service.create_connection(
            app_user.id, supplier_party_id=p.id,
            source_namespace="hat leerzeichen!", label="X",
        )


@pytest.mark.django_db
def test_unbekanntes_quellsystem_scheitert(app_user):
    p = _lieferant(app_user)
    with pytest.raises(ValueError):
        anbindung_service.create_connection(
            app_user.id, supplier_party_id=p.id, source_namespace="x",
            label="X", source_system="SAP",
        )


@pytest.mark.django_db
def test_fehlender_lieferant_scheitert(app_user):
    with pytest.raises(ValueError):
        anbindung_service.create_connection(
            app_user.id, supplier_party_id=None, source_namespace="x", label="X",
        )


@pytest.mark.django_db
def test_doppelter_namespace_scheitert(app_user):
    p = _lieferant(app_user)
    anbindung_service.create_connection(
        app_user.id, supplier_party_id=p.id, source_namespace="gut", label="G.U.T.",
    )
    with pytest.raises(ValueError):
        anbindung_service.create_connection(
            app_user.id, supplier_party_id=p.id, source_namespace="gut",
            label="Zweite",
        )


@pytest.mark.django_db
def test_gleicher_namespace_anderes_system_ok(app_user):
    """UNIQUE ist (system, namespace) — derselbe Namespace unter DATANORM und
    IDS_CONNECT ist erlaubt."""
    p = _lieferant(app_user)
    anbindung_service.create_connection(
        app_user.id, supplier_party_id=p.id, source_namespace="gut",
        label="G.U.T. IDS", source_system="IDS_CONNECT",
    )
    c2 = anbindung_service.create_connection(
        app_user.id, supplier_party_id=p.id, source_namespace="gut",
        label="G.U.T. DATANORM", source_system="DATANORM",
    )
    assert c2.source_system == "DATANORM"


@pytest.mark.django_db
def test_update_pflegbare_felder(app_user):
    p = _lieferant(app_user)
    c = anbindung_service.create_connection(
        app_user.id, supplier_party_id=p.id, source_namespace="gut", label="Alt",
    )
    c = anbindung_service.update_connection(
        app_user.id, connection_id=c.id, label="Neu",
        shop_url="https://shop.example", credential_reference="gut-prod",
        connection_kind="HERSTELLER", status="INACTIVE",
    )
    assert c.label == "Neu"
    assert c.shop_url == "https://shop.example"
    assert c.credential_reference == "gut-prod"
    assert c.connection_kind == "HERSTELLER"
    assert c.status == "INACTIVE"


@pytest.mark.django_db
def test_update_unveraenderliches_feld_scheitert(app_user):
    """Namespace/System/Lieferant sind unveränderlich → nicht als Feld annehmbar."""
    p = _lieferant(app_user)
    c = anbindung_service.create_connection(
        app_user.id, supplier_party_id=p.id, source_namespace="gut", label="G.U.T.",
    )
    with pytest.raises(ValueError):
        anbindung_service.update_connection(
            app_user.id, connection_id=c.id, source_namespace="anders",
        )


@pytest.mark.django_db
def test_list_include_inactive(app_user):
    p = _lieferant(app_user)
    c = anbindung_service.create_connection(
        app_user.id, supplier_party_id=p.id, source_namespace="gut", label="G.U.T.",
    )
    anbindung_service.update_connection(app_user.id, connection_id=c.id, status="INACTIVE")
    assert anbindung_service.list_connections(include_inactive=True).count() == 1
    assert anbindung_service.list_connections(include_inactive=False).count() == 0


# --- Zugangsdaten + Punchout -----------------------------------------------

@override_settings(MCN_MAIL_KEY=TEST_KEY)
@pytest.mark.django_db
def test_credentials_setzen_und_punchout(app_user):
    conn = _ids_conn(app_user)
    st = anbindung_service.set_credentials(
        app_user.id, connection_id=conn.id, username="handwerk1",
        customer_number="4711", password="geheim",
    )
    assert st["username"] == "handwerk1"
    assert st["customer_number"] == "4711"
    assert st["has_password"] is True
    assert "password" not in st  # Passwort wird NIE zurückgegeben

    po = anbindung_service.build_punchout(
        conn.id, hook_url="https://mcn.example/hook/T1"
    )
    assert po["url"] == "https://gut.example/ids"
    assert po["method"] == "POST"
    f = po["fields"]
    assert f["action"] == "WKE"
    assert f["name_kunde"] == "handwerk1"
    assert f["pw_kunde"] == "geheim"          # entschlüsselt für den Browser-Post
    assert f["kndnr"] == "4711"
    assert f["hookurl"] == "https://mcn.example/hook/T1"
    assert f["Version"] == "2.5"


@override_settings(MCN_MAIL_KEY=TEST_KEY)
@pytest.mark.django_db
def test_passwort_none_bleibt_unveraendert(app_user):
    conn = _ids_conn(app_user)
    anbindung_service.set_credentials(
        app_user.id, connection_id=conn.id, username="u", password="pw1"
    )
    # Nur Benutzername ändern, Passwort None → bleibt.
    st = anbindung_service.set_credentials(
        app_user.id, connection_id=conn.id, username="u2", password=None
    )
    assert st["username"] == "u2" and st["has_password"] is True


@override_settings(MCN_MAIL_KEY=TEST_KEY)
@pytest.mark.django_db
def test_passwort_leer_loescht(app_user):
    conn = _ids_conn(app_user)
    anbindung_service.set_credentials(
        app_user.id, connection_id=conn.id, username="u", password="pw1"
    )
    st = anbindung_service.set_credentials(
        app_user.id, connection_id=conn.id, username="u", password=""
    )
    assert st["has_password"] is False


@override_settings(MCN_MAIL_KEY=TEST_KEY)
@pytest.mark.django_db
def test_punchout_ohne_zugangsdaten_scheitert(app_user):
    conn = _ids_conn(app_user)
    with pytest.raises(ValueError):
        anbindung_service.build_punchout(conn.id, hook_url="https://x/hook")


@override_settings(MCN_MAIL_KEY=TEST_KEY)
@pytest.mark.django_db
def test_punchout_ohne_shop_url_scheitert(app_user):
    conn = _ids_conn(app_user, shop_url=None)
    anbindung_service.set_credentials(
        app_user.id, connection_id=conn.id, username="u", password="pw"
    )
    with pytest.raises(ValueError):
        anbindung_service.build_punchout(conn.id, hook_url="https://x/hook")


@override_settings(MCN_MAIL_KEY="")
@pytest.mark.django_db
def test_credentials_ohne_schluessel_nennt_mcn_mail_key(app_user):
    """Fehlt MCN_MAIL_KEY, nennt der Fehler DAS (nicht SMTP) und kein Passwort —
    der reale Demo-Fall, wo der Schlüssel bewusst weggelassen ist."""
    conn = _ids_conn(app_user)
    with pytest.raises(ValueError) as ei:
        anbindung_service.set_credentials(
            app_user.id, connection_id=conn.id, username="u", password="geheim"
        )
    msg = str(ei.value)
    assert "MCN_MAIL_KEY" in msg
    assert "Zugangsdaten" in msg      # im IDS-Kontext formuliert, nicht als SMTP-Fehler
    assert "geheim" not in msg        # das Passwort taucht nie in der Meldung auf


@override_settings(MCN_MAIL_KEY=TEST_KEY)
@pytest.mark.django_db
def test_punchout_nur_fuer_ids(app_user):
    conn = anbindung_service.create_connection(
        app_user.id, supplier_party_id=_lieferant(app_user).id,
        source_namespace="gut", label="G.U.T. DN", source_system="DATANORM",
        shop_url="https://x/ids",
    )
    anbindung_service.set_credentials(
        app_user.id, connection_id=conn.id, username="u", password="pw"
    )
    with pytest.raises(ValueError):
        anbindung_service.build_punchout(conn.id, hook_url="https://x/hook")
