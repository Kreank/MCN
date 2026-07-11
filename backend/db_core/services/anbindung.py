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

from db_core.db_context import business_transaction
from db_core.models import SupplierConnection
from db_core.services._validation import ensure_party_usable

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
