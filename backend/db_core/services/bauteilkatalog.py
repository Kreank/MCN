"""Bauteilkatalog (property.component_template) — Vorauswahl statt Zahlentipperei.

Fachlicher Hintergrund und die DB-Invarianten stehen im Modulkopf von
``db_core/migrations/0090_bauteilkatalog.py``. Zwei davon prägen diesen Service:

**1. Die Vorlage ist eine KOPIERQUELLE, kein Verweis.**  Dieser Service pflegt
den Katalog — er rechnet nichts und wird vom Heizlast-Rechner **nie** gelesen.
Beim Erfassen kopiert ``services/raum.set_aufbau`` den ``u_value`` der Vorlage in
die Zeile (``room_surface``/``room_opening``); ``template_id`` bleibt ein
**Herkunftsvermerk**. Korrigiert der Betrieb später einen Katalogwert, ändert das
**kein bestehendes Aufmaß** — dieselbe Regel wie bei der Belegposition, und aus
demselben Grund: Was dem Kunden vorgerechnet wurde, darf sich nicht hinterrücks
verschieben.

**2. Der Katalog wird OHNE U-Werte ausgeliefert.**  Die Seed-Zeilen tragen nur
Namen (Normrecht: keine DIN-Tabellen im Produkt). Eine Vorlage ohne ``u_value``
ist damit der **Normalzustand**, kein Fehler — sie verhält sich wie ein fehlender
U-Wert an der Wand: die Heizlast ist dann **unbekannt, nicht 0**. Deshalb gibt es
hier keine Pflicht, einen U-Wert zu hinterlegen, und keinen erfundenen Vorgabewert.

**Kein DELETE.**  ``property.component_template`` trägt den No-Delete-Trigger: Eine
Vorlage, die schon in einem Aufmaß steckt, würde ihre Herkunftsangabe ins Leere
zeigen lassen. Stillgelegt wird über ``status = 'INAKTIV'`` — das steuert nur die
**Auswahl** beim Erfassen, bestehende Zeilen bleiben unberührt (ihr Wert ist ja
kopiert).

Die Zahlen- und Skalenprüfung (``numeric(5, 3)``) teilt sich dieser Service mit
``services/raum`` — dieselbe Stelle, dieselben Grenzen, dieselben deutschen
Meldungen. Die Abhängigkeit läuft **nur in diese Richtung** (Katalog → Raum-Helfer);
``raum`` liest den Katalog über das Model, nicht über diesen Service, damit kein
Importzirkel entsteht.
"""
import uuid

from django.db import DataError, IntegrityError

from db_core.db_context import business_transaction
from db_core.models import ComponentTemplate
from db_core.services.raum import (
    NUMERIK_FLAECHE,
    OPENING_TYPES,
    SURFACE_TYPES,
    _constraint,
    _db_fehler,
    _dec,
    _numerik,
    _insert,
)

# --- Codelisten (Migration 0090) -------------------------------------------

KINDS = ("FLAECHE", "OEFFNUNG")
TEMPLATE_STATUS = ("AKTIV", "INAKTIV")

TEMPLATE_FIELDS = (
    "kind", "name", "default_surface_type", "default_opening_type",
    "u_value", "note", "status", "sort_index",
)

# integer in der DB.
MAX_SORT_INDEX = 2_147_483_647


# --- Lesen -----------------------------------------------------------------

def list_templates(kind=None, nur_aktive=True):
    """Vorlagen des Katalogs, sortiert wie im Auswahlmenü.

    ``nur_aktive`` ist der **Regelfall**: Eine stillgelegte Vorlage darf nicht
    mehr neu gewählt werden. Sie bleibt aber lesbar (``nur_aktive=False``), denn
    bestehende Aufmaße verweisen weiter auf sie („aus: Fenster, 2-fach") — die
    Herkunft muss anzeigbar bleiben.
    """
    if kind is not None and kind not in KINDS:
        raise ValueError(
            f"Ungültiges kind '{kind}'. Erlaubt: {', '.join(KINDS)}."
        )
    qs = ComponentTemplate.objects.all()
    if kind is not None:
        qs = qs.filter(kind=kind)
    if nur_aktive:
        qs = qs.filter(status="AKTIV")
    return list(qs.order_by("kind", "sort_index", "name", "id"))


def get_template(template_id):
    return ComponentTemplate.objects.filter(id=template_id).first()


# --- Validierung -----------------------------------------------------------

def _pruefe(daten, bestand=None):
    """Vorabvalidierung eines (Teil-)Payloads → normalisierte Werte.

    ``bestand`` ist die vorhandene Zeile beim PATCH: Sie liefert die **wirksame**
    Gattung (``kind``), gegen die die Bauteilart geprüft wird.
    """
    unbekannt = set(daten) - set(TEMPLATE_FIELDS)
    if unbekannt:
        raise ValueError(f"Unbekannte Felder: {', '.join(sorted(unbekannt))}")

    werte = dict(daten)

    # Die Gattung ist die Identität der Vorlage. Sie nachträglich zu drehen würde
    # jede Zeile, die schon auf sie zeigt, umdeuten (eine Wand hinge plötzlich an
    # einer Fenstervorlage). Wer sich vertan hat, legt eine neue Vorlage an und
    # setzt die alte auf INAKTIV.
    if bestand is None:
        kind = werte.get("kind")
        if kind not in KINDS:
            raise ValueError(
                f"Ungültiges kind '{kind}'. Erlaubt: {', '.join(KINDS)}."
            )
    else:
        if "kind" in werte and werte["kind"] != bestand.kind:
            raise ValueError(
                "Die Gattung (kind) einer Vorlage lässt sich nicht ändern — "
                "bestehende Aufmaße zeigen darauf. Neue Vorlage anlegen und die "
                "alte auf INAKTIV setzen."
            )
        werte.pop("kind", None)
        kind = bestand.kind

    if "name" in werte:
        name = (werte["name"] or "").strip()
        if not name:
            raise ValueError("name darf nicht leer sein.")
        werte["name"] = name
    elif bestand is None:
        raise ValueError("Pflichtfelder fehlen: name.")

    st = werte.get("default_surface_type")
    if st is not None and st not in SURFACE_TYPES:
        raise ValueError(
            f"Ungültiger default_surface_type '{st}'. "
            f"Erlaubt: {', '.join(SURFACE_TYPES)}."
        )
    ot = werte.get("default_opening_type")
    if ot is not None and ot not in OPENING_TYPES:
        raise ValueError(
            f"Ungültiger default_opening_type '{ot}'. "
            f"Erlaubt: {', '.join(OPENING_TYPES)}."
        )
    # component_template_art_passt_zur_gattung: die Art gehört zur Gattung.
    if kind == "FLAECHE" and ot is not None:
        raise ValueError(
            "Eine Flächenvorlage (kind='FLAECHE') schlägt keine Öffnungsart vor "
            "(default_opening_type)."
        )
    if kind == "OEFFNUNG" and st is not None:
        raise ValueError(
            "Eine Öffnungsvorlage (kind='OEFFNUNG') schlägt keine Flächenart vor "
            "(default_surface_type)."
        )

    if "u_value" in werte:
        u = _numerik(_dec(werte["u_value"], "u_value"), "u_value",
                     NUMERIK_FLAECHE["u_value"])
        if u is not None and u <= 0:
            raise ValueError("u_value muss größer als 0 sein.")
        # NULL bleibt NULL: Eine Vorlage ohne U-Wert ist der Auslieferungszustand,
        # kein Mangel — sie wird NICHT mit 0 gefüllt.
        werte["u_value"] = u

    if "status" in werte:
        if werte["status"] is None:
            raise ValueError("status ist ein Pflichtfeld und darf nicht leer sein.")
        if werte["status"] not in TEMPLATE_STATUS:
            raise ValueError(
                f"Ungültiger status '{werte['status']}'. "
                f"Erlaubt: {', '.join(TEMPLATE_STATUS)}."
            )

    if "sort_index" in werte:
        if werte["sort_index"] is None:
            raise ValueError("sort_index ist ein Pflichtfeld und darf nicht leer sein.")
        try:
            si = int(werte["sort_index"])
        except (TypeError, ValueError) as exc:
            raise ValueError("sort_index ist keine ganze Zahl.") from exc
        if not (-MAX_SORT_INDEX <= si <= MAX_SORT_INDEX):
            raise ValueError(
                f"sort_index muss zwischen {-MAX_SORT_INDEX} und {MAX_SORT_INDEX} liegen."
            )
        werte["sort_index"] = si

    return werte


def _dublette(name):
    return ValueError(
        f"Es gibt bereits eine Vorlage mit dem Namen '{name}' in dieser Gattung."
    )


# --- Schreiben -------------------------------------------------------------

def create_template(actor_app_user_id, daten):
    """Legt eine Vorlage an. Ein U-Wert ist NICHT Pflicht (siehe Modulkopf)."""
    werte = _pruefe(daten or {})

    zeile = {"id": uuid.uuid4()}
    for feld in TEMPLATE_FIELDS:
        if feld in werte:
            zeile[feld] = werte[feld]
    # NOT NULL in der DB: ein ausdrücklich gesendetes null ist kein „lösche das Feld".
    if zeile.get("status") is None:
        zeile["status"] = "AKTIV"
    if zeile.get("sort_index") is None:
        zeile["sort_index"] = 0

    try:
        with business_transaction(actor_app_user_id):
            _insert('property."component_template"', zeile)
    except IntegrityError as exc:
        if _constraint(exc) == "component_template_kind_name_key":
            raise _dublette(zeile["name"]) from exc
        raise _db_fehler(exc) from exc
    except DataError as exc:
        raise _db_fehler(exc) from exc
    return get_template(zeile["id"])


def update_template(actor_app_user_id, template_id, daten):
    """Teil-Update (PATCH). Nur übergebene Felder werden gesetzt.

    Ein ausdrückliches ``null`` auf ``u_value`` **löscht** den Wert (die Vorlage
    ist dann wieder wertlos — unbekannt, nicht 0). Bestehende Aufmaße bleiben
    davon unberührt: ihr Wert ist eine Kopie.
    """
    bestand = ComponentTemplate.objects.filter(id=template_id).first()
    if bestand is None:
        raise ValueError(f"Bauteilvorlage {template_id} existiert nicht")

    werte = _pruefe(daten or {}, bestand=bestand)
    if not werte:
        return bestand

    try:
        with business_transaction(actor_app_user_id):
            ComponentTemplate.objects.filter(id=template_id).update(**werte)
    except IntegrityError as exc:
        if _constraint(exc) == "component_template_kind_name_key":
            raise _dublette(werte.get("name", bestand.name)) from exc
        raise _db_fehler(exc) from exc
    except DataError as exc:
        raise _db_fehler(exc) from exc
    return get_template(template_id)
