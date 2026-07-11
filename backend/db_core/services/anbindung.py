"""Lieferanten-Anbindungs-Service (pricing.supplier_connection, Migration 0029/0040).

Eine Anbindung ist die Registry-Zeile eines Lieferanten-Katalogs: der Namespace,
unter dem seine Artikel/Preise im Stamm hängen (`article_supplier_reference`), das
Quellsystem (DATANORM oder IDS_CONNECT), der Shop und — als **Verweis**, nie das
Secret selbst — die Zugangsdaten (`credential_reference`, siehe unten).

Dieser Slice deckt die **Verwaltung** ab (Liste/Anlegen/Bearbeiten/Deaktivieren).
Der eigentliche IDS-Connect-Warenkorb-Roundtrip (Punchout zum Händler-Shop und
Rückfluss in ein Angebot/eine Bestellung) ist ein späterer Backend-Slice und
braucht externe Angaben (Endpunkte, Protokoll, Zugangsdaten je Händler).

Invarianten (physisch per DB-Trigger erzwungen, hier gespiegelt → 422 statt 500):
- **Identität unveränderlich**: `source_system`, `source_namespace` und
  `supplier_party_id` sind nach dem Anlegen fix (`protect_supplier_connection`,
  REV-A-05). Eine andere Zuordnung ist eine NEUE Anbindung — an einem Namespace
  hängen Artikelreferenzen. Der Bearbeiten-Pfad ändert diese Felder daher nie.
- **Kein Löschen** (GoBD-Schutzstandard): eine nicht mehr genutzte Anbindung wird
  auf `status=INACTIVE` gesetzt; der Namespace bleibt bestehen.
- `UNIQUE (source_system, source_namespace)` — vorab gespiegelt.

**Zugangsdaten-Doktrin (CLAUDE.md / 0029):** In dieser Tabelle steht NIE ein
Secret, nur `credential_reference` — ein Verweis (z. B. ein Schlüsselname im
Secret-Store). Der Wert wird als schlichter Text geführt; das eigentliche
Passwort/Token für den späteren Roundtrip liegt außerhalb (Secret-Store der
App-Schicht) und wird hier weder gespeichert noch angezeigt.
"""
import re
import uuid

from db_core import mail_crypto
from db_core.db_context import business_transaction
from db_core.models import SupplierConnection, SupplierCredential
from db_core.services._validation import ensure_party_usable

# IDS-Connect Warenkorb-Verfahren (itek 2.5): Aktionscodes des Punchout.
IDS_ACTIONS = ("WKE", "WKS")  # WKE = leeren Warenkorb füllen, WKS = eigenen übergeben
IDS_VERSION = "2.5"

_SOURCE_SYSTEMS = ("IDS_CONNECT", "DATANORM")
_KINDS = ("GROSSHAENDLER", "HERSTELLER")
_STATUS = ("ACTIVE", "INACTIVE")
# Spiegelt den DB-CHECK source_namespace ~ '^[a-z0-9][a-z0-9-]*$'.
_NAMESPACE_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _clean(value):
    """Leerstrings zu None normalisieren, Strings trimmen."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def list_connections(*, include_inactive=True):
    """Alle Anbindungen, nach System/Label sortiert (inkl. deaktivierter)."""
    qs = SupplierConnection.objects.select_related("supplier_party")
    if not include_inactive:
        qs = qs.filter(status="ACTIVE")
    return qs.order_by("source_system", "label", "id")


def create_connection(actor_app_user_id, *, supplier_party_id, source_namespace,
                      label, source_system="IDS_CONNECT",
                      connection_kind="GROSSHAENDLER", shop_url=None,
                      credential_reference=None):
    """Legt eine Lieferanten-Anbindung an (Status ACTIVE).

    `source_system`/`source_namespace`/`supplier_party_id` sind danach
    unveränderlich (Trigger) — bewusst wählen. `credential_reference` ist ein
    Verweis, kein Secret.
    """
    system = (_clean(source_system) or "IDS_CONNECT").upper()
    if system not in _SOURCE_SYSTEMS:
        raise ValueError("Quellsystem muss IDS_CONNECT oder DATANORM sein.")

    namespace = (_clean(source_namespace) or "").lower()
    if not namespace:
        raise ValueError("Namespace ist erforderlich.")
    if not _NAMESPACE_RE.match(namespace):
        raise ValueError(
            "Namespace darf nur Kleinbuchstaben, Ziffern und Bindestriche "
            "enthalten und muss mit Buchstabe/Ziffer beginnen (z. B. 'gut')."
        )

    label = _clean(label)
    if not label:
        raise ValueError("Bezeichnung ist erforderlich.")

    kind = (_clean(connection_kind) or "GROSSHAENDLER").upper()
    if kind not in _KINDS:
        raise ValueError("Art muss GROSSHAENDLER oder HERSTELLER sein.")

    if supplier_party_id is None:
        raise ValueError("Lieferant ist erforderlich.")
    ensure_party_usable(supplier_party_id, label="Lieferant")

    # UNIQUE (source_system, source_namespace) vorab spiegeln (sonst 500).
    if SupplierConnection.objects.filter(
        source_system=system, source_namespace=namespace
    ).exists():
        raise ValueError(
            f"Für {system} existiert bereits eine Anbindung mit dem Namespace "
            f"'{namespace}'."
        )

    with business_transaction(actor_app_user_id):
        conn = SupplierConnection.objects.create(
            id=uuid.uuid4(),
            supplier_party_id=supplier_party_id,
            source_system=system,
            source_namespace=namespace,
            label=label,
            shop_url=_clean(shop_url),
            credential_reference=_clean(credential_reference),
            status="ACTIVE",
            connection_kind=kind,
            version=1,
        )
    conn.refresh_from_db()
    return conn


def update_connection(actor_app_user_id, *, connection_id, **fields):
    """Ändert die pflegbaren Felder einer Anbindung.

    Nur `label`, `shop_url`, `credential_reference`, `status` und
    `connection_kind` sind änderbar — Quellsystem, Namespace und Lieferant sind
    unveränderlich (Trigger) und werden hier bewusst NICHT angenommen.
    """
    conn = SupplierConnection.objects.filter(id=connection_id).first()
    if conn is None:
        raise ValueError("Anbindung nicht gefunden.")
    allowed = ("label", "shop_url", "credential_reference", "status",
               "connection_kind")
    unknown = set(fields) - set(allowed)
    if unknown:
        raise ValueError(f"Unbekannte oder unveränderliche Felder: "
                         f"{', '.join(sorted(unknown))}")

    changed = []
    if "label" in fields:
        label = _clean(fields["label"])
        if not label:
            raise ValueError("Bezeichnung darf nicht leer sein.")
        conn.label = label
        changed.append("label")
    if "shop_url" in fields:
        conn.shop_url = _clean(fields["shop_url"])
        changed.append("shop_url")
    if "credential_reference" in fields:
        conn.credential_reference = _clean(fields["credential_reference"])
        changed.append("credential_reference")
    if "connection_kind" in fields:
        kind = (_clean(fields["connection_kind"]) or "").upper()
        if kind not in _KINDS:
            raise ValueError("Art muss GROSSHAENDLER oder HERSTELLER sein.")
        conn.connection_kind = kind
        changed.append("connection_kind")
    if "status" in fields:
        status = (_clean(fields["status"]) or "").upper()
        if status not in _STATUS:
            raise ValueError("Status muss ACTIVE oder INACTIVE sein.")
        conn.status = status
        changed.append("status")

    if changed:
        with business_transaction(actor_app_user_id):
            conn.save(update_fields=changed + ["updated_at"])
        conn.refresh_from_db()
    return conn


# --- IDS-Connect-Zugangsdaten (verschlüsselt) -------------------------------

def get_credential(connection_id):
    """Der Zugangsdaten-Satz einer Anbindung oder None."""
    return SupplierCredential.objects.filter(connection_id=connection_id).first()


def credential_status(connection_id):
    """Lesbarer Status der Zugangsdaten OHNE das Passwort (nie zurückgeben):
    `{username, customer_number, has_password}`."""
    cred = get_credential(connection_id)
    if cred is None:
        return {"username": None, "customer_number": None, "has_password": False}
    return {
        "username": cred.username,
        "customer_number": cred.customer_number,
        "has_password": cred.password_encrypted is not None,
    }


def set_credentials(actor_app_user_id, *, connection_id, **fields):
    """Legt die IDS-Zugangsdaten einer Anbindung an oder ändert sie (Upsert 1:1).

    **Nur ausdrücklich übergebene Felder** werden geändert (der Aufrufer nutzt
    `exclude_unset`): wer nur das Passwort setzt, verliert nicht den Benutzernamen.
    Felder: `username`, `customer_number` (Klartext), `password`. Für das Passwort
    gilt: **nicht übergeben** = unverändert, leer (""/None) = löschen (NULL), sonst
    Fernet-verschlüsselt speichern (`mail_crypto`/`MCN_MAIL_KEY`, fail-closed). Gibt
    den Status (ohne Passwort) zurück.
    """
    allowed = {"username", "customer_number", "password"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"Unbekannte Felder: {', '.join(sorted(unknown))}")

    conn = SupplierConnection.objects.filter(id=connection_id).first()
    if conn is None:
        raise ValueError("Anbindung nicht gefunden.")

    # Passwort-Konvention (wie Mailkonto): fehlt/None = unverändert, "" = löschen,
    # sonst setzen. Verschlüsselung VOR der Transaktion (fail-closed).
    password_gesetzt = fields.get("password") is not None
    cipher = None
    if password_gesetzt and fields["password"]:
        try:
            cipher = mail_crypto.encrypt(fields["password"])
        except mail_crypto.MailKeyError as exc:
            raise ValueError(str(exc))

    cred = get_credential(connection_id)
    with business_transaction(actor_app_user_id):
        if cred is None:
            cred = SupplierCredential.objects.create(
                id=uuid.uuid4(), connection_id=connection_id,
                username=_clean(fields.get("username")),
                customer_number=_clean(fields.get("customer_number")),
                password_encrypted=cipher,
                version=1,
            )
        else:
            changed = []
            if "username" in fields:
                cred.username = _clean(fields["username"])
                changed.append("username")
            if "customer_number" in fields:
                cred.customer_number = _clean(fields["customer_number"])
                changed.append("customer_number")
            if password_gesetzt:
                cred.password_encrypted = cipher  # None = löschen
                changed.append("password_encrypted")
            if changed:
                cred.save(update_fields=changed + ["updated_at"])
    return credential_status(connection_id)


def build_punchout(connection_id, *, hook_url, action="WKE", target=None,
                   warenkorb_xml=None):
    """Baut die IDS-Connect-Punchout-Formularfelder (itek 2.5) zum Öffnen des
    Händler-Shops.

    Der Aufrufer (Frontend) sendet ein auto-submittendes POST-Formular
    (`multipart/form-data`) an `url` (die Connector-/Shop-URL der Anbindung). Der
    Shop meldet den fertigen Warenkorb per POST an `hook_url` zurück.

    Gibt `{url, method, enctype, fields}` zurück. **Enthält das Klartext-Passwort**
    in `fields['pw_kunde']` — das ist dem IDS-Verfahren inhärent (der Browser des
    Handwerkers meldet sich beim Shop an); nur über HTTPS ausliefern.

    Wirft ValueError (→ 422), wenn Connector-URL oder Zugangsdaten fehlen oder der
    Aktionscode ungültig ist.
    """
    action = (action or "WKE").upper()
    if action not in IDS_ACTIONS:
        raise ValueError(f"Ungültige IDS-Aktion '{action}'. Erlaubt: WKE, WKS.")
    conn = SupplierConnection.objects.filter(id=connection_id).first()
    if conn is None:
        raise ValueError("Anbindung nicht gefunden.")
    if conn.source_system != "IDS_CONNECT":
        raise ValueError("Punchout ist nur für IDS-Connect-Anbindungen möglich.")
    connector_url = _clean(conn.shop_url)
    if not connector_url:
        raise ValueError(
            "Für den Punchout fehlt die Shop-/Connector-URL an der Anbindung."
        )

    cred = get_credential(connection_id)
    if cred is None or cred.password_encrypted is None or not cred.username:
        raise ValueError(
            "Für den Punchout fehlen die Zugangsdaten (Benutzername/Passwort)."
        )
    try:
        passwort = mail_crypto.decrypt(cred.password_encrypted)
    except mail_crypto.MailKeyError as exc:
        raise ValueError(str(exc))

    # IDS-2.5-Formularfelder (verbatim; nur gesetzte optionale Felder aufnehmen).
    fields = {
        "action": action,
        "name_kunde": cred.username,
        "pw_kunde": passwort,
        "hookurl": hook_url,
        "Version": IDS_VERSION,
    }
    if cred.customer_number:
        fields["kndnr"] = cred.customer_number
    if target:
        fields["Target"] = target
    if action == "WKS" and warenkorb_xml:
        fields["warenkorb"] = warenkorb_xml

    return {
        "url": connector_url,
        "method": "POST",
        "enctype": "multipart/form-data",
        "fields": fields,
    }
